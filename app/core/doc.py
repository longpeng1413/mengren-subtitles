"""字幕文件：保存在视频旁边（「视频名.猛人学习.json」），写不进去时放到 %APPDATA%\\猛人学习\\library。
1.6 以前叫「视频名.猛人字幕.json」：照样能读，下次保存时改成新名字。
另外负责导出 TXT / SRT，以及剪藏到 Obsidian。"""
import hashlib
import json
import os
import re
import shutil
import time
from contextlib import contextmanager
from pathlib import Path

from .config import DATA_DIR
from .llm import fmt

SUFFIX = '.猛人学习.json'
OLD_SUFFIX = '.猛人字幕.json'
VERSION = 1


def sidecar(media_path: str) -> Path:
    p = Path(media_path)
    return p.with_name(p.name + SUFFIX)


def old_sidecar(media_path: str) -> Path:
    p = Path(media_path)
    return p.with_name(p.name + OLD_SUFFIX)


def stored_file(media_path: str):
    """这个文件现在的字幕 / 整理结果存在哪里（新名字、旧名字、%APPDATA% 里的备用位置），都没有返回 None。"""
    for p in (sidecar(media_path), old_sidecar(media_path), fallback(media_path)):
        if p.exists():
            return p
    return None


def media_key(media_path: str) -> str:
    """同一个文件永远同一个编号（按完整路径算，不分大小写）。"""
    return hashlib.sha1(os.path.abspath(media_path).lower().encode('utf-8')).hexdigest()[:20]


def fallback(media_path: str) -> Path:
    return DATA_DIR / 'library' / f'{media_key(media_path)}.json'


@contextmanager
def write_lock(media_path: str):
    """同一节课的字幕文件同时只让一个程序写：界面和命令行（Agent）可能同时在改同一节课。"""
    d = DATA_DIR / 'locks'
    d.mkdir(parents=True, exist_ok=True)
    f = open(d / f'{media_key(media_path)}.lock', 'a+b')
    locked = False
    try:
        if os.name == 'nt':
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)  # 拿不到会重试约 10 秒，再不行报错
            locked = True
        yield
    finally:
        if locked:
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        f.close()


def load(media_path: str):
    for path in (sidecar(media_path), old_sidecar(media_path), fallback(media_path)):
        try:
            d = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(d, dict) and isinstance(d.get('segments'), list):
                d['_stored'] = str(path)
                return d
        except (OSError, ValueError):
            continue
    return None


def new(media_path: str, info: dict) -> dict:
    return {
        'version': VERSION,
        'media': os.path.abspath(media_path),
        'title': Path(media_path).stem,
        'duration': info.get('duration', 0),
        'lang': None,
        'provider': None,
        'segments': [],
        'summary': '',
        'chat': [],
        'clip': None,
        'created': time.strftime('%Y-%m-%d %H:%M:%S'),
        'updated': None,
    }


def clean_segments(segments):
    out = []
    for s in segments or []:
        try:
            start, end = float(s.get('start', 0)), float(s.get('end', 0))
        except (TypeError, ValueError):
            continue
        out.append({'start': round(max(0.0, start), 2), 'end': round(max(start, end), 2),
                    'text': str(s.get('text') or '').strip(), 'tr': str(s.get('tr') or '').strip()})
    out.sort(key=lambda s: s['start'])
    return out


def note_hash(doc) -> str:
    """会写进剪藏笔记的内容（标题、老师、课程重点、字幕、存疑句、是否已校对）的指纹。
    剪藏时记在 clip.hash 里；之后内容变了，媒体库就显示「待更新剪藏」。"""
    segs = [[s['start'], s['text'], s['tr']] for s in clean_segments(doc.get('segments'))]
    doubts = sorted([round(float(d.get('t') or 0), 2), str(d.get('reason') or '')] for d in doc.get('doubts') or [] if isinstance(d, dict))
    data = [doc.get('title') or '', doc.get('teacher') or '', (doc.get('summary') or '').strip(), segs, doubts, bool(doc.get('reviewed'))]
    shots = sorted([round(float(x.get('t') or 0), 2), round(float(x.get('at') or 0), 2)] + _shot_edit(x) for x in doc.get('sub_shots') or [] if isinstance(x, dict))
    if shots:  # 有字幕截图才加：没有的旧字幕文件指纹不变，不会全都变成「待更新剪藏」
        data.append(shots)
    edits = sorted([k] + _shot_edit(v) for k, v in (doc.get('fig_edits') or {}).items() if isinstance(v, dict) and _shot_edit(v))
    if edits:  # 课程重点里的图裁剪过、改过大小才加
        data.append(edits)
    return hashlib.sha1(json.dumps(data, ensure_ascii=False).encode('utf-8')).hexdigest()[:16]


def _shot_edit(x):
    """截图编辑过的裁剪范围和显示宽度（2026-11-02）；没编辑过返回空列表。"""
    from .media import norm_crop, norm_width
    crop, w = norm_crop(x.get('crop')), norm_width(x.get('w'))
    return [crop, w] if crop or w else []


def _embed(name, w=None):
    """笔记里的图片链接；设了显示宽度时写成 Obsidian 的 ![[图|宽度]]。"""
    from .media import norm_width
    w = norm_width(w)
    return f'![[image/{name}|{w}]]' if w else f'![[image/{name}]]'


SUMMARY_STALE_AT = 3  # 生成课程重点后字幕改了几句以上，提醒重新生成


def _sig(text) -> str:
    return hashlib.sha1(str(text or '').strip().encode('utf-8')).hexdigest()[:10]


def summary_basis(segments) -> list:
    """生成课程重点时用的是哪一版字幕：每句一个短指纹（不存全文），记在 doc['summary_src']。"""
    return [_sig(s.get('text')) for s in segments or [] if str(s.get('text') or '').strip()]


def summary_changes(doc) -> int:
    """生成课程重点之后，字幕改了几句（改过、新加的句子数）。没有课程重点时是 0。
    以前生成的课程重点没记 summary_src：按手动改过、而且现在还是改后样子的句子数估算
    （一键整理里 AI 校对和画面复核在课程重点之前，手动修改一般在之后）。"""
    if not (doc.get('summary') or '').strip() or doc.get('kind') == 'book':
        return 0
    segs = [str(s.get('text') or '').strip() for s in doc.get('segments') or [] if str(s.get('text') or '').strip()]
    src = doc.get('summary_src')
    if isinstance(src, list):
        left = {}
        for h in src:
            left[h] = left.get(h, 0) + 1
        n = 0
        for t in segs:
            h = _sig(t)
            if left.get(h):
                left[h] -= 1
            else:
                n += 1
        return n
    now = set(segs)
    return len({f.get('after') for f in doc.get('fixes') or [] if f.get('by') == 'manual' and f.get('after') in now})


def clip_stale(doc) -> bool:
    """剪藏后又改过要写进笔记的内容。1.7.2 以前剪藏的没有指纹，不判断。"""
    clip = doc.get('clip') if isinstance(doc.get('clip'), dict) else {}
    return bool(clip.get('hash')) and doc.get('kind') != 'book' and clip['hash'] != note_hash(doc)


def _write(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():  # 留一份上一次保存的版本，以防万一
        try:
            shutil.copy2(path, path.with_name(path.name + '.bak'))
        except OSError:
            pass
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, path)


def save(media_path: str, doc: dict, base_rev=None) -> dict:
    """保存。每保存一次 rev 加 1。传了 base_rev（界面打开 / 上次保存时的 rev）而磁盘上已经是别的 rev，
    说明这期间别的程序（Agent 自动整理）改过这节课：不覆盖，返回 {'conflict': True}。"""
    with write_lock(media_path):
        cur = load(media_path)
        disk_rev = int((cur or {}).get('rev') or 0)
        if base_rev is not None and cur is not None and int(base_rev or 0) != disk_rev:
            return {'conflict': True, 'rev': disk_rev, 'updated': cur.get('updated')}
        data = {k: v for k, v in doc.items() if not k.startswith('_')}
        data['version'] = VERSION
        data['media'] = os.path.abspath(media_path)
        data['segments'] = clean_segments(data.get('segments'))
        data['updated'] = time.strftime('%Y-%m-%d %H:%M:%S')
        data['rev'] = disk_rev + 1
        try:
            old, new = old_sidecar(media_path), sidecar(media_path)
            if old.exists() and not new.exists():  # 改名前的字幕文件：先改成新名字，保存时照样留一份上一版
                os.replace(old, new)
            _write(new, data)
            where = new
            for p in (old, old.with_name(old.name + '.bak')):
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
        except OSError:
            _write(fallback(media_path), data)
            where = fallback(media_path)
    return {'updated': data['updated'], 'rev': data['rev'], 'stored': str(where), 'stale': clip_stale(data),
            'summary_changes': summary_changes(data)}


# ------------------------------------------------------------------ 导出 ----
def to_txt(doc, mode='both', timestamps=True):
    lines = [doc.get('title') or '', '']
    for s in doc.get('segments', []):
        stamp = f'[{fmt(s["start"])}] ' if timestamps else ''
        if mode == 'tr':
            body = s.get('tr') or s.get('text', '')
        elif mode == 'src':
            body = s.get('text', '')
        else:
            body = s.get('text', '') + (f'\n{" " * len(stamp)}{s["tr"]}' if s.get('tr') else '')
        lines.append(stamp + body)
        if mode == 'both' and s.get('tr'):
            lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


def _srt_time(sec):
    ms = int(round(sec * 1000))
    return f'{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}'


def to_srt(doc, mode='both'):
    out = []
    for i, s in enumerate(doc.get('segments', []), 1):
        if mode == 'tr':
            body = s.get('tr') or s.get('text', '')
        elif mode == 'src':
            body = s.get('text', '')
        else:
            body = s.get('text', '') + (('\n' + s['tr']) if s.get('tr') else '')
        out.append(f'{i}\n{_srt_time(s["start"])} --> {_srt_time(max(s["end"], s["start"] + 0.5))}\n{body}\n')
    return '\n'.join(out)


def export(doc, dest: str, fmt_name='txt', mode='both', timestamps=True):
    text = to_srt(doc, mode) if fmt_name == 'srt' else to_txt(doc, mode, timestamps)
    Path(dest).write_text(text, encoding='utf-8-sig' if fmt_name == 'txt' else 'utf-8')
    return dest


# --------------------------------------------------------------- Obsidian ----
def safe_name(name):
    name = re.sub(r'[\\/:*?"<>|#^\[\]]', ' ', name).strip().rstrip('.')
    return re.sub(r'\s+', ' ', name)[:120] or '未命名'


def _yaml(v):
    return '"' + str(v or '').replace('\\', '/').replace('"', "'") + '"'


_END_PUNCT = tuple('。！？!?.…”"」』）)～~')


def source_boundary(doc, tail_problem=''):
    """来源情况：字幕在视频结束前很久就没了、最后一句没说完、视频结尾损坏。没有问题返回空字符串。"""
    segs = [s for s in doc.get('segments') or [] if str(s.get('text') or '').strip()]
    if not segs:
        return ''
    notes = []
    last = segs[-1]
    dur = float(doc.get('duration') or 0)
    if dur and dur - last['end'] > 8:
        notes.append(f'字幕在 {fmt(last["end"])} 结束，视频时长 {fmt(dur)}，最后约 {int(dur - last["end"])} 秒没有识别到讲话')
    text = last['text'].strip()
    if not text.endswith(_END_PUNCT):
        notes.append(f'最后一句没有说完（[{fmt(last["start"])}]「{text[-30:]}」）')
    if tail_problem:
        notes.append(tail_problem)
    return '；'.join(notes)


def _shift_headings(summary):
    """课程重点放在笔记的「## 课程重点」下面：去掉 AI 写的总标题，其余标题降到 ### 起。"""
    lines = (summary or '').strip().split('\n')
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and re.match(r'^#{1,6}\s+.*(课程重点|重点|笔记)', lines[0]) and len(lines[0]) < 60:
        lines.pop(0)
    levels = [len(m.group(1)) for m in (re.match(r'^(#{1,6})\s', x) for x in lines) if m]
    if levels:
        shift = 3 - min(levels)
        if shift:
            lines = [re.sub(r'^(#{1,6})(?=\s)', lambda m: '#' * max(1, min(6, len(m.group(1)) + shift)), x) for x in lines]
    return '\n'.join(lines).strip()


def doubt_map(doc):
    """AI 校对时标出的存疑句：{开始时间（保留两位小数）: 原因}。"""
    out = {}
    for d in doc.get('doubts') or []:
        try:
            out[round(float(d.get('t')), 2)] = str(d.get('reason') or '').strip()
        except (TypeError, ValueError):
            continue
    return out


def to_markdown(doc, teacher=None, series='', boundary=''):
    """蒸馏用的笔记：上半部分课程重点（含必要的插图），下半部分完整带时间戳的字幕（审核校验用的原始数据）。
    画面记录不写进笔记（用户 2026-10 要求：笔记只要课程重点和完整字幕）。"""
    media = doc.get('media', '')
    tag = (teacher or {}).get('name', '')  # 术语表档案的名字只当标签（课程体系，例如「峰级分析」），不当讲课老师
    segs = doc.get('segments', [])
    lines = [
        '---',
        # 属性名用中文，只留有用的（用户 2026-10 要求）。tags 是 Obsidian 固定的属性名，不能改；
        # 视频文件不能去掉：重新剪藏时靠它找回被挪走、改名的笔记（find_note）
        f'视频文件: {_yaml(media)}',
        # 用户明确不要：状态（以前给交叉审核用的「待审计」，现在不交叉审核了）、老师、系列（写在正文标题下面那一行，占地方少）、语言、时长、视频日期（是文件修改时间，会误导）、识别服务、句数等
        f'人工校对: {"true" if doc.get("reviewed") else "false"}',
        f'剪藏时间: {time.strftime("%Y-%m-%d %H:%M")}',
        'tags:',
        '  - 猛人学习',
    ]
    if tag:
        lines.append(f'  - {safe_name(tag).replace(" ", "_")}')
    lines += ['---', '', f'# {doc.get("title") or ""}', '']
    # 标题下面不再写老师、系列、时长这一行（用户 2026-10：老师和归类自己在 Obsidian 里处理）
    lines += ['## 课程重点', '', _shift_headings(doc.get('summary')) or '（还没有生成课程重点）', '']
    if boundary and '整理者注' not in (doc.get('summary') or ''):  # 课程重点里已经写了就不重复
        lines += [f'> 来源情况（软件检查）：{boundary}。整理时没有补写缺失或没说完的内容。', '']
    doubts = doubt_map(doc)
    lines += ['## 完整字幕', '', '> 以下是带时间戳的完整字幕，作为审核和校验的原始数据。'
              + ('已人工校对。' if doc.get('reviewed') else '尚未人工校对。')
              + ('〔存疑：…〕是 AI 校对时标出、需要回听确认的句子。' if doubts else ''), '']
    imgs = doc.get('_sub_imgs') or {}  # 字幕里手动插的截图（clip 时换成图片）：{挂在第几句后面: [图片行]}，-1 是第一句前面
    lines += [x for img in imgs.get(-1, []) for x in (img, '')]
    for i, s in enumerate(segs):
        why = doubts.get(round(float(s['start']), 2))
        lines.append((f'[{fmt(s["start"])}] {s.get("text", "")}' + (f' 〔存疑：{why}〕' if why else '')).rstrip())
        if s.get('tr'):
            lines.append(f'　　{s["tr"]}')
        for img in imgs.get(i, []):
            lines += ['', img, '']
    return '\n'.join(lines).rstrip() + '\n'


def sub_shot_index(segs, at):
    """字幕截图挂在哪一句后面：开始时间不晚于 at 的最后一句（句子删了、拆了也能找到位置）；-1 是第一句前面。和界面 findIndex 一样。"""
    k = -1
    for i, s in enumerate(segs or []):
        if float(s.get('start', 0)) <= float(at) + 0.05:
            k = i
        else:
            break
    return k


def sub_shot_images(doc, note_dir: Path, title, shot):
    """字幕里手动插的截图（2026-10-30 用户要求，sub_shots: [{t: 截图时间, at: 挂在哪一句后面（那一句的开始时间）}]）
    → {挂在第几句后面: ['![[image/…]]']}；截图复制到笔记旁边的 image，文件名和课程重点的插图同一规则（同一时间只存一份）。
    裁剪过的（crop，2026-11-02）另存「…__字幕裁剪.jpg」、每次剪藏重写（范围可能改了）；设了宽度（w）写进图片链接。"""
    from .media import norm_crop
    out = {}
    segs = doc.get('segments') or []  # 和 to_markdown 写字幕时用同一份，位置才对得上
    for x in sorted((x for x in doc.get('sub_shots') or [] if isinstance(x, dict)), key=lambda x: (float(x.get('at') or 0), float(x.get('t') or 0))):
        t = float(x.get('t') or 0)
        if shot is None:
            line = f'（画面 {fmt(t)}）'
        else:
            try:
                crop = norm_crop(x.get('crop'))
                name = f'{safe_name(title)}__{fmt(t).replace(":", "-")}' + ('__字幕裁剪' if crop else '') + '.jpg'
                dest = note_dir / 'image' / name
                if crop or not dest.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    _small_copy(Path(shot(t)), dest, crop)
                line = _embed(name, x.get('w'))
            except Exception:
                line = f'（画面 {fmt(t)}，截图失败）'
        out.setdefault(sub_shot_index(segs, x.get('at') or 0), []).append(line)
    return out


_FIG_LINE = re.compile(r'^\s*!\[\[\s*画面\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*\]\]\s*$')
_BLOCK_START = re.compile(r'^\s*([-*+]\s|\d+[.、)]|#{1,6}\s|>|\||```|~~~|!\[\[)')
_TS_ANY = re.compile(r'(?<![\d:])(?:\d{1,2}:)?\d{1,2}:\d{2}(?![\d:])')


def _md_blocks(lines):
    """课程重点的非空行分成块 [(第一行, 最后一行)]：一条重点、一个标题、一张表、一段代码、一张图。和界面 sumBlocks 一样。"""
    out, i, n = [], 0, len(lines)
    while i < n:
        if not lines[i].strip():
            i += 1
            continue
        st = i
        if re.match(r'^\s*(```|~~~)', lines[i]):
            f = lines[i].strip()[:3]
            i += 1
            while i < n and not lines[i].strip().startswith(f):
                i += 1
            i += 1
        elif re.match(r'^\s*\|', lines[i]):
            while i < n and re.match(r'^\s*\|', lines[i]):
                i += 1
        elif _FIG_LINE.match(lines[i]):
            i += 1
        else:
            i += 1
            while i < n and lines[i].strip() and not _BLOCK_START.match(lines[i]):
                i += 1
        out.append((st, min(i, n) - 1))
    return out


def _md_anchor(lines, t):
    """手动插的图放在哪一行后面：出处时间不晚于 t、离 t 最近的那一块；都比 t 晚就放在出处最早的那块前面；没有出处时间放最后。和界面 sumAnchor 一样。"""
    best = first = None
    for a, b in _md_blocks(lines):
        if _FIG_LINE.match(lines[a]) or re.match(r'^\s*(```|~~~)', lines[a]):  # 图、代码块里的时间不算
            continue
        for x in (_parse_ts(m) for m in _TS_ANY.findall(' '.join(lines[a:b + 1]))):
            if x <= t and (best is None or x > best[0]):
                best = (x, b)
            if first is None or x < first[0]:
                first = (x, a)
    return best[1] if best else first[1] - 1 if first else len(lines) - 1


def keep_sum_shots(old, new, shots):
    """重新生成课程重点时，把用户手动插的截图（2026-11-02，sum_shots 记着秒数）放回新的课程重点：
    只放旧课程重点里还在的（用户删掉的不放回）、新课程重点里还没有同一时间的图的；位置按出处时间找（_md_anchor）。
    返回 (新课程重点, 还在的 sum_shots)。"""
    def times(text):
        return {_parse_ts(m.group(1)) for m in (_FIG_LINE.match(x) for x in (text or '').split('\n')) if m}
    had, have = times(old), times(new)
    lines = (new or '').split('\n')
    keep = []
    for t in shots or []:
        try:
            t = int(t)
        except (TypeError, ValueError):
            continue
        if t in had and t not in keep:
            keep.append(t)
    add = sorted((t for t in keep if t not in have), reverse=True)  # 从晚到早插：挂在同一条后面的几张按时间先后排
    if not add:
        return new, keep
    for t in add:
        at = _md_anchor(lines, t)
        lines[at + 1:at + 1] = ['', f'![[画面 {fmt(t)}]]', '']
    out, fence = [], False  # 连着的空行只留一个（代码块里不动）
    for x in lines:
        if re.match(r'^\s*(```|~~~)', x):
            fence = not fence
        if not fence and not x.strip() and (not out or not out[-1].strip()):
            continue
        out.append(x)
    while out and not out[-1].strip():
        out.pop()
    return '\n'.join(out), keep


def _same_media(a, b):
    norm = lambda p: os.path.normcase(os.path.abspath(str(p).replace('/', os.sep))) if p else ''
    return bool(a) and norm(a) == norm(b)


def find_note(vault: Path, media: str, prefer: Path = None, also=()):
    """找已经剪藏过的笔记（用户可能在 Obsidian 里移动或改了名）：按笔记属性里的「视频文件」找。
    also：这个视频以前的路径（在软件里改过名、移动过文件夹），笔记里写的可能还是旧路径。"""
    medias = [media] + [x for x in also or () if x]
    roots = [p for p in (prefer, vault) if p and p.is_dir()]
    seen = set()
    for root in roots:
        for p in root.rglob('*.md'):
            if p in seen or '.obsidian' in p.parts or '.trash' in p.parts:
                continue
            seen.add(p)
            try:
                with open(p, 'r', encoding='utf-8', errors='replace') as f:
                    head = f.read(1500)
            except OSError:
                continue
            if not head.startswith('---') or ('猛人学习' not in head and '猛人字幕' not in head):
                continue
            m = re.search(r'(?m)^(?:视频文件|source):\s*"?(.*?)"?\s*$', head)  # 1.7.4 以前的笔记属性是英文
            if m and any(_same_media(m.group(1), x) for x in medias):
                return p
    return None


# ---- 重新剪藏时保留用户在 Obsidian 里加的内容 ----
# 软件只负责：属性里自己的那些字段、标题和说明行、「## 课程重点」「## 完整字幕」两节。
# 用户自己加的 ## 小节、属性、tags，以及写在这几块里的标注（> [!…]）都保留；标注跟着它上面那一行走。
APP_KEYS = {'标题', '系列', '视频日期', '视频文件', '时长', '语言', '字幕识别', '术语表', 'AI校对', '人工校对', '字幕句数', '剪藏时间',
            # 1.7.4 以前的英文属性名
            'title', 'teacher', 'series', 'lesson_date', 'source', 'duration', 'language', 'asr', 'glossary', 'corrected', 'reviewed',
            'status', 'segments', 'clipped'}
# 「老师」不在这里：1.7.8 起软件不写老师，用户自己在属性里填的要保留
MANAGED = ('', '课程重点', '完整字幕')  # '' 是第一个 ## 之前的部分（标题和说明行）
LEGACY = ('画面记录',)  # 以前的版本写过、现在不写的小节：重新剪藏时去掉


def _split_front(text):
    text = (text or '').replace('\r\n', '\n')
    if text.startswith('---\n'):
        end = text.find('\n---\n', 4)
        if end > 0:
            return text[4:end], text[end + 5:]
        if text.endswith('\n---'):
            return text[4:-4], ''
    return '', text


def _fm_blocks(fm):
    """属性 → [[名字, [行…]]]，一个属性可以跨几行（列表）。"""
    blocks = []
    for line in fm.split('\n'):
        m = re.match(r'^([^\s#-][^:]*):', line)
        if m:
            blocks.append([m.group(1).strip(), [line]])
        elif blocks:
            blocks[-1][1].append(line)
    return blocks


def _tag_list(lines):
    first = lines[0].split(':', 1)[1].strip()
    tags = [t.strip().strip('"\'') for t in first.strip('[]').split(',')] if first else []
    tags += [ln.strip()[2:].strip().strip('"\'') for ln in lines[1:] if ln.strip().startswith('- ')]
    return [t for t in tags if t]


def _sections(body):
    parts, cur = [], ['', []]
    for line in body.split('\n'):
        m = re.match(r'^##\s+(.+?)\s*$', line)
        if m:
            parts.append(cur)
            cur = [m.group(1), [line]]
        else:
            cur[1].append(line)
    parts.append(cur)
    return parts


def _is_callout(line):
    return line.lstrip().startswith('> [!')


def _callouts(lines):
    """取出标注块：[(它上面那一行, [标注的几行])]，以及去掉标注后剩下的行。"""
    found, rest, anchor, i = [], [], None, 0
    while i < len(lines):
        if _is_callout(lines[i]):
            blk = [lines[i]]
            i += 1
            while i < len(lines) and lines[i].lstrip().startswith('>') and not _is_callout(lines[i]):
                blk.append(lines[i])
                i += 1
            found.append((anchor, blk))
            continue
        if lines[i].strip():
            anchor = lines[i].strip()
        rest.append(lines[i])
        i += 1
    return found, rest


def _put_callouts(lines, found):
    """把标注放回它上面那一行的后面；那一行没了就放到这一节最后。返回 (新的行, 找不到原位置的个数)。"""
    out, lost = list(lines), []
    for anchor, blk in found:
        k = next((n for n, ln in enumerate(out) if anchor is not None and ln.strip() == anchor and not ln.lstrip().startswith('>')), None)
        if k is None:
            lost.append(blk)
            continue
        j = k + 1
        while j < len(out) and _is_callout(out[j]):
            j += 1
            while j < len(out) and out[j].lstrip().startswith('>') and not _is_callout(out[j]):
                j += 1
        piece = ([''] if out[j - 1].strip() else []) + blk + ([''] if j < len(out) and out[j].strip() else [])
        out[j:j] = piece
    if lost:
        while out and not out[-1].strip():
            out.pop()
        for blk in lost:
            out += [''] + blk
        out.append('')
    return out, len(lost)


# 书籍笔记：软件写的属性；小节是「一、本章主题」这类编号小节，以及上次剪藏时记下的小节名（全书总览的小节不编号）
BOOK_KEYS = {'标题', '类型', '书名', '作者', '部分', '章节', '页码范围', '来源类型', '来源文件', '整理时间', '已整理范围',
             # 1.7.4 以前的英文属性名
             'title', 'type', 'book', 'author', 'chapter', 'page_range', 'source_type', 'source', 'organized', 'completed_scope', 'status'}
_CN_SECTION = re.compile(r'^[一二三四五六七八九十百]+、')
_VOLATILE = ('状态', 'status', '剪藏时间', 'clipped', '整理时间', 'organized')  # 每次剪藏都会变、或由用户改的，不算改动


def _owned(name, kind, app_sections=None):
    """这一节是不是软件负责的。"""
    if kind == 'video':
        return name in MANAGED
    return name == '' or name in (app_sections or ()) or bool(_CN_SECTION.match(name))


def merge_note(old_text, new_text, app_tags=None, legacy_teacher=(), kind='video', app_sections=None):
    """新生成的笔记 + 用户在 Obsidian 里加的内容。返回 (合并后的文字, 找不到原位置、挪到小节末尾的标注个数)。
    app_tags：上次剪藏时软件写的标签（这次不写了的就去掉，用户加的保留）；1.7.8 以前剪藏的没有记，传 None。
    legacy_teacher：以前软件往「老师」属性里写的术语表名字，旧笔记里遇到就去掉（用户自己填的老师保留）。
    kind：'video' 或 'book'；app_sections：书籍笔记上次剪藏时软件写的小节名。
    用户自己的小节跟着它前面那个软件小节走。"""
    keys_owned = APP_KEYS if kind == 'video' else BOOK_KEYS
    old_fm, old_body = _split_front(old_text)
    new_fm, new_body = _split_front(new_text)
    old_blocks = _fm_blocks(old_fm)
    old_keys = {k: v for k, v in old_blocks}
    old_teacher = next((v[0].split(':', 1)[1].strip().strip('"') for k, v in old_blocks if k in ('老师', 'teacher')), '')
    if not old_teacher:  # 1.7.7 起老师只写在标题下面那一行
        m = re.search(r'(?m)^> 老师：(.+?)(?: ｜|$)', old_body)
        old_teacher = m.group(1).strip() if m else ''
    fm_lines, new_names = [], set()
    for key, lines in _fm_blocks(new_fm):
        new_names.add(key)
        if key == 'tags' and 'tags' in old_keys:  # tags 取并集：软件的在前，用户加的在后
            tags = _tag_list(lines)
            if app_tags is not None:
                stale = set(app_tags)  # 上次软件写的、这次没写的
            elif kind == 'video':
                stale = {safe_name(old_teacher).replace(' ', '_')} if old_teacher else set()  # 以前按老师名字加的标签
            else:
                stale = set()
            tags += [t for t in _tag_list(old_keys['tags']) if t not in tags and t not in stale]
            lines = ['tags:'] + [f'  - {t}' for t in tags]
        fm_lines += lines
    for key, lines in old_blocks:
        value = lines[0].split(':', 1)[1].strip().strip('"')
        if key == '老师' and app_tags is None and value in legacy_teacher:
            continue  # 1.7.4～1.7.7 软件写的「老师」（其实是术语表名字）
        if key in ('状态', 'status'):  # 1.7.8 起软件不写状态：还是默认的「待审计」就去掉，用户改过的保留
            if app_tags is None and value in ('待审计', ''):
                continue
            if key == 'status':
                lines = [f'状态: {_yaml(value)}'] + lines[1:]
                key = '状态'
        if key not in keys_owned and key not in new_names:
            fm_lines += lines
    new_sections = _sections(new_body)
    new_parts = {name: lines for name, lines in new_sections}
    owned_names = set(app_sections or ()) | set(new_parts)
    # 旧笔记：软件的小节取出标注；用户的小节记下跟在哪个软件小节后面
    found_by, follow, carry, anchor = {}, {}, [], ''
    for name, lines in _sections(old_body):
        if kind == 'video' and name in LEGACY:
            continue
        if _owned(name, kind, owned_names):
            found = _callouts(lines)[0]
            if name in new_parts:
                found_by[name] = found
                anchor = name
            else:
                carry += [(None, blk) for _, blk in found]  # 这一节这次没有了：标注挪到最后，不丢
        else:
            follow.setdefault(anchor if anchor in new_parts else None, []).append(lines)
    out, moved = [], 0
    for name, lines in new_sections:
        merged, n = _put_callouts(lines, found_by.get(name, []))
        out += merged
        moved += n
        for user_lines in follow.get(name, []):
            out += user_lines
    for user_lines in follow.get(None, []):
        out += user_lines
    if carry:
        out += [''] + [ln for _, blk in carry for ln in blk + ['']]
        moved += len(carry)
    text = '---\n' + '\n'.join(fm_lines) + '\n---\n' + '\n'.join(out)
    return re.sub(r'\n{3,}', '\n\n', text).rstrip() + '\n', moved


def note_sections(text):
    """笔记里的 ## 小节名（书籍剪藏时记下，下次用来分清哪些是软件写的）。"""
    return [name for name, _ in _sections(_split_front(text)[1]) if name]


def note_fingerprint(text: str, kind='video', app_sections=None) -> str:
    """笔记里软件负责的那部分的指纹，用来判断剪藏后有没有在 Obsidian 里改过它。
    不算：属性里的状态和剪藏时间、用户自己加的属性和小节、标注（这些重新剪藏时都会保留）。"""
    keys_owned = APP_KEYS if kind == 'video' else BOOK_KEYS
    fm, body = _split_front(text)
    keys = [[k, [ln.rstrip() for ln in v]] for k, v in _fm_blocks(fm) if k in keys_owned and k not in _VOLATILE]
    parts = []
    for name, lines in _sections(body):
        if _owned(name, kind, app_sections):
            parts.append([name, [ln.rstrip() for ln in _callouts(lines)[1] if ln.strip()]])  # 空行多少不算改动
    return 'v2:' + hashlib.sha1(json.dumps([keys, parts], ensure_ascii=False).encode('utf-8')).hexdigest()[:16]


def note_changed(path: Path, clip, kind='video', app_sections=None) -> bool:
    """上次剪藏后，笔记在 Obsidian 里被改过。
    有指纹（1.7.5 起）就比软件负责那部分的指纹；以前剪藏的没有，看文件修改时间是不是比剪藏时间晚。"""
    clip = clip if isinstance(clip, dict) else {}
    try:
        if not path.is_file():
            return False
        if str(clip.get('file_hash') or '').startswith('v2:'):
            return note_fingerprint(path.read_text(encoding='utf-8', errors='replace'), kind, app_sections) != clip['file_hash']
        t = time.mktime(time.strptime(str(clip.get('time') or ''), '%Y-%m-%d %H:%M:%S'))
    except (OSError, ValueError, OverflowError):
        return False
    return path.stat().st_mtime > t + 10


def backup_note(path: Path) -> Path:
    """覆盖在 Obsidian 里改过的笔记前，先备份到软件的数据文件夹（不放进知识库，免得多出一篇重复笔记）。"""
    folder = DATA_DIR / 'backups' / '笔记'
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f'{path.stem}-{time.strftime("%Y%m%d-%H%M%S")}{path.suffix}'
    shutil.copy2(path, dest)
    return dest


def note_name(pattern, doc, teacher=None, series=''):
    name = (pattern or '{title}')
    for k, v in (('{title}', doc.get('title') or '字幕'), ('{teacher}', (teacher or {}).get('display') or (teacher or {}).get('name', '')),
                 ('{series}', series or '')):
        name = name.replace(k, v)
    return safe_name(name)


_FIG = re.compile(r'!\[\[\s*画面\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*\]\]')


def _parse_ts(s):
    parts = [int(x) for x in s.split(':')]
    return parts[0] * 3600 + parts[1] * 60 + parts[2] if len(parts) == 3 else parts[0] * 60 + parts[1]


def _small_copy(src: Path, dest: Path, crop=None):
    """插进笔记的图缩到 960 宽、压缩一些，让知识库尽量小；压缩失败就原样复制。crop：先裁出用户框的范围（media.norm_crop 过的）。"""
    try:
        from .media import crop_vf, run, tool
        tmp = dest.with_name(dest.stem + '.part.jpg')
        vf = (crop_vf(crop) + ',' if crop else '') + "scale='min(960,iw)':-2"
        r = run([tool('ffmpeg'), '-y', '-v', 'error', '-i', str(src), '-vf', vf, '-q:v', '6', str(tmp)], timeout=60)
        if r.returncode == 0 and tmp.is_file() and tmp.stat().st_size > 0:
            os.replace(tmp, dest)
            return
        tmp.unlink(missing_ok=True)
    except Exception:
        pass
    shutil.copy2(src, dest)


REDRAW_LABEL = '**AI辅助理解图｜根据老师讲解和画面整理，非原画面**'


def sanitize_svg(code) -> str:
    """AI 写的 SVG 放进知识库前清一遍：只留 <svg>…</svg>，去掉脚本、事件、外部链接和外部资源。不像 SVG 返回空。"""
    s = re.sub(r'^\s*```(?:svg|xml)?\s*|\s*```\s*$', '', str(code or '').strip())
    i, j = s.find('<svg'), s.rfind('</svg>')
    if i < 0 or j < i:
        return ''
    s = s[i:j + 6]
    if len(s) > 200000:
        return ''
    for tag in ('script', 'foreignObject', 'iframe', 'object', 'embed', 'audio', 'video', 'image'):
        s = re.sub(rf'(?is)<\s*{tag}\b.*?(?:</\s*{tag}\s*>|/>)', '', s)
        s = re.sub(rf'(?is)<\s*/?\s*{tag}\b[^>]*>', '', s)
    s = re.sub(r'(?i)\s+on\w+\s*=\s*("[^"]*"|\'[^\']*\'|[^\s>]+)', '', s)
    s = re.sub(r'(?i)\s+(?:xlink:)?href\s*=\s*("(?!#)[^"]*"|\'(?!#)[^\']*\')', '', s)
    s = re.sub(r'(?i)url\(\s*[\'"]?(?!#)[^)]*\)', 'none', s)
    s = re.sub(r'(?i)@import[^;]*;?|javascript:', '', s)
    if 'xmlns=' not in s[:400]:
        s = s.replace('<svg', '<svg xmlns="http://www.w3.org/2000/svg"', 1)
    return s


def _redraw_block(f, t, note_dir: Path, title):
    """AI辅助理解图写进笔记：标注一行 + Mermaid 代码块，或 image 文件夹里的 SVG。"""
    r = f.get('redraw') or {}
    head = f'{REDRAW_LABEL}（[{fmt(t)}]）'
    if r.get('kind') == 'mermaid' and r.get('code'):
        return f'{head}\n\n```mermaid\n{r["code"].strip()}\n```'
    svg = sanitize_svg(r.get('code')) if r.get('kind') == 'svg' else ''
    if not svg:
        return None
    name = f'{safe_name(title)}__{fmt(t).replace(":", "-")}__AI辅助理解图.svg'
    dest = note_dir / 'image' / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(svg, encoding='utf-8')
    return f'{head}\n![[image/{name}]]'


def place_figures(summary, frames_list, note_dir: Path, title, shot, edits=None):
    """把课程重点里的 ![[画面 mm:ss]] 换成真正的图片：截图复制到笔记旁边的 image 文件夹；
    这张画面有 AI辅助理解图（redraw）的，放重画的图，不放看不清的截图。
    edits（doc 的 fig_edits，2026-11-02）：{'秒数': {crop, w}}，用户在「编辑截图」里裁剪、改大小的：裁剪的另存「…__裁剪.jpg」，宽度写进图片链接。"""
    from .media import norm_crop
    if not summary or not _FIG.search(summary):
        return summary

    def repl(m):
        t = _parse_ts(m.group(1))
        f = None
        for x in frames_list or []:
            if abs(float(x.get('t', 0)) - t) <= 3 and (f is None or abs(float(x['t']) - t) < abs(float(f['t']) - t)):
                f = x
        real_t = float(f['t']) if f else float(t)
        if f and f.get('redraw'):
            block = _redraw_block(f, real_t, note_dir, title)
            if block:
                return block
        if shot is None:
            return f'（画面 {m.group(1)}）'
        try:
            src = Path(shot(real_t))
        except Exception:
            return f'（画面 {m.group(1)}，截图失败）'
        e = (edits or {}).get(str(t))
        e = e if isinstance(e, dict) else {}
        crop = norm_crop(e.get('crop'))
        name = f'{safe_name(title)}__{fmt(real_t).replace(":", "-")}' + ('__裁剪' if crop else '') + '.jpg'
        dest = note_dir / 'image' / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        _small_copy(src, dest, crop)
        return _embed(name, e.get('w'))
    return _FIG.sub(repl, summary)


def clip(doc, vault: str, folder: str, teacher=None, series='', teacher_subfolder=True, name_pattern='{title}', boundary='', shot=None,
         force=False):
    """剪藏到 Obsidian。笔记在上次剪藏后被改过、又没有 force 时，不写，返回 {'conflict': True, …} 让用户确认；
    确认后（force）先备份旧笔记再覆盖。"""
    if not vault or not Path(vault).is_dir():
        raise OSError('找不到 Obsidian 库文件夹，请到「设置 → Obsidian」检查库的位置')
    target_dir = Path(vault) / (folder or '')
    if teacher and teacher_subfolder:
        target_dir = target_dir / safe_name(teacher.get('folder') or teacher.get('display') or teacher.get('name') or '')
    previous = (doc.get('clip') or {}).get('path')
    path = None
    if previous and Path(previous).is_file():
        path = Path(previous)  # 同一个视频再次剪藏时更新原来的笔记
    elif doc.get('media'):
        path = find_note(Path(vault), doc['media'], prefer=target_dir if target_dir.is_dir() else Path(vault) / (folder or ''),
                         also=doc.get('media_history') or ())
    if path is None:
        target_dir.mkdir(parents=True, exist_ok=True)
        base = note_name(name_pattern, doc, teacher, series)
        path = target_dir / (base + '.md')
        n = 2
        while path.exists():
            path = target_dir / f'{base} ({n}).md'
            n += 1
    backup = None
    if path.exists() and note_changed(path, doc.get('clip')):
        if not force:
            return {'conflict': True, 'path': str(path),
                    'modified': time.strftime('%Y-%m-%d %H:%M', time.localtime(path.stat().st_mtime))}
        backup = backup_note(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 插图文件名沿用第一次剪藏时的名字：在软件里改了名再剪藏，不会多出一套新名字的图片、旧的没人清
    img_base = (doc.get('clip') or {}).get('img_base') if isinstance(doc.get('clip'), dict) else None
    img_base = img_base or doc.get('title') or '字幕'
    out = dict(doc, summary=place_figures(doc.get('summary') or '', doc.get('frames'), path.parent, img_base, shot, doc.get('fig_edits')),
               _sub_imgs=sub_shot_images(doc, path.parent, img_base, shot))
    text, moved = to_markdown(out, teacher, series, boundary), 0
    if path.exists():  # 更新原来的笔记：保留用户在 Obsidian 里加的小节、标注和属性
        legacy = {x for x in ((teacher or {}).get('name'), (teacher or {}).get('display')) if x}
        text, moved = merge_note(path.read_text(encoding='utf-8', errors='replace'), text,
                                 ((doc.get('clip') or {}).get('tags') if isinstance(doc.get('clip'), dict) else None), legacy)
    path.write_text(text, encoding='utf-8')
    vault_name = Path(vault).name
    rel = path.relative_to(Path(vault)).as_posix()
    from urllib.parse import quote
    return {'path': str(path), 'uri': f'obsidian://open?vault={quote(vault_name)}&file={quote(rel)}',
            'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'hash': note_hash(doc), 'file_hash': note_fingerprint(text),
            'backup': str(backup) if backup else '', 'moved': moved, 'img_base': img_base,
            'tags': _tag_list(dict(_fm_blocks(_split_front(text)[0])).get('tags') or ['tags:'])}
