"""界面与后台之间的桥：界面通过 window.pywebview.api.call(名称, 参数) 调用这里的函数。
不开本地网络端口（有的电脑上安全软件或代理会干扰 127.0.0.1 连接），视频直接用 file:// 播放。"""
import hashlib
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from core import asr, bgqueue, book, cache, codex_auth, config, distill, doc as docs, frames, glossary, jobs, library, llm, media, mentor, ocr, roundtable, tasks, tts, workflow
from core.log import LOG_DIR, log

MEDIA_FILTER = ('音视频和文档 (*.mp4;*.m4v;*.mov;*.mkv;*.webm;*.avi;*.flv;*.ts;*.wmv;*.3gp;*.mpg;*.mpeg;'
                '*.mp3;*.m4a;*.aac;*.wav;*.flac;*.ogg;*.opus;*.wma;*.amr;*.ape;*.pdf;*.docx;*.doc;*.epub;*.mobi;*.azw3;*.azw;*.pptx;*.ppt;*.txt;*.md)',
                '书籍和文档 (*.pdf;*.docx;*.doc;*.epub;*.mobi;*.azw3;*.azw;*.pptx;*.ppt;*.txt;*.md)', '所有文件 (*.*)')


def file_url(path) -> str:
    return Path(path).resolve().as_uri()


class Api:
    def __init__(self):
        self._window = None
        self._quit = threading.Event()
        self._version = ''
        self._bundled = None

    # pywebview 会把 Api 的公开方法暴露给界面；统一从 call 进来，出错时返回 {'ok': False, 'error': …}
    def call(self, name, body=None):
        fn = getattr(self, 'do_' + str(name), None)
        if fn is None:
            return {'ok': False, 'error': '未知操作：' + str(name)}
        t0 = time.time()
        try:
            out = fn(body or {})
            out.setdefault('ok', True)
            return out
        except (asr.AsrError, llm.LlmError, media.MediaError, book.BookError, OSError, ValueError, codex_auth.AuthError, tasks.WorkError) as e:
            log.warning('%s 失败：%s', name, e)
            return {'ok': False, 'error': str(e)}
        except Exception as e:
            log.exception('%s 出错', name)
            return {'ok': False, 'error': f'出错了：{e}（详情已写入日志）'}
        finally:
            spent = time.time() - t0
            if spent > 8 and name not in ('pick_media', 'pick_media_multi', 'pick_folder', 'save_dialog', 'library_add_folder', 'library_add_files'):
                log.warning('%s 用了 %.1f 秒', name, spent)
            elif name not in ('job', 'position'):
                log.debug('%s %.2fs', name, spent)

    # ---------------------------------------------------------- 文件对话框 ----
    def do_pick_media(self, b):
        import webview
        r = self._window.create_file_dialog(webview.FileDialog.OPEN, file_types=MEDIA_FILTER)
        return {'path': r[0] if r else None}

    def do_pick_media_multi(self, b):
        import webview
        r = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True, file_types=MEDIA_FILTER)
        return {'paths': list(r or [])}

    def do_pick_text(self, b):
        """选一个 md / txt 文件，读出文字（导入术语说明用）。"""
        import webview
        r = self._window.create_file_dialog(webview.FileDialog.OPEN, file_types=('文本文件 (*.md;*.txt)', '所有文件 (*.*)'))
        if not r:
            return {'text': None}
        raw = Path(r[0]).read_bytes()
        for enc in ('utf-8-sig', 'gb18030'):
            try:
                return {'text': raw.decode(enc), 'name': Path(r[0]).name}
            except UnicodeDecodeError:
                continue
        raise ValueError('读不出这个文件的文字，请用 UTF-8 编码保存后再试')

    def do_pick_file(self, b):
        """选一个文件，只返回路径。"""
        import webview
        types = ('文本文件 (*.md;*.txt)', '所有文件 (*.*)') if b.get('kind') == 'text' else ('所有文件 (*.*)',)
        r = self._window.create_file_dialog(webview.FileDialog.OPEN, file_types=types)
        return {'path': r[0] if r else None}

    def do_pick_folder(self, b):
        import webview
        r = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=b.get('start') or '')
        return {'path': r[0] if r else None}

    def do_save_dialog(self, b):
        import webview
        ext = 'srt' if b.get('ext') == 'srt' else 'txt'
        types = ('文本文件 (*.txt)',) if ext == 'txt' else ('字幕文件 (*.srt)',)
        r = self._window.create_file_dialog(webview.FileDialog.SAVE, save_filename=b.get('name') or '', file_types=types)
        if not r:
            return {'path': None}
        path = r if isinstance(r, str) else r[0]
        return {'path': path if path.lower().endswith('.' + ext) else path + '.' + ext}

    # -------------------------------------------------------------- 设置 ----
    def do_settings(self, b):
        if b.get('settings'):
            config.update(b['settings'])
        s = config.load()
        return {'settings': config.public(s), 'provider_names': config.PROVIDER_NAMES, 'chat_providers': config.CHAT_PROVIDERS,
                'teachers': config.load_teachers(), 'default_template': llm.DEFAULT_TEMPLATE,
                'doubao_models': {k: {'name': v['name'], 'short': v['short'], 'note': v['note'], 'kind': v['kind']} for k, v in asr.DOUBAO_MODELS.items()},
                'doubao_order': list(asr.DOUBAO_ORDER)}

    def do_recent(self, b):
        return {'items': [r for r in config.load().get('recent', []) if os.path.isfile(r.get('path', ''))]}

    # -------------------------------------------------------------- 文件 ----
    def do_open(self, b):
        path = os.path.abspath(str(b.get('path') or '').strip().strip('"'))
        if not os.path.isfile(path):
            raise ValueError('找不到这个文件：' + path)
        if Path(path).suffix.lower() in book.DOC_EXT:
            return self._open_book(path)
        if Path(path).suffix.lower() not in media.MEDIA_EXT:
            raise ValueError('不支持这种文件。支持常见的视频（mp4、mkv、mov、webm…）、音频（mp3、m4a、wav…）和文档（pdf、docx、txt、md）')
        info = media.probe(path)
        d = docs.load(path) or docs.new(path, info)
        d['duration'] = info['duration'] or d.get('duration', 0)
        config.add_recent(path, d.get('title') or Path(path).stem)
        plan = media.play_plan(path, info)
        if not d.get('teacher'):
            d['teacher'] = library.folder_teacher(path)
        out = {'path': path, 'info': info, 'doc': d, 'plan': plan, 'position': config.get_position(path),
               'busy': tasks.busy(docs.media_key(path)), 'summary_changes': docs.summary_changes(d), 'pending': workflow.pending_now(path, d),
               'no_summary': library.folder_no_summary(path)}
        cached = media.playable_path(path, info)
        if plan == 'direct':
            out['src'] = file_url(path)
        elif cached.is_file():
            cache.touch(cached)
            out['src'] = file_url(cached)
        else:
            def convert(job):
                job.report(0, '正在转换成可播放的格式…' if plan == 'transcode' else '正在整理视频格式…')
                p = media.make_playable(path, info, plan, lambda f: job.report(f), job.cancel)
                return {'src': file_url(p)}
            out['convert_job'] = jobs.start('convert', convert).id
        return out

    def do_save(self, b):
        path = b.get('path')
        if not path or not os.path.isfile(path):
            raise ValueError('视频文件不见了，无法保存字幕')
        # base_rev：界面打开 / 上次保存时字幕文件的版本号。期间被 Agent 改过就返回 conflict，由界面问用户（force 时直接覆盖）
        return docs.save(path, b.get('doc') or {}, None if b.get('force') or 'base_rev' not in b else b.get('base_rev'))

    def do_position(self, b):
        path = b.get('path')
        if path and os.path.isfile(path):
            config.set_position(path, float(b.get('t') or 0), float(b.get('d') or 0))
        return {}

    # -------------------------------------------------------------- 任务 ----
    def do_job(self, b):
        job = jobs.get(b.get('id'))
        if not job:
            raise ValueError('任务不存在')
        return {'job': job.snapshot(int(b.get('since') or 0))}

    def do_steps(self, b):
        """界面上的整理按钮（生成字幕、AI 校对、找关键帧、画面复核存疑句、翻译、课程重点、查漏补缺、新术语、
        整理章节、全书总览，以及一键整理）都走这里：交给任务层 workflow.process，和命令行（Agent）是同一套规则。
        结果由后台写进字幕文件，界面在任务结束后重新读取（do_doc）。job.items 里是边做边显示的内容。"""
        path, steps, redo, more = self._step_args(b)

        def run(job):
            t = workflow.process(path, steps=steps, redo=redo, lang=b.get('lang'), provider=b.get('provider'), caller='gui',
                                 check_library=False, cancel=job.cancel, on_items=job.push,
                                 on_progress=lambda d: job.report(d.get('progress') or 0, d.get('message')), **more)
            return {'task': workflow.brief(t)}
        return {'job': jobs.start('steps', run).id}

    def _step_args(self, b, need_steps=True):
        """整理请求的参数：(文件, 步骤, 做过也重做的步骤, 界面上的选项)。"""
        path = os.path.abspath(str(b.get('path') or ''))
        if not os.path.isfile(path):
            raise ValueError('文件不见了：' + path)
        steps = [str(x) for x in b.get('steps') or []]
        if not steps and need_steps:
            raise ValueError('没有要做的步骤')
        workflow.stages_for(path, steps)  # 步骤名写错直接报错
        redo = [str(x) for x in (b['redo'] if 'redo' in b else steps)]  # 界面上点了就是要做，做过也重做
        more = {k: b[k] for k in workflow.GUI_OPTIONS if b.get(k)}
        if 'transcribe' in steps:
            config.update({'last_lang': b.get('lang') or 'zh', 'last_teacher': workflow.load_doc(path).get('teacher') or ''})
        return path, steps, redo, more

    # ---------------------------------------------------------- 后台整理 ----
    def do_bg_add(self, b):
        """排进后台整理（一键整理、媒体库右键「排进后台整理」）。没给 steps：所在文件夹定了整理规则就按规则，
        没有就按一键整理默认的步骤；都是只做还没做完的。"""
        path, steps, redo, more = self._step_args(b, need_steps=False)
        rule = None if steps else library.folder_rule(path)
        if rule and workflow.kind_of(path) != 'book':
            provider, opts = self._rule_job(rule)
            it = bgqueue.add(path, rule['steps'], (), rule['lang'], provider, opts)
        else:
            it = bgqueue.add(path, steps or None, redo if steps else (), b.get('lang'), b.get('provider'), more)
        return {'item': it, 'items': bgqueue.items()}

    @staticmethod
    def _rule_job(rule):
        """文件夹的整理规则 → (识别服务, 整理选项)。和一键整理对话框一样：没选老师也做 AI 校对（只改明显的错别字）。"""
        provider = config.load().get('asr_en' if rule.get('lang') == 'en' else 'asr_zh')
        opts = {'allow_no_teacher': True}
        if provider == 'doubao':
            opts.update({k: rule[k] for k in ('doubao_model', 'doubao_account') if rule.get(k)})
        return provider, opts

    def do_folder_rule(self, b):
        """「整个文件夹排进后台整理」对话框：这个文件夹（含沿用上层）的整理规则、老师、写不写课程重点，里面有几节课。"""
        folder = os.path.abspath(str(b.get('path') or ''))
        files = library.scan(folder)
        books = sum(1 for p in files if workflow.kind_of(p) == 'book')
        return dict(library.folder_settings(folder), lessons=len(files) - books, books=books)

    def do_bg_add_folder(self, b):
        """整个文件夹排进后台整理（2026-10-28，用户的文件夹有几百节课）：按对话框里定的规则，把还没整理完的课一节一节排进去。
        规则、老师、写不写课程重点记在这个文件夹上（下层沿用，以后右键单节排队也按它）。书的步骤不一样，不排。"""
        folder = os.path.abspath(str(b.get('path') or ''))
        if not os.path.isdir(folder):
            raise ValueError('找不到这个文件夹：' + folder)
        steps = [str(x) for x in b.get('steps') or []]
        bad = [x for x in steps if x not in workflow.VIDEO_STAGES]
        if bad or not steps:
            raise ValueError('不认识的步骤：' + '、'.join(bad) if bad else '没有勾选要做的步骤')
        rule = {'steps': steps, 'lang': 'en' if b.get('lang') == 'en' else 'zh',
                'doubao_model': str(b.get('doubao_model') or ''), 'doubao_account': str(b.get('doubao_account') or '')}
        changes = {'rule': rule, 'no_summary': 'summary' not in steps}
        if 'teacher' in b and (b.get('teacher') or '') != library.folder_settings(folder)['teacher']:
            changes['teacher'] = b.get('teacher') or ''
        library.set_folder(folder, **changes)
        provider, opts = self._rule_job(rule)
        facts_opts = dict(opts, lang=rule['lang'])
        batch = uuid.uuid4().hex[:10]
        n = {'added': 0, 'done': 0, 'busy': 0, 'books': 0, 'errors': 0}
        for path in library.scan(folder):
            if workflow.kind_of(path) == 'book':
                n['books'] += 1
            elif bgqueue.queued(path):
                n['busy'] += 1
            else:
                try:
                    todo = workflow.todo_for(path, steps, facts_opts)
                    if todo:
                        bgqueue.add(path, steps, (), rule['lang'], provider, opts, batch=batch)
                except Exception:  # noqa: BLE001  一节读不出（字幕文件坏了等）不影响别的
                    log.exception('整个文件夹排队：%s', path)
                    n['errors'] += 1
                    continue
                n['added' if todo else 'done'] += 1
        log.info('整个文件夹排进后台整理：%s %s', folder, n)
        return dict(n, items=bgqueue.items())

    def do_bg_list(self, b):
        return {'items': bgqueue.items()}

    def do_bg_cancel(self, b):
        return {'stopped': bgqueue.cancel(b.get('id') or None), 'items': bgqueue.items()}

    def do_bg_clear(self, b):
        bgqueue.clear_finished()
        return {'items': bgqueue.items()}

    # ---------------------------------------------------------- 老师工作台 ----
    def do_mentor_list(self, b):
        return {'mentors': mentor.list_mentors()}

    def do_mentor_pick(self, b):
        """弹出选择文件夹的窗口（从「04、外部知识转化库/01、老师分身蒸馏资料」开始）。选好以后界面先显示「正在查找课程笔记…」，再调 mentor_add。"""
        import webview
        vault = (config.load().get('obsidian') or {}).get('vault') or ''
        tries = [os.path.join(vault, distill.KB_DIR, distill.MENTOR_SUB), os.path.join(vault, distill.KB_DIR)] if vault else []
        start = next((p for p in tries if os.path.isdir(p)), vault)
        r = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=start or '')
        return {'folder': r[0] if r else ''}

    def do_mentor_add(self, b):
        """导入一个蒸馏好的老师文件夹（04、外部知识转化库/01、老师分身蒸馏资料/老师名）。没给路径就弹出选择文件夹的窗口。"""
        folder = b.get('folder')
        if not folder:
            folder = self.do_mentor_pick(b)['folder']
            if not folder:
                return {'mentor': None}
        try:
            m = mentor.add(folder)
        except mentor.MentorError as e:
            raise ValueError(str(e))
        return {'mentor': m, 'mentors': mentor.list_mentors()}

    def do_mentor_open(self, b):
        try:
            return mentor.overview(b.get('id'))
        except mentor.MentorError as e:
            raise ValueError(str(e))

    def do_mentor_refresh(self, b):
        """课程笔记或视频挪了位置后，重新按来源表找一遍。"""
        mentor.refresh(b.get('id'))
        return mentor.overview(b.get('id'))

    def do_mentor_remove(self, b):
        if distill.running(b.get('id')):
            raise ValueError('这位老师正在蒸馏，先停止蒸馏再移除')
        mentor.remove(b.get('id'))
        return {'mentors': mentor.list_mentors()}

    def do_mentor_chat(self, b):
        """打开一个对话；没给 cid 就新建一个。"""
        cid = b.get('cid')
        return {'chat': mentor.load_chat(b.get('id'), cid) if cid else mentor.new_chat(b.get('id'))}

    def do_mentor_chats(self, b):
        return {'chats': mentor.search(b.get('id'), b.get('q') or '')}

    def do_mentor_chat_rename(self, b):
        return {'chat': mentor.rename_chat(b.get('id'), b.get('cid'), b.get('title'))}

    def do_mentor_chat_delete(self, b):
        if b.get('all'):
            return {'deleted': mentor.delete_all_chats(b.get('id'))}
        mentor.delete_chat(b.get('id'), b.get('cid'))
        return {'deleted': 1}

    def do_mentor_ask(self, b):
        """向老师提问：按老师画像 + 观点库 + 相关原文交给「对话」用的 AI，边写边显示；问答写进对话记录（中途停止也保存已写的部分）。"""
        mid, cid, q = b.get('id'), b.get('cid'), str(b.get('question') or '').strip()
        if not q:
            raise ValueError('问题是空的')
        s = config.load()
        name = s['chat']
        llm.check(s, name)
        m = mentor.get(mid)
        chat = mentor.load_chat(mid, cid)
        messages, used = mentor.build_messages(m, chat['messages'], q)
        model = workflow._model_of(s, name)
        mentor.append(mid, cid, {'role': 'user', 'content': q})
        log.info('老师对话：%s，资料 %d 字（观点 %d/%d 条，原文 %d 段），%s', m['name'], used['chars'], used['atoms'], used['atoms_total'], used['windows'], model)

        def run(job):
            parts = []
            try:
                for piece in llm.stream(s, name, messages, job.cancel):
                    parts.append(piece)
                    job.push([piece])
            finally:
                text = ''.join(parts).strip()
                if text:
                    stopped = job.cancel.is_set()
                    mentor.append(mid, cid, {'role': 'assistant', 'content': text + ('\n\n（已停止）' if stopped else ''), 'model': model})
            return {'chat': mentor.load_chat(mid, cid), 'used': used}
        return {'job': jobs.start('mentor', run).id, 'used': used}

    def do_mentor_save(self, b):
        """收藏一条回答到 Obsidian 的「对话精选.md」。"""
        try:
            return mentor.save_favorite(b.get('id'), b.get('cid'), b.get('index'))
        except mentor.MentorError as e:
            raise ValueError(str(e))

    def do_mentor_save_all(self, b):
        """一键收藏整个对话（还没收藏的回答一次写进去）。"""
        try:
            return mentor.save_all(b.get('id'), b.get('cid'))
        except mentor.MentorError as e:
            raise ValueError(str(e))

    def do_mentor_favorites(self, b):
        """对话精选在哪、在 Obsidian 里打开的链接。"""
        return mentor.favorites(b.get('id'))

    def do_mentor_quote(self, b):
        return mentor.quote(b.get('id'), str(b.get('sid') or ''), float(b.get('t') or 0))

    # ------------------------------------------------- 老师蒸馏（distill.py） ----
    @staticmethod
    def _distill(fn, *a, **k):
        try:
            return fn(*a, **k)
        except (distill.DistillError, mentor.MentorError) as e:
            raise ValueError(str(e))

    def do_distill_target(self, b):
        """新建老师时：结果会写到哪个文件夹、那里已经有哪些文件。"""
        folder = self._distill(distill.target_folder, b.get('name'))
        have = [p.name for p in folder.glob('*.md')] if folder.is_dir() else []
        m = next((x for x in mentor.list_mentors(chats=False) if mentor._same(x['folder'], folder)), None)
        return {'folder': str(folder), 'files': have, 'mentor': m}

    def do_distill_create(self, b):
        """新建老师（或给同名老师加课）：只记下用哪些课，不调用 AI。"""
        m = self._distill(distill.create, b.get('name'), b.get('paths') or [])
        return {'mentor': m, 'mentors': mentor.list_mentors()}

    def do_distill_add(self, b):
        paths = self._distill(distill._check_paths, b.get('paths') or []) if b.get('paths') else []
        n = self._distill(distill.add_lessons, b.get('id'), paths)
        return {'added': n, 'mentors': mentor.list_mentors()}

    def do_distill_remove_lesson(self, b):
        self._distill(distill.remove_lesson, b.get('id'), str(b.get('sid') or ''))
        return self._distill(distill.status, b.get('id'))

    def do_distill_status(self, b):
        return self._distill(distill.status, b.get('id'))

    def do_distill_start(self, b):
        """开始（或接着）蒸馏，后台做；在 Obsidian 里改过要覆盖的文件时先返回 conflict 让用户确认。"""
        return self._distill(distill.start, b.get('id'), bool(b.get('force')))

    # -------------------------------------- 圆桌讨论、主题蒸馏（roundtable.py） ----
    @staticmethod
    def _round(fn, *a, **k):
        try:
            return fn(*a, **k)
        except (roundtable.RoundError, distill.DistillError, mentor.MentorError) as e:
            raise ValueError(str(e))

    def do_prompts(self, b):
        """软件用的全部蒸馏提示词（界面「查看提示词」）。"""
        return {'prompts': roundtable.prompt_list()}

    def do_round_list(self, b):
        return {'rounds': roundtable.list_rounds(), 'topics': roundtable.list_topics(), 'folders': roundtable.TOPIC_FOLDERS}

    def do_round_new(self, b):
        return {'chat': self._round(roundtable.new_round, b.get('mentors') or [])}

    def do_round_load(self, b):
        c = self._round(roundtable.load_round, b.get('cid'))
        return {'chat': c, 'running': roundtable.running(c['id'])}

    def do_round_ask(self, b):
        """圆桌提问：每位老师各自回答，再总结；后台做，job 的条目是 {kind: start|text|reset|done, who, …}。"""
        return self._round(roundtable.ask, b.get('cid'), b.get('question'), b.get('summarize', True) is not False)

    def do_round_reply(self, b):
        return self._round(roundtable.reply, b.get('cid'), b.get('summarize', True) is not False)

    def do_round_summary(self, b):
        return self._round(roundtable.summarize, b.get('cid'))

    def do_round_rename(self, b):
        return {'chat': self._round(roundtable.rename_round, b.get('cid'), b.get('title'))}

    def do_round_delete(self, b):
        self._round(roundtable.delete_round, b.get('cid'))
        return {}

    def do_round_save(self, b):
        """整场讨论收藏到 Obsidian 的「02、主题蒸馏资料/对应主题」。"""
        return self._round(roundtable.save_round, b.get('cid'), b.get('folder'))

    def do_topic_get(self, b):
        return self._round(roundtable.get_topic, b.get('id'))

    def do_topic_start(self, b):
        return self._round(roundtable.start_topic, b.get('topic'), b.get('mentors') or [], b.get('folder'), b.get('note') or '', b.get('id'))

    def do_topic_delete(self, b):
        self._round(roundtable.delete_topic, b.get('id'))
        return {}

    def do_tts(self, b):
        """朗读一句话（Edge 在线 / 百度）：返回 mp3 的 base64，只在内存里，界面播完就丢。"""
        import base64
        data = tts.speak(b.get('engine') or 'edge', b.get('text') or '', b.get('voice') or '', config.load(), b.get('voice_en') or '')
        return {'audio': base64.b64encode(data).decode('ascii'), 'mime': 'audio/mpeg'}

    def do_tts_voices(self, b):
        return {'edge': [{'id': v, 'name': n} for v, n in tts.EDGE_VOICES], 'edge_en': [{'id': v, 'name': n} for v, n in tts.EDGE_EN_VOICES]}

    def do_dropped(self, b):
        """从资源管理器拖进来的文件：界面先把文件交给浏览器内核（FilesDropped，pywebview 记下完整路径），再按文件名来取。"""
        from webview.dom import _dnd_state
        names = [str(x) for x in b.get('names') or []]
        end = time.time() + 2
        while True:
            hit = next((p for n in names for (base, p) in list(_dnd_state['paths']) if base == n), None)
            if hit or time.time() > end:
                break
            time.sleep(0.05)
        _dnd_state['paths'].clear()  # 用过就清掉，不攒着
        if hit:
            log.info('拖入文件：%s', hit)
        return {'path': hit or ''}

    def do_doc(self, b):
        """重新读取字幕文件（整理完一步后界面用）。"""
        path = os.path.abspath(str(b.get('path') or ''))
        if not os.path.isfile(path):
            raise ValueError('文件不见了：' + path)
        d = workflow.load_doc(path)
        return {'doc': d, 'stale': docs.clip_stale(d) if d.get('kind') != 'book' else False, 'busy': tasks.busy(docs.media_key(path)),
                'summary_changes': docs.summary_changes(d), 'pending': workflow.pending_now(path, d)}

    def do_cancel(self, b):
        job = jobs.get(b.get('id'))
        if job:
            job.cancel.set()
        return {}

    def do_chat(self, b):
        """AI 问答以任务形式逐段产出，界面轮询拼接。（课程重点走 do_steps）"""
        s = config.load()
        name = s['chat']
        llm.check(s, name)
        d = b.get('doc') or {}
        teacher = config.get_teacher(d.get('teacher'))
        messages = [{'role': 'system', 'content': llm.system_prompt(d, teacher)}]
        for m in (b.get('messages') or [])[-20:]:
            if m.get('role') in ('user', 'assistant') and m.get('content'):
                messages.append({'role': m['role'], 'content': str(m['content'])})

        def run(job):
            for piece in llm.stream(s, name, messages, job.cancel):
                job.push([piece])
            return {}
        return {'job': jobs.start('chat', run).id}

    def do_terms_add(self, b):
        """把勾选的新术语和识别错误加进老师的术语表（关联了 Obsidian 文件就写进文件，先备份）。"""
        return glossary.add(b.get('teacher'), b.get('terms'), b.get('errors'), str(b.get('title') or ''))

    def do_glossary_plan(self, b):
        """新老师的第一节课：术语表会建在哪里、已有的老师、这节课往上可以绑定老师的文件夹。"""
        return glossary.plan(str(b.get('path') or ''))

    def do_glossary_create(self, b):
        """新老师的第一节课：建老师档案和 Obsidian 术语表、绑定文件夹、写入勾选的术语。"""
        r = glossary.create(b.get('name'), b.get('terms'), b.get('errors'), str(b.get('title') or ''), str(b.get('bind') or ''))
        return dict(r, teachers=config.load_teachers(), groups=library.view() if r.get('bound') else None)

    def do_coverage(self, b):
        d = b.get('doc') or {}
        ratio, gaps = llm.coverage(d.get('segments'), d.get('summary') or '')
        return {'ratio': round(ratio, 3), 'gaps': [[a, e] for a, e, _ in gaps]}

    def do_terms_preview(self, b):
        """预览从术语表文件里读出来的术语和识别错误对照。"""
        path = str(b.get('path') or '')
        if not path:
            import webview
            r = self._window.create_file_dialog(webview.FileDialog.OPEN, file_types=('文本文件 (*.md;*.txt)', '所有文件 (*.*)'))
            if not r:
                return {'path': None}
            path = r[0]
        text = config.read_text_file(path)
        terms = llm.terms_from_markdown(text)
        words, hints, forced = llm.parse_terms(terms)
        ck = glossary.check_file(path)
        return {'path': path, 'terms': terms, 'words': len(words), 'hints': len(hints), 'forced': len(forced), 'chars': len(text),
                'dups': len(ck['dup_terms']) + len(ck['dup_errors'])}

    def do_terms_view(self, b):
        """「查看」术语表文件：软件读到的术语、识别错误，重复的在第几行，在 Obsidian 里打开的链接。"""
        return glossary.check_file(str(b.get('path') or ''))

    def do_terms_pending_filter(self, b):
        """打开「新术语」对话框前，去掉术语表里已经有的（几节课一起整理、一节节确认时会重复）。"""
        return glossary.filter_pending(b.get('teacher'), b.get('terms'), b.get('errors'))

    def do_test(self, b):
        s = config.load()
        name = b.get('provider')
        if name not in s['providers']:
            raise ValueError('未知的服务')
        if b.get('kind') == 'chat':
            reply = llm.complete(s, name, [{'role': 'user', 'content': '只回复两个字：你好'}])
            return {'message': '连接正常：' + reply.strip()[:20]}
        return {'message': asr.test_provider(s, name)}

    # -------------------------------------------------------- 导出与剪藏 ----
    def do_export(self, b):
        dest = str(b.get('dest') or '')
        if not dest:
            raise ValueError('没有选择保存位置')
        fmt_name = 'srt' if b.get('format') == 'srt' else 'txt'
        docs.export(b.get('doc') or {}, dest, fmt_name, b.get('mode') or 'both', bool(b.get('timestamps', True)))
        return {'path': dest}

    def do_text(self, b):
        d = b.get('doc') or {}
        mode = b.get('mode') or 'both'
        text = docs.to_srt(d, mode) if b.get('format') == 'srt' else docs.to_txt(d, mode, bool(b.get('timestamps', True)))
        return {'text': text}

    def do_clip(self, b):
        ob = config.load()['obsidian']
        d = b.get('doc') or {}
        teacher = config.get_teacher(d.get('teacher'))
        series = ''
        media_path = os.path.normcase(d.get('media') or '')
        for f in config.load_library()['folders']:
            if media_path.startswith(os.path.normcase(f['path']).rstrip('\\/') + os.sep):
                series = f.get('name') or Path(f['path']).name
        mp = d.get('media') or ''
        boundary = docs.source_boundary(d, media.tail_problem(mp) if mp and os.path.isfile(mp) else '')
        shot = (lambda t: frames.grab(mp, t)) if mp and os.path.isfile(mp) else None
        return docs.clip(d, ob.get('vault'), ob.get('folder'), teacher, series, ob.get('teacher_subfolder', True),
                         ob.get('name_pattern') or '{title}', boundary, shot, bool(b.get('force')))

    def do_reveal(self, b):
        """在资源管理器里显示文件，或用 Obsidian 打开笔记。"""
        target = str(b.get('target') or '')
        if target.startswith('obsidian://') or target.startswith('https://github.com/longpeng1413/'):
            os.startfile(target)
        elif os.path.exists(target):
            subprocess.Popen(['explorer', '/select,', os.path.normpath(target)])
        return {}

    # ------------------------------------------------------------ 书籍 ----
    @staticmethod
    def _book_info(info, d=None):
        shift = (d or {}).get('shift', info.get('shift')) if d and 'shift' in d else info.get('shift')
        return {'kind': info['kind'], 'kind_name': book.source_type(info), 'unit': {'pdf': '页', 'pptx': '张', 'ppt': '张'}.get(info['kind'], '段'),
                'pages': len(info['pages']), 'scan': info['scan'], 'shift': shift, 'detected_shift': info.get('shift'),
                'chars': info['chars'], 'toc': len(info.get('toc') or []), 'figures': sum(1 for p in info['pages'] if p['figure']),
                'companion': Path(info['companion']).name if info.get('companion') else ''}

    def _open_book(self, path):
        info = book.read(path)
        d = docs.load(path)
        if not d or d.get('kind') != 'book':
            d = book.new_doc(path, info)
        d['pages'] = len(info['pages'])
        if not d.get('teacher'):
            d['teacher'] = library.folder_teacher(path)
        config.add_recent(path, d.get('title') or Path(path).stem)
        return {'path': path, 'kind': 'book', 'doc': d, 'book': self._book_info(info, d),
                'viewer': file_url(path) if info['kind'] == 'pdf' else None, 'busy': tasks.busy(docs.media_key(path)),
                'pending': workflow.pending_now(path, d)}

    def do_book_pages(self, b):
        """Word / 文本在左边显示：每「页」的文字和图片。"""
        info = book.read(b.get('path'))
        shift = b.get('shift') if 'shift' in b else info.get('shift')
        return {'pages': [{'n': p['n'], 'ref': book.page_ref(p['n'], shift), 'text': p['text'],
                           'images': [file_url(x) for x in p.get('images') or []]} for p in info['pages']]}

    def do_book_read(self, b):
        """朗读原文：这几页去掉页眉页码、接成段落的文字；有图表的页标出来（PDF 的图由 book_page_img 按需渲染）。"""
        info = book.read(b.get('path'))
        shift = b.get('shift') if 'shift' in b else info.get('shift')
        pages = book.reading_pages(info, int(b.get('start') or 1), int(b.get('end') or 0) or None)
        return {'pdf': info['kind'] == 'pdf', 'pages': [dict(p, ref=book.page_ref(p['n'], shift), images=[file_url(x) for x in p['images']]) for p in pages]}

    def do_book_ocr(self, b):
        """扫描书识别文字（core/ocr.py，Windows 自带识别）：生成可搜索 PDF，用原来的文件名换上（整理结果接得上），
        原文件挪进同一文件夹的「_OCR前的原件…」（「_」开头的文件夹媒体库不列）；还没整理过的书按新文字重新分章。"""
        path = os.path.abspath(str(b.get('path') or ''))
        info = book.read(path)
        if info['kind'] != 'pdf' or not info.get('scan'):
            raise ValueError('只有扫描版的 PDF（每页是整张图片）需要识别文字')
        if tasks.busy(docs.media_key(path)):
            raise ValueError('这本书正在被别的任务整理，整理完再识别文字')
        ok, why = ocr.available()
        if not ok:
            raise ValueError(why)

        def run(job):
            p = Path(path)
            tmp = ocr.OCR_DIR / f'{uuid.uuid4().hex[:12]}.pdf'
            try:
                r = ocr.make_searchable(path, tmp, job.report, job.cancel)
                keep = p.parent / '_OCR前的原件（确认没问题可以删）'
                keep.mkdir(exist_ok=True)
                dest = keep / p.name
                if dest.exists():
                    dest = keep / f'{p.stem}-{time.strftime("%Y%m%d-%H%M%S")}{p.suffix}'
                try:
                    os.replace(p, dest)
                except OSError as e:
                    raise ValueError(f'原文件挪不动（可能被别的程序打开着，比如 WPS、PDF 阅读器）：{e}')
                shutil.move(str(tmp), str(p))
            finally:
                tmp.unlink(missing_ok=True)
            info2 = book.read(path)

            def change(d):
                if d.get('shift') is None and info2.get('shift') is not None:
                    d['shift'] = info2['shift']
                if not any((c.get('md') or '').strip() for c in d.get('chapters') or []):
                    d['chapters'] = [dict(c, md='') for c in book.detect_chapters(info2)]
                return len(d.get('chapters') or [])
            n = workflow._apply(path, change)
            log.info('识别文字：%s，%s 页、%s 字，原文件挪到 %s', path, r['pages'], r['chars'], dest)
            return dict(r, chapters=n, original=str(dest))
        return {'job': jobs.start('book_ocr', run).id}

    def do_book_fig(self, b):
        """整理笔记里的「原书图示」在软件里直接显示（以前要剪藏到 Obsidian 才看得到图）：
        和剪藏时同一个裁法（book.crop_figure，没写位置就整页），放进缓存的页面图文件夹（30 天没用自动删）。"""
        path = str(b.get('path') or '')
        n = int(b.get('n') or 1)
        region = [x for x in re.split(r'[,，\s]+', str(b.get('region') or '').strip()) if x]
        region = region if len(region) == 4 else None
        dest = book.pages_dir(path) / f'fig_{n:04d}_{hashlib.sha1(",".join(region or []).encode()).hexdigest()[:8]}.jpg'
        if not (dest.is_file() and dest.stat().st_size > 0):
            book.crop_figure(path, book.read(path), n, region, dest)
        return {'src': file_url(dest)}

    def do_book_page_img(self, b):
        """PDF 某一页的图（朗读原文时附在这一页文字后面看图表用；缓存 30 天没用自动删）。"""
        return {'src': file_url(book.render_page(b.get('path'), int(b.get('n')), 900))}

    def do_book_chat(self, b):
        """书的 AI 对话（边写边显示）：scope=chapter 读第 i 篇的原文全文 + 笔记，scope=book 读全部笔记 + 本机挑的相关原文页。
        问答写进书的整理结果 book_chat[key]（由后台写，不走界面保存：后台可能正在整理这本书，界面整份保存会撞）。"""
        s = config.load()
        name = s['chat']
        llm.check(s, name)
        path = str(b.get('path') or '')
        info = book.read(path)
        d = workflow.load_doc(path)
        scope = 'chapter' if b.get('scope') == 'chapter' else 'book'
        key = str(b.get('key') or 'book')
        history = [m for m in (b.get('messages') or []) if m.get('role') in ('user', 'assistant') and m.get('content')]
        messages = book.chat_messages(info, d, scope, int(b.get('i') if b.get('i') is not None else -1), history)
        question = next((m['content'] for m in reversed(history) if m['role'] == 'user'), '')

        def run(job):
            text = ''
            for piece in llm.stream(s, name, messages, job.cancel):
                text += piece
                job.push([piece])
            if text.strip():
                answer = text.strip() + ('（已停止）' if job.cancel.is_set() else '')
                workflow._apply(path, lambda doc: doc.setdefault('book_chat', {}).setdefault(key, []).extend(
                    [{'role': 'user', 'content': question}, {'role': 'assistant', 'content': answer}]))
            return {'saved': bool(text.strip())}
        return {'job': jobs.start('book_chat', run).id}

    def do_book_chat_clear(self, b):
        """清空书的某一段 AI 对话（这一章的或整本书的）。"""
        key = str(b.get('key') or '')
        workflow._apply(str(b.get('path') or ''), lambda doc: (doc.get('book_chat') or {}).pop(key, None))
        return {}

    def do_book_chapters(self, b):
        """重新识别章节。ai=True 时让 AI 读目录页（后台任务）。"""
        path = b.get('path')
        info = book.read(path)
        if 'shift' in b:
            info = dict(info, shift=b['shift'])
        if not b.get('ai'):
            return {'chapters': book.detect_chapters(info)}
        s = config.load()
        name = s['chat']
        llm.check(s, name)

        def run(job):
            job.report(0.1, 'AI 正在读目录…')
            return {'chapters': book.ai_chapters(s, name, info)}
        return {'job': jobs.start('book', run).id}

    def do_book_clip(self, b):
        d = b.get('doc') or {}
        ob = config.load()['obsidian']
        info = book.read(d['media'])
        return book.clip_book(d, info, ob.get('vault'), ob.get('book_folder') or '', ob.get('book_dir_pattern') or '《{title}》{author}',
                              bool(b.get('force')))

    # ------------------------------------------------------------ 画面 ----
    def _vision(self, s, need=True):
        return workflow.vision_provider(s, need)

    def do_frames_describe(self, b):
        """给指定的画面写（或重写）画面记录。"""
        path = b.get('path')
        if not path or not os.path.isfile(path):
            raise ValueError('视频文件不见了')
        s = config.load()
        name = self._vision(s)
        todo = [{'t': float(f['t']), **{k: v for k, v in f.items() if k != 't'}} for f in (b.get('frames') or []) if isinstance(f, dict) and 't' in f]
        if not todo:
            raise ValueError('没有要识别的画面')

        def run(job):
            try:
                out = frames.describe(s, name, path, todo, b.get('segments') or [], job.cancel, job.report,
                                      on_part=lambda part: job.push(part))
            except frames.Cancelled:
                return {'frames': []}
            return {'frames': out}
        return {'job': jobs.start('frames', run).id}

    def do_frames_redraw(self, b):
        """让 AI 重画指定的画面（AI辅助理解图）。返回每张的 redraw（AI 说不画的没有 redraw）。"""
        path = b.get('path')
        if not path or not os.path.isfile(path):
            raise ValueError('视频文件不见了')
        s = config.load()
        name = self._vision(s)
        todo = [{'t': float(f['t']), 'note': str(f.get('note') or '')} for f in (b.get('frames') or []) if isinstance(f, dict) and 't' in f]
        if not todo:
            raise ValueError('没有要重画的画面')

        def run(job):
            try:
                out = frames.redraw(s, name, path, todo, b.get('segments') or [], job.cancel, job.report)
            except frames.Cancelled:
                return {'frames': []}
            return {'frames': [{'t': f['t'], 'redraw': f.get('redraw')} for f in out]}
        return {'job': jobs.start('frames', run).id}

    def do_frame_url(self, b):
        path = b.get('path')
        if not path or not os.path.isfile(path):
            raise ValueError('视频文件不见了')
        return {'url': file_url(frames.grab(path, float(b.get('t') or 0), small=bool(b.get('small')), crop=b.get('crop')))}

    def do_models(self, b):
        name = b.get('provider')
        if name not in config.CHAT_PROVIDERS:
            raise ValueError('未知的服务')
        return {'models': llm.list_models(config.load(), name)}

    # ------------------------------------------------------- ChatGPT 账号 ----
    def do_codex_login(self, b):
        return {'url': codex_auth.start()}

    def do_codex_finish(self, b):
        try:
            return codex_auth.finish(b.get('url') or '')
        except codex_auth.AuthError as e:
            raise ValueError(str(e))

    def do_codex_logout(self, b):
        codex_auth.logout()
        return {}

    # ------------------------------------------------------------ 老师 ----
    def do_teacher_save(self, b):
        t = config.save_teacher(b.get('teacher') or {})
        return {'teacher': t, 'teachers': config.load_teachers()}

    def do_teacher_delete(self, b):
        config.delete_teacher(b.get('id'))
        return {'teachers': config.load_teachers()}

    # ---------------------------------------------------------- 媒体库 ----
    def do_library(self, b):
        tree = library.view()
        log.info('媒体库：最上层 %d 项、%d 个文件', len(tree), sum(n['count'] if n['type'] == 'folder' else 1 for n in tree))
        return {'groups': tree}

    def do_library_add_folder(self, b):
        """加文件夹。媒体库里已经有它下面的文件夹时返回 need_merge，界面问用户后带 merge=true/false 再调一次。"""
        path = b.get('path')
        if not path:
            import webview
            r = self._window.create_file_dialog(webview.FileDialog.FOLDER)
            path = r[0] if r else None
        if not path:
            return {'status': 'cancel', 'groups': None}
        r = library.add_folder(path, b.get('merge'))
        return dict(r, groups=library.view() if r['status'] == 'added' else None)

    def do_library_add_files(self, b):
        paths = b.get('paths')
        if not paths:
            paths = self.do_pick_media_multi({})['paths']
        r = library.add_files(paths or [])
        return dict(r, groups=library.view())

    def do_library_reorder(self, b):
        """拖动排序后记住顺序。parent 为空是最上层（names 是路径），否则是那个文件夹里的名字。"""
        library.reorder(b.get('parent') or '', [str(x) for x in b.get('names') or []])
        return {'groups': library.view()}

    def do_rename(self, b):
        """改文件或文件夹的名字（电脑上的文件、字幕文件、播放进度、媒体库一起改；Obsidian 里的笔记不改名）。"""
        new = library.rename(b.get('path') or '', b.get('name') or '')
        return {'path': new, 'groups': library.view()}

    def do_library_remove(self, b):
        library.remove(b.get('kind'), b.get('path') or '')
        return {'groups': library.view()}

    def do_library_set(self, b):
        library.set_folder(b.get('path') or '', **{k: b[k] for k in ('teacher', 'collapsed', 'name', 'no_summary', 'rule') if k in b})
        return {'groups': library.view() if b.get('refresh') else None}

    # ------------------------------------------------------------ 日志与退出 ----
    def do_log(self, b):
        """界面上的脚本错误也记进日志。"""
        level = {'error': 40, 'warn': 30}.get(b.get('level'), 20)
        log.log(level, '[界面] %s', str(b.get('message') or '')[:4000])
        return {}

    def do_about(self, b):
        s = config.load()
        bundled, self._bundled = self._bundled, None  # 只提示一次
        return {'version': self._version, 'bundled': bool(bundled), 'log_dir': str(LOG_DIR), 'data_dir': str(config.DATA_DIR),
                'cache_dir': str(config.CACHE_DIR), 'storage_root': str(config.STORAGE_ROOT),
                'storage_custom': s.get('storage_dir') or '', 'storage_default': str(config.default_storage_root()), **cache.info()}

    def do_set_storage(self, b):
        """更改缓存和日志的位置（空 = 软件所在文件夹），重启后生效。"""
        path = str(b.get('path') or '').strip()
        if b.get('pick'):
            import webview
            r = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=str(config.STORAGE_ROOT))
            if not r:
                return {'changed': False}
            path = r[0]
        if path:
            probe = Path(path) / config.CACHE_NAME
            try:
                probe.mkdir(parents=True, exist_ok=True)
                (probe / '.write-test').write_text('ok', encoding='utf-8')
                (probe / '.write-test').unlink()
            except OSError:
                raise ValueError('这个位置不能写入，请换一个文件夹')
        config.update({'storage_dir': path})
        log.info('缓存和日志位置改为：%s', path or '软件所在文件夹')
        return {'changed': True, 'root': path or str(config.default_storage_root())}

    # ------------------------------------------------------------ 设置导入导出 ----
    def do_export_settings(self, b):
        import json as _json
        import webview
        r = b.get('dest') or self._window.create_file_dialog(webview.FileDialog.SAVE, save_filename='猛人学习设置.json', file_types=('设置文件 (*.json)',))
        if not r:
            return {'path': None}
        dest = r if isinstance(r, str) else r[0]
        if not dest.lower().endswith('.json'):
            dest += '.json'
        s = config.load()
        s.pop('storage_dir', None)
        s.pop('recent', None)
        if not b.get('keys'):
            for name, p in s['providers'].items():
                for k in config.SECRETS:
                    if k in p:
                        p[k] = ''
                if 'oauth' in p:
                    p['oauth'] = {}
                for a in p.get('more_keys') or []:  # 豆包备用账号的密钥
                    a['key'] = ''
        data = {'app': config.APP_NAME, 'kind': 'settings-export', 'version': self._version,
                'exported': time.strftime('%Y-%m-%d %H:%M:%S'), 'with_keys': bool(b.get('keys')),
                'settings': s, 'teachers': config.load_teachers()}
        if b.get('library'):
            data['library'] = config.load_library()
        Path(dest).write_text(_json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
        log.info('导出设置到 %s（%s密钥）', dest, '含' if b.get('keys') else '不含')
        return {'path': dest}

    def do_import_settings(self, b):
        import json as _json
        import webview
        r = [b['path']] if b.get('path') else self._window.create_file_dialog(webview.FileDialog.OPEN, file_types=('设置文件 (*.json)', '所有文件 (*.*)'))
        if not r:
            return {'imported': False}
        try:
            data = _json.loads(Path(r[0]).read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            raise ValueError('读不出这个文件，请选择从猛人学习导出的设置文件')
        if not isinstance(data, dict) or data.get('app') not in (config.APP_NAME, config.OLD_APP_NAME) or not isinstance(data.get('settings'), dict):
            raise ValueError('这不是猛人学习导出的设置文件')
        cur = config.load()
        new = config._merge(config.DEFAULTS, data['settings'])
        new['storage_dir'] = cur.get('storage_dir', '')  # 位置跟电脑有关，保留这台电脑的
        new['recent'] = cur.get('recent', [])
        for name, p in new['providers'].items():  # 文件里没带密钥时，保留这台电脑上已有的
            for k in list(config.SECRETS) + ['oauth']:
                if k in p and not p[k] and cur['providers'].get(name, {}).get(k):
                    p[k] = cur['providers'][name][k]
            had = {a.get('id'): a.get('key') for a in cur['providers'].get(name, {}).get('more_keys') or [] if isinstance(a, dict)}
            for a in p.get('more_keys') or []:
                if isinstance(a, dict) and not a.get('key') and had.get(a.get('id')):
                    a['key'] = had[a.get('id')]
        config.save(new)
        n_t = 0
        for t in data.get('teachers') or []:
            if isinstance(t, dict) and t.get('id'):
                config.save_teacher(t)
                n_t += 1
        n_f = 0
        if isinstance(data.get('library'), dict):
            lib = config.load_library()
            known = {os.path.normcase(f['path']) for f in lib['folders']}
            for f in data['library'].get('folders') or []:
                if isinstance(f, dict) and f.get('path') and os.path.normcase(f['path']) not in known:
                    lib['folders'].append(f)
                    n_f += 1
            for p in data['library'].get('files') or []:
                if p not in lib['files']:
                    lib['files'].append(p)
            config.save_library(lib)
        log.info('从 %s 导入设置：%d 位老师，%d 个媒体库文件夹', r[0], n_t, n_f)
        return {'imported': True, 'teachers': n_t, 'folders': n_f, 'with_keys': bool(data.get('with_keys'))}

    def do_cache_clear(self, b):
        return {'freed': cache.cleanup(clear_all=True), **cache.info()}

    def do_open_dir(self, b):
        target = {'log': LOG_DIR, 'data': config.DATA_DIR, 'cache': config.CACHE_DIR}.get(b.get('which'), LOG_DIR)
        target.mkdir(parents=True, exist_ok=True)
        os.startfile(str(target))
        return {}

    def do_quit(self, b):
        """界面保存完毕，可以关窗了。"""
        log.info('界面已保存，准备退出')
        self._quit.set()
        return {}
