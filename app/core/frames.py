"""关键帧和画面记录：把视频里有知识信息的画面（课件、板书、老师手绘、K 线标注）找出来，
让能看图的 AI 写成文字「画面记录」，供 AI 校对、课程重点和对话参考；重要的图可以插进剪藏笔记。

流程：
1. 候选：每 2 秒取一张很小的灰度图。按 15 秒分一段，每段挑一张「最像这段的背景」的
   （老师走来走去挡住屏幕的那几秒会被避开）；老师说「大家请看」之类的话时也挑一张；去掉几乎一样的。
2. 筛选：候选图缩小后每 8 张一批交给 AI，只留下有知识信息、而且是同一页里最完整的那张。
3. 画面记录：留下的图用原尺寸交给 AI，照录课件文字、板书，区分课件原有元素和老师现场写画的，
   记下画面上的字幕，并说明这张图值不值得插进笔记。
4. AI辅助理解图（2026-10-15，用户定的规则见知识库视频规范第八节）：要插、但画面本身看不清（模糊、被挡、太小）的，
   让 AI 依据老师的讲解和画面里能看清的部分重画：关系图用 Mermaid 代码，形状图用 SVG。只画老师讲过、画过的，
   拿不准就不画（保留原截图）。结果记在画面的 redraw = {kind: 'mermaid'|'svg', code, note}，剪藏时代替截图（doc.place_figures）。
截图放在缓存里（「缓存/frames」），删了随时可以按时间重新截。"""
import hashlib
import json
import os
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import CACHE_DIR
from .media import MediaError, NO_WINDOW, cache_key, crop_vf, norm_crop, run, tool

W, H = 64, 36
STEP = 2.0            # 每隔几秒取一张小图
WINDOW = 15.0         # 每段几秒挑一张候选
SAME = 2.5            # 两张小图平均差小于这个值（0–255）算重复
MASK_BOTTOM = 0.2     # 比较时不看最下面 20%（烧录字幕一直在变）
CUES = re.compile(r'大家请看|大家看一下|你们看|请看|来看一下|看一下这|看这[里个张幅]|如图|这张图|板书|画一下|写一下|我画')
SCREEN_BATCH = 8
PICK_LIMIT = 8  # 一节课最多留几张可插的图（课程重点最多插 3 张）
REDRAW_LIMIT = 3  # 一节课最多重画几张（和插图上限一样）
DESCRIBE_BATCH = 3
WORKERS = 3


class Cancelled(Exception):
    pass


def frames_dir(path) -> Path:
    return CACHE_DIR / 'frames' / cache_key(path)


def _samples(path, duration, progress=None, cancel=None):
    """每 STEP 秒一张 64×36 灰度小图，返回 [(时间, bytes)]。"""
    args = [tool('ffmpeg'), '-v', 'error', '-i', path, '-an', '-vf', f'fps=1/{STEP},scale={W}:{H},format=gray',
            '-f', 'rawvideo', '-']
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    out, size, k = [], W * H, 0
    try:
        while True:
            if cancel is not None and cancel.is_set():
                proc.kill()
                raise Cancelled()
            buf = proc.stdout.read(size)
            if not buf or len(buf) < size:
                break
            out.append((k * STEP, buf))
            k += 1
            if progress and duration and k % 15 == 0:
                progress(min(1.0, k * STEP / duration))
    finally:
        try:
            proc.stdout.close()
        except OSError:
            pass
        proc.wait()
    return out


_ROWS = int(H * (1 - MASK_BOTTOM))


def _diff(a: bytes, b: bytes) -> float:
    n = W * _ROWS
    return sum(abs(x - y) for x, y in zip(a[:n], b[:n])) / n


def _median(imgs):
    cols = zip(*imgs)
    return bytes(sorted(c)[len(c) // 2] for c in cols)


def candidate_times(samples, segments=None):
    """每 WINDOW 秒挑一张最接近这段「背景」的；加上老师说「请看」的时间；去重。返回 [(时间, 原因)]。"""
    if not samples:
        return []
    per = max(1, int(WINDOW / STEP))
    picked = []
    for i in range(0, len(samples), per):
        group = samples[i:i + per]
        bg = _median([g[1] for g in group])
        best = min(group, key=lambda g: _diff(g[1], bg))
        picked.append((best[0], best[1], '定时'))
    for seg in segments or []:
        if CUES.search(str(seg.get('text') or '')):
            t = float(seg.get('end') or seg.get('start') or 0) + 1.5
            i = min(len(samples) - 1, max(0, int(round(t / STEP))))
            picked.append((samples[i][0], samples[i][1], '老师说请看'))
    picked.sort(key=lambda p: p[0])
    out = []
    for t, img, why in picked:
        if out and (abs(t - out[-1][0]) < STEP * 2 or _diff(img, out[-1][1]) < SAME):
            if why == '老师说请看':
                out[-1] = (t, img, why)
            continue
        out.append((t, img, why))
    return [(round(t, 2), why) for t, _, why in out]


def frame_file(path, t, small=False) -> Path:
    return frames_dir(path) / (f'{int(round(float(t) * 1000)):09d}' + ('_s' if small else '') + '.jpg')


def grab(path, t, small=False, crop=None) -> Path:
    """截一张图（已经截过就直接用）。small=True 时截 640 宽的小图，用来筛选。
    crop：用户在「编辑截图」里框的范围（media.norm_crop），从整张截图裁出来另存一张（同一个缓存文件夹，一起按 30 天清理）。"""
    crop = norm_crop(crop) if crop else None
    if crop:
        src = grab(path, t)
        out = src.with_name(src.stem + '_c' + hashlib.sha1(json.dumps(crop).encode()).hexdigest()[:8] + '.jpg')
        if out.is_file() and out.stat().st_size > 0:
            return out
        tmp = out.with_name(out.stem + '.part.jpg')
        r = run([tool('ffmpeg'), '-y', '-v', 'error', '-i', str(src), '-vf', crop_vf(crop), '-q:v', '3', str(tmp)], timeout=60)
        if r.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
            tmp.unlink(missing_ok=True)
            raise MediaError('裁剪截图失败：' + (r.stderr.strip()[:200] or '未知错误'))
        os.replace(tmp, out)
        return out
    out = frame_file(path, t, small)
    if out.is_file() and out.stat().st_size > 0:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + '.part.jpg')
    vf = ['-vf', 'scale=640:-2'] if small else []
    r = run([tool('ffmpeg'), '-y', '-v', 'error', '-ss', f'{float(t):.3f}', '-i', path, '-frames:v', '1', *vf, '-q:v', '3', str(tmp)],
            timeout=60)
    if r.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise MediaError('截图失败：' + (r.stderr.strip()[:200] or '未知错误'))
    os.replace(tmp, out)
    return out


def _fmt(sec):
    sec = int(max(0, sec))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m:02d}:{s:02d}'


def _subs_between(segments, a, b, limit=1500):
    lines = [f'[{_fmt(s["start"])}] {s["text"]}' for s in segments or [] if a - 1 <= float(s['start']) <= b + 1 and s.get('text')]
    return '\n'.join(lines)[:limit]


def _run_batches(batches, work, cancel, progress, label, lo, hi):
    """并发处理几批，按顺序收集结果。"""
    results = [None] * len(batches)
    done = [0]
    lock = threading.Lock()

    def one(i):
        if cancel is not None and cancel.is_set():
            return
        results[i] = work(batches[i])
        with lock:
            done[0] += 1
            progress(lo + (hi - lo) * done[0] / len(batches), f'{label}… {done[0]}/{len(batches)} 批')
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(one, range(len(batches))))
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    return results


SCREEN_PROMPT = (
    '下面是一节视频课程里按时间顺序截的几张画面（缩小过），每张前面标了编号和时间，后面附了这段时间的字幕。\n'
    '请挑出可能需要插进课程笔记的画面：只有那些光看文字不好理解、看图才明白的——走势图、K 线图和上面的标注、老师手画的曲线和图形、结构关系图。\n'
    '规则：\n'
    '- 纯文字的课件页、标题页、列表、只有老师在讲话的画面，一律不留（这些用文字就能写清楚）；\n'
    '- 同一张图只留画得最完整、被人挡得最少的一张；后面的比前面画得更多，留后面的；\n'
    '- 宁可少留，一节课一般只有几张。\n'
    '只输出 JSON：{"保留": [{"图": 编号, "原因": "一句话，比如：老师手画的上升趋势曲线"}]}；一张都不需要就输出 {"保留": []}。'
)

DESCRIBE_PROMPT = (
    '下面是一节视频课程的几张画面（原尺寸），每张前面标了编号和时间，后面附了这段时间老师说的话（语音识别字幕，可能有错字）。\n'
    '课程笔记只插必要的图，帮助理解文字说不清的地方。请对每张：\n'
    '- 「插图」：只有文字确实说不清的（手绘曲线的形状、K 线上标注的具体位置、复杂的结构关系图）才写 true；纯文字课件、列表、简单流程写 false；\n'
    '- 「说明」：一句话说清图上画的是什么，40 字以内，方便放到笔记对应的位置（例如：老师手画的下跌趋势，标出峰和级的位置）；只写看得见的，不解释含义、不补充知识，不要照抄课件上的文字；\n'
    '- 「看不清」：只有「插图」为 true、但画面本身看不清（模糊、被挡住、太小、反光），直接截图放进笔记也看不明白时写 true，否则写 false。\n'
    '只输出 JSON：{"画面": [{"图": 编号, "说明": "……", "插图": true 或 false, "看不清": true 或 false}]}'
)

REDRAW_PROMPT = (
    '下面是一节视频课程里的一张画面（原尺寸），以及这张画面前后老师说的话（语音识别字幕，可能有错字）。'
    '这张画面对理解课程很重要，但画面本身看不清（模糊、被挡住或太小），直接截图放进笔记看不明白。\n'
    '请依据老师的讲解和画面里能看清的部分，重画一张清楚的示意图放进课程笔记，笔记里会标注为「AI辅助理解图（非原画面）」。规则：\n'
    '- 只画老师讲过、画过的内容：可以重画画面里已有的结构，把明确的顺序、层级、位置画清楚；'
    '不得添加老师没讲的规则、条件、因果关系，不把推测画成确定结论；图上的字用老师的说法。\n'
    '- 结构、流程、层级、分类这类关系图，用 Mermaid 代码（flowchart）；走势形状、K 线示意、曲线、位置标注这类形状图，'
    '用 SVG 代码（宽不超过 800，白色背景，线条清楚，文字用中文；不要引用外部图片、字体和链接，不要写脚本）。\n'
    '- 拿不准画面上是什么、或者老师没讲清楚的，不要画，写「画」为 false 和原因（笔记里会继续用原截图）。\n'
    '只输出 JSON：{"画": true, "类型": "mermaid" 或 "svg", "代码": "……", "说明": "一句话说明画的是什么"}；'
    '不画时输出 {"画": false, "原因": "……"}'
)


PICK_PROMPT = (
    '下面是 AI 从一节课里挑出的可插图画面：每张有编号、时间、一句话说明，以及那段时间老师说的话。'
    '课程笔记整节课最多插 3 张图，只插文字确实说不清的地方。请从中选出最值得插的，不超过 {limit} 张：\n'
    '- 同一张图只留一张：说明几乎一样、或者讲的是同一件事的，留画得最完整的那张（通常是后面那张）；说明里写了被老师挡住的，换成同一张图里没被挡住的；\n'
    '- 优先留老师重点讲解、文字最难说清的（手绘曲线的形状、K 线上标注的位置、结构关系）；\n'
    '- 宁可少留。\n'
    '只输出 JSON：{{"保留": [编号, …]}}'
)


def pick(settings, name, frames, segments, limit=PICK_LIMIT):
    """可插的图太多时，让 AI 按说明去重、只留最必要的几张（只看文字，不再传图）。"""
    from . import llm
    if len(frames) <= limit:
        return frames
    lines = []
    for k, f in enumerate(frames, 1):
        said = _subs_between(segments, f['t'] - 20, f['t'] + 10, 300).replace('\n', ' ')
        lines.append(f'{k}. [{_fmt(f["t"])}] {f.get("note") or ""}\n   老师说：{said}')
    msg = PICK_PROMPT.format(limit=limit) + '\n\n' + '\n'.join(lines)
    for _ in range(2):
        try:
            data = _json(llm.complete(settings, name, [{'role': 'user', 'content': msg}], json_mode=True))
            keep = {int(x) for x in (data.get('保留') or [])}
            out = [f for k, f in enumerate(frames, 1) if k in keep][:limit]
            if out:
                return out
        except (ValueError, TypeError, AttributeError):
            continue
    return frames[:limit]


def _json(raw):
    raw = re.sub(r'^\s*```(?:json)?|```\s*$', '', (raw or '').strip()).strip()
    i, j = raw.find('{'), raw.rfind('}')
    return json.loads(raw[i:j + 1] if i >= 0 and j > i else raw)


def screen(settings, name, path, cands, segments, cancel=None, progress=lambda f, m=None: None, lo=0.0, hi=1.0):
    """让 AI 从候选里挑出有知识信息的画面。返回 [{t, why}]。"""
    from . import llm
    batches = [cands[i:i + SCREEN_BATCH] for i in range(0, len(cands), SCREEN_BATCH)]

    def work(batch):
        parts = [{'type': 'text', 'text': SCREEN_PROMPT}]
        for k, (t, _why) in enumerate(batch, 1):
            parts.append({'type': 'text', 'text': f'\n图{k}（{_fmt(t)}）'})
            parts.append({'type': 'image', 'path': str(grab(path, t, small=True))})
        parts.append({'type': 'text', 'text': '\n这段时间的字幕：\n' + _subs_between(segments, batch[0][0] - 10, batch[-1][0] + 10)})
        for _ in range(2):
            try:
                data = _json(llm.complete(settings, name, [{'role': 'user', 'content': parts}], json_mode=True))
                keep = data.get('保留', []) if isinstance(data, dict) else []
                out = []
                for x in keep:
                    k = int(x.get('图', 0)) if isinstance(x, dict) else 0
                    if 1 <= k <= len(batch):
                        out.append({'t': batch[k - 1][0], 'why': str(x.get('原因') or '').strip()[:80]})
                return out
            except (ValueError, TypeError, AttributeError):
                continue
        raise llm.LlmError('画面筛选结果格式不对，请重试或换一个 AI 服务')
    results = _run_batches(batches, work, cancel, progress, 'AI 正在挑选有内容的画面', lo, hi)
    seen, out = set(), []
    for r in results:
        for f in r or []:
            if f['t'] not in seen:
                seen.add(f['t'])
                out.append(f)
    return sorted(out, key=lambda f: f['t'])


def describe(settings, name, path, frames, segments, cancel=None, progress=lambda f, m=None: None, lo=0.0, hi=1.0, on_part=None):
    """为每张画面写画面记录。frames: [{t, …}]，返回同样的列表，加上 note、caption、figure。"""
    from . import llm
    batches = [frames[i:i + DESCRIBE_BATCH] for i in range(0, len(frames), DESCRIBE_BATCH)]

    def work(batch):
        parts = [{'type': 'text', 'text': DESCRIBE_PROMPT}]
        for k, f in enumerate(batch, 1):
            parts.append({'type': 'text', 'text': f'\n图{k}（{_fmt(f["t"])}）'})
            parts.append({'type': 'image', 'path': str(grab(path, f['t']))})
            parts.append({'type': 'text', 'text': '这段时间的字幕：\n' + _subs_between(segments, f['t'] - 25, f['t'] + 15, 800)})
        for _ in range(2):
            try:
                data = _json(llm.complete(settings, name, [{'role': 'user', 'content': parts}], json_mode=True))
                items = data.get('画面', []) if isinstance(data, dict) else []
                out = [dict(f) for f in batch]
                for x in items:
                    k = int(x.get('图', 0)) if isinstance(x, dict) else 0
                    if 1 <= k <= len(batch):
                        out[k - 1].update(note=str(x.get('说明') or x.get('记录') or '').strip()[:120], caption='',
                                          figure=bool(x.get('插图')) or bool(out[k - 1].get('manual')))  # 手动截的是用户挑的，默认可插
                        out[k - 1]['unclear'] = bool(x.get('看不清')) and out[k - 1]['figure']
                if on_part:
                    on_part(out)
                return out
            except (ValueError, TypeError, AttributeError):
                continue
        raise llm.LlmError('插图说明结果格式不对，请重试或换一个 AI 服务')
    results = _run_batches(batches, work, cancel, progress, 'AI 正在判断哪些图需要插', lo, hi)
    return [f for r in results for f in (r or [])]


def extract(settings, name, path, duration, segments, cancel=None, progress=lambda f, m=None: None, on_part=None):
    """完整流程：候选 → AI 挑可能要插的图 → 再确认、写一句说明，只留确实需要插的。
    name 为空时只截候选图（没有能看图的 AI）。返回 [{t, why, note, figure}]。"""
    progress(0.0, '正在分析画面…')
    samples = _samples(path, duration, lambda f: progress(0.15 * f, '正在分析画面…'), cancel)
    if not samples:
        raise MediaError('读不出画面，这个文件可能没有视频')
    cands = candidate_times(samples, segments)
    for n, (t, _) in enumerate(cands, 1):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        grab(path, t, small=True)
        if n % 5 == 0:
            progress(0.15 + 0.15 * n / len(cands), f'正在截候选画面… {n}/{len(cands)}')
    if not name:
        return [{'t': t, 'why': why} for t, why in cands]
    chosen = screen(settings, name, path, cands, segments, cancel, progress, 0.3, 0.6)
    if not chosen:
        return []
    kept = [f for f in describe(settings, name, path, chosen, segments, cancel, progress, 0.6, 0.93, on_part) if f.get('figure')]
    progress(0.94, '正在去掉重复的图…')
    kept = pick(settings, name, kept, segments)
    todo = [f for f in kept if f.get('unclear')][:REDRAW_LIMIT]
    if not todo:
        return kept
    done = {f['t']: f for f in redraw(settings, name, path, todo, segments, cancel, progress, 0.95, 1.0)}
    return [done.get(f['t'], f) for f in kept]


def clean_mermaid(code):
    """AI 给的 Mermaid 代码：去掉 ``` 围栏，只认常见的图类型；不像的返回空。"""
    s = re.sub(r'^\s*```(?:mermaid)?\s*|\s*```\s*$', '', str(code or '').strip()).strip()
    if not s or '```' in s or len(s) > 6000:
        return ''
    head = s.split('\n', 1)[0].strip().lower()
    kinds = ('flowchart', 'graph', 'sequencediagram', 'classdiagram', 'statediagram', 'mindmap', 'timeline', 'quadrantchart', 'block-beta')
    return s if head.startswith(kinds) else ''


def _one_redraw(x):
    """AI 的回答 → {kind, code, note}；不画或代码不对返回 None。"""
    from .doc import sanitize_svg
    if not isinstance(x, dict) or not x.get('画'):
        return None
    kind = str(x.get('类型') or '').strip().lower()
    code = clean_mermaid(x.get('代码')) if kind == 'mermaid' else sanitize_svg(x.get('代码')) if kind == 'svg' else ''
    return {'kind': kind, 'code': code, 'note': str(x.get('说明') or '').strip()[:80]} if code else None


def redraw(settings, name, path, frames, segments, cancel=None, progress=lambda f, m=None: None, lo=0.0, hi=1.0, on_part=None):
    """画面看不清的，让 AI 依据讲解和画面重画（AI辅助理解图）。返回同样的列表：画了的加上 redraw，
    AI 说不画（拿不准）的不加，剪藏时继续用原截图。"""
    from . import llm

    def work(batch):
        f = batch[0]
        parts = [{'type': 'text', 'text': REDRAW_PROMPT},
                 {'type': 'text', 'text': f'\n画面（{_fmt(f["t"])}）' + (f'，AI 之前写的说明：{f["note"]}' if f.get('note') else '')},
                 {'type': 'image', 'path': str(grab(path, f['t']))},
                 {'type': 'text', 'text': '这张画面前后老师说的话：\n' + _subs_between(segments, f['t'] - 40, f['t'] + 40, 2000)}]
        out = dict(f)
        for _ in range(2):
            try:
                data = _json(llm.complete(settings, name, [{'role': 'user', 'content': parts}], json_mode=True))
            except (ValueError, TypeError, AttributeError):
                continue
            got = _one_redraw(data)
            if got:
                out['redraw'] = got
            break
        if on_part:
            on_part([out])
        return [out]
    results = _run_batches([[f] for f in frames], work, cancel, progress, 'AI 正在重画看不清的画面', lo, hi)
    return [f for r in results for f in (r or [])]


RECHECK_PROMPT = (
    '下面几句字幕是 AI 校对时拿不准的「存疑句」（语音识别可能听错了）。每句附了：前后文、存疑原因、这句话说话时截的画面，'
    '以及附近的画面记录（课件文字、板书、画面字幕）。\n'
    '请看画面核对：画面底部常有烧录字幕，课件和板书上也可能有这个词。\n'
    '- 画面能确认这句该怎么写的：给出改正后的整句（只改识别错的字词，不改写、不润色、不删口语）；\n'
    '- 画面也确认原句没错的：「改为」写原句；\n'
    '- 画面看不清、或者画面里没有相关内容的：不改，写明还存疑的原因。\n'
    '只输出 JSON：{"结果": [{"编号": 编号, "改为": "整句（确定时才写）", "仍存疑": "原因（不确定时才写）"}]}'
)


def recheck_doubts(settings, name, path, items, frames_list, cancel=None, progress=lambda f, m=None: None, on_part=None):
    """用画面复核存疑句。items: [{i, start, end, text, reason, before, after}]。
    返回 [{i, text}（画面确认后的句子）或 {i, doubt}（仍存疑）]。"""
    from . import llm
    batches = [items[k:k + 3] for k in range(0, len(items), 3)]

    def moments(it):
        """这句话说话时截几张：画面字幕每几秒换一次，长句子每隔约 3 秒截一张，最多 4 张。"""
        a = float(it['start'])
        b = float(it.get('end') or a + 2)
        n = max(1, min(4, int((b - a) / 3) + 1))
        return [round(a + (b - a) * (k + 0.5) / n, 2) for k in range(n)]

    def work(batch):
        parts = [{'type': 'text', 'text': RECHECK_PROMPT}]
        for k, it in enumerate(batch, 1):
            near = notes_text(frames_list, float(it['start']) - 30, float(it['start']) + 30, 3000)
            ts = moments(it)
            parts.append({'type': 'text', 'text': f'\n【编号 {k}｜{_fmt(it["start"])}】\n前文：{it.get("before", "")}\n'
                          f'存疑句：{it["text"]}\n后文：{it.get("after", "")}\n存疑原因：{it.get("reason", "")}\n'
                          + (f'附近的画面记录：\n{near}\n' if near else '') + f'这句话说话时的画面（按时间顺序 {len(ts)} 张）：'})
            for t in ts:
                parts.append({'type': 'image', 'path': str(grab(path, t))})
        for _ in range(2):
            try:
                data = _json(llm.complete(settings, name, [{'role': 'user', 'content': parts}], json_mode=True))
                out = []
                for x in (data.get('结果', []) if isinstance(data, dict) else []):
                    k = int(x.get('编号', 0)) if isinstance(x, dict) else 0
                    if not 1 <= k <= len(batch):
                        continue
                    it = batch[k - 1]
                    new = str(x.get('改为') or '').strip()
                    if new and not str(x.get('仍存疑') or '').strip():
                        out.append({'i': it['i'], 'text': new})
                    else:
                        out.append({'i': it['i'], 'doubt': str(x.get('仍存疑') or it.get('reason') or '').strip()[:200]})
                if on_part:
                    on_part(out)
                return out
            except (ValueError, TypeError, AttributeError):
                continue
        raise llm.LlmError('画面复核结果格式不对，请重试或换一个 AI 服务')
    results = _run_batches(batches, work, cancel, progress, 'AI 正在用画面复核存疑句', 0.0, 1.0)
    return [x for r in results for x in (r or [])]


def nearest(frames, t, within=3.0):
    best = min(frames or [], key=lambda f: abs(float(f.get('t', 0)) - t), default=None)
    return best if best is not None and abs(float(best.get('t', 0)) - t) <= within else None


def notes_text(frames, a=None, b=None, limit=20000):
    """画面记录写成文字，给 AI 参考。a/b 限定时间范围。"""
    lines = []
    for f in sorted(frames or [], key=lambda f: float(f.get('t', 0))):
        t = float(f.get('t', 0))
        if (a is not None and t < a) or (b is not None and t > b) or not (f.get('note') or f.get('caption')):
            continue
        tag = '（可插图，原画面看不清，已有 AI辅助理解图）' if f.get('figure') and f.get('redraw') else '（可插图）' if f.get('figure') else ''
        line = f'[{_fmt(t)}]' + tag + ' ' + (f.get('note') or '').replace('\n', ' ')
        if f.get('caption'):
            line += f'　画面字幕：「{f["caption"]}」'
        lines.append(line)
    return '\n'.join(lines)[:limit]
