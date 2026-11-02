"""文档和书籍的来源级整理（对应知识库《外部书籍与文档整理规范》）：
- 支持原生 PDF、OCR 过的扫描 PDF、Word（.docx / 老版 .doc）、电子书（EPUB、MOBI、AZW3）、演示文稿（PPTX / PPT）、TXT / Markdown（新格式的读取在 ebook.py）；
- 文字层负责读正文，原页面负责看图表：有图、表、公式的页面截成图片一起交给能看图的 AI；
- 以书上印的页码（P42）作为回溯锚点；书上没有页码时写「PDF 第 X 页」；
- 一章一章整理：章节 MD 按规范的固定栏目；最后生成「00、全书总览」；
- 原书图示非必要不用，每章最多 2 张，按 AI 给的位置裁剪、压缩后放进笔记旁边的 image 文件夹。
整理结果存在原文件旁边的「文件名.猛人学习.json」里，剪藏时写成 Obsidian 笔记。"""
import ctypes
import os
import re
import threading
import time
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from . import ebook
from .config import CACHE_DIR
from .media import cache_key

# 媒体库里列出的文档：微信读书能导入的格式（TXT 除外：课程文件夹里的 txt 一般是说明文件），加上 PPT。txt / md 用「打开」
LIB_EXT = {'.pdf', '.docx', '.doc', '.epub', '.mobi', '.azw3', '.azw', '.pptx', '.ppt'}
DOC_EXT = LIB_EXT | {'.txt', '.md'}
KIND_NAMES = {'pdf': 'PDF', 'docx': 'Word 文档', 'doc': 'Word 文档（老版 .doc）', 'epub': '电子书（EPUB）',
              'mobi': 'Kindle 电子书（MOBI / AZW3）', 'pptx': '演示文稿（PPTX）', 'ppt': '演示文稿（老版 .ppt）', 'text': '文本文档'}
PART_PAGES = 18                       # 一次交给 AI 的最多页数
PART_CHARS = 36000                    # 一次交给 AI 的最多字数
PART_IMAGES = 10                      # 一次交给 AI 的最多页面图
PSEUDO_PAGE = 1800                    # Word / 文本按多少字分「页」
FIG_WORDS = re.compile(r'图\s*[\d一二三四五六七八九十]|表\s*[\d一二三四五六七八九十]|如图|如表|见图|见表|Figure|Fig\.|Table|公式|示意')
HEADER_GAP = 8  # 同一个「第X部分 / 第X章」隔这么几页之内又出现，算页眉（2026-10-29《研究是一门艺术》每两页印一次部名，被分成 121 章）
CHAPTER_RE = re.compile(r'^\s*(第[一二三四五六七八九十百零〇两\d]+[章篇部讲]|Chapter\s+\d+|CHAPTER\s+\d+)[\s:：、.．]*\S*')

_cache = {}
_lock = threading.Lock()
_pdf_lock = threading.RLock()  # pdfium 不能几个线程同时用（2026-10-29 朗读原文时同时取几页的图，全都「Failed to load page」）


class BookError(Exception):
    pass


# ------------------------------------------------------------------ 读取 ----
def _clean(text):
    text = (text or '').replace('\r\n', '\n').replace('\r', '\n').replace('\x00', '')
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def _label_of(text):
    """页面最上或最下单独一行的数字，当作书上印的页码。"""
    lines = [x.strip() for x in text.split('\n') if x.strip()]
    for line in (lines[-1:] + lines[:1]) if lines else []:
        m = re.fullmatch(r'[-—–·\s]*(\d{1,4})[-—–·\s]*', line)
        if m:
            return int(m.group(1))
    return None


def _read_pdf(path):
    import pypdfium2 as pdfium
    import pypdfium2.raw as c
    pdf = pdfium.PdfDocument(path)
    pages, toc = [], []
    try:
        for i in range(len(pdf)):
            page = pdf[i]
            try:
                tp = page.get_textpage()
                text = _clean(tp.get_text_range())
                tp.close()
                w, h = page.get_size()
                n_img = n_path = 0
                scan = False
                # 直接用底层函数数图片和线条：不生成页面对象的包装，免得它们在页面关闭后才被回收，导致程序崩溃
                for k in range(c.FPDFPage_CountObjects(page.raw)):
                    obj = c.FPDFPage_GetObject(page.raw, k)
                    kind = c.FPDFPageObj_GetType(obj)
                    if kind == c.FPDF_PAGEOBJ_IMAGE:
                        n_img += 1
                        l, b, r, t = ctypes.c_float(), ctypes.c_float(), ctypes.c_float(), ctypes.c_float()
                        if c.FPDFPageObj_GetBounds(obj, l, b, r, t) and (r.value - l.value) * (t.value - b.value) > 0.6 * w * h:
                            scan = True
                    elif kind == c.FPDF_PAGEOBJ_PATH:
                        n_path += 1
                label = None
                try:
                    pl = pdf.get_page_label(i)
                    label = int(pl) if pl and str(pl).isdigit() else None
                except Exception:
                    label = None
                pages.append({'n': i + 1, 'text': text, 'label': label or _label_of(text), 'scan': scan,
                              'figure': bool(FIG_WORDS.search(text)) if scan else (n_img > 0 or n_path >= 3)})
            finally:
                page.close()
        try:
            for item in pdf.get_toc(max_depth=3):
                if item.page_index is not None and item.title:
                    toc.append({'level': item.level, 'title': item.title.strip(), 'n': item.page_index + 1})
        except Exception:
            toc = []
        try:
            meta = pdf.get_metadata_dict()
        except Exception:
            meta = {}
    finally:
        pdf.close()
    return {'kind': 'pdf', 'pages': pages, 'toc': toc, 'title': (meta.get('Title') or '').strip(), 'author': (meta.get('Author') or '').strip()}


def _pseudo_pages(blocks):
    """把 [(文字, 是否标题, 标题级别, [图片])] 按 PSEUDO_PAGE 字切成「页」，标题尽量放在页首。"""
    pages, toc, cur, imgs = [], [], [], []
    size = 0

    def flush():
        nonlocal cur, imgs, size
        if cur or imgs:
            text = '\n'.join(cur)
            pages.append({'n': len(pages) + 1, 'text': text, 'label': None, 'scan': False, 'figure': bool(imgs) or bool(FIG_WORDS.search(text)), 'images': imgs})
        cur, imgs, size = [], [], 0
    for text, heading, level, pics in blocks:
        if heading and level == 1 and cur:  # 每章从新的一「页」开始，章节才分得开
            flush()
        if heading:
            toc.append({'level': level, 'title': text, 'n': len(pages) + 1})
        cur.append(text)
        imgs += pics
        size += len(text)
        if size >= PSEUDO_PAGE:
            flush()
    flush()
    return pages, toc


W_NS = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
R_NS = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
A_NS = '{http://schemas.openxmlformats.org/drawingml/2006/main}'


def _read_docx(path):
    out_dir = CACHE_DIR / 'pages' / cache_key(path)
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read('word/document.xml'))
        rels = {}
        if 'word/_rels/document.xml.rels' in z.namelist():
            for r in ET.fromstring(z.read('word/_rels/document.xml.rels')):
                rels[r.get('Id')] = r.get('Target')
        core = {}
        if 'docProps/core.xml' in z.namelist():
            for el in ET.fromstring(z.read('docProps/core.xml')):
                core[el.tag.split('}')[-1]] = (el.text or '').strip()
        body = root.find(W_NS + 'body')
        blocks, k = [], 0
        for el in list(body) if body is not None else []:
            tag = el.tag.split('}')[-1]
            if tag == 'p':
                style = el.find(f'{W_NS}pPr/{W_NS}pStyle')
                sval = (style.get(W_NS + 'val') if style is not None else '') or ''
                m = re.search(r'(?:Heading|heading|标题)\s*(\d)', sval) or (re.fullmatch(r'(\d)', sval) if sval else None)
                level = int(m.group(1)) if m else 0
                text = ''.join(t.text or '' for t in el.iter(W_NS + 't')).strip()
                pics = []
                for blip in el.iter(A_NS + 'blip'):
                    target = rels.get(blip.get(R_NS + 'embed'))
                    if target:
                        name = 'word/' + target.lstrip('/') if not target.startswith('word/') else target
                        try:
                            data = z.read(name)
                        except KeyError:
                            continue
                        k += 1
                        dest = out_dir / f'docx_{k:03d}{Path(target).suffix.lower() or ".png"}'
                        dest.write_bytes(data)
                        pics.append(str(dest))
                if pics:
                    text = (text + ' ' if text else '') + ' '.join(f'［图片{n}］' for n in range(k - len(pics) + 1, k + 1))
                if text:
                    if not level and CHAPTER_RE.match(text) and len(text) < 40:
                        level = 1
                    blocks.append((text, bool(level), level, pics))
            elif tag == 'tbl':
                rows = []
                for tr in el.iter(W_NS + 'tr'):
                    cells = [''.join(t.text or '' for t in tc.iter(W_NS + 't')).strip() for tc in tr.iter(W_NS + 'tc')]
                    rows.append('| ' + ' | '.join(cells) + ' |')
                if rows:
                    rows.insert(1, '|' + ' --- |' * (rows[0].count('|') - 1))
                    blocks.append(('\n'.join(rows), False, 0, []))
    pages, toc = _pseudo_pages(blocks)
    return {'kind': 'docx', 'pages': pages, 'toc': toc, 'title': core.get('title', ''), 'author': core.get('creator', '')}


def _read_text(path):
    raw = Path(path).read_bytes()
    for enc in ('utf-8-sig', 'gb18030'):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise BookError('读不出这个文件的文字，请用 UTF-8 编码保存后再试')
    blocks = []
    for para in re.split(r'\n\s*\n|\n(?=#)', text.replace('\r\n', '\n')):
        para = para.strip()
        if not para:
            continue
        m = re.match(r'^(#{1,3})\s+(.+)$', para.split('\n')[0])
        if m:
            blocks.append((m.group(2).strip(), True, len(m.group(1)), []))
            rest = '\n'.join(para.split('\n')[1:]).strip()
            if rest:
                blocks.append((rest, False, 0, []))
        elif CHAPTER_RE.match(para) and len(para.split('\n')[0]) < 40:
            first, _, rest = para.partition('\n')
            blocks.append((first.strip(), True, 1, []))
            if rest.strip():
                blocks.append((rest.strip(), False, 0, []))
        else:
            blocks.append((para, False, 0, []))
    pages, toc = _pseudo_pages(blocks)
    return {'kind': 'text', 'pages': pages, 'toc': toc, 'title': '', 'author': ''}


def _read_ebook(path, ext):
    """电子书、老版 Word：按块切成「页」；演示文稿：一张幻灯片一「页」。"""
    img_dir = CACHE_DIR / 'pages' / cache_key(path)
    if ext in ('.pptx', '.ppt'):
        slides, meta = ebook.read_pptx(path, img_dir) if ext == '.pptx' else ebook.read_ppt(path)
        pages, toc = [], []
        for k, (title, body, pics) in enumerate(slides, 1):
            text = (f'【第 {k} 张幻灯片】' + (title or '')).strip() + ('\n' + body if body else '')
            if title and CHAPTER_RE.match(title) and len(title) < 40:
                toc.append({'level': 1, 'title': title, 'n': k})
            pages.append({'n': k, 'text': text, 'label': None, 'scan': False, 'images': pics,
                          'figure': bool(pics) or bool(FIG_WORDS.search(text))})
        if not pages:
            raise BookError('这个演示文稿里没有读到文字')
        return {'kind': ext[1:], 'pages': pages, 'toc': toc, 'title': meta['title'], 'author': meta['author']}
    if ext == '.epub':
        blocks, meta = ebook.read_epub(path, img_dir)
        kind = 'epub'
    elif ext == '.doc':
        blocks, meta = ebook.read_doc(path)
        kind = 'doc'
    else:
        blocks, meta = ebook.read_mobi(path, img_dir)
        kind = 'mobi'
    if not any(b[0].strip() for b in blocks):
        raise BookError('这个文档里没有读到文字（可能是扫描成图片的电子书）')
    top = min((b[2] for b in blocks if b[1]), default=1)
    if top > 1:  # 章名用的是 h2 / h3（Kindle 书常见）：最高一级当章，才分得开
        blocks = [(t, h, lv - top + 1 if h else lv, p) for t, h, lv, p in blocks]
    pages, toc = _pseudo_pages(blocks)
    return {'kind': kind, 'pages': pages, 'toc': toc, 'title': meta['title'], 'author': meta['author']}


def read(path):
    """读取文档（按文件修改时间缓存）。返回 {kind, pages:[{n, text, label, scan, figure}], toc, title, author, shift}。"""
    path = os.path.abspath(path)
    try:
        key = (path.lower(), os.path.getmtime(path), os.path.getsize(path))
    except OSError:
        raise BookError('找不到这个文件：' + path)
    with _lock:
        if key in _cache:
            return _cache[key]
    ext = Path(path).suffix.lower()
    try:
        if ext == '.pdf':
            with _pdf_lock:
                info = _read_pdf(path)
        elif ext == '.docx':
            info = _read_docx(path)
        elif ext in ('.txt', '.md'):
            info = _read_text(path)
        elif ext in ('.epub', '.mobi', '.azw3', '.azw', '.doc', '.pptx', '.ppt'):
            info = _read_ebook(path, ext)
        else:
            raise BookError('不支持这种文档。支持 PDF、Word（.docx / .doc）、电子书（EPUB、MOBI、AZW3）、演示文稿（PPTX / PPT）、TXT 和 Markdown')
    except BookError:
        raise
    except ebook.EbookError as e:
        raise BookError(str(e))
    except Exception as e:
        raise BookError(f'读不出这个文档：{e}')
    info['shift'] = detect_shift(info['pages'])
    info['companion'] = companion_docx(path) if ext == '.pdf' else None
    info['scan'] = sum(1 for p in info['pages'] if p['scan']) > len(info['pages']) / 2
    info['chars'] = sum(len(p['text']) for p in info['pages'])
    with _lock:
        _cache.clear()
        _cache[key] = info
    return info


def companion_docx(pdf_path):
    """PDF 旁边同名的 Word（WPS 做 OCR 后导出的），文字一般比 PDF 文字层准。"""
    p = Path(pdf_path)
    for cand in (p.with_suffix('.docx'), p.with_name(re.sub(r'[（(]\s*OCR\s*[)）]', '', p.stem).strip() + '.docx')):
        if cand.is_file():
            return str(cand)
    return None


_CJK_SPACE = re.compile(r'(?<=[一-鿿，。、；：“”（）《》？！])[ \t　]+(?=[一-鿿，。、；：“”（）《》？！])')


def companion_text(info):
    """同名 Word 的全文（去掉 OCR 在汉字之间加的空格）。"""
    path = info.get('companion')
    if not path:
        return ''
    try:
        d = read(path)
    except BookError:
        return ''
    text = '\n'.join(p['text'] for p in d['pages'])
    text = re.sub(r'HYPERLINK\s+\\l\s+"[^"]*"\s*', '', text.replace('&quot;', '"'))
    return _CJK_SPACE.sub('', text)


def _norm(s):
    return re.sub(r'[\s\W_]+', '', s or '')


def companion_slice(info, chapters, idx, limit=40000):
    """按章名在 Word 全文里找到这一章（找不到返回空）。跳过目录：用最后一次出现的章名（正文在目录后面）。"""
    text = companion_text(info)
    if not text:
        return ''
    flat, pos = [], []
    for k, ch in enumerate(text):
        if not ch.isspace():
            flat.append(ch)
            pos.append(k)
    flat = ''.join(flat)

    def find(title):
        core = re.sub(r'^(第[一二三四五六七八九十百零〇两\d]+[章篇部讲])', '', _norm(title)) or _norm(title)
        core = core[:14]
        if len(core) < 4:
            return -1
        hits = [m.start() for m in re.finditer(re.escape(core), flat)]
        if not hits:
            return -1
        # 目录里的章名后面紧跟页码，正文里的不是；取第一个不在目录里的
        for h in hits:
            tail = flat[h + len(core):h + len(core) + 6]
            if not re.match(r'^\(?\d', tail):
                return pos[h]
        return pos[hits[-1]]
    a = find(chapters[idx]['title'])
    if a < 0:
        return ''
    b = len(text)
    for nxt in chapters[idx + 1:]:
        k = find(nxt['title'])
        if k > a:
            b = k
            break
    a = text.rfind('\n', 0, a) + 1  # 从章名所在行的开头算起（把「第一章」也带上）
    if b < len(text):
        b = text.rfind('\n', a, b) + 1 or b
    return text[a:b].strip()[:limit]


def detect_shift(pages):
    """书上页码和 PDF 页序的差（PDF 第 n 页 = 书上第 n - shift 页）。至少两页对得上才算。"""
    votes = Counter(p['n'] - p['label'] for p in pages if p.get('label'))
    if not votes:
        return None
    shift, count = votes.most_common(1)[0]
    return shift if count >= 2 or (count >= 1 and len(pages) <= 3) else None


def page_ref(n, shift):
    """回溯锚点：书上有页码时写 P42，没有时写「PDF 第 X 页」。"""
    if shift is not None and n - shift >= 1:
        return f'P{n - shift}'
    return f'PDF 第 {n} 页'


def range_ref(a, b, shift):
    if shift is not None and a - shift >= 1:
        return f'P{a - shift}' + (f'-P{b - shift}' if b != a else '')
    return f'PDF 第 {a}' + (f'-{b}' if b != a else '') + ' 页'


# ------------------------------------------------------------------ 章节 ----
def detect_chapters(info):
    """章节：PDF 书签 / Word 标题 / 文本里的「第X章」；都没有时按页数分段。返回 [{title, start, end}]（PDF 页序，含首尾）。"""
    pages = info['pages']
    total = len(pages)
    if not total:
        return []
    toc = info.get('toc') or []
    starts = toc_entries(toc) if toc else []
    if len(starts) < 2:
        starts = []
        last = {}  # 「第二部」「第3章」上次在第几页出现
        for p in pages:
            lines = [x.strip() for x in p['text'].split('\n') if x.strip()][:3]
            for k, line in enumerate(lines):
                m = CHAPTER_RE.match(line)
                if not (m and len(line) < 40 and '…' not in line and '...' not in line and not re.search(r'\d+\s*$', line)):
                    continue
                key = re.sub(r'\s+', '', m.group(1)).lower()
                seen_at, last[key] = last.get(key), p['n']
                if seen_at is not None and p['n'] - seen_at <= HEADER_GAP:
                    continue  # 几页之内又出现：是每页顶上的页眉（书名、部名），不是新的一章；接着看这页下一行
                if len(line) <= len(m.group(1).strip()) + 1 and k + 1 < len(lines) and len(lines[k + 1]) < 30 and not CHAPTER_RE.match(lines[k + 1]):
                    line += ' ' + lines[k + 1]  # 「第一部分」单独一行、名字在下一行
                starts.append((line, p['n'], ''))
                break
    out = chapters_from(starts, pages)
    if len(out) - (out[0]['title'] == '前言与目录' if out else 0) < 2:  # 只认出一个「章」多半是认错了（比如 OCR 把章名拆成两行），按页数分段，让用户用「AI 读目录分章」
        size = 30 if total > 40 else total
        return [{'title': (f'第 {a}–{min(total, a + size - 1)} 页' if total > size else '全文'), 'start': a, 'end': min(total, a + size - 1)}
                for a in range(1, total + 1, size)]
    return out


PART_RE = re.compile(r'^\s*(第\s*[一二三四五六七八九十百零〇两\d]+\s*(部分|部|篇|编|卷)|卷\s*[一二三四五六七八九十\d]+|[上中下]\s*[编篇卷部]|part\s+([ivxlc]+|\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b)', re.I)
CHAP_ONLY_RE = re.compile(r'^\s*(第\s*[一二三四五六七八九十百零〇两\d]+\s*章|chapter\s+\w+)', re.I)


def toc_entries(toc):
    """书自带的目录（PDF 书签、EPUB 目录、Word 标题）→ [(标题, 起始页, 所属部分)]，和 AI 读目录分章同一套规则（2026-10-29）：
    最上一层是「部分」（第一部分、第一篇、上编、Part 1……，或者下一层是「第X章」而它自己不是）时，按部分分组、下一层每一项单独成篇；
    否则最上一层就是章（下一层是节，不拆开）。以前只取最上一层，最上一层是部分的书会只分出几个部分。"""
    lv = sorted({x['level'] for x in toc})
    if sum(1 for x in toc if x['level'] == lv[0]) < 2 and len(lv) > 1:  # 只有一个顶层（书名）时从下一级算起
        toc, lv = [x for x in toc if x['level'] != lv[0]], lv[1:]
    top, nxt = lv[0], (lv[1] if len(lv) > 1 else None)
    groups = []  # [顶层, [下一层…]]
    for x in toc:
        if x['level'] == top:
            groups.append((x, []))
        elif x['level'] == nxt and groups:
            groups[-1][1].append(x)
    clean = lambda s: re.sub(r'\s+', ' ', str(s or '').strip())
    is_part = lambda g: bool(g[1]) and not CHAP_ONLY_RE.match(g[0]['title']) and (
        PART_RE.match(g[0]['title']) or sum(1 for k in g[1] if CHAP_ONLY_RE.match(k['title'])) >= 1)
    if not any(is_part(g) for g in groups):
        return [(clean(g[0]['title']), g[0]['n'], '') for g in groups]
    out = []
    for g in groups:
        if not is_part(g):
            out.append((clean(g[0]['title']), g[0]['n'], ''))
            continue
        part = clean(g[0]['title'])
        for j, k in enumerate(g[1]):  # 部分的标题页并进这一部分的第一篇
            out.append((clean(k['title']), min(k['n'], g[0]['n']) if j == 0 else k['n'], part))
    return out


def chapters_from(starts, pages):
    """[(标题, 起始 PDF 页, 所属部分)] → [{title, start, end, part?}]：同一页只留一个（有所属部分的优先，部分的标题页并进了它的第一篇），
    第一篇前面的页（正文前的封面、版权、目录）够多时作为「前言与目录」。"""
    total = len(pages)
    seen, uniq = {}, []
    for title, n, part in sorted(starts, key=lambda x: (x[1], not x[2])):
        if n not in seen:
            seen[n] = len(uniq)
            uniq.append((title, n, part))
    out = []
    if uniq and uniq[0][1] > 1:
        if FRONT.match(uniq[0][0]):  # 第一篇就是前言、序：封面、版权页并进去，不单独成篇
            uniq[0] = (uniq[0][0], 1, uniq[0][2])
        elif sum(len(pages[i - 1]['text']) for i in range(1, uniq[0][1])) > 400:
            out.append({'title': '前言与目录', 'start': 1, 'end': uniq[0][1] - 1})
    for k, (title, n, part) in enumerate(uniq):
        end = uniq[k + 1][1] - 1 if k + 1 < len(uniq) else total
        out.append(dict({'title': title, 'start': n, 'end': max(n, end)}, **({'part': part} if part else {})))
    return out


CJK_RE = re.compile(r'[一-鿿]')


def is_chinese(info):
    """这本书是不是中文书：前 60 页每页取前 400 字，汉字超过三成算中文。外文书整理稿也用中文写（LANG_RULE）。"""
    sample = re.sub(r'\s', '', ''.join((p.get('text') or '')[:400] for p in info['pages'][:60]))
    return len(CJK_RE.findall(sample)) > 0.3 * max(1, len(sample)) if sample else True


# 2026-10-29 用户：外文书的整理稿统一用中文写，术语附原文，方便对照原书
LANG_RULE = ('语言：这本书的原文不是中文。整理稿一律用简体中文写（标题、说明、表格、页码索引都是）；专业术语、概念、人名、书名、理论和方法的名字，'
             '第一次出现写成「中文译名（原文）」，之后用中文；作者的关键定义、核心论断、原话式的结论，中文后面附上原句（原文：“……”）。'
             '不要整段照抄原文，也不要只写原文不翻译；译名前后统一。')
PAGE_NO_RE = re.compile(r'^[\s\-—–·•]*([\dIVXLCivxlc]{1,6}|第\s*\d+\s*页)[\s\-—–·•]*$')
PARA_START_RE = re.compile(r'^([•●·▪■◆◇○□\-–]|\d+(\.\d+)*[\.、．\s]|[（(]\d+[）)]|[一二三四五六七八九十]+、|第[一二三四五六七八九十百\d]+[章节部篇讲])')


def reading_pages(info, start=1, end=None):
    """朗读原文用的文字（2026-10-29 用户要求）：去掉页眉、页脚和单独的页码，把排版折断的行接成段落。
    页眉页脚 = 在 3 页以上的开头 / 结尾两行出现的同一行字（比较时去掉空格和末尾的 i、I、1 这类识别噪声）。
    返回 [{n, paras, figure}]（figure：这一页有图表，界面附上这一页的图，朗读时跳过）。"""
    pages = info['pages']
    end = min(end or len(pages), len(pages))

    def key(line):
        return re.sub(r'[\s\dIil|，,、.。:：;；!！]+$', '', re.sub(r'\s+', '', line))[:24]

    def lines_of(p):
        return [x.strip() for x in (p.get('text') or '').split('\n') if x.strip()]
    edge = Counter()
    for p in pages:
        ls = lines_of(p)
        for k in {key(x) for x in ls[:2] + ls[-2:]}:
            if len(k) >= 2:
                edge[k] += 1
    common = {k for k, v in edge.items() if v >= 3}
    chinese = is_chinese(info)
    out = []
    for p in pages[max(0, start - 1):end]:
        ls = lines_of(p)
        ls = [x for k, x in enumerate(ls) if not (PAGE_NO_RE.match(x) or ((k < 2 or k >= len(ls) - 2) and key(x) in common))]
        figure = bool(p.get('figure') or FIG_WORDS.search(p.get('text') or ''))
        if figure and chinese:
            ls = [x for x in ls if CJK_RE.search(x)]  # 中文书有图表的页：图里识别出的坐标数字、零碎字母（「a」「4,000,000」）不读
        paras, cur, prev = [], '', ''
        full = sorted(len(x) for x in ls)[len(ls) * 3 // 4] if ls else 0  # 一整行大概多长
        for x in ls:
            if cur and (len(prev) < full * 0.8 or PARA_START_RE.match(x)):
                paras.append(cur)
                cur = ''
            cur += (' ' if cur and re.search(r'[A-Za-z0-9,.;:]$', cur) and re.match(r'[A-Za-z0-9(]', x) else '') + x
            prev = x
        if cur:
            paras.append(cur)
        out.append({'n': p['n'], 'paras': paras, 'figure': figure, 'images': list(p.get('images') or [])})
    return out


# 2026-10-29 用户：分出来的要和原书目录一致。以前只要「最上一级的章」，《研究是一门艺术》五个部分的开篇、
# 「研究的伦理」「给教师的附言」都被并进了前后的章，部分这一层也没了
TOC_PROMPT = (
    '下面是一本书 / 一份文档的目录页（标着「目录页」的是完整的目录）和前面几页的文字（每页前面标了 PDF 页序和书上页码）。'
    '请根据目录页（没有目录页时看正文标题）列出全书结构，要和原书目录一致：\n'
    '1. 「部分」：目录里把几章归在一起的上一级（第一部分、第一篇、上编、卷一……）。没有就是空列表。\n'
    '2. 「篇」：目录里和章同一级列出的每一项都单独列出，按原书顺序：前言、序、导论、各部分的开篇 / 引言 / 概述、每一章、部分末尾单独的篇目、结语、后记、附录……'
    '一项都不要漏，也不要几项合并；只是不要列章下面的节。每一篇写明属于哪个部分（标题和「部分」里的一字不差；不属于任何部分写空字符串）。\n'
    '3. 标题照抄目录（去掉页码和多余的空格、省略号；扫描识别出的明显错字改成正确的字，例如「硏究」写「研究」）；'
    '「开篇」「引言」这类要带上原书写的名字，例如「开篇 成为一名研究人员」。\n'
    '4. 书上页码就是目录里写的页码；章（或部分里的篇）本身没写页码时，用它下面第一节的页码。'
    '扫描识别出来的目录可能是两栏分开的（先列出「第一章、第一节……」，再列出名字和页码），按顺序对上即可。正文之前单独编页码的内容（页码和正文重复，或者是罗马数字）、目录里没写页码的，写这一项开始的 PDF 页序并加上 "PDF": true；'
    '看不出从哪页开始就不要列（程序会把正文前面的页自动作为「前言与目录」）。\n'
    '只输出 JSON：{"部分": [{"标题": "第一部分 ……", "书上页码": 1}], "篇": [{"标题": "第1章 ……", "书上页码": 9, "部分": "第一部分 ……"}]}。'
    '看不出章节就输出 {"部分": [], "篇": []}。'
)


TOC_HEAD_RE = re.compile(r'目\s*录|CONTENTS|Contents|TableofContents')
TOC_NUM_RE = re.compile(r'^[\s./／·…\-—_]*\d{1,4}$')
TOC_LABEL_RE = re.compile(r'^(第\s*[一二三四五六七八九十百零〇两\d]+\s*[章节部篇讲编卷]|开篇|序|前言|后记|附录)\s*$')
TOC_LINE_RE = re.compile(r'[一-鿿A-Za-z].*?[\s./／·…\-—_]*\d{1,4}\s*$')


def toc_pages(pages):
    """目录页从哪页到哪页（2026-10-29：以前只看前 25 页、每页前 1500 字，目录排得靠后或很密的书会漏掉后面的章）。
    目录页 = 至少 4 行、四成以上的行以页码结尾；从前 60 页（或全书前四分之一）里第一个带「目录」字样的这种页开始，
    往后连着的这种页都算（中间夹一页空白也接着找）。找不到返回 []。"""
    def tocish(p):
        lines = [x.strip() for x in (p.get('text') or '').split('\n') if x.strip()]
        # 两栏排的目录（左栏「第一节」、右栏节名和页码）OCR 后节名和页码分在不同行（2026-10-29《K线动能理论》）：
        # 单独的页码、「第X章 / 第X节」这种行也算目录行
        hits = sum(1 for x in lines if TOC_LINE_RE.match(x) or TOC_NUM_RE.match(x) or TOC_LABEL_RE.match(x))
        return hits >= 4 and hits >= 0.4 * len(lines)
    scan = pages[:max(60, len(pages) // 4)]
    start = next((p for p in scan if TOC_HEAD_RE.search(re.sub(r'\s', '', p.get('text') or '')) and tocish(p)), None) \
        or next((p for p in scan if tocish(p)), None)
    if not start:
        return []
    out, k = [start], start['n']  # pages[k] 是第 k+1 页
    while k < len(pages):
        if tocish(pages[k]):
            out.append(pages[k])
            k += 1
        elif len((pages[k].get('text') or '').strip()) < 30 and k + 1 < len(pages) and tocish(pages[k + 1]):
            k += 1  # 中间一页空白
        else:
            break
    return out


def ai_chapters(settings, name, info):
    """让 AI 读目录页分章：和原书目录一致，每一篇记下属于哪个部分（part）；部分的标题页并进这一部分的第一篇。"""
    from . import llm
    shift = info.get('shift')
    toc = toc_pages(info['pages'])
    head = []
    if toc:  # 找到了目录页：整份目录完整给，目录前后的页只给开头（让 AI 看出前言这类从哪页开始）
        tn, budget = {p['n'] for p in toc}, 60000
        for p in info['pages'][:min(len(info['pages']), max(tn) + 3)]:
            if p['n'] in tn:
                text = p['text'][:max(0, budget)]
                budget -= len(text)
                head.append(f'【目录页｜PDF 第 {p["n"]} 页｜{page_ref(p["n"], shift)}】\n{text}')
            elif p['n'] > max(tn) or p['n'] > min(tn) - 30:
                head.append(f'【PDF 第 {p["n"]} 页｜{page_ref(p["n"], shift)}（只有开头）】\n{p["text"][:300]}')
    else:  # 没认出目录页：和以前一样看前面几页
        for p in info['pages'][:25]:
            head.append(f'【PDF 第 {p["n"]} 页｜{page_ref(p["n"], shift)}】\n{p["text"][:1500]}')
    data = llm._json_from(llm.complete(settings, name, [{'role': 'user', 'content': TOC_PROMPT + '\n\n' + '\n\n'.join(head)}], json_mode=True))
    data = data if isinstance(data, dict) else {}
    total = len(info['pages'])

    def page_of(x):
        try:
            num = int(x['书上页码'])
        except (KeyError, TypeError, ValueError):
            return None
        n = num if (x.get('PDF') or shift is None) else num + shift
        return n if 1 <= n <= total else None
    norm = lambda s: re.sub(r'\s+', '', str(s or ''))
    parts = []  # [(标题, PDF 页)]
    for x in data.get('部分') or []:
        n = page_of(x) if isinstance(x, dict) else None
        if n and str(x.get('标题') or '').strip():
            parts.append((re.sub(r'\s+', ' ', str(x['标题']).strip()), n))
    by_name = {norm(t): t for t, _ in parts}
    items = []  # [标题, PDF 页, 部分]
    for x in data.get('篇') or data.get('章') or []:  # 「章」：旧的写法
        n = page_of(x) if isinstance(x, dict) else None
        title = re.sub(r'\s+', ' ', str(x.get('标题') or '').strip()) if isinstance(x, dict) else ''
        if not (n and title):
            continue
        if '部分' in x:
            part = by_name.get(norm(x['部分']), '')
        else:  # 没写属于哪个部分：按页码归到前面最近的部分
            part = next((t for t, pn in sorted(parts, key=lambda y: -y[1]) if pn <= n), '')
        items.append([title, n, part])
    if not items:
        return []
    for t, pn in parts:  # 部分的标题页（和部分的导言）并进这一部分的第一篇
        first = next((it for it in items if it[2] == t), None)
        if first and pn < first[1]:
            first[1] = pn
    return chapters_from([tuple(it) for it in items], info['pages'])


# ------------------------------------------------------------------ 页面图 ----
def pages_dir(path):
    return CACHE_DIR / 'pages' / cache_key(path)


def render_page(path, n, width=1100):
    """把 PDF 第 n 页渲染成 JPG（缓存）。"""
    out = pages_dir(path) / f'p{n:04d}_{width}.jpg'
    if out.is_file() and out.stat().st_size > 0:
        return out
    import pypdfium2 as pdfium
    with _pdf_lock:
        if out.is_file() and out.stat().st_size > 0:  # 排队时别的线程已经生成了
            return out
        out.parent.mkdir(parents=True, exist_ok=True)
        pdf = pdfium.PdfDocument(path)
        try:
            page = pdf[n - 1]
            w, _ = page.get_size()
            img = page.render(scale=max(0.5, width / max(1.0, w))).to_pil().convert('RGB')
            page.close()
        finally:
            pdf.close()
        tmp = out.with_name(out.stem + '.part.jpg')
        img.save(tmp, 'JPEG', quality=82)
        os.replace(tmp, out)
    return out


def page_image(path, info, n):
    """交给 AI 看的页面图：PDF 渲染；Word 用文档里的图片（第一张）。"""
    if info['kind'] == 'pdf':
        return [str(render_page(path, n))]
    return list(info['pages'][n - 1].get('images') or [])[:4]


def crop_figure(path, info, n, region, dest: Path, image_index=0):
    """按 AI 给的位置（页面宽高的比例：左, 上, 右, 下）裁出原书图示，缩到 960 宽压缩保存。"""
    from PIL import Image
    if info['kind'] == 'pdf':
        src = render_page(path, n, width=1600)
    else:
        imgs = info['pages'][n - 1].get('images') or []
        if not imgs:
            raise BookError('这一页没有图片')
        src = imgs[min(image_index, len(imgs) - 1)]
    img = Image.open(src).convert('RGB')
    W, H = img.size
    if region and len(region) == 4:
        x0, y0, x1, y1 = [max(0.0, min(1.0, float(v))) for v in region]
        if x1 - x0 > 0.05 and y1 - y0 > 0.05:
            m = 0.025
            img = img.crop((int(max(0, x0 - m) * W), int(max(0, y0 - m) * H), int(min(1, x1 + m) * W), int(min(1, y1 + m) * H)))
    if img.width > 960:
        img = img.resize((960, int(img.height * 960 / img.width)))
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, 'JPEG', quality=78, optimize=True)
    return dest


# ------------------------------------------------------------------ 整理 ----
# 2026-10-29 用户：以前的「来源级整理」（13 个固定栏目、不允许压缩知识、每段标页码、查漏补页）写出来比原文还长（105%～438%），
# 「不如自己去看书」。用户要的是提炼：用来快速复习、拿来做事、以后蒸馏和问答（原文一直在软件里，AI 要细节可以读原文）；
# 篇幅约原文的 1/5；不设固定栏目，按这一章的内容组织；一定要留：核心观点和结论、方法步骤清单、关键概念的原话定义、代表性的例子。
DEFAULT_RULES = """这是一章书的「提炼笔记」，不是转写：读者 5 分钟复习完这一章，知道作者的核心判断，拿到能照着做的方法。细节以后可以回原书查（原文一直在），所以不求面面俱到。

篇幅：按下面「篇幅」一行给的字数写，宁短勿长。同一个意思只写一次。

写法：
- 第一行：> 一句话：这一章回答了什么问题、作者的核心结论是什么（不超过 60 字）。
- 正文按这一章自己的逻辑组织（问题→答案、几个核心观点、一条流程……由你判断），用 2～5 个 ## 小标题。小标题直接写内容（例如「## 先把话题缩小成问题」），不要用「一、本章主题」「核心内容」「关键结论」这类栏目名。每个小标题末尾标一次页码范围（P83-P86），正文不逐句标页码。
- 每个小标题下写作者的观点和结论，用短句或要点；作者给的理由只留一句「因为……」，不复述推理过程。
- 关键概念用作者原话定义：**概念**：原话（不超过 40 字），一章最多 5 个，只挑理解这一章离不开的。
- 例子：一个观点最多配一个代表性的例子，一两句话说清，次要的例子不写。
- 这一章有能照着做的方法、步骤、清单时，最后单独一节「## 照着做」，用编号写 3～8 条（只写作者给的做法，每条一句）；正文里就不再重复这些步骤。没有可操作内容就不写这一节。

不要写：作者的铺垫、过渡、重复强调、「本章我们将……」、对其他章节的预告和回顾、背景故事、次要例子、引述的名人名言（除非它就是核心观点）；也不要写页码索引、知识框架、本章范围说明、「需要人工确认」「OCR 修正」这类栏目，不要开场白和结束语，不要一级标题（#）。

忠实：只写原书讲的，不补充外部知识，不评价作者，不替作者下结论；定义、数字、术语照录。扫描文字有明显错字、页面图能看清的，直接按页面图改，不用说明；看不清又影响意思的，在那句后面标（存疑）。

图：每章挑最关键的 1～2 张原书的图插进去（2026-10-30 用户：「难道一张图也不需要？」交易书的形态、走势离不开图）：
作者用图讲清楚、光看文字不好懂的形态、结构、流程、案例走势、关键表格；插在讲到它的那个小标题下面。装饰图、重复的图、一句话能说清的图不插；这一章确实没有这种图就不插。"""

FIGURE_RULE = (
    '插原书图示的写法：在需要的位置另起一行写 `![[原书图示 第N页 图号 图名 @左,上,右,下]]`，'
    'N 是下面标注里的 PDF 页序；@ 后面是这张图在页面上的位置，用页面宽高的比例（0 到 1）表示，比如 @0.08,0.30,0.92,0.75，尽量刚好框住图和图号；'
    '看不准位置就不写 @ 和后面的数字（软件会用整页），不要因为写不出位置就不插图；'
    '下一行写说明：*原书图示｜图 1-1 五行结构（P2）*。'
    '原图本身看不清（分辨率低、标注混乱）、但作者的结构或流程已经讲明白时，可以不插原图，改用 AI辅助理解图：'
    '另起一行写 **AI辅助理解图｜根据原书内容整理，非原书原图**（第N页 图号），下面用 ```mermaid 代码块画出作者已有的结构或流程；'
    '只能重画作者给出的内容，不得补写规则、条件或因果，拿不准就不画；计入每章 1～2 张的上限。'
)


def _rules(settings):
    p = (settings.get('book_rules_file') or '').strip()
    if p and os.path.isfile(p):
        from .config import read_text_file
        try:
            # 2026-10-29 起软件的提炼规则优先：知识库那份是 codex 写的初版（固定 13 个栏目、不允许压缩），用户说「不要被它限制」
            return (DEFAULT_RULES + '\n\n下面是用户知识库里的《外部书籍与文档整理规范》，只作补充参考（术语、格式习惯）；'
                    '和上面的提炼要求冲突时（篇幅、栏目、页码写法、要不要面面俱到），一律以上面为准：\n\n' + read_text_file(p)[:40000])
        except (OSError, ValueError):
            pass
    return DEFAULT_RULES


def source_type(info):
    if info['kind'] == 'pdf':
        return '扫描 PDF（OCR 文字层 + 原页面图）' if info.get('scan') else 'PDF（文字层 + 原页面图）'
    return KIND_NAMES.get(info['kind'], '文档')


def _book_head(meta, chapter, info, teacher):  # noqa: C901
    from . import llm
    lines = [f'书名：{meta.get("title") or ""}', f'作者：{meta.get("author") or "（未填写）"}',
             f'本次整理：{chapter["title"]}' + (f'（属于「{chapter["part"]}」）' if chapter.get('part') else '')
             + f'，{range_ref(chapter["start"], chapter["end"], info.get("shift"))}'
             + (f'（PDF 第 {chapter["start"]}-{chapter["end"]} 页）' if info['kind'] == 'pdf' else ''),
             '来源类型：' + source_type(info)]
    if not is_chinese(info):
        lines.append(LANG_RULE)
    tb = llm.teacher_block(teacher) if teacher else ''
    if tb:
        lines.append('作者 / 来源的术语说明（只用来识别术语、校正 OCR，不能当成本章讲过的内容）：\n' + tb)
    return '\n'.join(lines)


def _parts(info, chapter):
    pages = info['pages'][chapter['start'] - 1:chapter['end']]
    parts, cur, size = [], [], 0
    for p in pages:
        if cur and (len(cur) >= PART_PAGES or size + len(p['text']) > PART_CHARS):
            parts.append(cur)
            cur, size = [], 0
        cur.append(p)
        size += len(p['text'])
    if cur:
        parts.append(cur)
    return parts


def _content(path, info, pages, with_images):
    """一组页面：每页的文字（标好锚点），有图表的页附上页面图。"""
    shift = info.get('shift')
    flagged = [p for p in pages if p['figure'] or (info.get('scan') and len(p['text']) < 200)]
    flagged.sort(key=lambda p: (not FIG_WORDS.search(p['text']), p['n']))
    pick = {p['n'] for p in flagged[:PART_IMAGES]} if with_images else set()
    parts = []
    for p in pages:
        ref = page_ref(p['n'], shift)
        parts.append({'type': 'text', 'text': f'\n【PDF 第 {p["n"]} 页｜锚点写作（{ref}）】\n' + (p['text'] or '（这一页没有文字层）')})
        if p['n'] in pick:
            for img in page_image(path, info, p['n']):
                parts.append({'type': 'text', 'text': f'（PDF 第 {p["n"]} 页的页面图）'})
                parts.append({'type': 'image', 'path': img})
    return parts


def _figure_note(with_images):
    return FIGURE_RULE if with_images else '这次没有页面图（没有设置能看图的 AI）：不要插原书图示；图表只能按文字层整理，看不到的图形关系写「需视觉核验」。'


WORD_NOTE = ('\n\n===== 同名 Word 里对应这一章的正文（WPS 做 OCR 后导出，文字一般比上面 PDF 文字层准，但没有页码）=====\n'
             '用法：正文用词、术语、数字优先参考 Word，再用页面图核对；页码锚点一律按 PDF 每页开头的标注写；Word 里没有、PDF 里有的内容也要整理。\n')


def length_rule(info, chapter):
    """篇幅写成具体字数（只说「简洁」AI 不会真的写短）：约原文的 1/5，最多 1/4；短的一章至少给 300 字。"""
    n = sum(len(re.sub(r'\s', '', p.get('text') or '')) for p in info['pages'][chapter['start'] - 1:chapter['end']])
    target, most = max(300, round(n / 5 / 50) * 50), max(400, round(n / 4 / 50) * 50)
    return f'篇幅：这一章原文约 {n} 字，提炼笔记写 {target} 字左右，最多不超过 {most} 字（不算页码和标点）。'


def organize_chapter(settings, name, path, info, chapter, meta, teacher=None, vision=True, progress=lambda f, m=None: None, cancel=None,
                     chapters=None):
    """整理一章，返回章节 Markdown（不含属性和标题，由剪藏时加上）。"""
    from . import llm
    rules = _rules(settings)
    head = _book_head(meta, chapter, info, teacher)
    word = ''
    if info.get('companion') and chapters:
        try:
            word = companion_slice(info, chapters, chapters.index(chapter))
        except (ValueError, IndexError):
            word = ''
    parts = _parts(info, chapter)
    system = rules + '\n\n' + _figure_note(vision)
    head += '\n' + length_rule(info, chapter)
    if len(parts) == 1:
        progress(0.1, f'正在整理「{chapter["title"]}」…')
        content = [{'type': 'text', 'text': head + '\n\n请提炼这一章。以下是这一章的全部页面：'}] + _content(path, info, parts[0], vision)
        if word:
            content.append({'type': 'text', 'text': WORD_NOTE + word})
        md = llm.complete(settings, name, [{'role': 'system', 'content': system}, {'role': 'user', 'content': content}])
    else:
        notes = []
        for k, pages in enumerate(parts, 1):
            if cancel is not None and cancel.is_set():
                raise BookError('已取消')
            progress(0.8 * (k - 1) / len(parts), f'正在整理「{chapter["title"]}」第 {k}/{len(parts)} 段…')
            content = [{'type': 'text', 'text': head + f'\n\n这一章比较长，分 {len(parts)} 段读。这是第 {k} 段（{range_ref(pages[0]["n"], pages[-1]["n"], info.get("shift"))}）。'
                        '请按原书顺序列出这一段的要点（观点和结论、方法步骤、关键概念的原话定义、代表性的例子），每条一两句、标页码，'
                        '篇幅约这一段原文的四分之一；不要写小标题框架和一句话，后面会把各段合成一章的提炼笔记。需要插原书图示的地方照样写插图标记。'}] + _content(path, info, pages, vision)
            notes.append(llm.complete(settings, name, [{'role': 'system', 'content': system}, {'role': 'user', 'content': content}]))
        progress(0.85, f'正在合成「{chapter["title"]}」…')
        joined = '\n\n'.join(f'===== 第 {k} 段要点 =====\n{n}' for k, n in enumerate(notes, 1))
        md = llm.complete(settings, name, [{'role': 'system', 'content': system}, {'role': 'user', 'content':
                          head + '\n\n下面是这一章分段读完后的要点。请把它们合成这一章的提炼笔记：按上面的写法和篇幅写，'
                          '同一个意思只留一次，挑最重要的，次要的删掉；插图标记挑最关键的 1～2 张留下。\n\n' + joined
                          + (WORD_NOTE + word if word else '')}])
    return _strip_h1(md)


def _strip_h1(md):
    lines = (md or '').strip().split('\n')
    while lines and (not lines[0].strip() or re.match(r'^#\s', lines[0])):
        lines.pop(0)
    return '\n'.join(lines).strip()


_PREF = re.compile(r'P(\d{1,4})(?:\s*[-–—~至]\s*P?(\d{1,4}))?')
_PDFREF = re.compile(r'PDF\s*第\s*(\d{1,4})(?:\s*[-–—~至]\s*(\d{1,4}))?\s*页')


def cited_pages(md, shift):
    """整理稿里引用到的 PDF 页序。"""
    out = set()
    for m in _PREF.finditer(md or ''):
        if shift is None:
            continue
        a = int(m.group(1))
        b = int(m.group(2) or a)
        if b >= a and b - a < 200:
            out.update(range(a + shift, b + shift + 1))
    for m in _PDFREF.finditer(md or ''):
        a = int(m.group(1))
        b = int(m.group(2) or a)
        if b >= a and b - a < 200:
            out.update(range(a, b + 1))
    return out


def uncovered_pages(info, chapter, md):
    cited = cited_pages(md, info.get('shift'))
    return [p for p in info['pages'][chapter['start'] - 1:chapter['end']] if len(p['text'].strip()) >= 150 and p['n'] not in cited]


BOOK_REVIEW = (
    '你是书籍整理稿的查漏员。下面是一章的整理稿，以及整理稿里一次都没有引用到的几页原文。'
    '请检查这些页里有没有整理稿漏掉的、有知识意义的内容（定义、观点、论证、方法、规则、条件、案例、数字、公式、图表说明）。'
    '已经写到的（哪怕换了说法、标在别的页码上）不算漏；目录、版权页、空白、重复内容不算漏。'
    '只写原书讲了的，不补充、不评价；每条带锚点（按原文每页开头标注的写法）。'
    '只输出 JSON：{"补充": [{"放在": "整理稿里已有的小标题原文", "内容": "- 一条 Markdown 要点（P42）"}]}；没有漏掉就输出 {"补充": []}。'
)


def review_chapter(settings, name, info, chapter, md, meta, teacher=None):
    """查漏：没被引用到的页让 AI 再看一遍，补进对应小节。返回 (新稿, 补了几条, 没引用的页数)。"""
    from . import llm
    miss = uncovered_pages(info, chapter, md)
    if not miss:
        return md, 0, 0
    shift = info.get('shift')
    pages_text = '\n\n'.join(f'【PDF 第 {p["n"]} 页｜锚点写作（{page_ref(p["n"], shift)}）】\n{p["text"]}' for p in miss[:15])
    raw = llm.complete(settings, name, [{'role': 'system', 'content': BOOK_REVIEW}, {'role': 'user', 'content':
                       _book_head(meta, chapter, info, teacher) + '\n\n===== 整理稿 =====\n' + md + '\n\n===== 没有被引用到的页 =====\n' + pages_text}],
                       json_mode=True)
    try:
        data = llm._json_from(raw)
    except ValueError:
        return md, 0, len(miss)
    adds = [a for a in (data.get('补充', []) if isinstance(data, dict) else []) if isinstance(a, dict)]
    return (llm.insert_additions(md, adds) if adds else md), len(adds), len(miss)


VERIFY_PROMPT = (
    '你在核对一章书籍整理稿里「需要人工确认的位置」。下面先给出这一节的内容，再给出它提到的原书页面图（每张前面标了 PDF 页序和锚点）。\n'
    '请逐条看页面图：\n'
    '- 页面图上能看清、能确定的：给出确认结果；如果整理稿正文里写错了，给出要替换的原文和改正后的文字（原文必须是整理稿里一字不差、只出现一次的片段，尽量短）；\n'
    '- 页面图上也看不清、或者看清了但原书本身就有歧义的：保留在「需要人工确认」里，写明页面图上看到了什么；\n'
    '- 只核对来源写的是什么，不判断作者说得对不对，不用外部常识改写。\n'
    '只输出 JSON：{"替换": [{"原文": "…", "改为": "…"}], "已确认": ["| P5 | 文字层或 Word 的写法 | 页面图确认为… |"], "仍需确认": "新的「需要人工确认的位置」这一节正文（Markdown，不含标题；全部确认了就写空字符串）"}'
)


def _section(md, title_re):
    """找到「## 十一、…」这样的一节：返回 (开始行, 结束行, 行列表)；没有返回 None。"""
    lines = md.split('\n')
    for i, line in enumerate(lines):
        if re.match(r'^##\s+' + title_re, line):
            j = next((k for k in range(i + 1, len(lines)) if re.match(r'^##\s', lines[k])), len(lines))
            return i, j, lines
    return None


def verify_doubts(settings, name, path, info, chapter, md, meta, teacher=None, vision=True):
    """回页面核对：「需要人工确认的位置」里提到的页截图给 AI 看，能看清的改正、记进「已确认的 OCR 修正」。返回 (新稿, 确认了几处)。"""
    from . import llm
    if not vision or info['kind'] != 'pdf':
        return md, 0
    sec = _section(md, r'十一[、.]')
    if not sec:
        return md, 0
    i, j, lines = sec
    body = '\n'.join(lines[i + 1:j]).strip()
    pages = sorted(n for n in cited_pages(body, info.get('shift')) if chapter['start'] <= n <= chapter['end'])[:10]
    if not body or not pages:
        return md, 0
    shift = info.get('shift')
    content = [{'type': 'text', 'text': VERIFY_PROMPT + '\n\n' + _book_head(meta, chapter, info, teacher) + '\n\n===== 需要人工确认的位置 =====\n' + body}]
    for n in pages:
        content.append({'type': 'text', 'text': f'\n【PDF 第 {n} 页｜锚点写作（{page_ref(n, shift)}）】'})
        content.append({'type': 'image', 'path': str(render_page(path, n, width=1400))})
    try:
        data = llm._json_from(llm.complete(settings, name, [{'role': 'user', 'content': content}], json_mode=True))
    except (ValueError, llm.LlmError):
        return md, 0
    if not isinstance(data, dict):
        return md, 0
    fixed = 0
    for r in data.get('替换') or []:
        if isinstance(r, dict) and r.get('原文') and r.get('改为') is not None and md.count(str(r['原文'])) == 1:
            md = md.replace(str(r['原文']), str(r['改为']))
            fixed += 1
    rows = [str(x).strip() for x in data.get('已确认') or [] if str(x).strip().startswith('|')]
    rest = data.get('仍需确认')
    sec = _section(md, r'十一[、.]')
    if sec and isinstance(rest, str):
        i, j, lines = sec
        new = [] if not rest.strip() else [lines[i], '', rest.strip(), '']
        lines[i:j] = new
        md = '\n'.join(lines)
    if rows:
        sec12 = _section(md, r'十二[、.]')
        lines = md.split('\n')
        if sec12:
            i, j, lines = sec12
            k = j
            while k > i + 1 and not lines[k - 1].strip():
                k -= 1
            has_table = any(x.strip().startswith('|') for x in lines[i + 1:j])
            add = rows if has_table else ['', '| 页码 | 文字层 / Word 的写法 | 页面图确认结果 |', '| --- | --- | --- |'] + rows
            lines[k:k] = add
        else:
            at = next((k for k, x in enumerate(lines) if re.match(r'^##\s+十三[、.]', x)), len(lines))
            lines[at:at] = ['## 十二、已确认的 OCR 修正', '', '| 页码 | 文字层 / Word 的写法 | 页面图确认结果 |', '| --- | --- | --- |'] + rows + ['']
        md = '\n'.join(lines)
    return md, max(fixed, len(rows))


# 2026-10-29 和章节笔记一起改成提炼（以前 8 个固定栏目：基本信息、概念地图、来源边界……）
OVERVIEW_PROMPT = """请根据下面各章的提炼笔记，写这本书的「00、全书总览」：读者 10 分钟看完，知道这本书解决什么问题、主线怎么走、每一章讲什么、最值得记住和照着做的是什么。
写法：
- 第一行：> 一句话：这本书解决什么问题、作者的核心主张（不超过 80 字）。
- ## 全书主线：3～6 句话说清作者的整体思路，各部分之间怎么衔接。
- ## 各章导航：按全书结构写（有部分的按部分分组，部分名写成 ### 小标题），每章一行：[[文件名]]：这一章讲什么（一句话）。文件名见下面每章开头。
- ## 最值得记住的：全书最重要的 5～10 条结论，每条一句，后面标出自哪一章（[[文件名]]）。
- ## 照着做：全书最有用的方法和清单，5～10 条，每条一句，标出处；书里没有可操作的内容就不写这一节。
- 只依据这些笔记，不加外部知识，不评价作者；只完成了部分章节时，开头说明已完成哪些，不能把局部扩写成整本书的结论。
- 篇幅：3000 字以内。不要开场白和结束语，不要一级标题（#）。"""


# ------------------------------------------------------------------ AI 对话 ----
# 2026-10-29 用户：书也要像视频一样能问 AI。笔记是提炼（约原文 1/5），所以问答读原文：问这一章读这一章的原文全文 + 笔记；
# 问整本书读全部章节笔记（当地图）+ 本机从原文里挑出的相关页（mentor.rank，按字词重合打分，不联网）
CHAT_CHAPTER_CHARS = 80000   # 一章原文最多给这么多（最长的第 17 章约 2 万字）
CHAT_NOTES_CHARS = 60000     # 整本书：全部章节笔记最多给这么多
CHAT_PAGES = 14              # 整本书：再挑这么多页原文
CHAT_HISTORY = 12            # 带上最近这么多条对话
BOOK_CHAT_RULES = (
    '你是用户读书的助手，帮用户读懂、用好这本书。用中文回答。\n'
    '- 只根据下面给的原书内容回答；原书没讲的，明确说「书里没讲」；需要你自己补充时单独标【AI 补充】，不能混进作者的观点。\n'
    '- 引用原书时在句子后面标页码，写成（P42）或（P42-P45），没有书上页码时写（PDF 第 X 页），用户可以点回原书。\n'
    '- 作者的定义、数字、术语照原书；原书是外文时，引用关键原话附上原句。\n'
    '- 先给结论再展开，简洁直接；用户要清单、步骤时用编号列出。'
)


def chat_messages(info, doc, scope, idx, history):
    """书的 AI 对话：拼出给 AI 的消息。scope：chapter（第 idx 篇）/ book（整本书）。"""
    shift = doc.get('shift') if doc.get('shift') is not None else info.get('shift')
    chs = doc.get('chapters') or []
    rng = lambda c: range_ref(c['start'], c['end'], shift)
    head = (f'书名：《{doc.get("title") or ""}》' + (f'，作者：{doc["author"]}' if doc.get('author') else '')
            + f'\n全书结构：{structure(chs)}\n')
    pages_text = lambda ps: '\n\n'.join(f'【{page_ref(p["n"], shift)}】\n{p["text"]}' for p in ps)
    if scope == 'chapter' and 0 <= idx < len(chs):
        c = chs[idx]
        ps = info['pages'][c['start'] - 1:c['end']]
        body = (f'用户正在读：{c["title"]}' + (f'（属于「{c["part"]}」）' if c.get('part') else '') + f'，{rng(c)}\n\n'
                f'===== 这一章的原文（每页前面标了页码）=====\n{pages_text(ps)[:CHAT_CHAPTER_CHARS]}\n\n'
                f'===== 这一章的提炼笔记（只作导读，以原文为准）=====\n{(c.get("md") or "（还没整理）").strip()}')
    else:
        from .mentor import rank
        query = next((m['content'] for m in reversed(history) if m.get('role') == 'user'), '')
        notes = '\n\n'.join(f'===== {c["title"]}' + (f'（{c["part"]}）' if c.get('part') else '') + f'，{rng(c)} =====\n'
                            + ((c.get('md') or '').strip() or '（还没整理）') for c in chs)[:CHAT_NOTES_CHARS]
        hits = sorted(rank(query, [p for p in info['pages'] if len((p.get('text') or '').strip()) >= 80], lambda p: p['text'], CHAT_PAGES),
                      key=lambda p: p['n'])
        body = ((f'===== 全书总览 =====\n{doc["overview"].strip()}\n\n' if (doc.get('overview') or '').strip() else '')
                + f'===== 各章提炼笔记（当作全书地图）=====\n{notes}\n\n'
                + (f'===== 和问题最相关的原文页（软件按字词挑的，可能不全；细节以这些原文为准）=====\n{pages_text(hits)}' if hits
                   else '（没有找到和问题直接相关的原文页，只能根据笔记回答，回答时说明这一点）'))
    msgs = [{'role': 'system', 'content': BOOK_CHAT_RULES + '\n\n' + head + '\n' + body}]
    for m in history[-CHAT_HISTORY:]:
        if m.get('role') in ('user', 'assistant') and m.get('content'):
            msgs.append({'role': m['role'], 'content': str(m['content'])})
    return msgs


def structure(chapters):
    """全书结构，按部分分组：「前言与目录；第一部分 甲：开篇 乙、第1章 丙；第二部分 ……」。"""
    groups = []
    for c in chapters:
        part = c.get('part') or ''
        if not groups or groups[-1][0] != part:
            groups.append((part, []))
        groups[-1][1].append(c['title'])
    return '；'.join(f'{p}：{"、".join(ts)}' if p else '、'.join(ts) for p, ts in groups)


def overview(settings, name, meta, chapters, files, info=None):
    from . import llm
    done = [(c, f) for c, f in zip(chapters, files) if (c.get('md') or '').strip()]
    if not done:
        raise BookError('还没有整理好的章节')
    body = '\n\n'.join(f'===== 文件名：{f}｜{c["title"]}' + (f'（属于「{c["part"]}」）' if c.get('part') else '')
                       + f'（{c.get("range", "")}） =====\n{c["md"]}' for c, f in done)
    todo = [c['title'] for c in chapters if not (c.get('md') or '').strip()]
    head = (f'书名：{meta.get("title") or ""}\n作者：{meta.get("author") or "（未填写）"}\n'
            f'全书结构：{structure(chapters)}\n'
            + (f'还没有整理的章节：{"、".join(todo)}\n' if todo else '全部章节都已整理。\n')
            + (LANG_RULE + '\n' if info and not is_chinese(info) else ''))
    return _strip_h1(llm.complete(settings, name, [{'role': 'user', 'content': OVERVIEW_PROMPT + '\n\n' + head + '\n' + body}]))


# ------------------------------------------------------------------ 剪藏 ----
_FIG = re.compile(r'!\[\[\s*原书图示\s*第\s*(\d{1,4})\s*页\s*([^@\]]*?)\s*(?:@\s*([\d.\s,，]+))?\s*\]\]')


def place_figures(md, path, info, note_dir: Path):
    """把整理稿里的 ![[原书图示 第N页 图1-1 名称 @区域]] 换成裁好的图片。"""
    if not md or not _FIG.search(md):
        return md

    def repl(m):
        n = int(m.group(1))
        label = re.sub(r'\s+', '-', (m.group(2) or '').strip()) or f'第{n}页'
        region = [x for x in re.split(r'[,，\s]+', (m.group(3) or '').strip()) if x]
        if not (1 <= n <= len(info['pages'])):
            return f'（原书图示：{label}，页码不对）'
        from .doc import safe_name
        name = safe_name(f'原书图示-{label}')[:80] + '.jpg'
        try:
            crop_figure(path, info, n, region if len(region) == 4 else None, note_dir / 'image' / name)
        except Exception:
            return f'（原书图示：{label}，裁图失败）'
        return f'![[image/{name}]]'
    return _FIG.sub(repl, md)


def _yaml(v):
    return '"' + str(v or '').replace('\\', '/').replace('"', "'") + '"'


def chapter_file(k, title):
    from .doc import safe_name
    return f'{k:02d}、{safe_name(title)}'


FRONT = re.compile(r'^\s*(前言|序言?|自序|目录|引言|出版说明|内容简介)')


def chapter_files(chapters):
    """每章的笔记文件名：前言、目录这类不编号，正文章节从 01 开始。"""
    from .doc import safe_name
    out, k = [], 0
    for c in chapters:
        if FRONT.match(c.get('title') or ''):
            out.append(safe_name(c['title']))
        else:
            k += 1
            out.append(chapter_file(k, c.get('title') or f'第{k}部分'))
    return out


def clip_book(doc, info, vault, folder, dir_pattern='《{title}》{author}', force=False):
    """写进 Obsidian：《书名》作者/00、全书总览.md + 01、章节.md … + image/。返回 {dir, files, uri, records}。
    重新剪藏时和视频笔记一样合并：保留用户在 Obsidian 里加的小节、标注和属性。
    软件写的部分在 Obsidian 里改过、又没有 force 时，不写，返回 {'conflict': True, 'changed': [文件名…]}；
    确认后（force）先把这些笔记备份再更新。"""
    from .doc import (backup_note, merge_note, note_changed, note_fingerprint, note_sections, safe_name,
                      _fm_blocks, _split_front, _tag_list)
    if not vault or not Path(vault).is_dir():
        raise OSError('找不到 Obsidian 库文件夹，请到「设置 → Obsidian」检查库的位置')
    title = (doc.get('title') or Path(doc['media']).stem).strip('《》 ')
    author = (doc.get('author') or '').strip()
    dname = safe_name((dir_pattern or '《{title}》{author}').replace('{title}', title).replace('{author}', author).strip())
    base = Path(vault) / (folder or '') / dname
    old = (doc.get('clip') or {}).get('dir') if isinstance(doc.get('clip'), dict) else None
    if old and Path(old).is_dir():  # 剪藏过：沿用原来的文件夹（在软件里改了书名也不改 Obsidian 里的名字，用户在 Obsidian 里移动过也能找到）
        base = Path(old)
    shift = doc.get('shift') if doc.get('shift') is not None else info.get('shift')
    src_type = source_type(info)
    chapters = doc.get('chapters') or []
    clip = doc.get('clip') if isinstance(doc.get('clip'), dict) else {}
    prev = clip.get('files') if isinstance(clip.get('files'), dict) else {}
    tag_lines = ['tags:', '  - 外部原始资料', '  - 书籍', '  - 猛人学习', f'  - {safe_name(title).replace(" ", "_")}']
    todo = []  # (文件名, 生成笔记的函数)
    for c, fname in zip(chapters, chapter_files(chapters)):
        if not (c.get('md') or '').strip():
            continue

        def make(c=c):
            rng = range_ref(c['start'], c['end'], shift)
            body = place_figures(c['md'], doc['media'], info, base)
            return '\n'.join(['---', f'标题: {_yaml(c["title"])}', '类型: 书籍章节整理', f'书名: {_yaml(title)}',
                              f'作者: {_yaml(author)}', *([f'部分: {_yaml(c["part"])}'] if c.get('part') else []),
                              f'章节: {_yaml(c["title"])}', f'页码范围: {_yaml(rng)}',
                              f'来源类型: {_yaml(src_type)}', f'来源文件: {_yaml(doc["media"])}', f'整理时间: {time.strftime("%Y-%m-%d %H:%M")}',
                              *tag_lines, '---', '',
                              f'# {c["title"]}', '',
                              f'> 来源：《{title}》' + (f'，{author}' if author else '') + (f'，{c["part"]}' if c.get('part') else '') + '。', '>',
                              f'> 整理范围：{rng}' + (f'（PDF 第 {c["start"]}-{c["end"]} 页）' if info['kind'] == 'pdf' and shift is not None else '') + '。', '>',
                              '> 整理边界：只忠实说明本章如何表述，不使用外部常识纠正作者理论，不评价或验证作者观点。', '', '---', '', body.strip(), ''])
        todo.append((fname, make))
    if (doc.get('overview') or '').strip():
        done = [c['title'] for c in chapters if (c.get('md') or '').strip()]

        def make_overview():
            return '\n'.join(['---', f'标题: {_yaml(title + " 全书总览")}', '类型: 书籍总览', f'书名: {_yaml(title)}',
                              f'作者: {_yaml(author)}', f'来源类型: {_yaml(src_type)}', f'来源文件: {_yaml(doc["media"])}',
                              f'已整理范围: {_yaml("、".join(done) if len(done) < len(chapters) else "全书")}',
                              *tag_lines, '---', '', f'# 《{title}》全书总览', '', doc['overview'].strip(), ''])
        todo.insert(0, ('00、全书总览', make_overview))
    if not todo:
        raise BookError('还没有整理好的章节')
    # 先检查：软件写的部分在 Obsidian 里改过没有
    changed = []
    for fname, _ in todo:
        rec = prev.get(fname) if isinstance(prev.get(fname), dict) else {'time': clip.get('time')}
        if note_changed(base / (fname + '.md'), rec, 'book', rec.get('sections')):
            changed.append(fname)
    if changed and not force:
        return {'conflict': True, 'changed': changed, 'dir': str(base)}
    base.mkdir(parents=True, exist_ok=True)
    written, records, moved, backups = [], {}, 0, 0
    for fname, make in todo:
        path = base / (fname + '.md')
        fresh = make()  # 软件这次写的内容
        text = fresh
        if path.exists():
            rec = prev.get(fname) if isinstance(prev.get(fname), dict) else {}
            if fname in changed:
                backup_note(path)
                backups += 1
            text, n = merge_note(path.read_text(encoding='utf-8', errors='replace'), fresh,
                                 rec.get('tags'), (), 'book', rec.get('sections'))
            moved += n
        path.write_text(text, encoding='utf-8')
        written.append(str(path))
        # 记下这次软件写的小节和标签，下次用来分清哪些是用户加的
        app_secs = note_sections(fresh)
        records[fname] = {'sections': app_secs, 'tags': _tag_list(dict(_fm_blocks(_split_front(fresh)[0])).get('tags') or ['tags:']),
                          'file_hash': note_fingerprint(text, 'book', app_secs)}
    from urllib.parse import quote
    rel = Path(written[0]).relative_to(Path(vault)).as_posix()
    return {'dir': str(base), 'files': written, 'uri': f'obsidian://open?vault={quote(Path(vault).name)}&file={quote(rel)}',
            'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'records': records, 'moved': moved, 'backups': backups}


def new_doc(path, info):
    stem = Path(path).stem
    m = re.match(r'^《(.+?)》\s*(.*)$', stem)
    return {
        'version': 1, 'kind': 'book', 'media': os.path.abspath(path),
        'title': info.get('title') or (m.group(1) if m else stem), 'author': info.get('author') or (m.group(2).strip() if m else ''),
        'segments': [], 'summary': '', 'chat': [], 'clip': None,
        'shift': info.get('shift'), 'chapters': [dict(c, md='') for c in detect_chapters(info)], 'overview': '',
        'created': time.strftime('%Y-%m-%d %H:%M:%S'), 'updated': None,
    }
