"""后台自测（不联网、不需要 API Key）。运行：.venv\\Scripts\\python tests\\test_backend.py
会把设置和缓存放到临时目录，不影响真实数据。"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

for _old in Path(tempfile.gettempdir()).glob('mengren-test-*'):  # 以前没删干净的（日志文件开着时删不掉）
    shutil.rmtree(_old, ignore_errors=True)
TMP = Path(tempfile.mkdtemp(prefix='mengren-test-'))
os.environ['APPDATA'] = str(TMP / 'appdata')
os.environ['LOCALAPPDATA'] = str(TMP / 'local')
ROOT = Path(__file__).resolve().parents[1]
if not (ROOT / 'test-media' / '旧格式.avi').exists():  # 测试视频不放进仓库，用 ffmpeg 生成
    subprocess.run([sys.executable, str(ROOT / 'tests' / 'make_test_media.py')], check=True)
(TMP / 'appdata' / '猛人字幕').mkdir(parents=True)  # 故意用改名前的位置，测试自动搬家
(TMP / 'appdata' / '猛人字幕' / 'settings.json').write_text(json.dumps({'storage_dir': str(TMP / 'store')}), encoding='utf-8')
sys.path.insert(0, str(ROOT / 'app'))

from bridge import Api  # noqa: E402
from core import asr, config, doc as docs, media  # noqa: E402

MEDIA = TMP / 'media'
MEDIA.mkdir()
shutil.copy(ROOT / 'test-media' / '演示视频.mp4', MEDIA / '演示视频.mp4')
shutil.copy(ROOT / 'test-media' / '演示视频.mp4.猛人字幕.json', MEDIA / '演示视频.mp4.猛人字幕.json')
shutil.copy(ROOT / 'test-media' / '旧格式.avi', MEDIA / '旧格式.avi')
api = Api()
passed = 0


def check(name, cond, detail=''):
    global passed
    if not cond:
        print('✗', name, detail)
        raise SystemExit(1)
    passed += 1
    print('✓', name)


def run_steps(path, steps, **kw):
    """界面的整理按钮：交给任务层，等它做完，返回任务结果（brief）。"""
    j = wait(api.call('steps', dict({'path': str(path), 'steps': steps}, **kw))['job'])
    return j['result']['task'] if j['status'] == 'done' else {'status': j['status'], 'error': j.get('error'), 'stages': []}


def stage(task, name):
    return next((x for x in task.get('stages') or [] if x['name'] == name), {})


def wait(job_id, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = api.call('job', {'id': job_id})['job']
        if j['status'] != 'running':
            return j
        time.sleep(0.2)
    raise TimeoutError(job_id)


# 改名：设置文件夹自动从 %APPDATA%\\猛人字幕 搬到 %APPDATA%\\猛人学习
check('改名后设置自动搬家', config.DATA_DIR.name == '猛人学习' and config.DATA_DIR.exists() and not (TMP / 'appdata' / '猛人字幕').exists()
      and config.load()['storage_dir'] == str(TMP / 'store'))

# 打开已有字幕的 mp4：直接播放，读回字幕
r = api.call('open', {'path': str(MEDIA / '演示视频.mp4')})
check('打开 mp4', r['ok'] and r['plan'] == 'direct' and r['src'].startswith('file:///'), r)
check('读回字幕', len(r['doc']['segments']) == 8 and r['doc']['lang'] == 'en')
check('初始进度为 0', r['position'] == 0)

# 保存修改 + 备份
d = r['doc']
d['segments'][0]['text'] = 'Edited line'
d['segments'].append({'start': 1.0, 'end': 2.0, 'text': '乱序插入', 'tr': ''})
s = api.call('save', {'path': r['path'], 'doc': d})
check('保存字幕（改名前的字幕文件存成新名字，旧文件去掉）', s['ok'] and s['stored'].endswith('.猛人学习.json') and not (MEDIA / '演示视频.mp4.猛人字幕.json').exists(), s)
again = api.call('open', {'path': r['path']})['doc']
check('保存后能读回修改', again['segments'][0]['text'] == 'Edited line')
check('保存时按时间排序', [x['start'] for x in again['segments']] == sorted(x['start'] for x in again['segments']))
check('留有上一版备份', Path(s['stored'] + '.bak').exists())

# 播放进度
api.call('position', {'path': r['path'], 't': 12.5})
check('记住播放进度', api.call('open', {'path': r['path']})['position'] == 12.5)

# avi（mpeg4 + mp3）需要转码
r2 = api.call('open', {'path': str(MEDIA / '旧格式.avi')})
check('avi 需要转换', r2['ok'] and r2['plan'] == 'transcode' and r2.get('convert_job'), r2)
j = wait(r2['convert_job'])
check('转换完成', j['status'] == 'done' and j['result']['src'].endswith('.mp4'), j)
r3 = api.call('open', {'path': str(MEDIA / '旧格式.avi')})
check('第二次打开直接用缓存', 'convert_job' not in r3 and r3['src'].endswith('.mp4'))

# 导出
out_txt = TMP / 'out.txt'
api.call('export', {'doc': again, 'dest': str(out_txt), 'format': 'txt', 'mode': 'both', 'timestamps': True})
txt = out_txt.read_text(encoding='utf-8-sig')
check('导出 TXT（双语+时间戳）', '[00:00] Edited line' in txt and '        欢迎来到交易心理学这一课' in txt and '今天我们聊聊' in txt, txt[:200])
out_srt = TMP / 'out.srt'
api.call('export', {'doc': again, 'dest': str(out_srt), 'format': 'srt', 'mode': 'tr'})
srt = out_srt.read_text(encoding='utf-8')
check('导出 SRT', srt.startswith('1\n00:00:00,500 --> ') and '今天我们聊聊' in srt, srt[:120])
plain = api.call('text', {'doc': again, 'format': 'txt', 'mode': 'src', 'timestamps': False})['text']
check('只要原文、不带时间戳', '[' not in plain and 'Edited line' in plain)

# 剪藏到 Obsidian（临时库）
vault = TMP / '库'
vault.mkdir()
api.call('settings', {'settings': {'obsidian': {'vault': str(vault), 'folder': '学习/猛人字幕'}}})
again['summary'] = '## 一句话概括\n测试总结 [00:05]'
c = api.call('clip', {'doc': again})
note = Path(c['path'])
check('剪藏生成笔记', c['ok'] and note.exists() and note.parent == vault / '学习' / '猛人字幕', c)
body = note.read_text(encoding='utf-8')
check('笔记：上半部分课程重点，下半部分完整字幕', body.index('## 课程重点') < body.index('## 完整字幕') and '[00:00] Edited line' in body and '人工校对: false' in body)
fm_keys = [ln.split(':', 1)[0] for ln in body.split('\n---\n', 1)[0].split('\n')[1:] if ln and not ln.startswith(' ')]
check('笔记属性只留有用的几个（英文课也一样）', fm_keys == ['视频文件', '人工校对', '剪藏时间', 'tags'], fm_keys)
check('不写语言、时长、视频日期、字幕识别、字幕句数这些', all(f'\n{k}:' not in body for k in ('老师', '系列', '语言', '时长', '视频日期', '字幕识别', '字幕句数', 'AI校对', '标题', '术语表')), body[:400])
check('笔记属性用中文（tags 是 Obsidian 固定的，不改）', all(k in body for k in ('视频文件: ', '剪藏时间: ')) and '状态:' not in body and '视频：[' not in body
      and 'status:' not in body and 'source:' not in body and '\ntags:\n' in body, body[:700])
check('obsidian 链接', c['uri'].startswith('obsidian://open?vault='))
again['clip'] = {'path': c['path']}
c2 = api.call('clip', {'doc': again})
check('再次剪藏更新同一篇笔记', c2['path'] == c['path'])

# 设置：密钥掩码与更新
api.call('settings', {'settings': {'providers': {'deepseek': {'key': 'sk-1234567890abcd'}}}})
pub = api.call('settings', {})['settings']
check('密钥只显示后四位', pub['providers']['deepseek']['key'] == '••••abcd' and pub['providers']['deepseek']['has_key'])
api.call('settings', {'settings': {'providers': {'deepseek': {'key': '••••abcd'}}}})
check('提交掩码不会覆盖密钥', config.load()['providers']['deepseek']['key'] == 'sk-1234567890abcd')
api.call('settings', {'settings': {'providers': {'deepseek': {'key': None}}}})
check('可以清除密钥', config.load()['providers']['deepseek']['key'] == '')
tk = run_steps(r['path'], ['transcribe'], lang='en', provider='openai')
check('未填密钥时给出提示', tk['status'] == 'failed' and '还没有填写' in stage(tk, 'transcribe').get('error', '') and tk['error_code'] == 'config', tk)

# 切段
mids = [10, 19.5, 31, 48, 61, 75]
ch = media.plan_chunks(mids, 80, 20, 40)
check('切段不超过最长长度', all(b - a <= 40 for a, b in ch) and ch[0][0] == 0 and ch[-1][1] == 80, ch)

# 豆包流式协议：编码、解码往返
frame = asr._frame(0x9, 0x1, 0x1, json.dumps({'result': {'text': '你好'}}).encode())
raw = frame[:4] + b'\x00\x00\x00\x01' + frame[4:]  # 服务端回包多 4 字节序号
kind, flags, data = asr._parse_frame(raw)
check('流式协议解析', kind == 'response' and data['result']['text'] == '你好')
text, segs = asr.doubao_utterances({'result': {'text': 'ab', 'utterances': [{'start_time': 1000, 'end_time': 2500, 'text': '你好。'}]}})
check('豆包分句时间', segs == [{'start': 1.0, 'end': 2.5, 'text': '你好。'}])
cues = asr.cues_from('第一句。第二句很长很长。', None, 10, 20)
check('无时间戳时按句长分配', len(cues) == 2 and cues[0]['start'] == 10 and abs(cues[-1]['end'] - 20) < 0.01)

# 抽音频 + 静音检测
wav = media.extract_wav(str(MEDIA / '演示视频.mp4'), 40)
check('抽出 16k 单声道音频', wav.exists() and wav.stat().st_size > 1_000_000)

# 老师与术语表
from core import codex_auth, library, llm  # noqa: E402
_seg = [{'start': 0, 'end': 1, 'text': '一句', 'tr': ''}]
(TMP / '存疑课.mp4.猛人学习.json').write_text(json.dumps({'segments': _seg, 'doubts': [{'t': 0, 'reason': '听不清', 'text': '一句'}, {'t': 0.5, 'reason': '疑为别字', 'text': '一句'}]}), encoding='utf-8')
(TMP / '无存疑.mp4.猛人学习.json').write_text(json.dumps({'segments': _seg}), encoding='utf-8')
check('媒体库数出还没确认的存疑句（好标「存疑 N」）', library._status(str(TMP / '存疑课.mp4'), {}).get('doubts') == 2 and not library._status(str(TMP / '无存疑.mp4'), {}).get('doubts'))
t = api.call('teacher_save', {'teacher': {'name': '大聪老师', 'terms': '底层逻辑\n峰级, 破级\n风级, 分 => 峰级\n托破旋 =>! 拓扑学',
                                          'about': '期货交易', 'notes': '峰：行情推进的方向', 'lang': 'zh'}})['teacher']
check('保存老师（含术语说明）', t['id'] and t['notes'] and len(api.call('settings', {})['teachers']) == 1)
words, hints, forced = llm.parse_terms(t['terms'])
check('解析术语表', words == ['底层逻辑', '峰级', '破级', '拓扑学'] and hints == [('风级', '峰级'), ('分', '峰级')] and forced == [('托破旋', '拓扑学')], (words, hints, forced))
check('只直接替换标了 =>! 的', llm.apply_fixes('部分讲到托破旋', forced) == '部分讲到拓扑学')
check('参考对照和术语说明写进 AI 提示', '分 → 峰级' in llm.teacher_block(t) and '峰：行情推进的方向' in llm.teacher_block(t))
check('课程重点不套固定模板', '不要套用固定栏目' in llm.DEFAULT_TEMPLATE and '不补充老师没讲的知识' in llm.DEFAULT_TEMPLATE)
ctx_d, ctx_p, fx = asr.asr_context(t, '交易底层逻辑', 'zh')
check('豆包热词', json.loads(ctx_d)['hotwords'][0]['word'] == '底层逻辑')
check('Whisper 提示词', ctx_p.startswith('术语：底层逻辑') and '交易底层逻辑' in ctx_p, ctx_p)
check('老师信息写进 AI 提示', '底层逻辑、峰级、破级、拓扑学' in llm.system_prompt({'title': 'x', 'segments': []}, t))

# 剪藏到老师子文件夹
again['teacher'] = t['id']
again['clip'] = None
c3 = api.call('clip', {'doc': again})
check('没有剪藏记录时，按视频路径找到已有笔记并更新', c3['path'] == c['path'], c3)
t3 = Path(c3['path']).read_text(encoding='utf-8')
check('标题下面不写老师、系列、时长；术语表名字只当标签', '时长：' not in t3 and '系列：' not in t3 and '老师：' not in t3 and '\n老师:' not in t3 and '  - 大聪老师' in t3
      and t3.split('\n---\n', 1)[1].lstrip().split('\n\n')[:2] == [f'# {again["title"]}', '## 课程重点'], t3[:400])
shutil.rmtree(vault / '学习')
c3b = api.call('clip', {'doc': again})
check('按老师分子文件夹', Path(c3b['path']).parent.name == '大聪老师', c3b)

# ---------------------------------------------------------------- 1.4 ----
# 关联 Obsidian 术语表文件
gloss = TMP / '课程术语表.md'
gloss.write_text('''# 课程术语表 V1.1

# 三、峰

正确写法：

> **峰**

# 十、破级

- **标准写法**：破级

# 三十七、已知ASR常见错误映射

| ASR常见识别 | 正确术语 |
| --- | --- |
| 风、分 | 峰 |
| 破极、破局 | 破级 |
| 止行、部分“止盈” | 可能为“指形”，必须结合上下文 |
| 冲门 | 葱门（仅限上下文明确指向师门群体） |

--hotwords "王老师,峰级线"
''', encoding='utf-8')
extracted = llm.terms_from_markdown(gloss.read_text(encoding='utf-8'))
w2, h2, f2 = llm.parse_terms(extracted)
check('从术语表文档读出术语', w2[:4] == ['峰', '破级', '王老师', '峰级线'] and not f2, (w2, extracted))
check('读出识别错误对照（只作参考）', ('冲门', '葱门（仅限上下文明确指向师门群体）') in h2 and ('分', '峰') in h2 and ('止行', '指形（可能为“指形”，必须结合上下文）') in h2, h2)
check('识别错误的说明不进热词', '葱门' in w2 and all('仅限' not in x for x in w2), w2)
t2 = api.call('teacher_save', {'teacher': {'name': '王老师（交易课）', 'display': '王老师', 'terms': '底层逻辑', 'terms_file': str(gloss)}})['teacher']
rt = config.get_teacher(t2['id'])
check('关联文件：术语合并、整份文件当术语说明', '底层逻辑' in rt['terms'] and '冲门 => 葱门' in rt['terms'] and '已知ASR常见错误映射' in rt['notes'])
check('原始档案不被改动', config.get_teacher(t2['id'], resolve=False)['terms'] == '底层逻辑')
check('Obsidian 里改了术语表，软件用最新的', (gloss.write_text(gloss.read_text(encoding='utf-8') + '\n# 九、新术语\n\n正确写法：**新词**\n', encoding='utf-8') or True)
      and '新词' in config.get_teacher(t2['id'])['terms'])
pv = api.call('terms_preview', {'path': str(gloss)})
check('术语表文件预览', pv['ok'] and pv['words'] >= 5 and pv['hints'] >= 4, pv)
gloss.unlink()
check('文件不见了也能继续用档案里的术语', config.get_teacher(t2['id'])['terms'] == '底层逻辑' and config.get_teacher(t2['id']).get('terms_file_error'))

# 课程重点要求：老师自述、课程安排、不加自己的归纳
for key in ('老师自述', '课程安排', '整理者注', '不要写「这说明', '不要写「图示中」'):
    check('课程重点要求：' + key, key in llm.DEFAULT_TEMPLATE)

# 覆盖率和查漏插入
segs = [{'start': i * 10.0, 'end': i * 10 + 9.0, 'text': f'第{i}句讲了一些内容，有知识意义的话。', 'tr': ''} for i in range(30)]
summ = '一句话说明。\n\n## 一、开头\n\n- 要点一 [00:00] [00:30]\n\n## 二、结尾\n\n- 要点二 [04:10]'
ratio, gaps = llm.coverage(segs, summ)
check('覆盖率：找出没有出处的大段', 0 < ratio < 1 and gaps and gaps[0][0] == 70.0, (ratio, [(a, b) for a, b, _ in gaps]))
new = llm.insert_additions(summ, [{'放在': '一、开头', '内容': '- 老师自述：教过两万多学生 [01:20]'}, {'放在': '不存在的小节', '内容': '课程安排：线下课讲 [03:00]'}])
check('查漏：补到对应小节末尾', new.index('教过两万多学生') < new.index('## 二、结尾') and new.index('要点一') < new.index('教过两万多学生'), new)
check('查漏：找不到小节的放到最后', new.rstrip().endswith('- 课程安排：线下课讲 [03:00]') and '## 其他补充' in new, new)
orig_complete = llm.complete
llm.complete = lambda *a, **k: json.dumps({'补充': [{'放在': '二、结尾', '内容': '- 漏掉的举例 [02:00]'}]}, ensure_ascii=False)
out, n_add, b4, af = llm.review_summary({}, 'codex', {'title': 't', 'segments': segs}, None, summ)
check('查漏流程：补完覆盖率变高', n_add == 1 and af > b4 and '漏掉的举例 [02:00]' in out, (n_add, b4, af))

# AI 校对：存疑句
llm.complete = lambda *a, **k: json.dumps({'改动': {'1': '峰级的入门'}, '存疑': {'2': '疑似识别错误，可能是「讲论」'}}, ensure_ascii=False)
fx2, dbt = llm.correct_batch({}, 'codex', [(1, '风级的入门'), (2, '这是一个关于峰级的讲论文')], 't', None)
check('AI 校对：改动和存疑分开返回', fx2 == {1: '峰级的入门'} and dbt == {2: '疑似识别错误，可能是「讲论」'}, (fx2, dbt))
llm.complete = lambda *a, **k: json.dumps({'改动': {'1': '峰级的入门'}})
check('AI 校对：兼容没有存疑的回答', llm.correct_batch({}, 'codex', [(1, '风级的入门')], 't', None) == ({1: '峰级的入门'}, {}))
llm.complete = orig_complete

# 来源情况、标题层级、剪藏
bd = docs.source_boundary({'duration': 1478.0, 'segments': [{'start': 1460.68, 'end': 1464.0, 'text': '特别是在王老师线下课啊，你也可以'}]})
check('来源情况：字幕提前结束、最后一句没说完', '最后约 14 秒' in bd and '没有说完' in bd, bd)
check('来源情况：完整的不报', docs.source_boundary({'duration': 10, 'segments': [{'start': 0, 'end': 9.5, 'text': '好。'}]}) == '')
sh = docs._shift_headings('# 课程重点：交易底层逻辑\n\n概述\n\n## 一、人事钱\n\n### 1. 钱\n')
check('课程重点去掉总标题、标题降一级', sh.startswith('概述') and '### 一、人事钱' in sh and '#### 1. 钱' in sh, sh)
shutil.rmtree(vault / '学习')
d14 = dict(again, teacher=t2['id'], clip=None, summary='# 课程重点：演示\n\n## 一、开头\n要点 [00:05]',
           doubts=[{'t': again['segments'][1]['start'], 'reason': '疑似识别错误'}])
api.call('settings', {'settings': {'obsidian': {'vault': str(vault), 'folder': '学习/猛人字幕', 'teacher_subfolder': True, 'name_pattern': '{title}（猛人字幕）'}}})
c4 = api.call('clip', {'doc': d14})
n4 = Path(c4['path'])
b4_ = n4.read_text(encoding='utf-8')
check('笔记文件名格式、老师称呼', n4.name == '演示视频（猛人字幕）.md' and n4.parent.name == '王老师' and '老师：' not in b4_, c4)
check('笔记：标题层级、状态、存疑句', '\n### 一、开头' in b4_ and '# 课程重点：演示' not in b4_ and '状态:' not in b4_ and '〔存疑：疑似识别错误〕' in b4_, b4_[:600])
moved = vault / '别处' / '改了名.md'
moved.parent.mkdir()
n4.replace(moved)
# 模拟 1.7.4 以前的笔记：属性是英文（status、source）
moved.write_text(moved.read_text(encoding='utf-8').replace('\ntags:\n', '\nstatus: "已确认-资料"\ntags:\n', 1).replace('视频文件: ', 'source: '), encoding='utf-8')
d14['clip'] = {'path': str(n4)}
c5 = api.call('clip', {'doc': d14})
check('笔记被移动改名后，再剪藏仍更新原笔记', Path(c5['path']) == moved and not n4.exists(), c5)
check('再剪藏保留笔记里的状态（以前英文的 status 也认得，改写成中文属性）', '状态: "已确认-资料"' in moved.read_text(encoding='utf-8') and 'status:' not in moved.read_text(encoding='utf-8'))
api.call('teacher_delete', {'id': t2['id']})

# ---------------------------------------------------------------- 1.5 画面 ----
from core import cache, frames  # noqa: E402
blank, page2 = bytes(frames.W * frames.H), bytes([200]) * (frames.W * frames.H)
samples = [(i * 2.0, blank if i < 10 else page2) for i in range(20)]
ct = frames.candidate_times(samples, [{'start': 30.0, 'end': 31.0, 'text': '来，大家请看这里'}])
check('候选画面：每段一张、去掉重复、老师说请看时补一张', [w for _, w in ct] == ['定时', '老师说请看'] and ct[1][0] >= 32, ct)
mp4 = str(MEDIA / '演示视频.mp4')
g = frames.grab(mp4, 3.0)
check('截图', g.exists() and g.stat().st_size > 1000 and frames.grab(mp4, 3.0) == g)
check('截小图', frames.grab(mp4, 3.0, small=True).name.endswith('_s.jpg'))
fu = api.call('frame_url', {'path': mp4, 't': 3.0})
check('画面图片地址', fu['ok'] and fu['url'].startswith('file:///'), fu)
api.call('settings', {'settings': {'vision': 'deepseek'}})
fk = run_steps(mp4, ['frames'])
check('画面识别选了 DeepSeek 时提示它不能看图（这一步跳过）', stage(fk, 'frames').get('status') == 'skipped' and '不能看图' in stage(fk, 'frames').get('reason', ''), fk)
check('没有能看图的 AI 时只截候选画面', all('why' in f for f in frames.extract({}, '', mp4, 40, [])) and frames.extract({}, '', mp4, 40, []))
shots = [{'t': 5.0, 'note': '课件标题：交易心理学', 'caption': '欢迎来到', 'figure': True}, {'t': 20.0, 'note': '老师手画曲线'}]
nt = frames.notes_text(shots)
check('画面记录写成文字', '[00:05]（可插图） 课件标题：交易心理学　画面字幕：「欢迎来到」' in nt and '[00:20] 老师手画曲线' in nt, nt)
check('画面记录按时间段取', '老师手画曲线' not in frames.notes_text(shots, 0, 10))
sp = llm.system_prompt({'title': 'x', 'segments': [], 'frames': shots}, None)
check('画面说明写进 AI 提示', '===== 画面（可插图）=====' in sp and '课件标题：交易心理学' in sp)
check('没有画面记录时不写', '画面记录' not in llm.system_prompt({'title': 'x', 'segments': []}, None))
check('课程重点要求：有画面时怎么写、怎么插图', '![[画面 mm:ss]]' in llm.DEFAULT_TEMPLATE and '画面显示' in llm.DEFAULT_TEMPLATE)
seen = {}
llm.complete = lambda s_, n_, msgs, **k: seen.setdefault('u', msgs[-1]['content']) and json.dumps({'改动': {}, '存疑': {}})
llm.correct_all({}, 'codex', [(0, '甲'), (1, '乙')], 't', None, threading.Event(), lambda *a: None, lambda p: None, {0: 4.0, 1: 6.0}, shots + [{'t': 300.0, 'note': '很远的画面'}])
check('AI 校对参考同一时间段的画面记录', '课件标题：交易心理学' in seen['u'] and '很远的画面' not in seen['u'], seen.get('u', '')[:300])
llm.complete = orig_complete
shutil.rmtree(vault / '学习', ignore_errors=True)
d15 = dict(again, clip=None, teacher='', frames=shots, summary='## 一、开头\n- 要点 [00:05]\n![[画面 00:05]]\n- 再一个 [00:20]')
c6 = api.call('clip', {'doc': d15})
b6 = Path(c6['path']).read_text(encoding='utf-8')
img = Path(c6['path']).parent / 'image' / '演示视频__00-05.jpg'
check('剪藏：插图复制到笔记旁边的 image 文件夹', '![[image/演示视频__00-05.jpg]]' in b6 and img.exists() and img.stat().st_size > 1000, b6[:800])
check('剪藏：笔记只有课程重点和完整字幕，不附画面记录', '## 画面记录' not in b6 and '课件标题：交易心理学' not in b6 and b6.index('## 课程重点') < b6.index('## 完整字幕'))
check('剪藏：插图压缩得比原截图小', img.stat().st_size < frames.grab(mp4, 5.0).stat().st_size, (img.stat().st_size, frames.grab(mp4, 5.0).stat().st_size))
cl = dict(d15, clip={'path': c6['path'], 'time': c6['time'], 'hash': c6['hash']})
check('剪藏后内容没变：不提示待更新剪藏', c6['hash'] and not docs.clip_stale(cl))
check('剪藏后改了课程重点：提示待更新剪藏', docs.clip_stale(dict(cl, summary=cl['summary'] + '\n- 新加一条')))
check('剪藏后改了字幕：提示待更新剪藏', docs.clip_stale(dict(cl, segments=[dict(x, text=x['text'] + '改') if k == 0 else x for k, x in enumerate(cl['segments'])])))
check('对话、画面这类不进笔记的改动：不提示', not docs.clip_stale(dict(cl, chat=[{'role': 'user', 'content': '问'}], frames=[])))
check('以前剪藏的没有指纹：不提示', not docs.clip_stale(dict(cl, clip={'path': c6['path']}, summary='变了')))

# 剪藏前检查：笔记在 Obsidian 里改过，先问再覆盖，覆盖前备份
g0 = api.call('clip', {'doc': cl})
check('笔记没改过：直接更新', not g0.get('conflict') and g0['file_hash'], g0)
cl2 = dict(cl, clip={'path': g0['path'], 'time': g0['time'], 'hash': g0['hash'], 'file_hash': g0['file_hash']})
note = Path(g0['path'])
note.write_text(note.read_text(encoding='utf-8').replace('\ntags:\n', '\n状态: "已审计"\ntags:\n', 1), encoding='utf-8')
g1 = api.call('clip', {'doc': cl2})
check('只在 Obsidian 里加了属性（例如状态）：不算改动，照常更新并保留', not g1.get('conflict') and '状态: "已审计"' in note.read_text(encoding='utf-8'), g1)
cl3 = dict(cl, clip={'path': g1['path'], 'time': g1['time'], 'hash': g1['hash'], 'file_hash': g1['file_hash']})
note.write_text(note.read_text(encoding='utf-8') + '\n我在 Obsidian 里加的一句 [[另一篇笔记]]\n', encoding='utf-8')
g2 = api.call('clip', {'doc': cl3})
check('在 Obsidian 里改过正文：不覆盖，先问用户', g2.get('conflict') and g2['path'] == str(note) and '我在 Obsidian 里加的一句' in note.read_text(encoding='utf-8'), g2)
g3 = api.call('clip', {'doc': cl3, 'force': True})
bk = Path(g3['backup'])
check('确认后覆盖，覆盖前把旧笔记备份到软件数据文件夹（不放进知识库）', not g3.get('conflict') and bk.is_file() and '我在 Obsidian 里加的一句' in bk.read_text(encoding='utf-8')
      and '我在 Obsidian 里加的一句' not in note.read_text(encoding='utf-8') and config.DATA_DIR in bk.parents and '状态: "已审计"' in note.read_text(encoding='utf-8'), g3)
old_clip = dict(cl, clip={'path': g3['path'], 'time': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time() - 120))})
check('以前剪藏的（没有文件指纹）：文件比剪藏时间新就先问', api.call('clip', {'doc': old_clip}).get('conflict'))
os.utime(note, (time.time() - 600, time.time() - 600))
check('以前剪藏的：文件没比剪藏时间新就直接更新', not api.call('clip', {'doc': old_clip}).get('conflict'))

# 重新剪藏只更新软件负责的部分，保留用户在 Obsidian 里加的小节、标注、属性
m0 = api.call('clip', {'doc': cl, 'force': True})
nt = Path(m0['path'])
st = {'path': m0['path'], 'time': m0['time'], 'hash': m0['hash'], 'file_hash': m0['file_hash']}
tx = nt.read_text(encoding='utf-8')
check('剪藏的课程重点里有这两条要点', '- 要点 [00:05]\n' in tx and '- 再一个 [00:20]' in tx, tx[:900])
tx = tx.replace('- 要点 [00:05]\n', '- 要点 [00:05]\n\n> [!note] 我的理解\n> 先看要点\n\n', 1)
tx = tx.replace('- 再一个 [00:20]', '- 再一个 [00:20]\n> [!tip] 这一条后来会被删掉', 1)
tx = tx.replace('## 完整字幕', '## 我的笔记\n\n链接到 [[峰级线]]\n\n## 完整字幕', 1)
tx = tx.replace('\ntags:\n', '\n相关课程: "[[第3课]]"\ntags:\n', 1).replace('  - 猛人学习\n', '  - 猛人学习\n  - 我的标签\n', 1)
tx = tx.rstrip() + '\n\n## 画面记录\n\n以前版本写的画面记录\n'
nt.write_text(tx, encoding='utf-8')
cl4 = dict(cl, clip=st, summary='## 一、开头\n- 要点 [00:05]\n- 新的一条 [00:10]')
m1 = api.call('clip', {'doc': cl4})
t1 = nt.read_text(encoding='utf-8')
check('只加了自己的小节、标注、属性：重新剪藏不弹窗', not m1.get('conflict'), m1)
check('重新剪藏：课程重点更新了', '- 新的一条 [00:10]' in t1 and '- 再一个 [00:20]' not in t1, t1[:1200])
check('重新剪藏：写在要点下面的标注跟着那条要点', t1.index('- 要点 [00:05]') < t1.index('> [!note] 我的理解') < t1.index('- 新的一条 [00:10]') and '> 先看要点' in t1, t1[:1200])
check('重新剪藏：原来那条要点没了，标注挪到这一节最后，不丢', '> [!tip] 这一条后来会被删掉' in t1 and t1.index('> [!tip]') < t1.index('## 我的笔记') and m1['moved'] == 1, t1[:1500])
check('重新剪藏：自己加的小节原样保留在原来的位置', '## 我的笔记\n\n链接到 [[峰级线]]' in t1 and t1.index('## 课程重点') < t1.index('## 我的笔记') < t1.index('## 完整字幕'))
check('重新剪藏：自己加的属性和标签保留', '相关课程: "[[第3课]]"' in t1 and '  - 我的标签' in t1 and t1.count('  - 猛人学习') == 1)
check('重新剪藏：以前版本写的画面记录去掉', '## 画面记录' not in t1)
st2 = {'path': m1['path'], 'time': m1['time'], 'hash': m1['hash'], 'file_hash': m1['file_hash']}
nt.write_text(t1.replace('- 新的一条 [00:10]', '- 新的一条（我在 Obsidian 里改的）[00:10]'), encoding='utf-8')
check('在 Obsidian 里直接改了课程重点的文字：先问再覆盖', api.call('clip', {'doc': dict(cl4, clip=st2)}).get('conflict'))
old_n = '---\n视频文件: "x.mp4"\ntags:\n  - 猛人学习\n  - 旧老师\n  - 我的标签\n---\n\n# 课\n\n> 老师：旧老师 ｜ 时长：01:00\n\n## 课程重点\n\n- 甲\n'
new_n = '---\n视频文件: "x.mp4"\ntags:\n  - 猛人学习\n  - 新老师\n---\n\n# 课\n\n> 老师：新老师 ｜ 时长：01:00\n\n## 课程重点\n\n- 甲\n'
mt = docs.merge_note(old_n, new_n)[0]
check('老师改名后重新剪藏：旧老师的标签去掉，自己加的标签保留', '  - 新老师' in mt and '  - 旧老师' not in mt and '  - 我的标签' in mt, mt[:200])
# 老师由用户自己在 Obsidian 里填：重新剪藏保留；以前软件写的「老师」（术语表名字）去掉
o1 = '---\n老师: "峰级分析"\n视频文件: "x.mp4"\ntags:\n  - 猛人学习\n  - 峰级分析\n---\n\n# 课\n\n## 课程重点\n\n- 甲\n'
n1 = '---\n视频文件: "x.mp4"\ntags:\n  - 猛人学习\n  - 峰级分析\n---\n\n# 课\n\n## 课程重点\n\n- 甲\n'
check('旧笔记里软件写的「老师」（术语表名字）去掉', '老师:' not in docs.merge_note(o1, n1, None, {'峰级分析'})[0])
o2 = o1.replace('老师: "峰级分析"', '老师: "咖米大葱老师"')
check('自己在 Obsidian 里填的老师：重新剪藏保留', '老师: "咖米大葱老师"' in docs.merge_note(o2, n1, None, {'峰级分析'})[0])
check('1.7.8 起剪藏的笔记：「老师」属性一律当成用户填的', '老师: "峰级分析"' in docs.merge_note(o1, n1, ['猛人学习', '峰级分析'], {'峰级分析'})[0])
o3 = o1.replace('  - 峰级分析\n', '  - 旧术语表\n  - 我的标签\n')
m3 = docs.merge_note(o3, n1, ['猛人学习', '旧术语表'], ())[0]
check('上次软件写的标签这次不写了就去掉，自己加的标签保留', '  - 旧术语表' not in m3 and '  - 我的标签' in m3 and '  - 峰级分析' in m3, m3[:200])
o4 = '---\n视频文件: "x.mp4"\n状态: "待审计"\ntags:\n  - 猛人学习\n---\n\n# 课\n\n## 课程重点\n\n- 甲\n'
check('旧笔记里软件写的「状态: 待审计」去掉', '状态' not in docs.merge_note(o4, n1, None, ())[0])
check('旧笔记里自己改过的状态保留', '状态: "已确认-资料"' in docs.merge_note(o4.replace('待审计', '已确认-资料'), n1, None, ())[0])
check('课程重点模板：不让 AI 用标注框（留给用户写理解）', '不要用「> [!…]」这种标注框' in llm.DEFAULT_TEMPLATE)
check('插图非必要不用：最多 3 张，纯文字课件不插', '最多 3 张' in llm.DEFAULT_TEMPLATE and '纯文字课件' in frames.DESCRIBE_PROMPT
      and '纯文字的课件页' in frames.SCREEN_PROMPT and '不要照抄课件上的文字' in frames.DESCRIBE_PROMPT)

# 找插图：只留确实需要插的图；手动截取的默认可插
orig_screen, orig_describe = frames.screen, frames.describe
frames.screen = lambda *a, **k: [{'t': 5.0, 'why': '手画曲线'}, {'t': 20.0, 'why': '文字页'}]
frames.describe = lambda *a, **k: [{'t': 5.0, 'why': '手画曲线', 'note': '老师手画的上升趋势', 'figure': True},
                                   {'t': 20.0, 'why': '文字页', 'note': '纯文字课件', 'figure': False}]
ex = frames.extract({}, 'codex', mp4, 40, [])
frames.screen, frames.describe = orig_screen, orig_describe
check('找插图：只留需要插的图', [f['t'] for f in ex] == [5.0], ex)
many = [{'t': float(i * 30), 'note': f'第{i}张', 'figure': True} for i in range(12)]
seen_p = {}
llm.complete = lambda s_, n_, msgs, **k: seen_p.setdefault('u', msgs[-1]['content']) and json.dumps({'保留': [2, 5, 9]})
pk = frames.pick({}, 'codex', many, [{'start': 60.0, 'end': 62.0, 'text': '看这张图'}])
check('可插的图太多时去重、只留最必要的几张', [f['t'] for f in pk] == [30.0, 120.0, 240.0] and '不超过 8 张' in seen_p['u'] and '看这张图' in seen_p['u'], pk)
check('可插的图不多时不再筛', frames.pick({}, 'codex', many[:5], []) == many[:5])
llm.complete = orig_complete
orig_c = llm.complete
llm.complete = lambda *a, **k: json.dumps({'画面': [{'图': 1, '说明': '一页文字课件', '插图': False}]})
dm = frames.describe({}, 'codex', mp4, [{'t': 5.0, 'manual': True}], [])
llm.complete = orig_c
check('手动截取的画面：写一句说明，默认可插', dm[0]['figure'] and dm[0]['note'] == '一页文字课件' and not dm[0].get('caption'), dm)

# AI辅助理解图（2026-10-15）：画面看不清时 AI 依据讲解重画（Mermaid 或 SVG），剪藏时代替截图
bad_svg = ('```svg\n<svg width="10" height="10" onload="alert(1)"><script>alert(2)</script><a href="https://evil.example/x">'
           '<rect width="10" height="10" fill="url(https://evil.example/p.png)"/></a><image href="file:///C:/x.png"/><use href="#ok"/></svg>\n```')
clean_svg = docs.sanitize_svg(bad_svg)
check('AI 画的 SVG：去掉脚本、事件、外部链接和外部图片，保留图内引用', clean_svg.startswith('<svg xmlns=') and 'script' not in clean_svg
      and 'onload' not in clean_svg and 'evil' not in clean_svg and '<image' not in clean_svg and 'href="#ok"' in clean_svg, clean_svg)
check('不是 SVG / Mermaid 的不要', docs.sanitize_svg('这不是图') == '' and frames.clean_mermaid('随便写的') == ''
      and frames.clean_mermaid('```mermaid\nflowchart LR\n A-->B\n```') == 'flowchart LR\n A-->B')
orig_c = llm.complete
llm.complete = lambda *a, **k: json.dumps({'画面': [{'图': 1, '说明': '模糊的手画结构', '插图': True, '看不清': True},
                                                   {'图': 2, '说明': '清楚的走势', '插图': True, '看不清': False},
                                                   {'图': 3, '说明': '纯文字课件', '插图': False, '看不清': True}]})
dd = frames.describe({}, 'codex', mp4, [{'t': 5.0}, {'t': 10.0}, {'t': 15.0}], [])
check('判断画面看不清：只有要插的图才算', dd[0]['unclear'] and not dd[1]['unclear'] and not dd[2]['unclear'], dd)


def fake_redraw_ai(settings, name, messages, json_mode=False):
    txt = json.dumps(messages, ensure_ascii=False)
    if '（00:05）' in txt:
        return json.dumps({'画': True, '类型': 'mermaid', '代码': '```mermaid\nflowchart LR\n  A[破级] --> B[首峰]\n```', '说明': '破级到首峰'})
    if '（00:10）' in txt:
        return json.dumps({'画': True, '类型': 'svg', '代码': '<svg width="100" height="50" onclick="x()"><path d="M0 50 L50 0" stroke="#000"/></svg>'})
    return json.dumps({'画': False, '原因': '老师没讲清楚'})


llm.complete = fake_redraw_ai
rd = frames.redraw({}, 'codex', mp4, [{'t': 5.0, 'note': 'a', 'figure': True}, {'t': 10.0, 'note': 'b', 'figure': True},
                                      {'t': 15.0, 'note': 'c', 'figure': True}], [])
llm.complete = orig_c
check('AI 重画：关系图写成 Mermaid，形状图写成 SVG（清理过），拿不准的不画', rd[0]['redraw']['kind'] == 'mermaid'
      and rd[0]['redraw']['code'].startswith('flowchart LR') and rd[1]['redraw']['kind'] == 'svg' and 'onclick' not in rd[1]['redraw']['code']
      and 'redraw' not in rd[2], rd)
check('课程重点知道哪张已经有 AI辅助理解图', '（00:05）' not in frames.notes_text(rd) and '已有 AI辅助理解图' in frames.notes_text(rd))
note_dir = TMP / 'redraw-note'
placed = docs.place_figures('要点一\n![[画面 00:05]]\n要点二\n![[画面 00:10]]\n要点三\n![[画面 00:15]]', rd, note_dir, '测试课',
                            lambda t: frames.grab(mp4, t))
svg_file = note_dir / 'image' / '测试课__00-10__AI辅助理解图.svg'
check('剪藏：有重画的放 AI辅助理解图并标注（Mermaid 代码块 / SVG 文件），没有的放截图',
      placed.count('AI辅助理解图｜根据老师讲解和画面整理，非原画面') == 2 and '```mermaid\nflowchart LR' in placed
      and '![[image/测试课__00-10__AI辅助理解图.svg]]' in placed and svg_file.read_text(encoding='utf-8').startswith('<svg')
      and '![[image/测试课__00-15.jpg]]' in placed and (note_dir / 'image' / '测试课__00-15.jpg').is_file(), placed)
# 字幕里手动插的截图（2026-10-30）：挂在哪一句后面按「那一句的开始时间」找，剪藏时插进完整字幕对应的位置
_ss_segs = [{'start': 0.0, 'end': 4.0, 'text': '第一句', 'tr': ''}, {'start': 4.0, 'end': 8.0, 'text': '第二句', 'tr': ''},
            {'start': 8.0, 'end': 12.0, 'text': '第三句', 'tr': ''}]
check('字幕截图：挂在开始时间不晚于它的最后一句后面；-1 是第一句前面；句子删了挂到前一句',
      docs.sub_shot_index(_ss_segs, 4.0) == 1 and docs.sub_shot_index(_ss_segs, -1) == -1 and docs.sub_shot_index(_ss_segs[:1] + _ss_segs[2:], 4.0) == 0)
_ss_doc = {'title': '截图课', 'media': str(mp4), 'segments': _ss_segs, 'summary': '要点',
           'sub_shots': [{'t': 5.0, 'at': 4.0}, {'t': 1.0, 'at': -1}]}
_ss_dir = TMP / 'subshot-note'
_ss_imgs = docs.sub_shot_images(_ss_doc, _ss_dir, '截图课', lambda t: frames.grab(mp4, t))
_ss_md = docs.to_markdown(dict(_ss_doc, _sub_imgs=_ss_imgs))
_full = _ss_md.split('## 完整字幕')[1]
check('字幕截图：剪藏时插进「完整字幕」，在挂的那一句下面（第一句前面的也在）',
      _full.index('![[image/截图课__00-01.jpg]]') < _full.index('第一句') < _full.index('第二句') < _full.index('![[image/截图课__00-05.jpg]]') < _full.index('第三句')
      and (_ss_dir / 'image' / '截图课__00-05.jpg').is_file(), _full)
check('字幕截图：截图变了媒体库提示「待更新剪藏」；没有截图的旧字幕文件指纹不变',
      docs.note_hash(_ss_doc) != docs.note_hash(dict(_ss_doc, sub_shots=[{'t': 5.0, 'at': 8.0}, {'t': 1.0, 'at': -1}]))
      and docs.note_hash(dict(_ss_doc, sub_shots=[])) == docs.note_hash({k: v for k, v in _ss_doc.items() if k != 'sub_shots'}))
# 编辑截图：裁剪、调整大小（2026-11-02）
check('裁剪范围：不合法、太小、整张都当没裁剪；宽度 80～1600 之外当自动', media.norm_crop([0.1, 0.2, 0.6, 0.7]) == [0.1, 0.2, 0.6, 0.7]
      and media.norm_crop([0, 0, 1, 1]) is None and media.norm_crop([0.5, 0.5, 0.51, 0.9]) is None and media.norm_crop('x') is None
      and media.norm_width(360) == 360 and media.norm_width(20) is None and media.norm_width(None) is None)


def _dims(f):
    r = media.run([media.tool('ffprobe'), '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height', '-of', 'csv=p=0', str(f)])
    return tuple(int(x) for x in r.stdout.strip().split(','))


_full_f, _crop_f = frames.grab(mp4, 3.0), frames.grab(mp4, 3.0, crop=[0.25, 0.25, 0.75, 0.75])
_fw, _fh = _dims(_full_f)
_cw, _ch = _dims(_crop_f)
check('界面显示裁剪后的截图：从整张截图裁出框的那一块，另存一张、原图不变', _crop_f != _full_f and abs(_cw - _fw / 2) <= 2 and abs(_ch - _fh / 2) <= 2
      and _dims(_full_f) == (_fw, _fh) and _crop_f.parent == _full_f.parent, (_fw, _fh, _cw, _ch))
_ed_dir = TMP / 'shot-edit-note'
_placed = docs.place_figures('- 甲 [00:03]\n\n![[画面 00:03]]\n\n![[画面 00:06]]', [], _ed_dir, '编辑课', lambda t: frames.grab(mp4, t),
                             {'3': {'crop': [0.25, 0.25, 0.75, 0.75], 'w': 360}, '6': {'w': 500}})
check('剪藏：课程重点里裁剪过的图另存「…__裁剪.jpg」，宽度写进 Obsidian 的图片链接', '![[image/编辑课__00-03__裁剪.jpg|360]]' in _placed
      and '![[image/编辑课__00-06.jpg|500]]' in _placed and abs(_dims(_ed_dir / 'image' / '编辑课__00-03__裁剪.jpg')[0] - _fw / 2) <= 2, _placed)
_ed_imgs = docs.sub_shot_images({'segments': _ss_segs, 'sub_shots': [{'t': 5.0, 'at': 4.0, 'crop': [0, 0, 0.5, 1], 'w': 300}]}, _ed_dir, '编辑课',
                                lambda t: frames.grab(mp4, t))
check('剪藏：字幕里裁剪过的截图另存「…__字幕裁剪.jpg」，带宽度', _ed_imgs == {1: ['![[image/编辑课__00-05__字幕裁剪.jpg|300]]']}
      and (_ed_dir / 'image' / '编辑课__00-05__字幕裁剪.jpg').is_file(), _ed_imgs)
check('裁剪、改大小后媒体库提示「待更新剪藏」；没编辑过的指纹不变', docs.note_hash(dict(_ss_doc, fig_edits={'3': {'w': 360}})) != docs.note_hash(_ss_doc)
      and docs.note_hash(dict(_ss_doc, fig_edits={'3': {}})) == docs.note_hash(_ss_doc)
      and docs.note_hash(dict(_ss_doc, sub_shots=[{'t': 5.0, 'at': 4.0, 'crop': [0, 0, 0.5, 1]}, {'t': 1.0, 'at': -1}])) != docs.note_hash(_ss_doc))
# 课程重点里手动插的截图（2026-11-02）：重新生成时放回去
_new = '一句话。\n\n## 一、甲\n\n- 甲 [00:10]\n  接着说 [00:12]\n- 乙 [01:00]\n\n| 项 | 时间 |\n|---|---|\n| 丙 | 02:00 |\n\n```\n03:00 代码里\n```\n\n## 二、丁\n\n- 丁 [05:00]'
_old = '旧的\n\n![[画面 00:30]]\n\n![[画面 02:30]]\n\n![[画面 03:30]]\n\n![[画面 00:05]]\n\n![[画面 09:00]]'
_out, _keep = docs.keep_sum_shots(_old, _new, [30, 150, 210, 5, 540, 600])
check('课程重点截图放回：跟在出处时间不晚于它的那一条后面（一条重点多行不拆开、表格整张、代码块里的时间不算）',
      '  接着说 [00:12]\n\n![[画面 00:30]]\n\n- 乙' in _out and '| 丙 | 02:00 |\n\n![[画面 02:30]]\n\n![[画面 03:30]]\n\n```' in _out
      and '## 一、甲\n\n![[画面 00:05]]\n\n- 甲 [00:10]' in _out and _out.endswith('- 丁 [05:00]\n\n![[画面 09:00]]')
      and _keep == [30, 150, 210, 5, 540], _out)
check('课程重点截图放回：新的里面已经有就不重复；没有要放回的原样返回', docs.keep_sum_shots('![[画面 00:30]]', _new + '\n\n![[画面 00:30]]', [30])[0].count('00:30]]') == 1
      and docs.keep_sum_shots('无图', _new + '\n\n\n', [30]) == (_new + '\n\n\n', []))

# 课程重点：老师自述、口述与课件不一致、未讲明
T = llm.DEFAULT_TEMPLATE
check('课程重点：老师自述（含课程安排）一律保留并标「老师自述：」', '老师自述一律保留，不评价、不核实、不删除' in T and '课程安排也按老师自述处理' in T and '原创' in T and '出版社' in T)
check('课程重点：口述与课件不一致时分别保留并加整理者注', '老师口述：……' in T and '整理者注：口述与课件不一致' in T)
check('课程重点：没讲明的标（未讲明），只标一次，不补写', '标「（未讲明）」，只标这一次' in T and '不补写、不推测' in T)
check('课程重点：对别人的看法不算自述；没有不一致时不写「老师口述」', '不是自述' in T and '没有不一致时不要写「老师口述：」' in T)
check('查漏补缺也照这几条写', '「老师自述：」' in llm.REVIEW_SYSTEM and '（未讲明）' in llm.REVIEW_SYSTEM and '口述与课件不一致' in llm.REVIEW_SYSTEM)

check('AI 提示里档案名写成「术语表：」，不当成老师名字（可以是课程体系，例如峰级）', llm.teacher_block({'name': '峰级', 'terms': ''}).startswith('术语表：峰级'))

# 逐课积累术语表
sp_ = llm.fix_spans([{'t': 3.0, 'before': '这个风机线很重要', 'after': '这个峰级线很重要', 'by': 'ai'},
                     {'t': 9.0, 'before': '甲乙丙丁戊', 'after': '甲乙', 'by': 'manual'}])
check('校对改动 → 换掉的字（拆分、删字不算）', [(x['wrong'], x['right']) for x in sp_] == [('风机', '峰级')], sp_)
md_terms = '| 术语 | 首次出现 | 老师原话 |\n| --- | --- | --- |\n| 棍态 | 《第4课》[01:00] | 原话 |\n\n| ASR常见识别 | 正确术语 | 出处 |\n| --- | --- | --- |\n| 风机线 | 峰级线 | 《第4课》[05:13] |\n'
tm = llm.terms_from_markdown(md_terms)
check('术语表文档：逐课补充的新术语表和识别错误表都认得', '棍态' in tm and '风机线 => 峰级线' in tm, tm)
tm3 = llm.parse_terms(llm.terms_from_markdown('| ASR常见识别 | 正确术语 | 出处 |\n| --- | --- | --- |\n| 圆你就、原起初，趋势 | 原始趋势 | x |\n'))
check('识别错误表：错写里的中文逗号不拆开（不会多出「趋势 => 原始趋势」）', ('原起初趋势', '原始趋势') in tm3[1]
      and not any(w == '趋势' for w, _ in tm3[1]), tm3)
tm4 = llm.parse_terms(llm.terms_from_markdown('| 直接替换的错写 | 正确术语 |\n| --- | --- |\n| 风极线、风级线 | 峰级线 |\n| 托破旋 | 拓扑学 |\n'))
check('术语表文档：「直接替换」表识别完直接替换（不当成参考）', ('风极线', '峰级线') in tm4[2] and ('托破旋', '拓扑学') in tm4[2]
      and not tm4[1] and llm.apply_fixes('这条风级线', tm4[2]) == '这条峰级线', tm4)
tm5 = llm.parse_terms('风极 => 峰级\n托破旋 =>! 拓扑学\n风极 => 峰级\n托破旋 =>! 拓扑学')
check('手写的和文件里的同一条只算一次', tm5[1] == [('风极', '峰级')] and tm5[2] == [('托破旋', '拓扑学')], tm5)

from core import glossary  # noqa: E402
base = '---\ntitle: 术语表\n---\n\n# 一、峰\n\n正确写法：\n\n> **峰**\n\n# 二、级\n\n正确写法：\n\n> **级**\n'
m1, nt, ne = glossary.merge_markdown(base, [{'term': '棍态', 't': 60, 'quote': '这叫棍态'}],
                                     [{'wrong': '风机线', 'right': '峰级线', 't': 313, 'quote': '风机线'}], '4、基本功-峰级分析')
check('写进术语表：原来的内容一个字不动，末尾加「逐课补充」一节', m1.startswith(base.rstrip('\n')) and '# 三、逐课补充（猛人学习）' in m1 and nt == 1 and ne == 1, m1[-500:])
check('写进术语表：表格行带出处', '| 棍态 | 《4、基本功-峰级分析》[01:00] | 这叫棍态 |' in m1 and '| 风机线 | 峰级线 | 《4、基本功-峰级分析》[05:13] |' in m1, m1[-500:])
m2, nt2, ne2 = glossary.merge_markdown(m1, [{'term': '棍态', 't': 1}, {'term': '夹棍', 't': 2}],
                                       [{'wrong': '风级线', 'right': '峰级线', 't': 5}, {'wrong': '风机线', 'right': '峰级线', 't': 6}], '5、下一课')
check('再加一课：重复的不加，同一个正确写法的错写并进一行', nt2 == 1 and ne2 == 1 and m2.count('| 棍态 |') == 1 and '| 风机线、风级线 | 峰级线 |' in m2
      and m2.count('逐课补充（猛人学习）') == 1 and '《5、下一课》[00:05]' in m2, m2[-600:])
m3 = glossary.merge_markdown(base.replace('\n', '\r\n'), [{'term': '棍态', 't': 1}], [], 'x')[0]
check('写进术语表：保留原来的换行格式', '\r\n' in m3 and '\n' not in m3.replace('\r\n', ''))
tm2 = llm.terms_from_markdown(m2)
check('写进去的内容下次就能用上', '棍态' in tm2 and '夹棍' in tm2 and '风机线, 风级线 => 峰级线' in tm2, tm2)

gfile = TMP / '课程术语表.md'
gfile.write_text(base, encoding='utf-8')
gt = config.save_teacher({'name': '术语积累测试', 'terms_file': str(gfile)})
orig_check, orig_c = llm.check, llm.complete
llm.check = lambda *a, **k: None
seen3 = {}


def fake_terms(s_, n_, msgs, **k):
    seen3['u'] = msgs[-1]['content']
    return json.dumps({'新术语': [{'术语': '峰', '时间': '00:01', '原话': '峰'}, {'术语': '棍态', '时间': '01:00', '原话': '这叫棍态'}],
                       '识别错误': [{'错写': '风机', '正确': '峰级', '时间': '00:03', '原话': '风机线'}, {'错写': '峰', '正确': '峰', '时间': '00:01'}]})


llm.complete = fake_terms
tdoc = {'title': '第4课', 'teacher': gt['id'], 'segments': [{'start': 3.0, 'end': 5.0, 'text': '这个峰级线很重要', 'tr': ''}, {'start': 60.0, 'end': 62.0, 'text': '这叫棍态', 'tr': ''}],
        'fixes': [{'t': 3.0, 'before': '这个风机线很重要', 'after': '这个峰级线很重要', 'by': 'manual'},
                  {'t': 60.0, 'before': '这叫滚态', 'after': '这叫棍态了', 'by': 'ai'}]}
tj = llm.suggest_terms(config.load(), 'deepseek', tdoc, config.get_teacher(gt['id']))
llm.check, llm.complete = orig_check, orig_c
check('找新术语：去掉术语表里已有的', [x['term'] for x in tj['terms']] == ['棍态']
      and [(x['wrong'], x['right'], x['t']) for x in tj['errors']] == [('风机', '峰级', 3.0)], tj)
check('找新术语：把校对改动交给 AI，撤销掉的不算', '「风机」→「峰级」（人工改）' in seen3['u'] and '滚态' not in seen3['u'], seen3['u'][:600])
ta = api.call('terms_add', {'teacher': gt['id'], 'title': '第4课', 'terms': tj['terms'], 'errors': tj['errors']})
gtext = gfile.read_text(encoding='utf-8')
check('加进关联的术语表文件', ta['file'] == str(gfile) and ta['terms'] == 1 and ta['errors'] == 1 and '| 棍态 | 《第4课》[01:00] | 这叫棍态 |' in gtext and gtext.startswith(base.rstrip('\n')), ta)
check('写之前先备份', any((config.DATA_DIR / 'backups' / '术语表').glob('课程术语表-*.md')))
rt = config.get_teacher(gt['id'])
check('老师档案下次读到新术语和识别错误', '棍态' in rt['terms'] and '风机 => 峰级' in rt['terms'], rt['terms'][-200:])
nt_ = config.save_teacher({'name': '没关联文件的老师', 'terms': '峰级'})
ta2 = api.call('terms_add', {'teacher': nt_['id'], 'terms': [{'term': '棍态'}, {'term': '峰级'}], 'errors': [{'wrong': '风机', 'right': '峰级'}]})
check('没关联文件时加进老师档案', ta2['file'] == '' and ta2['terms'] == 1 and config.get_teacher(nt_['id'])['terms'] == '峰级\n棍态\n风机 => 峰级', config.get_teacher(nt_['id'])['terms'])
config.delete_teacher(gt['id'])
config.delete_teacher(nt_['id'])
seen2 = {}


def fake_recheck(s_, n_, msgs, **k):
    seen2['parts'] = msgs[-1]['content']
    return json.dumps({'结果': [{'编号': 1, '改为': '峰级的入门'}, {'编号': 2, '仍存疑': '画面里没有字幕'}]}, ensure_ascii=False)


llm.complete = fake_recheck
rc = frames.recheck_doubts({}, 'codex', mp4, [{'i': 3, 'start': 4.0, 'end': 6.0, 'text': '风级的入门', 'reason': '疑似识别错误'},
                                            {'i': 7, 'start': 20.0, 'end': 22.0, 'text': '讲论文', 'reason': '不通'}], shots)
llm.complete = orig_complete
check('用画面复核存疑句：能确认的改正、看不清的继续存疑', rc == [{'i': 3, 'text': '峰级的入门'}, {'i': 7, 'doubt': '画面里没有字幕'}], rc)
check('复核时附上说话时的画面和附近的画面记录', sum(1 for p in seen2['parts'] if p['type'] == 'image') == 2 and any('课件标题：交易心理学' in p.get('text', '') for p in seen2['parts']))
api.call('settings', {'settings': {'vision': 'deepseek'}})
rk = run_steps(mp4, ['recheck'])
check('复核存疑句也要能看图的 AI（这一步跳过）', stage(rk, 'recheck').get('status') == 'skipped' and '不能看图' in stage(rk, 'recheck').get('reason', ''), rk)
(config.CACHE_DIR / 'frames' / 'old').mkdir(parents=True, exist_ok=True)
oldf = config.CACHE_DIR / 'frames' / 'old' / 'x.jpg'
oldf.write_bytes(b'0')
os.utime(oldf, (time.time() - 40 * 86400, time.time() - 40 * 86400))
cache.cleanup()
check('30 天没用的截图自动清掉', not oldf.exists() and g.exists())

# ---------------------------------------------------------------- 1.6 书籍 ----
from core import book  # noqa: E402
import zipfile  # noqa: E402
if not (ROOT / 'test-media' / '测试书籍.pdf').exists():
    import subprocess
    subprocess.run([sys.executable, str(ROOT / 'tests' / 'make_test_book.py')], check=True)
BOOKS = TMP / 'books'
BOOKS.mkdir()
pdfp = BOOKS / '《测试书籍》测试作者.pdf'
shutil.copy(ROOT / 'test-media' / '测试书籍.pdf', pdfp)
info = book.read(str(pdfp))
check('PDF：读出每页文字', len(info['pages']) == 8 and '三段结构' in info['pages'][3]['text'])
check('PDF：认出书上的页码（PDF 第 3 页 = P1）', info['shift'] == 2 and book.page_ref(3, 2) == 'P1' and book.page_ref(1, 2) == 'PDF 第 1 页')
check('PDF：认出有图的页', info['pages'][3]['figure'] and not info['pages'][4]['figure'])
chs = book.detect_chapters(info)
check('PDF：按「第X章」分章，目录页不算', [(c['title'], c['start'], c['end']) for c in chs] == [('第一章 趋势的起点', 3, 5), ('第二章 仓位的分配', 6, 8)], chs)
check('页码范围写法', book.range_ref(3, 5, 2) == 'P1-P3' and book.range_ref(3, 5, None) == 'PDF 第 3-5 页')
nd = book.new_doc(str(pdfp), info)
check('书名、作者从文件名「《书名》作者」读出', nd['title'] == '测试书籍' and nd['author'] == '测试作者' and len(nd['chapters']) == 2 and nd['segments'] == [])
pg = book.render_page(str(pdfp), 4)
check('渲染页面图', pg.exists() and pg.stat().st_size > 5000)
check('引用到的页', book.cited_pages('……（P1-P2）……（PDF 第 8 页）', 2) == {3, 4, 8})
check('没被引用的页', [p['n'] for p in book.uncovered_pages(info, chs[0], '- 定义（P1）')] == [] and
      [p['n'] for p in book.uncovered_pages(dict(info, pages=[dict(p, text=p['text'] * 5) for p in info['pages']]), chs[0], '- 定义（P1）')] == [4, 5])
# Word
docx = BOOKS / '讲义.docx'
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
paras = ''.join(f'<w:p><w:pPr><w:pStyle w:val="{st}"/></w:pPr><w:r><w:t>{t}</w:t></w:r></w:p>' if st else f'<w:p><w:r><w:t>{t}</w:t></w:r></w:p>'
                for st, t in [('Heading1', '第一章 开始'), ('', '正文一' * 50), ('Heading1', '第二章 继续'), ('', '正文二' * 50)])
table = '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>模型</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>比例</w:t></w:r></w:p></w:tc></w:tr><w:tr><w:tc><w:p><w:r><w:t>金字塔</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>40%</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
with zipfile.ZipFile(docx, 'w') as z:
    z.writestr('word/document.xml', f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{W}"><w:body>{paras}{table}</w:body></w:document>')
    z.writestr('docProps/core.xml', '<?xml version="1.0"?><cp:coreProperties xmlns:cp="x" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>讲义标题</dc:title><dc:creator>某作者</dc:creator></cp:coreProperties>')
dinfo = book.read(str(docx))
dch = book.detect_chapters(dinfo)
check('Word：按标题分章、表格转成 Markdown', [c['title'] for c in dch] == ['第一章 开始', '第二章 继续'] and '| 金字塔 | 40% |' in dinfo['pages'][-1]['text'] and dinfo['title'] == '讲义标题', (dch, dinfo['pages'][-1]['text'][-60:]))
txt = BOOKS / '文章.md'
txt.write_text('# 第一部分\n\n' + '甲' * 300 + '\n\n# 第二部分\n\n' + '乙' * 300, encoding='utf-8')
check('文本：按 # 标题分章', [c['title'] for c in book.detect_chapters(book.read(str(txt)))] == ['第一部分', '第二部分'])
# 打开、整理（AI 用假的）、剪藏
ob = api.call('open', {'path': str(pdfp)})
check('打开 PDF 进入书籍模式', ob['ok'] and ob['kind'] == 'book' and ob['viewer'].startswith('file:///') and ob['book']['pages'] == 8 and len(ob['doc']['chapters']) == 2, ob.get('error'))
api.call('settings', {'settings': {'chat': 'openai', 'providers': {'openai': {'key': 'sk-test-1234567890'}}}})
calls = []


def fake_complete(s_, n_, msgs, **k):
    text = json.dumps([m['content'] if isinstance(m['content'], str) else [p.get('text', '[图]') for p in m['content']] for m in msgs], ensure_ascii=False)
    calls.append(text)
    if '查漏员' in text:
        return json.dumps({'补充': []})
    return '## 一、本章主题\n\n讲趋势。（P1）\n\n## 十三、一句话概括\n\n三段。（P1-P3）\n\n![[原书图示 第4页 图1-1 三段结构 @0.1,0.3,0.9,0.6]]\n*原书图示｜图 1-1 三段结构（P2）*'


llm.complete = fake_complete
jb = run_steps(pdfp, ['organize'], chapters=[0])
bmd = docs.load(str(pdfp))['chapters'][0]['md']
check('整理一章（写进书旁边的整理结果）', jb['status'] == 'completed' and bmd.startswith('## 一、本章主题') and not docs.load(str(pdfp))['chapters'][1]['md'], jb)
check('有图表的页附上页面图、锚点写法正确', '[图]' in calls[0] and '锚点写作（P2）' in calls[0] and '【PDF 第 4 页' in calls[0])
# 2026-10-29 改成提炼：不设固定栏目、篇幅写成具体字数（约原文 1/5）、不逐段标页码、每章最多 1 张图；不再查漏补页
check('整理要求是提炼：不设固定栏目、要留的四样、每章挑最关键的 1～2 张图', '十三、' not in book.DEFAULT_RULES and '提炼笔记' in book.DEFAULT_RULES
      and '照着做' in book.DEFAULT_RULES and '原话' in book.DEFAULT_RULES and '最关键的 1～2 张' in book.DEFAULT_RULES)
check('插原书图示：看不准位置可以不写，软件用整页（不要因此不插图）', '看不准位置就不写' in book.FIGURE_RULE)
import urllib.parse  # noqa: E402
import urllib.request  # noqa: E402
_f1 = api.call('book_fig', {'path': str(pdfp), 'n': 4, 'region': '0.1,0.3,0.9,0.6'})
_f2 = api.call('book_fig', {'path': str(pdfp), 'n': 4, 'region': ''})
check('整理笔记里的原书图示在软件里直接显示：按位置裁出来；没写位置就整页', _f1['ok'] and _f2['ok'] and _f1['src'] != _f2['src']
      and all(Path(urllib.request.url2pathname(urllib.parse.urlparse(x['src']).path)).is_file() for x in (_f1, _f2)), (_f1, _f2))
check('篇幅写成具体字数：约原文 1/5，最多 1/4', '篇幅：这一章原文约' in calls[0] and '字左右，最多不超过' in calls[0], calls[0][:400])
check('不再查漏补页', not any('查漏员' in c for c in calls), len(calls))
_lr = book.length_rule({'pages': [{'n': 1, 'text': '字' * 10000}]}, {'start': 1, 'end': 1})
check('篇幅：1 万字的一章写 2000 字左右、最多 2500', '原文约 10000 字' in _lr and '2000 字左右' in _lr and '不超过 2500' in _lr, _lr)
rules = TMP / '规范.md'
rules.write_text('# 外部书籍与文档整理规范 V9\n只回答原书讲了什么。', encoding='utf-8')
_rv = book._rules({'book_rules_file': str(rules)})
check('关联知识库里的书籍整理规范：只作补充，冲突时以软件的提炼要求为准', '规范 V9' in _rv and _rv.index('提炼笔记') < _rv.index('规范 V9') and '规范 V9' not in book._rules({}))
llm.complete = orig_complete
bdoc = ob['doc']
bdoc['chapters'][0]['md'] = bmd
bdoc['overview'] = '## 一、书籍基本信息\n\n测试。'
api.call('save', {'path': str(pdfp), 'doc': bdoc})
check('书籍整理结果存进旁边的文件', json.loads(docs.sidecar(str(pdfp)).read_text(encoding='utf-8'))['chapters'][0]['md'].startswith('## 一'))
api.call('settings', {'settings': {'obsidian': dict(config.load()['obsidian'], vault=str(vault), book_folder='资料/书籍')}})
bc = api.call('clip', {'doc': bdoc}) if False else api.call('book_clip', {'doc': bdoc})
bdir = vault / '资料' / '书籍' / '《测试书籍》测试作者'
ch1 = bdir / '01、第一章 趋势的起点.md'
check('剪藏书籍：总览 + 章节文件', bc['ok'] and (bdir / '00、全书总览.md').exists() and ch1.exists() and not (bdir / '02、第二章 仓位的分配.md').exists(), bc)
cb = ch1.read_text(encoding='utf-8')
check('章节笔记：属性、来源说明、页码范围', '类型: 书籍章节整理' in cb and '页码范围: "P1-P3"' in cb and '状态:' not in cb and '整理范围：P1-P3（PDF 第 3-5 页）' in cb , cb[:500])
figp = bdir / 'image' / '原书图示-图1-1-三段结构.jpg'
check('原书图示按位置裁剪、压缩', '![[image/原书图示-图1-1-三段结构.jpg]]' in cb and figp.exists() and figp.stat().st_size < 60000, cb[-300:])
from PIL import Image  # noqa: E402
with Image.open(figp) as im:
    check('裁剪后的图比整页小', im.width <= 960 and im.height < im.width, im.size)
ch1.write_text(cb.replace('类型: 书籍章节整理', '类型: 书籍章节整理\nstatus: "已确认-资料"'), encoding='utf-8')  # 以前的英文属性，用户改过
api.call('book_clip', {'doc': bdoc})
check('再次剪藏书籍保留状态（以前英文的 status 也认得）', '状态: "已确认-资料"' in ch1.read_text(encoding='utf-8'))

# 书籍重新剪藏：和视频笔记一样，保留用户在 Obsidian 里加的小节、标注和属性
md_orig = bdoc['chapters'][0]['md']
bdoc['chapters'][0]['md'] = '## 一、本章主题\n\n本章讲趋势的起点（P1）。\n\n## 二、核心内容\n\n- 趋势由三段构成（P2）\n\n## 十三、一句话概括\n\n趋势从三段开始。'
bc2 = api.call('book_clip', {'doc': bdoc})
bdoc['clip'] = {'dir': bc2['dir'], 'time': bc2['time'], 'files': bc2['records']}
rec1 = bc2['records']['01、第一章 趋势的起点']
check('书籍剪藏记下每篇笔记软件写的小节和指纹', '一、本章主题' in rec1['sections'] and rec1['file_hash'].startswith('v2:') and '00、全书总览' in bc2['records'], rec1)
lines = ch1.read_text(encoding='utf-8').split('\n')
i1 = lines.index('## 一、本章主题')
first = next(ln for ln in lines[i1 + 1:] if ln.strip())
i2 = next(k for k, ln in enumerate(lines) if ln.startswith('## 二、'))
lines[i2:i2] = ['## 我的笔记', '', '这一章对照 [[峰级线]] 看', '']
lines[i1 + 1 + lines[i1 + 1:].index(first) + 1:i1 + 1 + lines[i1 + 1:].index(first) + 1] = ['', '> [!note] 我的理解', '> 先看本章主题', '']
txt = '\n'.join(lines).replace('\ntags:\n', '\n关联课程: "[[第4课]]"\ntags:\n', 1).replace('  - 书籍\n', '  - 书籍\n  - 我的书单\n', 1)
ch1.write_text(txt, encoding='utf-8')
bc3 = api.call('book_clip', {'doc': bdoc})
t3 = ch1.read_text(encoding='utf-8')
check('书籍：只加了自己的东西，重新剪藏不弹窗', not bc3.get('conflict'), bc3)
check('书籍：自己加的小节留在原来的位置', t3.index('## 一、本章主题') < t3.index('## 我的笔记') < t3.index('## 二、') and '这一章对照 [[峰级线]] 看' in t3, t3[:1500])
check('书籍：标注跟着那一行', t3.index(first) < t3.index('> [!note] 我的理解') < t3.index('## 我的笔记'), t3[:1500])
check('书籍：自己加的属性和标签保留', '关联课程: "[[第4课]]"' in t3 and '  - 我的标签' not in t3 and '  - 我的书单' in t3 and t3.count('  - 猛人学习') == 1)
check('书籍：之前保留的状态还在', '状态: "已确认-资料"' in t3)
bdoc['clip'] = {'dir': bc3['dir'], 'time': bc3['time'], 'files': bc3['records']}
md0 = bdoc['chapters'][0]['md']
bdoc['chapters'][0]['md'] = md0.replace(first, '（本章主题重新整理过）', 1)
bc4 = api.call('book_clip', {'doc': bdoc})
t4 = ch1.read_text(encoding='utf-8')
check('书籍：标注上面那一行没了，标注挪到这一节最后，不丢', '> [!note] 我的理解' in t4 and bc4['moved'] == 1 and t4.index('（本章主题重新整理过）') < t4.index('> [!note] 我的理解') < t4.index('## 我的笔记'), t4[:1500])
bdoc['clip'] = {'dir': bc4['dir'], 'time': bc4['time'], 'files': bc4['records']}
ch1.write_text(t4.replace('（本章主题重新整理过）', '（我在 Obsidian 里直接改了这句）'), encoding='utf-8')
bc5 = api.call('book_clip', {'doc': bdoc})
check('书籍：直接改了软件写的小节，先问再覆盖', bc5.get('conflict') and bc5['changed'] == ['01、第一章 趋势的起点'] and '（我在 Obsidian 里直接改了这句）' in ch1.read_text(encoding='utf-8'), bc5)
bc6 = api.call('book_clip', {'doc': bdoc, 'force': True})
check('书籍：确认后先备份再更新，自己的内容照样保留', bc6['backups'] == 1 and '（我在 Obsidian 里直接改了这句）' not in ch1.read_text(encoding='utf-8')
      and '## 我的笔记' in ch1.read_text(encoding='utf-8') and any((config.DATA_DIR / 'backups' / '笔记').glob('01、第一章 趋势的起点-*.md')), bc6)
bdoc['chapters'][0]['md'] = md_orig
bdoc['clip'] = {'dir': bc6['dir'], 'time': bc6['time'], 'files': bc6['records']}
check('笔记文件名：前言、目录不编号，正文从 01 开始', book.chapter_files([{'title': '前言与目录'}, {'title': '第一章 甲'}, {'title': '第二章 乙'}]) == ['前言与目录', '01、第一章 甲', '02、第二章 乙'])
# 每页顶上印着部名（页眉）：不能每页都当成新的一章（2026-10-29《研究是一门艺术》被分成 121 章）；页眉下面一行才是章名的也要认出来
_hp = lambda n, t: {'n': n, 'label': None, 'text': t + '\n正文内容' * 120, 'images': 0, 'figure': False}
_hdr = [_hp(1, '前言\n一些说明'), _hp(2, '第一部分\n研究与读者'), _hp(3, '第1章 思想的足迹'), _hp(4, '第一部分 研究与读者'), _hp(5, '第一部分 研究与读者i'),
        _hp(6, '第一部分 研究与读者\n第2章 与读者建立联系'), _hp(7, '第一部分 研究与读者'), _hp(8, '第二部分\n提问题'), _hp(9, '第二部分提问题'), _hp(10, '第二部分 提问题')]
check('页眉不算新的一章，页眉下面的章名照样认出', [(c['title'], c['start'], c['end']) for c in book.detect_chapters({'pages': _hdr, 'toc': []})]
      == [('前言与目录', 1, 1), ('第一部分 研究与读者', 2, 2), ('第1章 思想的足迹', 3, 5), ('第2章 与读者建立联系', 6, 7), ('第二部分 提问题', 8, 10)],
      book.detect_chapters({'pages': _hdr, 'toc': []}))
# AI 读目录分章要和原书目录一致（2026-10-29）：开篇、结语这些单独成篇，部分的标题页并进它的第一篇，每篇记下属于哪个部分
_toc_reply = {'部分': [{'标题': '第一部分 甲', '书上页码': 2}, {'标题': '第二部分  乙', '书上页码': 10}],
              '篇': [{'标题': '前言', '书上页码': 2, 'PDF': True, '部分': ''}, {'标题': '开篇 丙', '书上页码': 3, '部分': '第一部分 甲'},
                     {'标题': '第1章 丁', '书上页码': 6, '部分': '第一部分 甲'}, {'标题': '第2章 戊', '书上页码': 12, '部分': '第二部分 乙'},
                     {'标题': '第3章 己', '书上页码': 14}, {'标题': '结语', '书上页码': 16, '部分': ''}]}
_oc = llm.complete
llm.complete = lambda *a, **k: json.dumps(_toc_reply, ensure_ascii=False)
_tinfo = {'pages': [{'n': k, 'text': '正文' * 100} for k in range(1, 21)], 'shift': 2}
_tch = book.ai_chapters({}, 'x', _tinfo)
llm.complete = _oc
check('AI 读目录：和原书目录一致，开篇单独一篇、部分的标题页并进开篇、没写部分的按页码归、结语不属于部分、前言前面的封面并进前言',
      [(c['title'], c['start'], c['end'], c.get('part', '')) for c in _tch] == [
          ('前言', 1, 3, ''), ('开篇 丙', 4, 7, '第一部分 甲'), ('第1章 丁', 8, 11, '第一部分 甲'),
          ('第2章 戊', 12, 15, '第二部分 乙'), ('第3章 己', 16, 17, '第二部分 乙'), ('结语', 18, 20, '')], _tch)
check('全书总览拿到的全书结构按部分分组', book.structure(_tch) == '前言；第一部分 甲：开篇 丙、第1章 丁；第二部分 乙：第2章 戊、第3章 己；结语', book.structure(_tch))
# 书自带的目录（书签、EPUB 目录）按完整层次分章：最上一层是部分时按部分分组、下一层每项单独成篇；最上一层是章时节不拆开
_bm = [{'level': 1, 'title': '前言', 'n': 2}, {'level': 1, 'title': '第一部分 甲', 'n': 4}, {'level': 2, 'title': '开篇 丙', 'n': 5},
       {'level': 2, 'title': '第1章 丁', 'n': 8}, {'level': 3, 'title': '1.1 一节', 'n': 9}, {'level': 1, 'title': '第二部分 乙', 'n': 12},
       {'level': 2, 'title': '第2章 戊', 'n': 14}, {'level': 1, 'title': '附录', 'n': 18}]
check('书签是「部分 → 章 → 节」：按部分分组，开篇单独一篇，节不拆开', [(c['title'], c['start'], c['end'], c.get('part', '')) for c in book.detect_chapters(dict(_tinfo, toc=_bm))] == [
    ('前言', 1, 3, ''), ('开篇 丙', 4, 7, '第一部分 甲'), ('第1章 丁', 8, 11, '第一部分 甲'), ('第2章 戊', 12, 17, '第二部分 乙'), ('附录', 18, 20, '')],
    book.detect_chapters(dict(_tinfo, toc=_bm)))
_bm2 = [{'level': 1, 'title': '第一章 甲', 'n': 1}, {'level': 2, 'title': '第一节', 'n': 2}, {'level': 1, 'title': '第二章 乙', 'n': 10}, {'level': 2, 'title': '第一节', 'n': 11}]
check('书签是「章 → 节」：一章一篇，节不拆开、不分组', [(c['title'], c['start'], c['end'], c.get('part', '')) for c in book.detect_chapters(dict(_tinfo, toc=_bm2))] == [
    ('第一章 甲', 1, 9, ''), ('第二章 乙', 10, 20, '')], book.detect_chapters(dict(_tinfo, toc=_bm2)))
# 目录页自动找：目录排在第 30 页以后、而且很长，整份目录都要交给 AI（以前只看前 25 页、每页前 1500 字）
_lp = [{'n': k, 'text': '序言正文' * 60} for k in range(1, 61)]
_lp[31] = {'n': 32, 'text': '目 录\n' + '\n'.join(f'第{k}章 ' + '很长的章名' * 6 + f'……{k * 10}' for k in range(1, 60))}
_lp[32] = {'n': 33, 'text': '\n'.join(f'第{k}章 后面一页的章名……{k * 10}' for k in range(60, 80)) + '\n附录 最后一项……900'}
check('自动找到目录页：从带「目录」的那页起，连着的目录页都算', [p['n'] for p in book.toc_pages(_lp)] == [32, 33], [p['n'] for p in book.toc_pages(_lp)])
# 两栏排的目录 OCR 后：「第一节」一栏、节名一栏、页码单独一行，「目」「录」两个字分在后面（2026-10-29《K线动能理论》）
_tc = [{'n': k, 'text': '正文' * 100} for k in range(1, 21)]
_tc[8] = {'n': 9, 'text': '\n'.join(['第一章', '第一节', '第二节', '第二章', '第一节', '目', '录', '基本概念', '客观规律', '3', '产生背景', '6',
                                     '特性和应用', '作用', '19', '1'])}
_tc[9] = {'n': 10, 'text': '\n'.join(['第三章', '第一节', '第二节', '形态概念', '分界线', '51', '引力效应', '60', '2'])}
check('两栏排的目录（节名和页码分在不同行）也认得出是目录页', [p['n'] for p in book.toc_pages(_tc)] == [9, 10], [p['n'] for p in book.toc_pages(_tc)])
check('AI 读目录：章没写页码时用下面第一节的页码', '用它下面第一节的页码' in book.TOC_PROMPT and '两栏' in book.TOC_PROMPT)
_seen = {}
llm.complete = lambda s_, n_, msgs, **k: _seen.setdefault('u', msgs[-1]['content']) and json.dumps({'部分': [], '篇': []})
book.ai_chapters({}, 'x', {'pages': _lp, 'shift': None})
llm.complete = _oc
check('AI 读目录：整份目录都交给 AI（目录很长、排在第 30 页以后也不漏）', '第59章 很长的章名' in _seen['u'] and '附录 最后一项' in _seen['u'] and '目录页' in _seen['u'], len(_seen.get('u', '')))
# 外文书：整理稿统一用中文写、术语附原文（2026-10-29）；中文书不加这条
_en = {'pages': [{'n': k, 'text': 'Research is a craft that anyone can learn. ' * 8} for k in range(1, 6)], 'kind': 'pdf', 'shift': None}
_ch = {'title': 'Chapter 1', 'start': 1, 'end': 2}
check('外文书：整理要求里写明用中文写、术语附原文；中文书没有这条', not book.is_chinese(_en) and book.is_chinese(_tinfo)
      and book.LANG_RULE in book._book_head({}, _ch, _en, None) and book.LANG_RULE not in book._book_head({}, _ch, dict(_tinfo, kind='pdf'), None))
_seen.clear()
llm.complete = lambda s_, n_, msgs, **k: _seen.setdefault('u', msgs[-1]['content']) and '## 一、书籍基本信息'
book.overview({}, 'x', {'title': 'The Craft of Research'}, [dict(_ch, md='整理稿', range='P1-P2')], ['01、Chapter 1'], _en)
llm.complete = _oc
check('外文书：全书总览也要求用中文写', book.LANG_RULE in _seen.get('u', ''))
# 书的 AI 对话（2026-10-29）：问这一章读这一章原文全文 + 笔记；问整本书读全部笔记 + 本机挑的相关原文页；问答由后台存
_cinfo = {'pages': [{'n': 1, 'text': '第一章 趋势\n趋势从三段开始，第一段是试探。' * 6}, {'n': 2, 'text': '第二段是确认。' * 15},
                    {'n': 3, 'text': '仓位要按风险来分配，每次不超过百分之二。' * 6}], 'shift': None}
_cdoc = {'title': '测试书', 'chapters': [{'title': '第一章 趋势', 'start': 1, 'end': 2, 'md': '> 一句话：趋势分三段。'},
                                       {'title': '第二章 仓位', 'start': 3, 'end': 3, 'md': '> 一句话：按风险分仓。'}]}
_m = book.chat_messages(_cinfo, _cdoc, 'chapter', 0, [{'role': 'user', 'content': '第一段是什么？'}])
check('书的 AI 对话·问这一章：给这一章的原文全文和笔记，不给别的章', '第二段是确认' in _m[0]['content'] and '趋势分三段' in _m[0]['content']
      and '仓位要按风险' not in _m[0]['content'] and _m[-1]['content'] == '第一段是什么？')
_m = book.chat_messages(_cinfo, _cdoc, 'book', -1, [{'role': 'user', 'content': '仓位怎么按风险分配？'}])
check('书的 AI 对话·问整本书：给全部笔记，再挑和问题相关的原文页', '趋势分三段' in _m[0]['content'] and '按风险分仓' in _m[0]['content']
      and '每次不超过百分之二' in _m[0]['content'], _m[0]['content'][-300:])
_os = llm.stream
llm.stream = lambda s_, n_, msgs, cancel=None, json_mode=False: iter(['趋势', '分三段（P1）。'])
_j = wait(api.call('book_chat', {'path': str(pdfp), 'scope': 'chapter', 'i': 0, 'key': 'ch:3-5', 'messages': [{'role': 'user', 'content': '讲了什么？'}]})['job'])
llm.stream = _os
_bc = (docs.load(str(pdfp)).get('book_chat') or {}).get('ch:3-5')
check('书的 AI 对话：问答由后台存进书的整理结果', _j['status'] == 'done'
      and _bc == [{'role': 'user', 'content': '讲了什么？'}, {'role': 'assistant', 'content': '趋势分三段（P1）。'}], (_j, _bc))
api.call('book_chat_clear', {'path': str(pdfp), 'key': 'ch:3-5'})
check('书的 AI 对话：可以清空', 'ch:3-5' not in (docs.load(str(pdfp)).get('book_chat') or {}))
# 扫描书识别文字（2026-10-29，core/ocr.py）：只有图片的 PDF → 可搜索 PDF（页面不变，下面一层看不见的文字），原文件挪进「_OCR前的原件」
from core import ocr  # noqa: E402
import pypdfium2 as _pdfium  # noqa: E402
_scan = BOOKS / '扫描书' / '《扫描测试》.pdf'
_scan.parent.mkdir(exist_ok=True)
_srcpdf, _new = _pdfium.PdfDocument(str(pdfp)), _pdfium.PdfDocument.new()
for _i in range(len(_srcpdf)):  # 每页渲染成图片，做成一本只有图片的「扫描书」
    import io as _io
    _pg = _srcpdf[_i]; _w, _h = _pg.get_size()
    _b = _io.BytesIO(); _pg.render(scale=1).to_pil().convert('RGB').save(_b, 'JPEG'); _b.seek(0); _pg.close()
    _np = _new.new_page(_w, _h); _im = _pdfium.PdfImage.new(_new); _im.load_jpeg(_b, inline=False, autoclose=False)
    _im.set_matrix(_pdfium.PdfMatrix().scale(_w, _h)); _np.insert_obj(_im); _np.gen_content(); _np.close()
_new.save(str(_scan)); _new.close(); _srcpdf.close()
check('扫描书：认得出是扫描版、没有文字', book.read(str(_scan))['scan'] and book.read(str(_scan))['chars'] == 0)
r = api.call('book_ocr', {'path': str(pdfp)})
check('普通 PDF（有文字的电子版）不让识别', not r['ok'] and '扫描版' in r['error'], r)
_oa, _orc = ocr.available, ocr.recognize
ocr.available = lambda: (True, '')


def _fake_recognize(images, work):  # 每页两行：标题行和正文行（y 从上往下），像 Windows 识别返回的那样带每个字的位置
    out = {}
    for f in sorted(images.glob('p*.png')):
        n = int(f.stem[1:])
        out[n] = {'w': 1000, 'h': 1400, 'lines': [
            {'text': {3: '第 一 章 趋 势 的 起 点', 6: '第 二 章 仓 位 的 分 配'}.get(n, f'第 {n} 页 的 标 题'), 'words': [{'x': 100, 'y': 100, 'w': 500, 'h': 40}]},
            {'text': f'这 是 第 {n} 页 识 别 出 来 的 正 文 。', 'words': [{'x': 100, 'y': 300, 'w': 700, 'h': 30}]}]}
    return out


ocr.recognize = _fake_recognize
_j = wait(api.call('book_ocr', {'path': str(_scan)})['job'])
ocr.available, ocr.recognize = _oa, _orc
_ri = book.read(str(_scan))
_t3 = next(p['text'] for p in _ri['pages'] if p['n'] == 3)
check('识别文字：生成可搜索 PDF，软件读得出每页的文字，行序对（标题在正文前）', _j['status'] == 'done' and _ri['chars'] > 0
      and _t3.index('第一章趋势的起点') < _t3.index('这是第3页识别出来的正文'), (_j, _t3))
_orig = _scan.parent / '_OCR前的原件（确认没问题可以删）' / _scan.name
check('识别文字：原文件挪进同一文件夹的「_OCR前的原件」，新文件用原来的名字', _orig.exists() and _scan.exists() and book.read(str(_orig))['chars'] == 0, _j)
check('识别文字：还没整理过的书按新文字重新分章', any(c['title'].startswith('第一章') for c in docs.load(str(_scan))['chapters']), docs.load(str(_scan))['chapters'])
check('识别文字：临时文件做完就删了', not [x for x in ocr.OCR_DIR.glob('*') if x.name != 'winocr.ps1'], list(ocr.OCR_DIR.glob('*')))
shutil.rmtree(_scan.parent, ignore_errors=True)
# 几页的图同时生成（朗读原文时一屏有好几页带图）：pdfium 不能同时用，要排队，不然全都「Failed to load page」
import concurrent.futures as _cf
shutil.rmtree(book.pages_dir(pdfp), ignore_errors=True)
with _cf.ThreadPoolExecutor(4) as _ex:
    _outs = list(_ex.map(lambda n: book.render_page(pdfp, n, 333), [1, 2, 3, 1, 2, 3]))
check('几页的图同时生成也不出错', all(Path(o).is_file() and Path(o).stat().st_size > 0 for o in _outs), _outs)
# 朗读原文的文字：去掉每页都有的页眉、页码，排版折断的行接成段落，有图表的页标出来
_rp = [{'n': k, 'text': f'研究是一门艺术 页眉{"i" if k % 2 else ""}\n这是第{k}页第一段的第一行，排得满满的一整行\n第一段接着写完。\n第二段只有短短一行。\n• 第{k}个要点\n'
        + ('a\n4,000,000 3,500,000 •\n如图 3 所示的流程\n' if k == 3 else '') + f'{k + 10}', 'images': []} for k in range(1, 6)]
_rd = book.reading_pages({'pages': _rp}, 2, 3)
check('朗读原文：只取这几页，去掉页眉和页码', [p['n'] for p in _rd] == [2, 3] and not any('页眉' in x or x.strip() in ('12', '13') for p in _rd for x in p['paras']), _rd)
check('朗读原文：折断的行接成段落，「•」开头另起一段', _rd[0]['paras'][:3] == ['这是第2页第一段的第一行，排得满满的一整行第一段接着写完。', '第二段只有短短一行。', '• 第2个要点'], _rd[0]['paras'])
check('朗读原文：有图表的页标出来（界面附上这一页的图，朗读跳过）', not _rd[0]['figure'] and _rd[1]['figure'], _rd)
check('朗读原文：中文书有图表的页，图里识别出的零碎字母和数字不读', not any(x in ('a', '4,000,000 3,500,000 •') for x in _rd[1]['paras'])
      and any('如图 3' in x for x in _rd[1]['paras']), _rd[1]['paras'])
check('只认出一章时按页数分段', [c['title'] for c in book.detect_chapters(dict(info, toc=[], pages=[dict(p, text=p['text'].replace('第二章', '仓位一节')) for p in info['pages']]))] == ['全文'])
# 同名 Word（WPS 做 OCR 后导出的）当正文参考
shutil.copy(docx, BOOKS / '《测试书籍》测试作者.docx')
with zipfile.ZipFile(BOOKS / '《测试书籍》测试作者.docx', 'w') as z:
    body = ''.join(f'<w:p><w:r><w:t>{t}</w:t></w:r></w:p>' for t in ['目录', '第一章 趋势的起点 (1)', '第二章 仓位的分配 (4)', '第 一 章  趋 势 的 起 点', 'Word 里第一章的正文', '第二章 仓位的分配', 'Word 里第二章的正文'])
    z.writestr('word/document.xml', f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>')
book._cache.clear()
info2 = book.read(str(pdfp))
sl = book.companion_slice(info2, chs, 0)
check('找到同名 Word，按章名取出这一章的正文（跳过目录）', info2['companion'] and 'Word 里第一章的正文' in sl and 'Word 里第二章的正文' not in sl and '(1)' not in sl, sl)
# 回页面核对
md_v = '## 一、本章主题\n\n回调幅度为 30%。（P1）\n\n## 十一、需要人工确认的位置\n\n- P2：文字层写 30%，Word 写 50%。\n\n## 十三、一句话概括\n\n三段。'
llm.complete = lambda *a, **k: json.dumps({'替换': [{'原文': '回调幅度为 30%', '改为': '回调幅度为 50%'}], '已确认': ['| P2 | 文字层 30%、Word 50% | 页面图确认为 50% |'], '仍需确认': ''}, ensure_ascii=False)
mv, nv = book.verify_doubts({}, 'codex', str(pdfp), info, chs[0], md_v, {'title': 't'})
llm.complete = orig_complete
check('回页面核对：改正正文、记进 OCR 修正、清掉已确认的', nv == 1 and '回调幅度为 50%' in mv and '十一、需要人工确认' not in mv and '## 十二、已确认的 OCR 修正' in mv
      and mv.index('## 十二') < mv.index('## 十三') and '| P2 | 文字层 30%、Word 50% | 页面图确认为 50% |' in mv, mv)
(BOOKS / '_关键帧缓存').mkdir()
shutil.copy(pdfp, BOOKS / '_关键帧缓存' / '视觉资料.pdf')
lb = api.call('library_add_folder', {'path': str(BOOKS)})
names = [i['name'] for i in lb['groups'][-1]['children']]
check('媒体库列出 PDF 和 Word、不列「_」开头的辅助文件夹', '《测试书籍》测试作者' in names and '讲义' in names and '视觉资料' not in names and '文章' not in names, names)
bi = next(i for i in lb['groups'][-1]['children'] if i['name'] == '《测试书籍》测试作者')
check('媒体库显示整理进度', bi.get('book') == {'done': 1, 'total': 2}, bi)
api.call('library_remove', {'kind': 'folder', 'path': str(BOOKS)})

# 媒体库
lib = api.call('library_add_folder', {'path': str(MEDIA)})
check('添加文件夹：告诉界面加了几个视频', lib['status'] == 'added' and lib['video'] == 2 and lib['groups'][0]['count'] == 2, lib)
item = next(i for i in lib['groups'][0]['children'] if i['name'] == '演示视频')
check('媒体库显示字幕和进度', item['subs'] == 9 and item['pos'] == 12.5, item)
api.call('library_set', {'path': str(MEDIA), 'teacher': t['id']})
check('文件夹绑定老师', library.folder_teacher(str(MEDIA / '旧格式.avi')) == t['id'])
check('打开时自动用文件夹的老师', api.call('open', {'path': str(MEDIA / '旧格式.avi')})['doc']['teacher'] == t['id'])
r_ = api.call('library_add_files', {'paths': [str(MEDIA / '演示视频.mp4')]})
check('已经在加进来的文件夹里的文件，不重复加', r_['added'] == 0 and r_['inside'] == 1, r_)
single = TMP / '单独的视频.mp4'
shutil.copy(MEDIA / '演示视频.mp4', single)
r_ = api.call('library_add_files', {'paths': [str(single)]})
check('添加单个文件：和文件夹一起按加入先后排在最上层', r_['added'] == 1 and r_['groups'][-1]['type'] == 'file' and r_['groups'][-1]['top'], r_['groups'][-1])
check('移除文件夹', [n['type'] for n in api.call('library_remove', {'kind': 'folder', 'path': str(MEDIA)})['groups']] == ['file'])
api.call('library_remove', {'kind': 'file', 'path': str(single)})
api.call('teacher_delete', {'id': t['id']})
check('删除老师', api.call('settings', {})['teachers'] == [])

# ChatGPT 登录（不真正登录）
login = codex_auth._Login()
check('ChatGPT 登录网址', 'code_challenge_method=S256' in login.url and 'client_id=app_' in login.url)
check('拒绝不匹配的回调', login.accept('http://localhost:1455/auth/callback?code=x&state=wrong') and login.error)
api.call('settings', {'settings': {'chat': 'codex', 'providers': {'codex': {'chat_model': 'x'}}}})
check('选了 ChatGPT 但没登录时提示', '还没有用 ChatGPT 账号登录' in api.call('chat', {'doc': {}})['error'])
api.call('settings', {'settings': {'translate': 'claude'}})
tt = run_steps(mp4, ['translate'], any_lang=True, translate_all=True)
check('Claude 没填密钥时提示', tt['status'] == 'failed' and '还没有填写Claude的 API Key' in stage(tt, 'translate').get('error', ''), tt)

# 缓存清理
from core import cache  # noqa: E402
import os  # noqa: E402,F811
(config.CACHE_DIR / 'audio').mkdir(parents=True, exist_ok=True)
old_wav = config.CACHE_DIR / 'audio' / 'old.wav'
old_wav.write_bytes(b'0' * 1000)
os.utime(old_wav, (time.time() - 5 * 86400, time.time() - 5 * 86400))
(config.CACHE_DIR / 'chunks-x').mkdir(exist_ok=True)
(config.CACHE_DIR / 'play').mkdir(parents=True, exist_ok=True)
old_play, new_play = config.CACHE_DIR / 'play' / 'old.mp4', config.CACHE_DIR / 'play' / 'new.mp4'
old_play.write_bytes(b'0' * 1000)
new_play.write_bytes(b'0' * 1000)
os.utime(old_play, (time.time() - 40 * 86400, time.time() - 40 * 86400))
cache.cleanup()
check('自动清理 3 天前的音频和残留片段', not old_wav.exists() and not (config.CACHE_DIR / 'chunks-x').exists())
check('转换后的视频 30 天没打开过就删，最近用过的留着', not old_play.exists() and new_play.exists())
check('清空缓存', api.call('cache_clear', {})['cache'] == 0)

# 设置导出 / 导入
api.call('settings', {'settings': {'providers': {'deepseek': {'key': 'sk-export-test-123456'}}, 'theme': 'purple'}})
api.call('teacher_save', {'teacher': {'name': '导出测试老师', 'terms': '峰级'}})
exp = api.call('export_settings', {'dest': str(TMP / 'exp.json'), 'keys': True})
data = json.loads(Path(exp['path']).read_text(encoding='utf-8'))
check('导出设置（含密钥和老师）', data['settings']['providers']['deepseek']['key'] == 'sk-export-test-123456' and data['teachers'][0]['name'] == '导出测试老师')
exp2 = api.call('export_settings', {'dest': str(TMP / 'exp2.json'), 'keys': False})
check('导出时可以不带密钥', json.loads(Path(exp2['path']).read_text(encoding='utf-8'))['settings']['providers']['deepseek']['key'] == '')
config.save(config._merge(config.DEFAULTS, {}))           # 模拟新电脑
for t_ in config.load_teachers():
    config.delete_teacher(t_['id'])
imp = api.call('import_settings', {'path': str(TMP / 'exp.json')})
s_ = config.load()
check('导入设置', imp['imported'] and s_['providers']['deepseek']['key'] == 'sk-export-test-123456' and s_['theme'] == 'purple' and config.load_teachers()[0]['name'] == '导出测试老师')
api.call('import_settings', {'path': str(TMP / 'exp2.json')})
check('导入不带密钥的文件时保留已有密钥', config.load()['providers']['deepseek']['key'] == 'sk-export-test-123456')
(TMP / 'bad.json').write_text('{"a": 1}', encoding='utf-8')
check('拒绝不是猛人学习的文件', '不是猛人学习导出的设置文件' in api.call('import_settings', {'path': str(TMP / 'bad.json')})['error'])

# 解压即用：软件文件夹里附带的设置，第一次启动时自动导入
from core import portable  # noqa: E402
bundle = TMP / '软件文件夹'
bundle.mkdir()
shutil.copy(TMP / 'exp.json', bundle / '猛人学习设置.json')
check('这台电脑已有设置时不自动导入', portable.import_bundled(api, bundle) is None and (bundle / '猛人学习设置.json').exists())
config.SETTINGS_FILE.unlink()
for t_ in config.load_teachers():
    config.delete_teacher(t_['id'])
rb = portable.import_bundled(api, bundle)
check('新电脑第一次启动：自动导入附带的设置', rb and rb['imported'] and config.load()['providers']['deepseek']['key'] == 'sk-export-test-123456'
      and config.load_teachers()[0]['name'] == '导出测试老师', rb)
check('导入后设置文件改名，提醒删除', not (bundle / '猛人学习设置.json').exists() and (bundle / '猛人学习设置（已导入，可删除）.json').exists())

# AI 校对：改动和存疑通过任务推给界面（1.6.1 曾因为变量写错，一校对就报错）
orig_check, orig_complete2 = llm.check, llm.complete
llm.check = lambda *a, **k: None
llm.complete = lambda s_, n_, msgs, **k: json.dumps({'改动': {'0': '甲改好了'}, '存疑': {'1': '听不清'}})
cdoc = MEDIA / '校对测试.mp4'
shutil.copy(MEDIA / '演示视频.mp4', cdoc)
docs.save(str(cdoc), dict(docs.new(str(cdoc), {'duration': 40}), segments=[{'start': 1.0, 'end': 2.0, 'text': '甲', 'tr': ''}, {'start': 2.0, 'end': 3.0, 'text': '乙', 'tr': ''}]))
cj = run_steps(cdoc, ['correct'], allow_no_teacher=True)
cd = docs.load(str(cdoc))
check('AI 校对任务正常完成，改动和存疑写进字幕文件', cj['status'] == 'completed' and cd['segments'][0]['text'] == '甲改好了'
      and cd['doubts'] == [{'t': 2.0, 'reason': '听不清', 'text': '乙'}] and cd['corrected'] and stage(cj, 'correct')['result'] == {'fixed': 1, 'doubts': 1}, (cj, cd))
llm.check, llm.complete = orig_check, orig_complete2

# 豆包流式识别：边发边收，并报告发送进度
import gzip, queue, struct, wave  # noqa: E402,E401
import websocket  # noqa: E402
from core import tasks  # noqa: E402


class FakeWS:
    def __init__(self):
        self.q, self.n = queue.Queue(), 0

    def send_binary(self, b):
        self.q.put(b[1] & 0x0F)

    def recv(self):
        last = bool(self.q.get(timeout=10) & 0x2)
        self.n += 1
        data = {'result': {'text': '你好世界。', 'utterances': [{'start_time': 0, 'end_time': 1000, 'text': '你好世界。'}]}} if last else {}
        body = gzip.compress(json.dumps(data).encode())
        return bytes([0x11, (0x9 << 4) | (0x3 if last else 0x1), 0x11, 0]) + struct.pack('>i', -self.n if last else self.n) + struct.pack('>I', len(body)) + body

    def close(self):
        pass


wav12 = TMP / 'stream.wav'
with wave.open(str(wav12), 'wb') as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b'\0\0' * 16000 * 12)
orig_conn = websocket.create_connection
websocket.create_connection = lambda *a, **k: FakeWS()
ss = config.load()
ss['providers']['doubao'].update(model='stream2', key='test-key', stream_speed=20)
seen_p = []
t0 = time.time()
st = asr.doubao_stream(ss, wav12, 0, 12, 'zh', threading.Event(), '', seen_p.append)
websocket.create_connection = orig_conn
check('流式识别拿到最终结果', st[0].startswith('你好世界') and st[1][0]['end'] == 1.0, st)
check('流式识别报告发送进度，最后到 100%', seen_p and seen_p[-1] == 1.0 and len(seen_p) == 60, (len(seen_p), seen_p[-1:]))
check('流式识别 20 倍速：12 秒音频不到 2 秒发完', time.time() - t0 < 2, time.time() - t0)

# 流式识别中途收到服务端 55 开头的错误：只重发出错的那一段（2026-10-15，实测发到 92% 时出过 55000000 remote or network error）
conns = []


class FlakyWS(FakeWS):
    def __init__(self, err):
        super().__init__()
        self.err = err
        conns.append(self)

    def recv(self):
        if self.err and self.n == 3:  # 发到一半出错
            self.n += 1
            body = gzip.compress(self.err[1].encode())
            return bytes([0x11, 0xF0, 0x11, 0]) + struct.pack('>II', self.err[0], len(body)) + body
        return super().recv()


HICCUP = (55000000, '{"error":"kiteX processing ... remote or network error[remote]: error_code=1204"}')
plan_ws = []
websocket.create_connection = lambda *a, **k: FlakyWS(plan_ws.pop(0) if plan_ws else None)
orig_wait = asr._retry_wait
asr._retry_wait = lambda *a, **k: 0.01
plan_ws[:] = [HICCUP]
a1 = asr.doubao_stream(ss, wav12, 0, 6, 'zh', threading.Event())
n1 = len(conns)
a2 = asr.doubao_stream(ss, wav12, 6, 12, 'zh', threading.Event())
check('流式识别：一段中途出 55 错误，只重发这一段（连了 2 次），下一段照常只连 1 次',
      a1[0].startswith('你好世界') and a2[0].startswith('你好世界') and n1 == 2 and len(conns) == 3, (n1, len(conns)))
conns.clear()
plan_ws[:] = [(55000001, 'account balance insufficient')]
try:
    asr.doubao_stream(ss, wav12, 0, 6, 'zh', threading.Event()); e_ = None
except asr.AsrError as e:
    e_ = e
check('流式识别：55 错误里说的是额度用完，马上停、不重发', isinstance(e_, asr.QuotaError) and len(conns) == 1
      and tasks.classify(e_)[:2] == ('quota', False), (e_, len(conns)))
conns.clear()
plan_ws[:] = [HICCUP] * (asr.RETRIES + 1)
try:
    asr.doubao_stream(ss, wav12, 0, 6, 'zh', threading.Event()); e_ = None
except asr.AsrError as e:
    e_ = e
check('流式识别：一直出 55 错误，重发几次后放弃，报成可以重试的错误', e_ is not None and len(conns) == asr.RETRIES + 1
      and '55000000' in str(e_) and tasks.classify(e_)[:2] == ('overloaded', True), (e_, len(conns)))
conns.clear()
plan_ws[:] = [(45000001, 'invalid audio format')]
try:
    asr.doubao_stream(ss, wav12, 0, 6, 'zh', threading.Event()); e_ = None
except asr.AsrError as e:
    e_ = e
check('流式识别：别的错误（45 开头）不重发', e_ is not None and len(conns) == 1 and not isinstance(e_, asr.QuotaError), (e_, len(conns)))
asr._retry_wait = orig_wait
websocket.create_connection = orig_conn

# ChatGPT 回答到一半在数据流里报的错：太忙可以重试，用量限额马上停
busy = llm._codex_error({'type': 'response.failed', 'response': {'error': {'code': 'server_is_overloaded',
                         'message': 'Our servers are currently overloaded. Please try again later.'}}})
limit = llm._codex_error({'type': 'error', 'error': {'type': 'usage_limit_reached', 'message': 'The usage limit has been reached'}})
check('ChatGPT 数据流里的「太忙」：可以重试', str(busy).startswith('ChatGPT 出错') and tasks.classify(busy)[:2] == ('overloaded', True), busy)
check('ChatGPT 数据流里的用量限额：认成额度不足，不重试', str(limit).startswith('ChatGPT 额度不足') and tasks.classify(limit)[:2] == ('quota', False), limit)

# 豆包：只留四种模型，几个账号 × 每种模型按顺序自动换（2026-10-14；1.0 标准版 2026-10-15 加回）
check('豆包只留四种模型，顺序是流式 2.0 → 极速版 1.0 → 录音文件 1.0 → 录音文件 2.0',
      list(asr.DOUBAO_MODELS) == ['stream2', 'flash', 'std1', 'std2'] and asr.DOUBAO_ORDER == ('stream2', 'flash', 'std1', 'std2')
      and asr.DOUBAO_MODELS['std1']['resource'] == 'volc.bigasr.auc' and asr.DOUBAO_MODELS['std1']['kind'] == 'task')
dp = {'key': 'main-key-123456', 'name': '', 'more_keys': [{'id': 'b1', 'name': '小号', 'key': 'second-key-123456'},
                                                          {'id': 'b2', 'name': '', 'key': ''}], 'spent': {'main|stream2': '2026-10-14'}}
check('账号：主账号叫「账号 1」，没填密钥的备用账号不算', [(a['id'], a['name']) for a in asr.doubao_accounts(dp)] == [('main', '账号 1'), ('b1', '小号')])
check('顺序：每种模型先把所有账号用完再换下一种，跳过用完的',
      [(a['id'], m) for a, m in asr.doubao_plan(dp)] == [('b1', 'stream2'), ('main', 'flash'), ('b1', 'flash'), ('main', 'std1'), ('b1', 'std1'), ('main', 'std2'), ('b1', 'std2')])
check('用户选定一种模型：只用这一种（账号照样换），不换别的模型；旧设置里的 model 字段不算选定',
      [(a['id'], m) for a, m in asr.doubao_plan(dict(dp, pick='std1'))] == [('main', 'std1'), ('b1', 'std1')]
      and [(a['id'], m) for a, m in asr.doubao_plan(dict(dp, pick='stream2'))] == [('b1', 'stream2')]
      and len(asr.doubao_plan(dict(dp, model='stream2'))) == 7 and len(asr.doubao_plan(dict(dp, pick='nosuch'))) == 7)
try:
    asr.recognise('x.mp4', 10, 'doubao', 'zh', {'providers': {'doubao': dict(dp, pick='stream2', more_keys=[])}}, None, lambda *a, **k: None, lambda c: None)
    e_ = ''
except asr.QuotaError as e:
    e_ = str(e)
check('用户选定一个账号：只用这个账号（模型照样按顺序换）；账号删了就当自动',
      [(a['id'], m) for a, m in asr.doubao_plan(dict(dp, pick_account='b1'))] == [('b1', 'stream2'), ('b1', 'flash'), ('b1', 'std1'), ('b1', 'std2')]
      and [(a['id'], m) for a, m in asr.doubao_plan(dict(dp, pick_account='main', pick='flash'))] == [('main', 'flash')]
      and len(asr.doubao_plan(dict(dp, pick_account='gone'))) == 7)
check('账号可以用名字、第几个、id 来指定', asr.doubao_account_id(dp, '小号') == 'b1' and asr.doubao_account_id(dp, '2') == 'b1'
      and asr.doubao_account_id(dp, '1') == 'main' and asr.doubao_account_id(dp, 'auto') == '' and asr.doubao_account_id(dp, '没有这个') is None)
try:
    asr.recognise('x.mp4', 10, 'doubao', 'zh', {'providers': {'doubao': dict(dp, pick_account='main', pick='stream2')}}, None, lambda *a, **k: None, lambda c: None)
    e2_ = ''
except asr.QuotaError as e:
    e2_ = str(e)
check('选定的账号用完了：直接报错，写明是哪个账号，不换别的账号', '账号 1' in e2_ and '换一个账号' in e2_, e2_)
check('选定的模型在所有账号上都用完了：直接报错，写明是哪一种，不换别的', '流式识别 2.0' in e_ and '换一种模型' in e_, e_)
try:
    asr.check_ready({'providers': {'doubao': {'more_keys': [{'id': 'x', 'key': 'k'}]}}}, 'doubao'); e_ = ''
except asr.AsrError as e:
    e_ = str(e)
check('只填了备用账号也能识别', e_ == '', e_)

# 换账号 / 模型：额度用完记下来、换下一个、前面收到的字幕作废；没开通跳过
config.update({'providers': {'doubao': {'key': 'main-key-123456', 'more_keys': [{'id': 'b1', 'name': '小号', 'key': 'second-key-123456'}], 'spent': {}}}})
orig_once = asr._recognise_once
tried, got, prog = [], [], []


def fake_once(path, duration, provider, lang, s, cancel, progress, on_cues, teacher=None, title=''):
    d = s['providers']['doubao']
    tried.append((d['key'][:4], d['model']))
    on_cues([{'start': 0, 'end': 1, 'text': '半截'}])
    if (d['key'][:4], d['model']) == ('main', 'stream2'):
        raise asr.QuotaError('豆包额度不足或账户欠费：quota exceeded')
    if (d['key'][:4], d['model']) == ('seco', 'stream2'):
        raise asr.AuthError('豆包鉴权失败：没开通')
    return [{'start': 0, 'end': 1, 'text': '好了'}]


asr._recognise_once = fake_once
ss = config.load()
cues = asr.recognise('x.mp4', 10, 'doubao', 'zh', ss, threading.Event(), lambda f, m, extra=None: prog.append((m, extra)), got.append)
check('额度用完和没开通的都换下一个：主账号流式 → 小号流式 → 主账号极速版',
      tried == [('main', 'stream2'), ('seco', 'stream2'), ('main', 'flash')] and cues[0]['text'] == '好了', tried)
check('换之前通知前面收到的字幕作废', got.count(None) == 2 and got[-1][0]['text'] == '半截', got)
check('识别完告诉调用方用的是哪个、中途换过什么', prog[-1][1] == {'used': '账号 1 · 极速版 1.0', 'notes': ['账号 1 · 流式识别 2.0 的额度用完了', '小号 · 流式识别 2.0 用不了（没开通这种模型，或密钥不对）']}, prog[-1])
check('换的时候进度里说清楚', any('换成 小号 · 流式识别 2.0 重新识别' in (m or '') for m, _ in prog), prog)
spent = config.load()['providers']['doubao']['spent']
check('额度用完的记进设置（没开通的不记）', list(spent) == ['main|stream2'], spent)
tried.clear()
asr.recognise('x.mp4', 10, 'doubao', 'zh', config.load(), threading.Event(), lambda *a: None, lambda c: None)
check('下次直接跳过用完的', tried[0] == ('seco', 'stream2'), tried)

asr._recognise_once = lambda *a, **k: (_ for _ in ()).throw(asr.QuotaError('豆包额度不足或账户欠费：x'))
try:
    asr.recognise('x.mp4', 10, 'doubao', 'zh', config.load(), threading.Event(), lambda *a: None, lambda c: None); e_ = ''
except asr.QuotaError as e:
    e_ = str(e)
check('全都用完：报额度不足，说清楚怎么重新启用', e_.startswith('豆包额度不足') and '重新启用' in e_, e_)
from core import tasks  # noqa: E402
check('全都用完的错误归为额度问题，不自动重试', tasks.classify(asr.QuotaError(e_))[:2] == ('quota', False))
try:
    asr.recognise('x.mp4', 10, 'doubao', 'zh', config.load(), threading.Event(), lambda *a: None, lambda c: None); e_ = ''
except asr.QuotaError as e:
    e_ = str(e)
check('都标记成用完以后，不再去试', '都标记成用完了' in e_, e_)
asr._recognise_once = orig_once

# 设置：备用账号的密钥打码、保存时不冲掉、换了密钥清掉「用完了」
pub = config.public(config.load())['providers']['doubao']
check('给界面看的备用账号密钥只显示后四位', pub['more_keys'][0]['key'] == '••••3456' and pub['has_key'], pub['more_keys'])
config.update({'providers': {'doubao': {'more_keys': [{'id': 'b1', 'name': '小号改名', 'key': '••••3456'}, {'name': '新号', 'key': 'third-key-123456'}]}}})
mk = config.load()['providers']['doubao']['more_keys']
check('密钥是掩码表示不改；新加的账号自动给一个编号', mk[0] == {'id': 'b1', 'name': '小号改名', 'key': 'second-key-123456'}
      and mk[1]['name'] == '新号' and mk[1]['key'] == 'third-key-123456' and mk[1]['id'], mk)
check('只改名字不清「用完了」', 'main|stream2' in config.load()['providers']['doubao']['spent'])
config.update({'providers': {'doubao': {'spent': {'main|stream2': '2026-10-14', 'b1|flash': '2026-10-14'}}}})
config.update({'providers': {'doubao': {'more_keys': [{'id': 'b1', 'name': '小号', 'key': 'changed-key-123456'}]}}})
check('换了密钥（等于换了账号）、删掉的账号：以前的「用完了」作废', config.load()['providers']['doubao']['spent'] == {'main|stream2': '2026-10-14'}
      and len(config.load()['providers']['doubao']['more_keys']) == 1, config.load()['providers']['doubao'])
config.update({'providers': {'doubao': {'key': 'new-main-key-123456'}}})
check('主账号换了密钥，它的「用完了」也作废', config.load()['providers']['doubao']['spent'] == {})
exp3 = api.call('export_settings', {'dest': str(TMP / 'exp3.json'), 'keys': False})
d3 = json.loads(Path(exp3['path']).read_text(encoding='utf-8'))['settings']['providers']['doubao']
check('导出设置（不含密钥）：备用账号的密钥也清掉', d3['more_keys'][0]['key'] == '' and d3['key'] == '' and 'tos_sk' not in d3, d3)
api.call('import_settings', {'path': exp3['path']})
check('导入不含密钥的设置：保留这台电脑上的备用账号密钥', config.load()['providers']['doubao']['more_keys'][0]['key'] == 'changed-key-123456')

# 录音文件 2.0：直接发 base64 音频，不用对象存储；403 但说的是额度时算额度用完
import requests as _rq  # noqa: E402
sent_body, calls_, res_ids = {}, [], []


class FakeResp:
    def __init__(self, status, code, msg='', data=None):
        self.status_code, self.headers, self._d, self.text = status, {'X-Api-Status-Code': code, 'X-Api-Message': msg}, data or {}, msg

    def json(self):
        return self._d


def fake_post(url, json=None, headers=None, **k):
    calls_.append(url.rsplit('/', 1)[-1])
    res_ids.append((headers or {}).get('X-Api-Resource-Id'))
    if url.endswith('submit'):
        sent_body.update(json)
        return FakeResp(200, '20000000')
    if len(calls_) < 3:
        return FakeResp(200, '20000001')
    return FakeResp(200, '20000000', data={'result': {'text': '你好。', 'utterances': [{'start_time': 0, 'end_time': 900, 'text': '你好。'}]}})


orig_post, orig_sleep = _rq.post, time.sleep
_rq.post, asr.time.sleep = fake_post, lambda x: None
mp3 = TMP / 'std2.mp3'
mp3.write_bytes(b'ID3fake')
ts = asr.with_account(config.load(), {'key': 'k', 'app_id': '', 'access_token': ''}, 'std2')
txt, segs = asr.doubao_task(ts, mp3, 'zh', threading.Event())
check('录音文件 2.0：直接发音频（base64），提交后查询到结果', sent_body['audio'].get('data') and 'url' not in sent_body['audio']
      and calls_ == ['submit', 'query', 'query'] and txt == '你好。', (calls_, list(sent_body.get('audio', {}))))
_rq.post = lambda *a, **k: FakeResp(403, '45000000', 'resource quota exhausted')
try:
    asr.doubao_task(ts, mp3, 'zh', threading.Event()); e_ = None
except asr.AsrError as e:
    e_ = e
check('403 但说的是额度用完：算额度用完（换下一个、记下来）', isinstance(e_, asr.QuotaError), e_)
_rq.post = lambda *a, **k: FakeResp(401, '45000000', 'invalid api key')
try:
    asr.doubao_task(ts, mp3, 'zh', threading.Event()); e_ = None
except asr.AsrError as e:
    e_ = e
check('401 密钥不对：算鉴权失败', isinstance(e_, asr.AuthError) and '录音文件识别 2.0' in str(e_), e_)
calls_.clear()
_rq.post = fake_post
ts1 = asr.with_account(config.load(), {'key': 'k', 'app_id': '', 'access_token': ''}, 'std1')
txt1, _ = asr.doubao_task(ts1, mp3, 'zh', threading.Event())
check('录音文件 1.0 标准版：同样的提交 + 查询，用 1.0 的资源编号', txt1 == '你好。' and calls_ == ['submit', 'query', 'query']
      and res_ids[-1] == 'volc.bigasr.auc', (calls_, res_ids[-1:]))
_rq.post, asr.time.sleep = orig_post, orig_sleep
mp3.unlink()

# 选择文件对话框的筛选写法：pywebview 只认「文字和空格 (*.扩展名;…)」，说明里有「/」等符号会直接报错、打不开对话框
# （2026-10-15 关联术语表文件时出过「Markdown / 文本 (*.md;*.txt) is not a valid file filter」）
import ast, re  # noqa: E402,E401
from webview.util import parse_file_type  # noqa: E402
bad = []
for node in ast.walk(ast.parse((ROOT / 'app' / 'bridge.py').read_text(encoding='utf-8'))):
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.search(r'\(\*\.[^)]*\)$', node.value):
        try:
            parse_file_type(node.value)
        except ValueError:
            bad.append(node.value)
check('选择文件对话框的筛选写法都是对的', not bad, bad)

# 老师工作台（core/mentor.py）：导入蒸馏好的老师文件夹，按老师画像对话，收藏到 Obsidian
from core import jobs, llm as _llm, mentor  # noqa: E402
KB = TMP / 'kb'
(KB / '.obsidian').mkdir(parents=True)
(KB / '03、收集箱').mkdir()
(KB / '03、收集箱' / '演示课.md').write_text(f'---\n视频文件: "{(MEDIA / "演示视频.mp4").as_posix()}"\n人工校对: false\n---\n\n# 演示课\n', encoding='utf-8')
TEACHER = KB / '04、外部知识转化库' / '测试老师'
TEACHER.mkdir(parents=True)
(TEACHER / '01、观点库（逐节提取）.md').write_text(
    '# 测试老师：观点库\n\n## 来源\n\n| 编号 | 课程 | 笔记 |\n| --- | --- | --- |\n| S01 | 演示课 | [[演示课]] |\n| S02 | 不存在的课 | [[没有这篇笔记]] |\n\n'
    '### 观点\n\n| 编号 | 观点 | 条件 / 边界 | 出处 | 类型 |\n| --- | --- | --- | --- | --- |\n'
    '| S01-01 | 峰级的入门很重要，要先学会画峰级线。 | — | S01 00:02–00:04 | 观点 |\n'
    '| S01-02 | 止损要设在结构外面。 | 只适用于日内 | S01 00:06 | 方法 |\n\n'
    '### 老师自述\n\n| 编号 | 内容 | 出处 |\n| --- | --- | --- |\n| S01-A1 | 自己偏做日内。 | S01 00:00 |\n', encoding='utf-8')
(TEACHER / '03、老师画像（对话用）.md').write_text('# 测试老师画像\n\n## 一、规则\n出处写成 ［S01 00:02］。\n', encoding='utf-8')
mt = mentor.add(str(TEACHER))
check('老师工作台：导入老师文件夹，按来源表找到课程笔记里的视频（找不到的标出来）', mt['name'] == '测试老师'
      and [(s['sid'], s['found']) for s in mt['sources']] == [('S01', True), ('S02', False)], mt['sources'])
try:
    mentor.add(str(TEACHER))
    check('同一位老师不能重复导入', False)
except mentor.MentorError:
    check('同一位老师不能重复导入', True)
ov = mentor.overview(mt['id'])
check('读出观点库每一条（编号、出处时间），老师自述也算', ov['atoms']['S01-01']['t'] == 2 and ov['atoms']['S01-02']['src'] == 'S01 00:06'
      and 'S01-A1' in ov['atoms'], ov['atoms'])
msgs, used = mentor.build_messages(mentor.get(mt['id']), [], '峰级线怎么画')
sysmsg = msgs[0]['content']
check('交给 AI 的资料：老师画像 + 观点库 + 相关的课程原文 + 出处规则', '# 测试老师画像' in sysmsg and '[S01-01]' in sysmsg
      and '# 和这个问题相关的课程原文' in sysmsg and '只能引用上面给出的编号和时间' in sysmsg and msgs[-1]['content'] == '峰级线怎么画'
      and used['atoms'] == 3, used)
check('挑相关的：字词重合多的排前面', mentor.rank('止损设在哪', [{'t': '峰级线的画法'}, {'t': '止损要设在结构外面'}], lambda x: x['t'], 1)[0]['t'] == '止损要设在结构外面')
check('出处那一秒前后的原话（鼠标停在出处上显示）', mentor.quote(mt['id'], 'S01', 2)['text'].startswith('[00:'))
_old_stream, _old_check = _llm.stream, _llm.check
_llm.stream = lambda s, n, m, cancel=None, json_mode=False: iter(['峰级线要先找高低点', '［S01 00:02］。'])
_llm.check = lambda *a, **k: None
c = api.call('mentor_chat', {'id': mt['id']})['chat']
r = api.call('mentor_ask', {'id': mt['id'], 'cid': c['id'], 'question': '峰级线怎么画？'})
for _ in range(100):
    j = jobs.get(r['job'])
    if j.status != 'running':
        break
    time.sleep(0.05)
_llm.stream, _llm.check = _old_stream, _old_check
c = mentor.load_chat(mt['id'], c['id'])
check('提问：问答都存进对话记录，第一句问题当标题，记下用的模型', r['ok'] and [m['role'] for m in c['messages']] == ['user', 'assistant']
      and c['messages'][1]['content'] == '峰级线要先找高低点［S01 00:02］。' and c['title'] == '峰级线怎么画？' and c['messages'][1].get('model'), c)
check('搜索对话：按内容也能找到', [x['id'] for x in mentor.search(mt['id'], '高低点')] == [c['id']] and mentor.search(mt['id'], '没有这句') == [])
sv = api.call('mentor_save', {'id': mt['id'], 'cid': c['id'], 'index': 1})
fav = (TEACHER / '对话精选.md').read_text(encoding='utf-8')
check('收藏：问答追加到老师文件夹的「对话精选.md」，标记已收藏', sv['ok'] and '峰级线怎么画？' in fav and '峰级线要先找高低点' in fav
      and sv['chat']['messages'][1].get('saved'), fav[:200])
api.call('mentor_save', {'id': mt['id'], 'cid': c['id'], 'index': 1})
check('再收藏前先备份原来的对话精选（最多留 30 份）', len(list((config.DATA_DIR / 'backups' / '对话精选').glob('测试老师-*.md'))) == 1
      and (TEACHER / '对话精选.md').read_text(encoding='utf-8').count('**问：**') == 2)
check('只能收藏老师的回答', not api.call('mentor_save', {'id': mt['id'], 'cid': c['id'], 'index': 0})['ok'])
c = mentor.append(mt['id'], c['id'], {'role': 'user', 'content': '第二问'}, {'role': 'assistant', 'content': '第二答'},
                  {'role': 'user', 'content': '第三问'}, {'role': 'assistant', 'content': '第三答'})
sa = api.call('mentor_save_all', {'id': mt['id'], 'cid': c['id']})
fav = (TEACHER / '对话精选.md').read_text(encoding='utf-8')
check('一键收藏整个对话：没收藏的一次写进去，已经收藏的不重复', sa['ok'] and sa['count'] == 2 and fav.count('**问：**') == 4
      and fav.index('第二问') < fav.index('第三问') and all(x.get('saved') for x in sa['chat']['messages'] if x['role'] == 'assistant'), (sa, fav[-300:]))
check('一键收藏：都收藏过了就什么都不写', api.call('mentor_save_all', {'id': mt['id'], 'cid': c['id']})['count'] == 0
      and (TEACHER / '对话精选.md').read_text(encoding='utf-8').count('**问：**') == 4)
fv = api.call('mentor_favorites', {'id': mt['id']})
check('打开对话精选：给出在 Obsidian 里打开的链接', fv['exists'] and fv['uri'].startswith('obsidian://open?vault=kb&file=')
      and '%E5%AF%B9%E8%AF%9D%E7%B2%BE%E9%80%89.md' in fv['uri'], fv)
(KB / '03、收集箱' / '深层' / '更深').mkdir(parents=True)
(KB / '03、收集箱' / '深层' / '更深' / '另一课.md').write_text('# x\n', encoding='utf-8')
_walks = []
_old_walk = mentor.os.walk
mentor.os.walk = lambda top, *a, **k: (_walks.append(top), _old_walk(top, *a, **k))[1]
found = mentor._find_notes(str(KB), ['演示课', '另一课', '没有这篇'])
mentor.os.walk = _old_walk
check('导入老师更快：整个库只翻一遍，一次找齐几篇笔记', set(found) == {'演示课', '另一课'} and len(_walks) == 1, (found, _walks))
api.call('mentor_chat_rename', {'id': mt['id'], 'cid': c['id'], 'title': '画线'})
check('对话改名', mentor.list_chats(mt['id'])[0]['title'] == '画线')
api.call('mentor_chat_delete', {'id': mt['id'], 'cid': c['id']})
check('删除对话', mentor.list_chats(mt['id']) == [])
api.call('mentor_remove', {'id': mt['id']})
check('移除老师：软件里的记录删掉，Obsidian 里的文件不动', api.call('mentor_list', {})['mentors'] == []
      and (TEACHER / '01、观点库（逐节提取）.md').is_file() and (TEACHER / '对话精选.md').is_file())

# 老师蒸馏（core/distill.py）：从媒体库的课自动生成观点库、课程蒸馏、老师画像（假 AI）
from core import distill, fake_ai, library, workflow  # noqa: E402
config.update({'obsidian': dict(config.load().get('obsidian') or {}, vault=str(KB))})
DS = TMP / 'distill-media'
DS.mkdir()
for n in ('第一课', '第二课'):
    shutil.copy(ROOT / 'test-media' / '演示视频.mp4', DS / f'{n}.mp4')
    d_ = docs.load(str(MEDIA / '演示视频.mp4'))
    d_['title'] = n
    docs.save(str(DS / f'{n}.mp4'), d_)
library.add_folder(str(DS))
check('蒸馏：测试用的课在媒体库里', workflow.allowed(str(DS / '第一课.mp4')))
try:
    distill.create('蒸馏老师', [str(MEDIA / '旧格式.avi')])
    e_ = ''
except distill.DistillError as e:
    e_ = str(e)
check('蒸馏：没有字幕的课不能选', '还没有字幕' in e_ or '不在媒体库' in e_, e_)
_old_stream, _old_check = _llm.stream, _llm.check
_llm.stream, _llm.check = fake_ai.stream, (lambda *a, **k: None)
dm = api.call('distill_create', {'name': '蒸馏老师', 'paths': [str(DS / '第一课.mp4')]})['mentor']
DT = KB / '04、外部知识转化库' / '01、老师分身蒸馏资料' / '蒸馏老师'
check('蒸馏：新建老师只记下用哪些课，结果写到「04、外部知识转化库/01、老师分身蒸馏资料/老师名」', dm['folder'] == str(DT) and dm.get('distilled')
      and [x['state'] for x in distill.status(dm['id'])['lessons']] == ['todo'])
res = distill.run(dm['id'], jobs.Job('distill'))
A = (DT / '01、观点库（逐节提取）.md').read_text(encoding='utf-8')
P = (DT / '03、老师画像（对话用）.md').read_text(encoding='utf-8')
D = (DT / '02、课程蒸馏.md').read_text(encoding='utf-8')
check('蒸馏：观点库和人间落叶的格式一样（程序编号、出处带课程编号、说得很满标 ⚠️、自述 A1、存疑）',
      '| S01 | 第一课 | （还没剪藏到 Obsidian：第一课.mp4） |' in A and '| S01-01 | 风险要先算好（假 AI）。 | — | S01 00:02–00:05 | 观点 |' in A
      and '| S01-02 | 「截断亏损」。 | 未讲明 | S01 00:10 | 方法 ⚠️ |' in A and '| S01-A1 | 我是假老师。 | S01 00:01 |' in A
      and '- 先算风险（S01-01）' in A and '- S01 00:08 假 AI 标的存疑' in A, A[:1500])
_rv = bool(docs.load(str(DS / '第一课.mp4')).get('reviewed'))
check('蒸馏：三份文件都注明来源有几节人工校对过，没校对的在观点库开头说明（蒸馏规范 V2.0 第四节）',
      all(f'来源人工校对: {int(_rv)}/1 节' in t for t in (A, P, D)) and (('还没人工校对' in A) != _rv), A[:700])
check('蒸馏：老师画像第一节是程序固定写的规则，后面是 AI 写的', '## 一、你的身份和规则（给 AI）' in P and '「**蒸馏老师**」的数字分身' in P
      and '1 节课（S01～S01' in P and P.index('## 一、') < P.index('## 二、他是谁'), P[:800])
check('蒸馏：课程蒸馏有说明和 AI 合并的正文', D.startswith('---\n老师: 蒸馏老师\n类型: 课程蒸馏') and '## 一、一句话' in D and '程序检查' not in D, D[:600])
check('蒸馏：做完能直接在老师工作台对话（没剪藏的课也能对上视频）', res['points'] == 2 and mentor.get(dm['id'])['distill_done']
      and [(s['sid'], s['found']) for s in mentor.get(dm['id'])['sources']] == [('S01', True)]
      and mentor.build_messages(mentor.get(dm['id']), [], '风险')[1]['atoms'] == 3, mentor.get(dm['id'])['sources'])
calls = []
_llm.stream = lambda s, n, m, cancel=None, json_mode=False: (calls.append(m[0]['content']), fake_ai.stream(s, n, m, cancel, json_mode))[1]
api.call('distill_add', {'id': dm['id'], 'paths': [str(DS / '第一课.mp4'), str(DS / '第二课.mp4')]})
st_ = distill.status(dm['id'])
check('蒸馏：加新课，已经在的不重复加，新课接着编号', [(x['sid'], x['state']) for x in st_['lessons']] == [('S01', 'done'), ('S02', 'todo')], st_['lessons'])
res = distill.run(dm['id'], jobs.Job('distill'))
check('蒸馏：更新时只提取新课，再重新合并、写画像', calls.count(distill.EXTRACT_SYSTEM) == 1 and distill.MERGE_SYSTEM in calls
      and distill.PERSONA_SYSTEM in calls and res['extracted'] == 1 and '| S02-01 |' in (DT / '01、观点库（逐节提取）.md').read_text(encoding='utf-8'), (res, len(calls)))
check('蒸馏：覆盖前把原来的三份文件备份', len(list((config.DATA_DIR / 'backups' / '蒸馏').glob('蒸馏老师-*'))) == 1)
calls.clear()
distill.run(dm['id'], jobs.Job('distill'))
check('蒸馏：什么都没变时再蒸馏，不再调用 AI（结果存着）', calls == [], len(calls))
(DT / '02、课程蒸馏.md').write_text(D + '\n我在 Obsidian 里加的话\n', encoding='utf-8')
r_ = api.call('distill_start', {'id': dm['id']})
check('蒸馏：在 Obsidian 里改过要覆盖的文件，先问用户', r_['ok'] and r_.get('conflict') == ['02、课程蒸馏.md'], r_)
d2 = docs.load(str(DS / '第二课.mp4'))
d2['segments'][0]['text'] = '改过的字幕'
docs.save(str(DS / '第二课.mp4'), d2)
check('蒸馏：字幕改过的课标「改过」，再蒸馏时重新提取', [x['state'] for x in distill.status(dm['id'])['lessons']] == ['done', 'changed'])
_old_limit, distill.MERGE_LIMIT, distill.GROUP_CHARS = distill.MERGE_LIMIT, 10, 10
calls.clear()
distill.run(dm['id'], jobs.Job('distill'))
distill.MERGE_LIMIT, distill.GROUP_CHARS = _old_limit, 60000
check('蒸馏：观点库太大（几十节的大课）先分组小结再合并', calls.count(distill.GROUP_SYSTEM) == 2 and calls.count(distill.EXTRACT_SYSTEM) == 1, calls)
check('蒸馏：查出 AI 写的、观点库里没有的编号', distill.bad_refs('见（S01-01、02、09）和 S02-A1、S07-03、2024 年', mentor.parse_atoms(A + '| S02-A1 | x | S02 00:01 |')) == ['S01-09', 'S07-03'])
check('蒸馏：出处时间超出课程长度的标「时间待核对」', distill._fix_src('S03', '99:00', 600) == ('S03 99:00（时间待核对）', False)
      and distill._fix_src('S03', 'S03 01:00-02:00', 600) == ('S03 01:00–02:00', True))
_llm.stream, _llm.check = _old_stream, _old_check
api.call('mentor_remove', {'id': dm['id']})
OLD = KB / '04、外部知识转化库' / '搬家老师'
shutil.copytree(TEACHER, OLD)
mv = mentor.add(str(OLD))
shutil.move(str(OLD), str(KB / '04、外部知识转化库' / '01、老师分身蒸馏资料' / '搬家老师'))
check('老师文件夹挪进「01、老师分身蒸馏资料」后，软件自己找到新位置、改好记录',
      mentor.get(mv['id'])['folder'] == str(KB / '04、外部知识转化库' / '01、老师分身蒸馏资料' / '搬家老师')
      and mentor.overview(mv['id'])['files']['atoms'].endswith('01、观点库（逐节提取）.md'), mentor.get(mv['id'])['folder'])
mentor.remove(mv['id'])

# 新老师的第一节课：找专用术语 → 填老师名字 → 建老师档案和 Obsidian 术语表、绑定文件夹（core/glossary.py create）
from core import glossary  # noqa: E402
L1 = str(DS / '第一课.mp4')
pl = glossary.plan(L1)
check('建术语表之前：告诉界面建在知识库哪里、能绑定哪个文件夹（默认加进媒体库的那个）',
      pl['folder'].endswith('02、规则体系\\02、课程专用规则') and pl['binds'][-1]['path'] == str(DS) and pl['binds'][-1]['top'], pl)
d_ = docs.load(L1)
check('没选老师时：命令行照旧不找新术语，界面上（建术语表）照样找',
      workflow.why_skip('terms', d_, workflow.Facts(config.load(), teacher=None)) is not None
      and workflow.why_skip('terms', d_, workflow.Facts(config.load(), opts={'new_glossary': True}, teacher=None), force=True) is None)
gc = api.call('glossary_create', {'name': '喜悦老师', 'bind': str(DS), 'title': '第一课',
                                  'terms': [{'term': '觉知', 't': 3, 'quote': '保持觉知'}], 'errors': [{'wrong': '绝知', 'right': '觉知', 't': 5, 'quote': '绝知'}]})
GF = KB / '02、规则体系' / '02、课程专用规则' / '喜悦老师课程术语表.md'
gtxt = GF.read_text(encoding='utf-8') if GF.is_file() else ''
gt = config.get_teacher(gc.get('teacher'))
check('建术语表：新老师档案关联知识库里新建的术语表（结构照峰级：怎么用 / 术语 / 识别错误对照 / 逐课补充），勾选的写进逐课补充',
      gc['ok'] and gc['created'] and gc['file_created'] and gc['file'] == str(GF) and '# 一、怎么用' in gtxt and '# 三、识别错误对照' in gtxt
      and '# 四、逐课补充（猛人学习）' in gtxt and '| 觉知 | 《第一课》' in gtxt and '| 绝知 | 觉知 |' in gtxt, (gc, gtxt[-600:]))
check('建术语表：软件读得出新术语表（觉知当热词，绝知 → 觉知 作校对参考），这个文件夹里的课以后都用这位老师',
      gt and '觉知' in gt['terms'] and '绝知 => 觉知' in gt['terms'] and library.folder_teacher(str(DS / '第二课.mp4')) == gc['teacher'], gt and gt['terms'])
gc2 = api.call('glossary_create', {'name': '喜悦老师', 'title': '第二课', 'terms': [{'term': '臣服', 't': 1, 'quote': '臣服'}], 'errors': []})
check('再填同一个老师名字：加进他原来的术语表，不另建', gc2['ok'] and not gc2['created'] and not gc2['file_created'] and gc2['teacher'] == gc['teacher']
      and '| 臣服 | 《第二课》' in GF.read_text(encoding='utf-8') and '| 觉知 |' in GF.read_text(encoding='utf-8'))

# 术语表去重（2026-10-28）：写之前看整份文件；打开对话框前再筛；「查看」报告重复在第几行；读的时候重复的只算一次
DT_TEXT = glossary.template('去重老师').replace('| 术语 | 含义 |\n| --- | --- |\n', '| 术语 | 含义 |\n| --- | --- |\n| 觉知 | 作者整理的 |\n') \
    .replace('| ASR常见识别 | 正确术语 | 说明 |\n| --- | --- | --- |\n', '| ASR常见识别 | 正确术语 | 说明 |\n| --- | --- | --- |\n| 绝知 | 觉知 | |\n') \
    .replace('| 直接替换的错写 | 正确术语 |\n| --- | --- |\n', '| 直接替换的错写 | 正确术语 |\n| --- | --- |\n| 绝知 | 觉知 |\n')
new_, nt_, ne_ = glossary.merge_markdown(DT_TEXT, [{'term': '觉知', 't': 1}, {'term': ' 新词 ', 't': 2}], [{'wrong': '绝知', 'right': '觉知'}, {'wrong': '新错', 'right': '新词'}], '第三课')
sec4_ = new_[new_.index('# 四、逐课补充（猛人学习）'):]
check('去重：作者整理在「二、术语」「识别错误对照」里的，不再重复加进逐课补充', nt_ == 1 and ne_ == 1 and '| 觉知 |' not in sec4_ and '| 绝知 |' not in sec4_
      and '| 新词 | 《第三课》' in sec4_ and '| 新错 | 新词 |' in sec4_, sec4_)
w_, h_, f_ = _llm.parse_terms('绝知 => 觉知\n绝知 =>! 觉知\n觉知, 觉知')
check('去重：同一条在参考对照和直接替换里都有，读的时候只算直接替换', h_ == [] and f_ == [('绝知', '觉知')] and w_ == ['觉知'], (w_, h_, f_))
DUP = TMP / '去重术语表.md'
DUP.write_text(new_.replace('| 新词 | 《第三课》', '| 觉知 | 又写了一遍 |  |\n| 新词 | 《第三课》'), encoding='utf-8')
ck_ = glossary.check_file(str(DUP))
check('查看术语表：报告重复的术语和识别错误在第几行（只报告，不改文件）', [d['term'] for d in ck_['dup_terms']] == ['觉知']
      and len(ck_['dup_terms'][0]['where']) == 2 and any(d['wrong'] == '绝知' and len(d['where']) == 2 and '直接替换' in d['where'][1] for d in ck_['dup_errors'])
      and '新词' in ck_['words'], ck_['dup_terms'] + ck_['dup_errors'])
ft_ = config.save_teacher({'name': '筛选老师', 'terms': '觉知\n绝知 => 觉知'})
fp_ = api.call('terms_pending_filter', {'teacher': ft_['id'], 'terms': [{'term': '觉知'}, {'term': '臣服'}, {'term': '臣服 '}],
                                       'errors': [{'wrong': '绝知', 'right': '觉知'}, {'wrong': '陈服', 'right': '臣服'}]})
check('去重：打开新术语对话框前，去掉术语表里已经有的和候选里重复的', [x['term'] for x in fp_['terms']] == ['臣服']
      and [x['wrong'] for x in fp_['errors']] == ['陈服'] and fp_['removed'] == 3, fp_)
config.delete_teacher(ft_['id'])
library.set_folder(str(DS), teacher='')
config.delete_teacher(gc['teacher'])

# 圆桌讨论和主题蒸馏（core/roundtable.py，假 AI）
from core import roundtable  # noqa: E402
MS = KB / '04、外部知识转化库' / '01、老师分身蒸馏资料'
pair = []
for n in ('甲老师', '乙老师'):
    shutil.copytree(TEACHER, MS / n)
    (MS / n / '03、老师画像（对话用）.md').write_text(f'# {n}画像\n', encoding='utf-8')
    pair.append(mentor.add(str(MS / n))['id'])


def _wait(job_id):
    for _ in range(400):
        if jobs.get(job_id).status != 'running':
            return jobs.get(job_id)
        time.sleep(0.05)
    return jobs.get(job_id)


try:
    roundtable.new_round(pair[:1])
    e_ = ''
except roundtable.RoundError as e:
    e_ = str(e)
check('圆桌：至少两位老师', '至少选两位' in e_, e_)
calls, asks = [], []
_llm.stream, _llm.check = (lambda s, n, m, cancel=None, json_mode=False: (calls.append(m[0]['content']), asks.append(m[-1]['content']), fake_ai.stream(s, n, m, cancel, json_mode))[2]), (lambda *a, **k: None)
rc = api.call('round_new', {'mentors': pair})['chat']
r_ = api.call('round_ask', {'cid': rc['id'], 'question': '风险怎么定？'})
j_ = _wait(r_['job'])
rc = roundtable.load_round(rc['id'])
check('圆桌：每位老师各自回答（只带他自己的画像），最后 AI 总结', j_.status == 'done'
      and [(x['role'], x.get('name')) for x in rc['messages']] == [('user', None), ('teacher', '甲老师'), ('teacher', '乙老师'), ('summary', 'AI 总结')]
      and '# 甲老师画像' in calls[0] and '乙老师画像' not in calls[0] and '你只代表「甲老师」' in calls[0]
      and '# 乙老师画像' in calls[1] and calls[2] == roundtable.ROUND_SUMMARY_SYSTEM and rc['title'] == '风险怎么定？', [x['role'] for x in rc['messages']])
check('圆桌：边写边推给界面（谁在说、写了什么）', any(isinstance(x, dict) and x.get('kind') == 'start' and x.get('who') == pair[1] for x in j_.items)
      and any(isinstance(x, dict) and x.get('kind') == 'text' and x.get('who') == 'summary' for x in j_.items))
calls.clear()
j_ = _wait(api.call('round_reply', {'cid': rc['id']})['job'])
rc = roundtable.load_round(rc['id'])
check('圆桌：互相回应一轮（看到别的老师的回答），再重新总结', j_.status == 'done'
      and [x.get('round') for x in rc['messages'] if x['role'] == 'teacher'] == [1, 1, 2, 2] and rc['messages'][-1]['role'] == 'summary'
      and '## 乙老师的回答' in asks[-3] and '## 甲老师的回答' in asks[-2] and '风险怎么定？' in asks[-3] and len(calls) == 3, asks[-3:])
sv = api.call('round_save', {'cid': rc['id'], 'folder': roundtable.TOPIC_FOLDERS[3]})
RF = Path(sv['file'])
check('圆桌：收藏到「02、主题蒸馏资料/04、操作技术相关资料/日期、问题、多位老师讨论.md」', sv['ok'] and RF.parent.name == '04、操作技术相关资料'
      and RF.parent.parent.name == '02、主题蒸馏资料' and RF.name.endswith('、风险怎么定、多位老师讨论.md')
      and '### 乙老师（回应其他老师）' in RF.read_text(encoding='utf-8') and '### AI 总结' in RF.read_text(encoding='utf-8'), sv)
api.call('round_save', {'cid': rc['id'], 'folder': roundtable.TOPIC_FOLDERS[3]})
check('圆桌：再收藏更新同一个文件，覆盖前备份', len(list(RF.parent.glob('*.md'))) == 1
      and len(list((config.DATA_DIR / 'backups' / '圆桌讨论').glob('*.md'))) == 1)
calls.clear()
tp = api.call('topic_start', {'topic': '止损', 'mentors': pair, 'folder': roundtable.TOPIC_FOLDERS[0]})
j_ = _wait(tp['job'])
tg = api.call('topic_get', {'id': tp['topic']['id']})
check('主题蒸馏：每位老师先整理一次，再合并；写进「02、主题蒸馏资料/对应主题/日期、主题、跨来源蒸馏.md」', j_.status == 'done'
      and calls == [roundtable.TOPIC_TEACHER_SYSTEM, roundtable.TOPIC_TEACHER_SYSTEM, roundtable.TOPIC_MERGE_SYSTEM]
      and tg['text'].replace('\r\n', '\n').startswith('---\n类型: 主题蒸馏\n主题: 止损') and '## 一、一句话' in tg['text'] and tg['topic']['file'].endswith('、止损、跨来源蒸馏.md')
      and tg['names'] == {'甲老师': pair[0], '乙老师': pair[1]}, (j_.status, j_.error, {k: v for k, v in tg.items() if k != 'text'}, tg.get('text', '')[:300]))
_llm.stream, _llm.check = _old_stream, _old_check
check('主题蒸馏：查出出处里老师不对、编号不存在的', roundtable.bad_refs('［甲老师 S01-01］［甲老师 S09-01］［丙 S01-01］［乙老师 S01 00:02］',
      {'甲老师': mentor.get(pair[0]), '乙老师': mentor.get(pair[1])}) == ['丙 S01-01', '甲老师 S09-01'])
pm = roundtable.prompts_markdown()
check('提示词：软件用的每一种都能显示、能生成知识库文件，和代码里的一字不差', len(roundtable.prompt_list()) == 11
      and distill.EXTRACT_SYSTEM.strip() in pm and roundtable.TOPIC_MERGE_SYSTEM.strip() in pm and mentor.CHAT_RULES in pm)
api.call('round_delete', {'cid': rc['id']})
api.call('topic_delete', {'id': tp['topic']['id']})
check('删除圆桌讨论、主题蒸馏记录（Obsidian 里写好的文件不动）', roundtable.list_rounds() == [] and roundtable.list_topics() == [] and RF.is_file())
for mid in pair:
    mentor.remove(mid)

# 朗读（core/tts.py）：中括号里的视频时间不读
from core import tts  # noqa: E402
check('朗读：去掉 [01:50]、[1:00:29]、[01:42]-[02:23] 这类时间，别的方括号照读',
      tts.clean('交易难不难 [01:50]，看技术[1:00:29]。[01:42]-[02:23] 结束 [注意]') == '交易难不难 ，看技术。 结束 [注意]', tts.clean('交易难不难 [01:50]，看技术[1:00:29]。[01:42]-[02:23] 结束 [注意]'))
try:
    tts.speak('edge', '[01:50]')
    e_ = ''
except tts.TtsError as e:
    e_ = str(e)
check('朗读：只有时间、没有文字时不去联网', '没有要读的文字' in e_, e_)
_calls = []
def _slow_then_fast(text):
    _calls.append(time.time())
    time.sleep(3 if len(_calls) == 1 else 0.05)  # 第一次很慢（像 Edge 偶尔一句要十几秒），第二次很快
    return b'ok' + str(len(_calls)).encode()
_old_hedge, tts.HEDGE_AFTER = tts.HEDGE_AFTER, 0.3
t0 = time.time()
got = tts._hedged(_slow_then_fast, 'x')
tts.HEDGE_AFTER = _old_hedge
check('朗读：一句迟迟不回来就再要一次，用先回来的（不干等）', got == b'ok2' and len(_calls) == 2 and time.time() - t0 < 1.5, (got, time.time() - t0))
r = api.call('tts_voices', {})
check('朗读：Edge 在线声音列表（晓晓在第一个）', r['ok'] and r['edge'][0]['id'] == 'zh-CN-XiaoxiaoNeural' and len(r['edge']) == 5)
# 英文句子自动换英文声音（2026-10-29，外文书朗读原文）；中文句子夹几个英文词还是中文声音
check('朗读：英文句子算英文，中文句子夹英文词不算', tts.is_english('Research is a craft.') and not tts.is_english('研究是一门 craft。') and not tts.is_english('P42'))
_old_edge = tts._edge
tts._edge = lambda text, voice, proxy: voice.encode()
check('朗读：Edge 读英文句子用英文声音（没选就用 Aria），中文句子用选的中文声音',
      tts.speak('edge', 'Research is a craft.', 'zh-CN-YunxiNeural') == b'en-US-AriaNeural'
      and tts.speak('edge', 'Research is a craft.', 'zh-CN-YunxiNeural', None, 'en-GB-SoniaNeural') == b'en-GB-SoniaNeural'
      and tts.speak('edge', '研究是一门艺术。', 'zh-CN-YunxiNeural', None, 'en-GB-SoniaNeural') == b'zh-CN-YunxiNeural')
tts._edge = _old_edge
check('朗读：声音列表里有英文声音', r['edge_en'][0]['id'] == 'en-US-AriaNeural' and len(r['edge_en']) == 5)

# 从资源管理器拖文件进来：浏览器内核记下完整路径（FilesDropped），界面按文件名取（2026-10-19 起不再用 pywebview 的 DOM 拖放事件）
from webview.dom import _dnd_state  # noqa: E402
_dnd_state['paths'][:] = [('甲.mp4', r'D:\课\甲.mp4')]
r = api.call('dropped', {'names': ['甲.mp4']})
check('拖进来的文件：按文件名取到完整路径，用过就清掉', r['ok'] and r['path'] == r'D:\课\甲.mp4' and not _dnd_state['paths'], r)
t0 = time.time()
r = api.call('dropped', {'names': ['乙.mp4']})
check('没找到时等一会儿就返回空的，不会一直等', r['ok'] and r['path'] == '' and time.time() - t0 < 3)

# 代码里没有写错的变量名（pyflakes）
import importlib.util  # noqa: E402
if importlib.util.find_spec('pyflakes'):
    pf = subprocess.run([sys.executable, '-m', 'pyflakes', str(ROOT / 'app')], capture_output=True, text=True, encoding='utf-8', errors='replace')
    bad = [x for x in pf.stdout.splitlines() if 'undefined name' in x or 'syntax' in x.lower()]
    check('代码里没有未定义的变量名', not bad, bad)
else:
    print('（没装 pyflakes，跳过变量名检查：pip install pyflakes）')

# 数据文件安全：只存一项不能冲掉整份设置；读不出时不能当成空的
api.call('settings', {'settings': {'providers': {'deepseek': {'key': 'sk-safe-test-1234567'}}, 'obsidian': {'vault': str(TMP)}}})
config.save({'book_rules_file': 'x.md'})
s_safe = config.load()
check('config.save 只传一项：其他设置（密钥、Obsidian 位置）不会被冲掉', s_safe['book_rules_file'] == 'x.md'
      and s_safe['providers']['deepseek']['key'] == 'sk-safe-test-1234567' and s_safe['obsidian']['vault'] == str(TMP))
check('写设置时留上一版副本和每日快照', config.SETTINGS_FILE.with_name('settings.json.bak').exists()
      and any(config.SNAPSHOT_DIR.glob('settings-*.json')))
lib_good = config.LIBRARY_FILE.read_text(encoding='utf-8') if config.LIBRARY_FILE.exists() else None
config.save_library({'folders': [{'path': str(TMP), 'name': '安全测试'}], 'files': []})
config.save_library({'folders': [{'path': str(TMP), 'name': '安全测试2'}], 'files': []})
config.LIBRARY_FILE.write_text('{坏掉的文件', encoding='utf-8')
check('媒体库文件坏了：改用上一版副本，不当成空的', config.load_library()['folders'][0]['name'] == '安全测试')
config.LIBRARY_FILE.with_name('library.json.bak').unlink()
try:
    config.load_library(); e_safe = ''
except config.DataError as e:
    e_safe = str(e)
check('媒体库文件坏了又没有副本：明确报错，不悄悄当成空的', '读不出' in e_safe, e_safe)
r_safe = api.call('library', {})
check('界面拿到的是错误，不是空列表', r_safe.get('ok') is False and '读不出' in r_safe.get('error', ''), r_safe)
config.LIBRARY_FILE.unlink()
check('文件本来就不存在：才当成空的', config.load_library() == {'folders': [], 'files': []})
if lib_good is not None:
    config.LIBRARY_FILE.write_text(lib_good, encoding='utf-8')
config.save({'book_rules_file': ''})

# 点 × 收到右下角托盘：默认打开；托盘菜单有「打开」「退出」
check('点 × 收到右下角：默认打开', config.load().get('close_to_tray') is True)
import tray  # noqa: E402
_t = tray.Tray(on_open=lambda: None, on_quit=lambda: None)
check('托盘图标能建起来（菜单：打开、退出）', _t.start() and _t.icon is not None
      and [str(i) for i in _t.icon.menu.items] == [tray.MENU_OPEN, tray.MENU_QUIT])
_t.stop()

# 被其他打包应用（Claude 桌面版）转存走的数据：正常启动时搬回来
from core import appdata
lib_keep = config.LIBRARY_FILE.read_text(encoding='utf-8') if config.LIBRARY_FILE.exists() else None
vdir = TMP / 'local' / 'Packages' / 'Claude_test' / 'LocalCache' / 'Roaming' / config.APP_NAME
(vdir / 'backups' / '笔记').mkdir(parents=True, exist_ok=True)
(vdir / 'library.json').write_text(json.dumps({'folders': [{'path': str(TMP), 'name': '转存的媒体库'}], 'files': []}, ensure_ascii=False), encoding='utf-8')
(vdir / 'backups' / '笔记' / '旧笔记.md').write_text('x', encoding='utf-8')
old_set = config.SETTINGS_FILE.read_text(encoding='utf-8')
(vdir / 'settings.json').write_text('{"theme": "old"}', encoding='utf-8')
os.utime(vdir / 'settings.json', (1, 1))  # 转存的设置比这边旧：不能覆盖
check('找到转存的数据文件夹', appdata.virtual_dirs() == [vdir])
check('测试环境的进程没有被转存', appdata.is_virtualized() is False)
moved_v = appdata.adopt_virtual()
check('转存的媒体库搬回来了（这边旧的先备份）', config.load_library()['folders'][0]['name'] == '转存的媒体库'
      and 'library.json' in moved_v and (lib_keep is None or any((config.DATA_DIR / 'backups' / '数据').glob('搬回前-*/library.json'))), moved_v)
check('备份等其他文件也搬回来', (config.DATA_DIR / 'backups' / '笔记' / '旧笔记.md').exists())
check('转存的比这边旧的不覆盖', config.SETTINGS_FILE.read_text(encoding='utf-8') == old_set)
check('搬完把转存的文件夹改名，下次不再搬', not vdir.exists() and vdir.with_name(config.APP_NAME + appdata.ADOPTED).is_dir()
      and appdata.virtual_dirs() == [])
if lib_keep is not None:
    config.LIBRARY_FILE.write_text(lib_keep, encoding='utf-8')
else:
    config.LIBRARY_FILE.unlink()

# 这边从没真正用过（只有一份默认设置，比转存的还新）：整份采用转存的，旧的先备份
real_keep = config.DATA_DIR
config.DATA_DIR = TMP / 'appdata2' / config.APP_NAME
config.DATA_DIR.mkdir(parents=True)
(config.DATA_DIR / 'settings.json').write_text('{"theme": "默认"}', encoding='utf-8')
vdir2 = TMP / 'local' / 'Packages' / 'Claude_test2' / 'LocalCache' / 'Roaming' / config.APP_NAME
vdir2.mkdir(parents=True)
(vdir2 / 'settings.json').write_text('{"theme": "真的"}', encoding='utf-8')
(vdir2 / 'library.json').write_text('{"folders": [], "files": []}', encoding='utf-8')
os.utime(vdir2 / 'settings.json', (1, 1))
appdata.adopt_virtual()
check('这边没用过：转存的设置即使更旧也整份采用，这边的先备份',
      '真的' in (config.DATA_DIR / 'settings.json').read_text(encoding='utf-8')
      and any('默认' in f.read_text(encoding='utf-8') for f in (config.DATA_DIR / 'backups' / '数据').glob('搬回前-*/settings.json')))
config.DATA_DIR = real_keep

# 缓存和日志位置
check('缓存和日志在指定位置，文件夹名写着可随时删除', config.CACHE_DIR == TMP / 'store' / '缓存（可随时删除）' and config.LOG_DIR.name == '日志（可随时删除）')
r_ = api.call('set_storage', {'path': str(TMP / 'store2')})
check('更改缓存位置', r_['changed'] and config.load()['storage_dir'] == str(TMP / 'store2'))

print(f'\n全部通过：{passed} 项')
import logging  # noqa: E402
logging.shutdown()  # 先关掉日志文件，否则临时文件夹删不干净
shutil.rmtree(TMP, ignore_errors=True)
