'use strict';
/* 猛人学习 · 界面 */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const sleep = ms => new Promise(r => setTimeout(r, ms));

const S = {
  settings: null, doubaoModels: {}, chatProviders: [], providerNames: {}, teachers: [], defaultTemplate: '',
  path: null, doc: null, info: null,
  dirty: false, saving: null, saveTimer: null, lastSaved: null,
  active: -1, editing: null, userScrollAt: 0, undo: [], job: null,
  hits: [], hitPos: -1, chatAbort: null, lib: [], resumeAt: 0, resumePending: false, loopIdx: null, frameUrls: {},
};
const LAYOUT_KEYS = ['font_size', 'line_height', 'cue_gap', 'cue_width', 'font_family', 'split', 'sidebar', 'lib_w', 'ai_dock', 'ai_hidden', 'ai_w', 'video_h'];

/* ---------------------------------------------------------------- 工具 ---- */
async function api(name, body = {}) {
  const bridge = window.pywebview && window.pywebview.api;
  if (!bridge) throw new Error('请从「猛人学习」程序里打开这个界面');
  let d;
  try { d = await bridge.call(name, body); } catch (e) { throw new Error('后台出错：' + (e.message || e)); }
  if (!d || !d.ok) throw new Error((d && d.error) || '出错了');
  return d;
}
function bridgeReady() {
  if (window.pywebview && window.pywebview.api && window.pywebview.api.call) return Promise.resolve();
  return new Promise(r => window.addEventListener('pywebviewready', () => r(), { once: true }));
}
function fmt(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
  const p = n => String(n).padStart(2, '0');
  return h ? `${h}:${p(m)}:${p(s)}` : `${p(m)}:${p(s)}`;
}
function parseTs(str) {
  const parts = str.split(':').map(Number);
  if (parts.some(isNaN)) return null;
  return parts.length === 3 ? parts[0] * 3600 + parts[1] * 60 + parts[2] : parts[0] * 60 + parts[1];
}
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const mb = n => n >= 1024 ** 3 ? (n / 1024 ** 3).toFixed(1) + ' GB' : (n / 1024 ** 2).toFixed(0) + ' MB';

function toast(msg, opts = {}) {
  const el = document.createElement('div');
  el.className = 'toast' + (opts.error ? ' error' : '');
  el.innerHTML = `<span>${esc(msg)}</span>`;
  if (opts.action) {
    const b = document.createElement('button');
    b.className = 'btn small'; b.textContent = opts.action.label;
    b.onclick = () => { opts.action.run(); el.remove(); };
    el.appendChild(b);
  }
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), opts.ms || (opts.error ? 8000 : 3500));
  return el;
}
const fail = e => { toast(e.message || String(e), { error: true }); logToFile('warn', '提示错误：' + (e.message || e)); };
function logToFile(level, message) {
  try { if (window.pywebview && window.pywebview.api) window.pywebview.api.call('log', { level, message }); } catch (_) { /* 记日志失败不影响使用 */ }
}
window.addEventListener('error', e => logToFile('error', `${e.message} @ ${e.filename}:${e.lineno}:${e.colno}
${e.error && e.error.stack || ''}`));
window.addEventListener('unhandledrejection', e => logToFile('error', '未处理的异步错误：' + (e.reason && (e.reason.stack || e.reason.message) || e.reason)));

/* 简单 Markdown（AI 回答用），时间点可点击跳转 */
function md(src) {
  const blocks = [];
  src = String(src || '').replace(/```[\w-]*\n?([\s\S]*?)```/g, (_, c) => { blocks.push(c); return `\u0000${blocks.length - 1}\u0000`; });
  const inline = t => esc(t)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<i>$2</i>')
    .replace(/\[?(\d{1,2}:\d{2}(?::\d{2})?)\]?/g, (m, ts) => {
      const t = parseTs(ts); return t == null ? m : `<a class="tslink" data-t="${t}">[${ts}]</a>`;
    });
  const out = []; let list = null, para = [], table = null;
  const flushPara = () => { if (para.length) { out.push('<p>' + para.map(inline).join('<br>') + '</p>'); para = []; } };
  const cells = line => line.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
  const flushTable = () => {
    if (!table) return;
    const [head, ...rows] = table;
    out.push('<table><thead><tr>' + head.map(c => `<th>${inline(c)}</th>`).join('') + '</tr></thead><tbody>' +
      rows.map(r => '<tr>' + r.map(c => `<td>${inline(c)}</td>`).join('') + '</tr>').join('') + '</tbody></table>');
    table = null;
  };
  const lines = src.split('\n');
  const flushList = () => { if (list) { out.push(`<${list.tag}${list.start > 1 ? ` start="${list.start}"` : ''}>` + list.items.map(i => `<li>${inline(i)}</li>`).join('') + `</${list.tag}>`); list = null; } };
  // 列表项之间空一行（AI 常这样写）仍是同一个列表：以前每条各成一个列表，编号全是「1.」（2026-10-21 用户截图）
  const sameListNext = li => { let k = li + 1; while (k < lines.length && !lines[k].trim()) k++;
    const nx = lines[k] || ''; return !!list && (list.tag === 'ol' ? /^\s*\d+[.)]\s+/.test(nx) : /^\s*[-*+]\s+/.test(nx)); };
  for (let li = 0; li < lines.length; li++) {
    const line = lines[li].trimEnd();
    let m;
    if (table && /^\s*\|/.test(line)) { table.push(cells(line)); continue; }
    if (table) flushTable();
    if (/^\s*\|.*\|\s*$/.test(line) && /^\s*\|?\s*:?-{2,}/.test(lines[li + 1] || '')) { flushPara(); flushList(); table = [cells(line)]; li++; continue; }
    if (/^\u0000\d+\u0000$/.test(line.trim())) { flushPara(); flushList(); out.push(`<pre><code>${esc(blocks[+line.trim().slice(1, -1)])}</code></pre>`); }
    else if ((m = line.trim().match(/^!\[\[\s*原书图示\s*第\s*(\d{1,4})\s*页\s*([^@\]]*?)\s*(?:@\s*([\d.\s,，]+))?\s*\]\]$/))) {  // 书的整理笔记里的原书图示：软件里直接显示（hydrateBookFigs）
      flushPara(); flushList();
      out.push(`<figure class="book-fig" data-pn="${m[1]}" data-reg="${esc((m[3] || '').trim())}"><img alt="" title="点击看大图"><figcaption class="tts-skip">原书图示　第 ${m[1]} 页　${esc(m[2] || '')}</figcaption></figure>`);
    }
    else if ((m = line.trim().match(/^!\[\[\s*画面\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*\]\]$/))) { flushPara(); flushList(); const t = parseTs(m[1]); out.push(`<figure class="shot-fig" data-ft="${t}"><img alt=""><figcaption><a class="tslink" data-t="${t}">[${m[1]}]</a> 画面</figcaption></figure>`); }
    else if ((m = line.match(/^(#{1,6})\s+(.*)/))) { flushPara(); flushList(); const n = Math.min(4, m[1].length); out.push(`<h${n}>${inline(m[2])}</h${n}>`); }
    else if ((m = line.match(/^\s*[-*+]\s+(.*)/))) { flushPara(); if (!list || list.tag !== 'ul') { flushList(); list = { tag: 'ul', items: [] }; } list.items.push(m[1]); }
    else if ((m = line.match(/^\s*(\d+)[.)]\s+(.*)/))) { flushPara(); if (!list || list.tag !== 'ol') { flushList(); list = { tag: 'ol', items: [], start: +m[1] }; } list.items.push(m[2]); }
    else if ((m = line.match(/^>\s?(.*)/))) { flushPara(); flushList(); out.push(`<blockquote>${inline(m[1])}</blockquote>`); }
    else if (!line.trim()) { flushPara(); if (!sameListNext(li)) flushList(); }
    else { flushList(); para.push(line); }
  }
  flushPara(); flushList(); flushTable();
  return out.join('');
}

/* --------------------------------------------------------------- 外观 ---- */
const THEMES = [
  { id: 'dark', name: '墨黑', bg: '#0e0e10', fg: '#ebe8e1' },
  { id: 'purple', name: '帝王紫', bg: '#170f25', fg: '#efe9f7' },
  { id: 'dim', name: '深灰', bg: '#24252a', fg: '#ecebe7' },
  { id: 'light', name: '浅色', bg: '#fbf9f5', fg: '#22201c' },
];
const ACCENTS = ['#e8b04a', '#ff8a3d', '#ff5a5a', '#b48cff', '#4c9bff', '#3ccf91', '#ece8df'];
const FONTS = {
  yahei: '"Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", sans-serif',
  hei: 'SimHei, "Heiti SC", sans-serif',
  deng: 'DengXian, "Microsoft YaHei", sans-serif',
  song: 'SimSun, "Songti SC", serif',
  kai: 'KaiTi, STKaiti, serif',
  fang: 'FangSong, STFangsong, serif',
};

function applyLook() {
  const s = S.settings, root = document.documentElement.style;
  document.documentElement.dataset.theme = s.theme || 'dark';
  root.setProperty('--accent', s.accent || '#e8b04a');
  root.setProperty('--cue-size', (s.font_size || 17) + 'px');
  root.setProperty('--cue-line', s.line_height || 1.6);
  root.setProperty('--cue-gap', (s.cue_gap ?? 9) + 'px');
  root.setProperty('--cue-width', s.cue_width ? s.cue_width + 'px' : 'none');
  root.setProperty('--cue-font', FONTS[s.font_family] || FONTS.yahei);
  $('#txSize').textContent = s.font_size;
  $('#txLine').textContent = (+s.line_height).toFixed(1);
  $('#txGap').textContent = s.cue_gap;
  $('#txWidth').textContent = s.cue_width ? s.cue_width : '全宽';
  $('#txFont').value = s.font_family || 'yahei';
  $('#txTheme').value = s.theme;
  // 书的整理笔记、原文文字版（和字幕分开设）
  root.setProperty('--bk-size', (s.book_font_size || 15) + 'px');
  root.setProperty('--bk-line', s.book_line_height || 1.8);
  root.setProperty('--bk-gap', (s.book_gap ?? 8) + 'px');
  root.setProperty('--bk-width', s.book_width ? s.book_width + 'px' : 'none');
  root.setProperty('--bk-font', FONTS[s.book_font_family] || FONTS.yahei);
  $('#bkTxSize').textContent = s.book_font_size || 15;
  $('#bkTxLine').textContent = (+(s.book_line_height || 1.8)).toFixed(1);
  $('#bkTxGap').textContent = s.book_gap ?? 8;
  $('#bkTxWidth').textContent = s.book_width ? s.book_width : '全宽';
  $('#bkTxFont').value = s.book_font_family || 'yahei';
  $('#bkTxTheme').value = s.theme;
  $('#optFollow').checked = !!s.follow;
  $('#optTr').checked = !!s.show_tr;
  $('#optOverlay').checked = !!s.overlay;
  $('#cues').classList.toggle('hide-tr', !s.show_tr);
  $('#overlay').hidden = !s.overlay;
  const cp = s.chat;
  $('#aiModel').textContent = `${S.providerNames[cp] || cp} · ${(s.providers[cp] || {}).chat_model || '未选模型'}`;
  applyLayout();
}

function applyLayout() {
  const s = S.settings;
  $('#library').hidden = !s.sidebar; $('#libSplit').hidden = !s.sidebar;
  $('#library').style.width = (s.lib_w || 270) + 'px';
  $('#left').style.width = Math.round((s.split || 0.5) * 100) + '%';
  const main = $('#stageMain'), panel = $('#aiPanel');
  const side = s.ai_dock === 'side', hidden = !!s.ai_hidden;
  main.classList.toggle('ai-side', side && !hidden);
  main.classList.toggle('ai-hidden', hidden);
  const target = hidden ? null : (side ? $('#aiDockSide') : $('#aiDockBottom'));
  if (target && panel.parentElement !== target) target.appendChild(panel);
  if (!target && panel.parentElement !== document.body) document.body.appendChild(panel);
  panel.classList.toggle('docked', !!target);
  const stageW = $('#stage').getBoundingClientRect().width || 1200;
  const aiW = clamp(s.ai_w || 440, 280, Math.max(300, stageW * 0.65));
  $('#aiDockSide').style.width = aiW + 'px';
  // AI 在右侧时，视频和字幕按剩下的宽度分配比例
  if (side && !hidden) $('#left').style.width = `calc((100% - ${Math.round(aiW + 20)}px) * ${(s.split || 0.5).toFixed(3)})`;
  $('#playerArea').style.flex = (!side && !hidden && !$('#playerWrap').classList.contains('audio')) ? `0 0 ${Math.round((s.video_h || 0.55) * 100)}%` : '';
  $('#btnShowAi').hidden = !hidden;
}

let prefTimer = null, prefPatch = {};
function savePrefs(patch) {
  Object.assign(S.settings, patch); Object.assign(prefPatch, patch);
  applyLook();
  clearTimeout(prefTimer);
  prefTimer = setTimeout(() => { const p = prefPatch; prefPatch = {}; api('settings', { settings: p }).catch(() => {}); }, 400);
}
function keepLayout(fresh) {
  for (const k of LAYOUT_KEYS) fresh[k] = S.settings[k];
  return fresh;
}

async function loadSettings() {
  const d = await api('settings', {});
  S.settings = d.settings; S.doubaoModels = d.doubao_models; S.doubaoOrder = d.doubao_order || []; S.chatProviders = d.chat_providers;
  S.providerNames = d.provider_names; S.teachers = d.teachers; S.defaultTemplate = d.default_template;
  applyLook(); renderTeacherSelect();
}
const teacherById = id => S.teachers.find(t => t.id === id) || null;
function teacherOptions(selected, none = '未指定老师（不用术语表）') {
  return `<option value="">${none}</option>` + S.teachers.map(t => `<option value="${t.id}" ${t.id === selected ? 'selected' : ''}>${esc(t.name)}</option>`).join('');
}
function renderTeacherSelect() {
  $('#teacherSelect').innerHTML = teacherOptions(S.doc && S.doc.teacher, '未指定老师') + '<option value="__manage">＋ 管理老师和术语表…</option>';
  $('#teacherChip').hidden = !S.doc;
}

/* ------------------------------------------------------------ 媒体库 ---- */
/* 树形（2026-10-14）：最上层是加进来的文件夹和单个文件（按加入先后），文件夹按实际层级展开。
   同一层里带序号的按序号、没序号的按下载时间；没序号的可以拖动排序，带序号的拖乱了会提示改名。
   排列规则在后台 core/library.py，这里只显示、记住展开收起、把拖好的顺序交给后台。 */
async function loadLibrary(groups) {
  try { S.lib = groups || (await api('library')).groups; S.libError = ''; } catch (e) { S.libError = e.message || String(e); fail(e); }
  renderLibrary();
}
/* 媒体库里所有的文件（任意层），可以带过滤 */
function libFiles(nodes = S.lib, out = []) {
  for (const n of nodes || []) { if (n.type === 'folder') libFiles(n.children, out); else out.push(n); }
  return out;
}
function libNode(path, nodes = S.lib) {
  const p = (path || '').toLowerCase();
  for (const n of nodes || []) {
    if (n.path.toLowerCase() === p) return n;
    if (n.type === 'folder') { const f = libNode(path, n.children); if (f) return f; }
  }
  return null;
}
/* 这节课所在的最里层文件夹是不是「不写课程重点」（后台已经算好了上层沿用） */
function libNoSummary(path, nodes = S.lib) {
  for (const n of nodes || []) {
    if (n.type === 'folder' && insideDir(path, n.path)) return libNoSummary(path, n.children) ?? !!n.no_summary;
  }
  return null;
}
const insideDir = (path, dir) => !!path && !!dir && path.toLowerCase().startsWith(dir.toLowerCase().replace(/[\\/]+$/, '') + '\\');
/* 整理步骤的中文名（和 workflow.LABELS 一样） */
const STEP_LABEL = { transcribe: '生成字幕', correct: 'AI 校对', frames: '找关键帧', recheck: '用画面复核存疑句', translate: '翻译',
  summary: '课程重点', review: '查漏补缺', terms: '新术语', chapters: '分章', organize: '逐章整理', overview: '全书总览' };
/* 有整理结果、但一键整理默认的步骤还没做完（例如写完课程重点后额度用完，查漏、新术语没做）：标「未整理完」 */
function pendingTag(it) {
  const left = it.pending || [];
  const done = it.book ? it.book.total && it.book.done === it.book.total : it.summary || (it.no_summary && it.subs);  // 不写课程重点的文件夹：有字幕就算整理过
  if (!done || !left.length) return '';
  return `<span class="tag warn" title="一键整理还差：${left.map(n => STEP_LABEL[n] || n).join('、')}。打开后用一键整理（做过的步骤会自动跳过），或让 AI 用命令行 --todo 接着整理">未整理完</span>`;
}
function itemHTML(it, depth = 0, parent = '') {
  const tags = [];
  if (it.book) tags.push(`<span class="tag ${it.book.done ? 'ok' : ''}">整理 ${it.book.done}/${it.book.total}</span>`);
  if (it.subs) tags.push(`<span class="tag ok" title="这节课的字幕有 ${it.subs} 句">字幕 ${it.subs} 句</span>`);
  if (it.doubts && !it.reviewed) tags.push(`<span class="tag warn" title="AI 校对标出 ${it.doubts} 句存疑、还没确认。打开这节课，点字幕栏上方的「存疑」逐句回听，改好或点「已确认」">存疑 ${it.doubts}</span>`);
  if (it.summary) tags.push((it.summary_changes || 0) >= SUMMARY_STALE_AT
    ? `<span class="tag warn" title="生成课程重点之后，字幕又改了 ${it.summary_changes} 句。打开这节课，在课程重点上方点「重新生成」">重点待更新</span>`
    : '<span class="tag acc">重点</span>');
  if (pendingTag(it)) tags.push(pendingTag(it));
  if (bgTag(it)) tags.push(bgTag(it));
  if (it.reviewed) tags.push('<span class="tag ok">已校对</span>');
  if (it.clipped) tags.push(it.stale ? '<span class="tag warn" title="剪藏后在软件里又改过字幕或课程重点。再点一次「剪藏」，Obsidian 里的笔记才会更新">待更新剪藏</span>'
    : '<span class="tag" title="Obsidian 里的笔记和软件里的内容一致">已剪藏</span>');
  if (it.pos && it.duration) tags.push(`<span>看到 ${fmt(it.pos)}</span>`);
  const pct = it.pos && it.duration ? Math.min(100, it.pos / it.duration * 100) : 0;
  const dot = it.clipped && !it.stale ? 'done' : (it.subs || (it.book && it.book.done) ? 'has' : '');
  return `<div class="lib-item ${S.path && it.path.toLowerCase() === S.path.toLowerCase() ? 'active' : ''}" data-open="${esc(it.path)}" title="${esc(it.path)}"
    draggable="true" data-drag="${esc(it.path)}" data-parent="${esc(parent)}" data-fname="${esc(it.fname || '')}" data-num="${it.num ? 1 : 0}" style="--depth:${depth}">
    <i class="dot ${dot}"></i><div class="lib-name">${esc(it.name)}</div>
    ${tags.length ? `<div class="lib-meta">${tags.join('')}</div>` : ''}
    ${pct ? `<div class="lib-prog"><i style="width:${pct.toFixed(1)}%"></i></div>` : ''}</div>`;
}
/* 左上角「书籍」只列书和文档，「课程」只列音视频（2026-10-29）；文件夹里没有这一类的不显示（空文件夹、找不到的文件夹两边都显示） */
const BOOK_FILE_RE = /\.(pdf|docx?|epub|mobi|azw3?|pptx?|txt|md)$/i;
const libKind = () => (S.view === 'book' ? 'book' : 'media');
const kindOf = it => it.kind || (BOOK_FILE_RE.test(it.path) ? 'book' : 'media');
function kindCount(n) {
  if (n.type !== 'folder') return kindOf(n) === libKind() ? 1 : 0;
  return n.children.reduce((s, c) => s + kindCount(c), 0);
}
/* 搜索时：文件名或它所在的文件夹名包含关键词就显示 */
function libMatch(n, q, chain) {
  if (n.type === 'folder') {
    if (!kindCount(n) && !(n.top && (!n.count || n.missing))) return false;
    if (!q) return true;
    return (chain + ' ' + n.name).toLowerCase().includes(q) || n.children.some(c => libMatch(c, q, chain + ' ' + n.name));
  }
  if (kindOf(n) !== libKind()) return false;
  return !q || (chain + ' ' + n.name).toLowerCase().includes(q);
}
function folderHTML(n, depth, parent, q, chain) {
  const kids = n.children.filter(c => libMatch(c, q, chain + ' ' + n.name));
  const open = q || !n.collapsed;  // 打开课时 revealInLibrary 已经把它所在的文件夹展开；之后用户想收起就能收起
  const t = n.teacher && teacherById(n.teacher);
  return `<div class="lib-group ${open ? '' : 'collapsed'} ${n.top ? 'top' : ''}">
    <div class="lib-ghead" data-folder="${esc(n.path)}" draggable="true" data-drag="${esc(n.path)}" data-parent="${esc(parent)}"
      data-fname="${esc(n.fname || '')}" data-num="${n.num ? 1 : 0}" style="--depth:${depth}" title="${esc(n.path)}">
      <svg class="caret" viewBox="0 0 24 24"><path d="M6 9l6 6 6-6"/></svg>
      <span class="lib-gname">${esc(n.name)}</span>
      ${t ? `<span class="lib-gteacher" title="这个文件夹（和没单独绑定的下层文件夹）里的课用这位老师的术语表">${esc(t.name)}</span>` : ''}
      ${n.summary === 'off' ? '<span class="lib-gteacher" title="这个文件夹（和没单独设置的下层文件夹）里的课不写课程重点：一键整理默认不勾，后台整理和命令行跳过">不写重点</span>'
    : n.summary === 'on' ? '<span class="lib-gteacher" title="上层设了不写课程重点，这个文件夹单独设成要写">写重点</span>' : ''}
      <span class="lib-gcount" title="${libKind() === 'book' ? '这里面的书和文档' : '这里面的视频和音频'}">${kindCount(n)}</span>
      <button class="lib-more" data-more="${esc(n.path)}" title="更多">⋯</button>
    </div>
    ${n.missing ? `<div class="lib-missing" style="--depth:${depth}">找不到这个文件夹，可能被移动或删除了</div>` : ''}
    <div class="lib-items">${kids.map(c => nodeHTML(c, depth + 1, n.path, q, chain + ' ' + n.name)).join('')
      || (n.missing ? '' : `<div class="lib-missing muted" style="--depth:${depth + 1}">${n.count ? (libKind() === 'book' ? '这里没有书和文档' : '这里没有视频和音频') : '没有音视频和文档'}</div>`)}</div>
  </div>`;
}
function nodeHTML(n, depth, parent, q, chain) {
  return n.type === 'folder' ? folderHTML(n, depth, parent, q, chain) : itemHTML(n, depth, parent);
}
/* 打开一节课时把它所在的各层文件夹展开一次（记住），方便看到它在哪。
   以前是每次画列表都强制展开，结果正在看的课所在的文件夹（连最上层）点了收不起来。 */
function revealInLibrary(path) {
  let changed = false;
  const walk = nodes => {
    for (const n of nodes || []) {
      if (n.type !== 'folder' || !insideDir(path, n.path)) continue;
      if (n.collapsed) { n.collapsed = false; changed = true; api('library_set', { path: n.path, collapsed: false }).catch(() => {}); }
      walk(n.children);
    }
  };
  walk(S.lib);
  if (changed) renderLibrary();
}
function renderLibrary() {
  const q = $('#libSearch').value.trim().toLowerCase();
  const body = $('#libBody');
  if (S.libError && !S.lib.length) {
    body.innerHTML = `<div class="lib-empty">媒体库暂时读不出来：<br>${esc(S.libError)}<br>点上面的「刷新」重试。</div>`;
    return;
  }
  if (!S.lib.length) {
    body.innerHTML = '<div class="lib-empty">还没有添加文件。<br>点上面的 <b>＋文件夹</b> 一次加入整个文件夹（含子文件夹）里的视频、音频和文档，以后点哪个看哪个。</div>';
    return;
  }
  const scroll = body.scrollTop;
  body.innerHTML = S.lib.filter(n => libMatch(n, q, '')).map(n => nodeHTML(n, 0, '', q, '')).join('')
    || `<div class="lib-empty">${q ? '没有找到。' : libKind() === 'book'
      ? '媒体库里还没有书。<br>加进来的文件夹里的 PDF、Word、EPUB、MOBI、PPT 会列在这里；也可以点上面的 <b>＋文件</b> 单独加一本。'
      : '媒体库里还没有视频、音频。<br>加进来的文件夹里的视频、音频会列在这里。'}</div>`;
  body.scrollTop = scroll;
}
function patchLib(path, patch, later) {
  if (!path) return;
  const p = path.toLowerCase();
  for (const it of libFiles()) if (it.path.toLowerCase() === p) Object.assign(it, patch);
  clearTimeout(patchLib.timer);
  if (later) patchLib.timer = setTimeout(renderLibrary, 800); else renderLibrary();
}
function syncLibFromDoc() {
  if (!S.doc) return;
  if (S.doc.kind === 'book') {
    const chs = S.doc.chapters || [];
    patchLib(S.path, { book: { done: chs.filter(c => (c.md || '').trim()).length, total: chs.length }, clipped: !!S.doc.clip, pending: S.pending || [] });
    return;
  }
  patchLib(S.path, { subs: S.doc.segments.length, summary: !!(S.doc.summary || '').trim(), clipped: !!S.doc.clip, stale: !!S.docStale, reviewed: !!S.doc.reviewed, duration: S.doc.duration, doubts: (S.doc.doubts || []).length,
    summary_changes: S.summaryChanges || 0, pending: S.pending || [] });
}
function showCtx(x, y, html, bindings) {
  const m = $('#ctxMenu');
  m.innerHTML = html; m.hidden = false;
  const r = m.getBoundingClientRect();
  m.style.left = Math.max(4, Math.min(x, innerWidth - r.width - 8)) + 'px';
  m.style.top = Math.max(4, Math.min(y, innerHeight - r.height - 8)) + 'px';
  bindings(m);
}
function folderMenu(path, x, y) {
  const n = libNode(path); if (!n) return;
  showCtx(x, y, `<div class="menu-label">这个文件夹的老师 / 术语表</div>
    <select data-a="teacher">${teacherOptions(n.teacher, n.top ? '不指定' : '不指定（沿用上一层）')}</select>
    <button class="menu-item" data-a="bgall" title="定好一套整理规则（做哪几步、老师、豆包模型和账号），把这个文件夹（含子文件夹）里还没整理完的课一节一节排进后台整理">整个文件夹排进后台整理…</button>
    <button class="menu-item" data-a="nosum" title="灵修类这种逐字稿课程：只识别、校对，直接蒸馏老师。打开后一键整理默认不勾「生成课程重点」，后台整理和命令行跳过课程重点和查漏补缺，媒体库不再因为没写重点标「未整理完」。下层文件夹沿用，也能单独改回来">${n.no_summary ? '✓ ' : ''}这里的课不写课程重点</button>
    <button class="menu-item" data-a="rename">重命名…</button>
    <button class="menu-item" data-a="rescan">重新扫描</button>
    <button class="menu-item" data-a="reveal">在资源管理器中打开</button>
    <button class="menu-item" data-a="distill" title="AI 逐节提取观点库，再合并成课程蒸馏、写老师画像，写进 Obsidian；做完能在老师工作台里和这位老师对话">用这个文件夹的课蒸馏一位老师…</button>
    ${n.top ? '<button class="menu-item danger" data-a="remove">从媒体库移除（不删除文件）</button>' : ''}`, m => {
    $('[data-a=distill]', m).onclick = () => { m.hidden = true; switchView('mentor'); openDistill({ folder: n.path, name: n.name }); };
    $('[data-a=teacher]', m).onchange = async e => {
      n.teacher = e.target.value; m.hidden = true;
      await api('library_set', { path: n.path, teacher: n.teacher }); renderLibrary();
      toast(n.teacher ? '已设置：这个文件夹和它下面没单独绑定的文件夹，里面的课都用这位老师的术语表' : (n.top ? '已取消绑定' : '已取消绑定，沿用上一层文件夹的设置'));
    };
    $('[data-a=bgall]', m).onclick = () => { m.hidden = true; openFolderBg(n); };
    $('[data-a=nosum]', m).onclick = async () => {
      m.hidden = true;
      const off = !n.no_summary;
      try { await loadLibrary((await api('library_set', { path: n.path, no_summary: off, refresh: true })).groups); } catch (e) { fail(e); return; }
      if (S.path && insideDir(S.path, n.path)) S.noSummary = libNoSummary(S.path);
      toast(off ? `「${n.name}」里的课以后不写课程重点：一键整理默认不勾，后台整理和命令行跳过（想写某一节时，在一键整理里勾上就行）`
        : `「${n.name}」里的课以后照常写课程重点`, { ms: 6000 });
    };
    $('[data-a=rename]', m).onclick = () => { m.hidden = true; renameNode(n); };
    $('[data-a=rescan]', m).onclick = () => { m.hidden = true; loadLibrary(); };
    $('[data-a=reveal]', m).onclick = () => { m.hidden = true; api('reveal', { target: n.path }); };
    const rm = $('[data-a=remove]', m);
    if (rm) rm.onclick = async () => { m.hidden = true; if (confirm(`从媒体库移除「${n.name}」？\n不会删除电脑上的文件。`)) loadLibrary((await api('library_remove', { kind: 'folder', path: n.path })).groups); };
  });
}
function itemMenu(path, x, y) {
  const n = libNode(path); if (!n) return;
  showCtx(x, y, `<button class="menu-item" data-a="open">打开</button>
    ${bgOf(path) ? '' : '<button class="menu-item" data-a="bg" title="按一键整理默认的步骤，只做还没做完的；排在后台一节一节整理，不影响你看别的课">排进后台整理</button>'}
    <button class="menu-item" data-a="rename">重命名…</button>
    <button class="menu-item" data-a="reveal">在资源管理器中显示</button>
    ${n.top ? '<button class="menu-item danger" data-a="remove">从媒体库移除（不删除文件）</button>' : ''}`, m => {
    $('[data-a=open]', m).onclick = () => { m.hidden = true; openPath(path); };
    const bg = $('[data-a=bg]', m);
    if (bg) bg.onclick = async () => {
      m.hidden = true;
      if (S.path && S.path.toLowerCase() === path.toLowerCase()) await flushSave();
      try { await bgAdd({ path }); toast(`《${n.name}》排进后台整理了：只做还没做完的步骤，整理完会提示你`); } catch (e) { fail(e); }
    };
    $('[data-a=rename]', m).onclick = () => { m.hidden = true; renameNode(n); };
    $('[data-a=reveal]', m).onclick = () => { m.hidden = true; api('reveal', { target: path }); };
    const rm = $('[data-a=remove]', m);
    if (rm) rm.onclick = async () => { m.hidden = true; loadLibrary((await api('library_remove', { kind: 'file', path })).groups); };
  });
}
/* 整个文件夹排进后台整理（2026-10-28，几百节课的文件夹）：定一套规则，后台把还没整理完的课一节一节排进去。
   规则记在文件夹上（core/library.py 的 rule），下次打开对话框带出来；不勾课程重点 = 文件夹设成「不写课程重点」。 */
const FB_DEFAULT = ['transcribe', 'correct', 'frames', 'recheck', 'translate', 'summary', 'review', 'terms'];
async function openFolderBg(n) {
  let info;
  try { info = await api('folder_rule', { path: n.path }); } catch (e) { fail(e); return; }
  const dlg = $('#dlgFolderBg'), rule = info.rule || {};
  const has = new Set(rule.steps || FB_DEFAULT.filter(s => !(info.no_summary && (s === 'summary' || s === 'review'))));
  $('#fbTitle').textContent = `整个文件夹排进后台整理：${n.name}`;
  $('#fbInfo').textContent = `里面有 ${info.lessons} 节音视频${info.books ? `（另有 ${info.books} 本书 / 文档，步骤不一样，这次不排）` : ''}。`
    + (info.rule ? '下面是上次定的规则，可以改。' : '定好下面的规则，点「开始排队」。');
  $('#fbAsr').checked = has.has('transcribe');
  $('#fbCorrect').checked = has.has('correct');
  $('#fbFrames').checked = has.has('frames');
  $('#fbTranslate').checked = has.has('translate');
  $('#fbSummary').checked = has.has('summary');
  $('#fbTerms').checked = has.has('terms');
  $('#fbTeacher').innerHTML = teacherOptions(info.teacher || '');
  const lang = rule.lang || (teacherById(info.teacher) || {}).lang || S.settings.last_lang || 'zh';
  $$('input[name=fbLang]').forEach(r => r.checked = r.value === lang);
  fillDbPickers($('#fbDbModel'), $('#fbDbAcct'));
  for (const [el, v] of [[$('#fbDbModel'), rule.doubao_model], [$('#fbDbAcct'), rule.doubao_account]]) {
    if (v && [...el.options].some(o => o.value === v)) el.value = v;
  }
  const syncDb = () => {  // 要生成字幕、而且用豆包时，才显示「豆包用哪种模型」
    const l = $('input[name=fbLang]:checked').value;
    $('#fbDbField').hidden = !$('#fbAsr').checked || (l === 'en' ? S.settings.asr_en : S.settings.asr_zh) !== 'doubao';
  };
  $('#fbAsr').onchange = syncDb;
  $$('input[name=fbLang]').forEach(r => r.onchange = syncDb);
  syncDb();
  dlg.returnValue = '';
  dlg.showModal();
  dlg.onclose = () => { if (dlg.returnValue === 'ok') startFolderBg(n); };
}
async function startFolderBg(n) {
  const steps = [];
  if ($('#fbAsr').checked) steps.push('transcribe');
  if ($('#fbCorrect').checked) steps.push('correct');
  if ($('#fbFrames').checked) steps.push('frames', 'recheck');
  if ($('#fbTranslate').checked) steps.push('translate');
  if ($('#fbSummary').checked) steps.push('summary', 'review');
  if ($('#fbTerms').checked) steps.push('terms');
  if (!steps.length) { toast('没有勾选要做的步骤，没有排队', { error: true }); return; }
  const inside = S.path && insideDir(S.path, n.path);
  try {
    if (inside) await flushSave();
    const r = await api('bg_add_folder', { path: n.path, steps, lang: $('input[name=fbLang]:checked').value, teacher: $('#fbTeacher').value,
      doubao_model: $('#fbDbModel').value, doubao_account: $('#fbDbAcct').value });
    applyBg(r.items); pollBg();
    await loadLibrary();
    if (inside) S.noSummary = libNoSummary(S.path);
    const more = [r.done ? `${r.done} 节已经按这个规则整理完` : '', r.busy ? `${r.busy} 节本来就在排队或正在整理` : '',
      r.books ? `${r.books} 本书 / 文档不排` : '', r.errors ? `${r.errors} 节读不出字幕文件，跳过了（详见日志）` : ''].filter(Boolean).join('，');
    toast(r.added ? `「${n.name}」排进后台整理 ${r.added} 节，一节一节做，可以照常看课、校对。${more ? more + '。' : ''}进度看上方的「后台整理」`
      : `「${n.name}」里没有要整理的课${more ? '：' + more : ''}`, { ms: 10000, error: !!r.errors && !r.added });
  } catch (e) { fail(e); }
}
/* 改名：电脑上的文件 / 文件夹、字幕文件、播放进度、媒体库一起改；Obsidian 里已经剪藏的笔记不改名 */
async function renameNode(n, suggested) {
  const isDir = n.type === 'folder';
  const cur = isDir ? (n.fname || n.path.split(/[\\/]/).pop()) : n.name;
  const name = (window.prompt(`给这个${isDir ? '文件夹' : '文件'}改名（电脑上的${isDir ? '文件夹' : '文件'}也会改名${isDir ? '' : '，扩展名不变'}）：`, suggested || cur) || '').trim();
  if (!name || name === cur) return false;
  const affected = !!S.path && (S.path.toLowerCase() === n.path.toLowerCase() || (isDir && insideDir(S.path, n.path)));
  if (affected && S.job) { toast('这节课还有任务在进行，等它完成后再改名', { error: true }); return false; }
  const oldOpen = S.path;
  if (affected) { await flushSave(); releaseMedia(); }
  try {
    const r = await api('rename', { path: n.path, name });
    S.lib = r.groups; renderLibrary();
    if (affected) await window.openPath(isDir ? r.path + oldOpen.slice(n.path.length) : r.path);
    toast('已改名' + (n.clipped || isDir ? '。Obsidian 里已经剪藏的笔记保留原来的文件名' : ''), { ms: 6000 });
    return true;
  } catch (e) {
    fail(e);
    if (affected) await window.openPath(oldOpen);
    return false;
  }
}
/* 改名前放开正在播放 / 显示的文件（Windows 上被占用的文件改不了名） */
function releaseMedia() {
  const v = $('#video');
  try { v.pause(); v.removeAttribute('src'); v.load(); } catch (_) { /* 没有在播放 */ }
  const pdf = $('#bookPdf'); if (pdf && pdf.getAttribute('src')) pdf.src = 'about:blank';
}
/* 拖动排序：只能在同一层里拖；带序号的拖乱了序号顺序，提示改名 */
function libDrag() {
  const body = $('#libBody');
  let drag = null;
  const siblings = parent => $$('[data-drag]', body).filter(el => el.dataset.parent === parent);
  const clear = () => $$('.drop-before,.drop-after', body).forEach(el => el.classList.remove('drop-before', 'drop-after'));
  body.addEventListener('dragstart', e => {
    const el = e.target.closest('[data-drag]'); if (!el || $('#libSearch').value.trim()) { e.preventDefault(); return; }
    drag = { el, path: el.dataset.drag, parent: el.dataset.parent };
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', drag.path);
  });
  body.addEventListener('dragover', e => {
    if (!drag) return;
    const el = e.target.closest('[data-drag]');
    clear();
    if (!el || el === drag.el || el.dataset.parent !== drag.parent) return;
    e.preventDefault(); e.stopPropagation();
    const r = el.getBoundingClientRect();
    el.classList.add(e.clientY < r.top + r.height / 2 ? 'drop-before' : 'drop-after');
  });
  body.addEventListener('dragleave', e => { if (!body.contains(e.relatedTarget)) clear(); });
  body.addEventListener('dragend', () => { clear(); drag = null; });
  body.addEventListener('drop', async e => {
    if (!drag) return;
    e.preventDefault(); e.stopPropagation();
    const el = e.target.closest('[data-drag]');
    const d = drag; drag = null;
    const before = el && el.classList.contains('drop-before');
    clear();
    if (!el || el === d.el || el.dataset.parent !== d.parent) return;
    const list = siblings(d.parent).filter(x => x !== d.el);
    list.splice(list.indexOf(el) + (before ? 0 : 1), 0, d.el);
    await libReorder(d, list);
  });
}
async function libReorder(d, list) {
  const top = !d.parent;
  if (!top && d.el.dataset.num === '1') {
    // 带序号的：拖完后它和别的带序号的顺序变了，就不是「排列」而是「序号不对」了，提示改名
    const nums = list.filter(x => x.dataset.num === '1').map(x => x.dataset.drag);
    const now = siblings0(d.parent).filter(x => x.dataset.num === '1').map(x => x.dataset.drag);
    if (nums.join('\n') !== now.join('\n')) {
      const n = libNode(d.path);
      toast('带序号的文件按序号排列。要把它排到这里，请改一下序号（例如改成 3.5、 或 03-1、），改完会自动按序号排好', { ms: 9000 });
      if (n) await renameNode(n);
      return;
    }
  }
  const names = list.map(x => top ? x.dataset.drag : x.dataset.fname);
  // 书籍 / 课程分开列：这一层另一类的（现在看不见）按原来的顺序接在后面，它们之间的先后不变
  const all = top ? S.lib : ((libNode(d.parent) || {}).children || []);
  for (const n of all) {
    const name = top ? n.path : n.fname;
    if (name && !list.some(x => x.dataset.drag.toLowerCase() === n.path.toLowerCase())) names.push(name);
  }
  try { loadLibrary((await api('library_reorder', { parent: d.parent, names })).groups); } catch (e) { fail(e); }
}
const siblings0 = parent => $$('[data-drag]', $('#libBody')).filter(el => el.dataset.parent === parent);
async function addFolder() {
  try {
    toast('选择窗口里只显示文件夹，看不到里面的文件是正常的：选中要加的文件夹，点「选择文件夹」就行', { ms: 8000 });
    let r = await api('library_add_folder');
    if (r.status === 'cancel') return;
    if (r.status === 'need_merge') {
      const what = [r.children.length ? `文件夹「${r.children.join('」「')}」` : '', r.files ? `${r.files} 个单独加的文件` : ''].filter(Boolean).join('和');
      const merge = confirm(`媒体库里已经单独加了它下面的${what}。\n\n确定：合并。按实际层级显示，原来单独加的并进这个文件夹里（绑定的老师、拖好的顺序都保留）。\n取消：重复加入。原来的不动，它们会在媒体库里出现两次。`);
      r = await api('library_add_folder', { path: r.path, merge });
    }
    if (r.status === 'exists') return toast('这个文件夹已经在媒体库里了');
    if (r.status === 'inside') return toast(`这个文件夹已经在「${r.parent_name}」里面了，展开它就能看到`, { ms: 6000 });
    loadLibrary(r.groups);
    if (!S.settings.sidebar) savePrefs({ sidebar: true });
    const got = [r.video ? `视频 ${r.video} 个` : '', r.audio ? `音频 ${r.audio} 个` : '', r.doc ? `文档 ${r.doc} 个` : ''].filter(Boolean).join('、');
    const skipped = Object.entries(r.skipped || {}).sort((a, b) => b[1] - a[1]);
    const skipText = skipped.length ? `。另有 ${skipped.reduce((s, x) => s + x[1], 0)} 个文件不是能整理的格式，没有加入（${skipped.slice(0, 4).map(([k, v]) => `${k} ${v} 个`).join('、')}）` : '';
    toast((got ? `已加入：${got}` : '这个文件夹里没有找到视频、音频或文档（支持 mp4 等视频、mp3 等音频、PDF、Word、EPUB、MOBI、AZW3、PPT）') + skipText
      + (r.merged ? '。原来单独加的已经并进来了' : ''), { ms: 10000 });
  } catch (e) { fail(e); }
}
async function addFiles() {
  try {
    const r = await api('library_add_files');
    if (r.added) { loadLibrary(r.groups); if (!S.settings.sidebar) savePrefs({ sidebar: true }); }
    if (r.added || r.inside) toast((r.added ? `已添加 ${r.added} 个文件` : '') + (r.inside ? `${r.added ? '；' : ''}${r.inside} 个已经在媒体库的文件夹里了，没有重复加` : ''));
  } catch (e) { fail(e); }
}

/* ------------------------------------------------------------ 打开文件 ---- */
async function pickAndOpen() {
  try { const { path } = await api('pick_media'); if (path) openPath(path); } catch (e) { fail(e); }
}
window.openPath = async function (path) {
  if (S.job && !(S.jobInfo && S.jobInfo.id === S.job && S.jobInfo.detachable)) return toast('这个任务做完前不能切换（只要几十秒），请稍等或点上方的「取消」', { error: true });
  // 左上角跟着打开的东西切：书 →「书籍」，音视频 →「课程」（在老师工作台里点出处、拖入文件等也一样）
  const want = BOOK_FILE_RE.test(path) ? 'book' : 'course';
  if (S.view !== want && typeof switchView === 'function') switchView(want);
  try {
    await flushSave();
    const d = await api('open', { path });
    if (S.job && S.jobInfo && S.jobInfo.path !== d.path) {  // 切到别的课：这个任务在后台接着做
      S.detached.set(S.job, S.jobInfo);
      if (S.jobInfo.label !== '正在准备播放…') toast(`《${S.jobInfo.title}》的「${S.jobInfo.label.replace(/[…\s]+$/, '').replace(/中$/, '')}」会在后台接着做，做完会提示你`, { ms: 7000 });
      S.job = null; S.chatAbort = null; updateButtons();
    }
    if (typeof TTS !== 'undefined') TTS.stop();  // 换课：停止朗读
    if (d.kind === 'book') { openBook(d); return; }
    setMode('media');
    S.resumeAt = d.position || 0;
    S.path = d.path; S.doc = d.doc; S.info = d.info; S.undo = []; S.active = -1; S.dirty = false;
    revealInLibrary(d.path);
    // 换课时关掉单句循环：「存疑 / 回听」会自动打开它，以前一直开着，换到别的课就一直循环同一句
    $('#optLoop').checked = false; S.loopIdx = null; updateLoopHint();
    S.docStale = libFiles().some(it => it.stale && it.path.toLowerCase() === d.path.toLowerCase());
    S.lastSaved = d.doc.updated || null; S.rev = d.doc.rev || 0; S.summaryChanges = d.summary_changes || 0; S.pending = d.pending || [];
    S.noSummary = !!d.no_summary;
    $('#btnUndo').disabled = true;
    $('#empty').hidden = true; $('#stageMain').hidden = false;
    $('#fileTitle').textContent = d.doc.title; $('#fileTitle').title = d.path; document.title = `${d.doc.title} - 猛人学习`;
    if (summaryEditing) toggleSummaryEdit(false);
    setupPlayer(d);
    renderTeacherSelect(); renderCues(); renderSummary(); renderChat(); renderFrames(); updateButtons(); updateSaveLabel();
    $('#optReviewed').checked = !!S.doc.reviewed;
    renderLibrary(); busyNotice(d);
    if (d.convert_job) runJob(d.convert_job, '正在准备播放…', { onDone: job => setSource(job.result.src), detachable: true, quiet: true });
  } catch (e) { fail(e); }
};
function setupPlayer(d) {
  const v = $('#video');
  const audio = !d.info.has_video;
  $('#playerWrap').classList.toggle('audio', audio);
  $('#audioCover').hidden = !audio;
  $('#audioName').textContent = d.doc.title;
  v.removeAttribute('src'); v.load();
  applyLayout();
  if (d.src) setSource(d.src);
}
function setSource(src) {
  const v = $('#video');
  S.resumePending = S.resumeAt > 3;
  v.src = src; v.playbackRate = +$('#speed').value;
  v.addEventListener('loadedmetadata', () => {
    const j = S.jumpTo;  // 从老师工作台的出处跳过来：直接跳到那一秒播放（mentor.js）
    if (j && S.path && j.path === S.path.toLowerCase()) {
      S.jumpTo = null; S.resumePending = false;
      v.currentTime = Math.min(j.t, Math.max(0, (v.duration || j.t) - 1)); v.play().catch(() => {});
      return;
    }
    const t = S.resumeAt;
    if (S.resumePending && t < (v.duration || 0) - 3) {
      v.currentTime = t;
      toast(`从上次的 ${fmt(t)} 继续`, { action: { label: '从头播放', run: () => { v.currentTime = 0; savePosition(true); } }, ms: 6000 });
    }
    S.resumePending = false;
  }, { once: true });
}

/* ------------------------------------------------------------ 书籍和文档 ---- */
function setMode(mode) {
  S.mode = mode;
  document.body.classList.toggle('mode-book', mode === 'book');
  $('#bookMain').hidden = mode !== 'book';
  if (mode === 'book') {  // 停掉视频，免得把视频的播放进度记到书上
    $('#stageMain').hidden = true;
    const v = $('#video'); v.pause(); v.removeAttribute('src'); v.load();
  }
}
function bookShift() { const s = S.doc.shift; return s === null || s === undefined || s === '' ? null : +s; }
function pageRef(n) { const s = bookShift(); return s !== null && n - s >= 1 ? `P${n - s}` : `PDF 第 ${n} 页`; }
function openBook(d) {
  S.path = d.path; S.doc = d.doc; S.info = { has_video: false, book: d.book }; S.book = d.book; S.viewer = d.viewer; S.pending = d.pending || [];
  revealInLibrary(d.path);
  S.undo = []; S.dirty = false; S.lastSaved = d.doc.updated || null; S.rev = d.doc.rev || 0; S.bookSel = S.bookSel && S.bookSel.path === d.path ? S.bookSel : { path: d.path, i: -1 };
  S.bookChecked = new Set();
  setMode('book');
  $('#empty').hidden = true;
  $('#fileTitle').textContent = d.doc.title; $('#fileTitle').title = d.path; document.title = `${d.doc.title} - 猛人学习`;
  $('#bkTitle').value = d.doc.title || ''; $('#bkAuthor').value = d.doc.author || '';
  const s = bookShift();
  $('#bkFirst').value = s === null ? '' : s + 1;
  $('#bkFirst').closest('.field').hidden = d.book.kind !== 'pdf';
  $('#bkInfo').textContent = `${d.book.kind_name}，${d.book.pages} ${d.book.unit}，约 ${Math.round(d.book.chars / 1000)} 千字，${d.book.figures} 页有图表` +
    (d.book.kind === 'pdf' && d.book.detected_shift === null ? '。没认出书上的页码，请填上面的「书上第 1 页是 PDF 第几页」，否则锚点写成「PDF 第 X 页」' : '') +
    (d.book.scan && d.book.chars < d.book.pages * 50 ? '。⚠ 这个 PDF 几乎没有文字（每页都是图片），请先点下面的「识别文字（OCR）」' : '') +
    (d.book.companion ? `。同时参考同名 Word「${d.book.companion}」的正文` : '') +
    (!d.book.toc && (d.doc.chapters || []).every(c => /^第 \d+–\d+ 页$|^全文$/.test(c.title)) ? '。⚠ 没认出章节，现在是按页数分段；建议点「AI 读目录分章」' : '');
  updateShiftHint();
  if (d.viewer) { $('#bookPdf').hidden = false; $('#bookText').hidden = true; $('#bookPdf').src = d.viewer; }
  else {
    $('#bookPdf').hidden = true; $('#bookText').hidden = false; $('#bookText').innerHTML = '<div class="muted">正在读取…</div>';
    api('book_pages', { path: d.path }).then(r => {
      $('#bookText').innerHTML = r.pages.map(p => `<div class="bpage" id="bp-${p.n}"><span class="bref">第 ${p.n} ${S.book.unit || '段'}</span>${esc(p.text)}${p.images.map(u => `<img src="${esc(u)}" alt="">`).join('')}</div>`).join('');
    }).catch(fail);
  }
  // 书籍信息平时收起（核对过就不用总看着）；刚打开还没整理、或者有要注意的（⚠）时展开
  $('#bkMetaBox').open = $('#bkInfo').textContent.includes('⚠') || !(d.doc.chapters || []).some(c => (c.md || '').trim());
  $('#bkSrcMode [data-m=orig]').textContent = d.viewer ? 'PDF' : '原样';
  // 扫描版 PDF 才有「识别文字」：没有文字的叫「识别文字（OCR）」，已经有一层（比如 WPS 认得很差的）叫「重新识别文字」
  $('#bkOcr').hidden = !(d.book.kind === 'pdf' && d.book.scan);
  $('#bkOcr').textContent = d.book.chars < d.book.pages * 50 ? '识别文字（OCR）' : '重新识别文字';
  $('#bkOcr').classList.toggle('primary', d.book.chars < d.book.pages * 50);
  $('#bkSrcText').innerHTML = ''; delete $('#bkSrcText').dataset.key;
  applyBookLayout(); setSrcMode(false);
  renderTeacherSelect(); renderBook(); updateButtons(); updateSaveLabel(); renderLibrary(); busyNotice(d);
}
/* Agent（命令行）正在后台自动整理这节课 */
function busyNotice(d) {
  if (d.busy) toast(`后台正在自动整理这节课（${d.busy.since} 开始）。整理完之前在这里改的内容，保存时会先问你。`, { ms: 9000 });
}
function updateShiftHint() {
  const s = bookShift();
  $('#bkShiftHint').textContent = s === null ? '不填就写成「PDF 第 X 页」' : `锚点：PDF 第 ${s + 1} 页 = P1`;
}
function bookJump(n) {
  if (!S.doc || S.mode !== 'book') return;
  n = clamp(Math.round(n), 1, (S.book && S.book.pages) || 1);
  if (S.viewer) $('#bookPdf').src = S.viewer + '#page=' + n;
  else { const el = $('#bp-' + n); if (el) el.scrollIntoView({ block: 'start' }); }
}
function bookMd(src) {
  return md(src).replace(/PDF\s*第\s*(\d{1,4})(?:\s*[-–—]\s*\d{1,4})?\s*页/g, m => `<a class="pglink" data-n="${m.match(/\d+/)[0]}">${m}</a>`)
    .replace(/(^|[^A-Za-z0-9_"=])P(\d{1,4})(?!\d)/g, (m, pre, p) => `${pre}<a class="pglink" data-p="${p}">P${p}</a>`);
}
function chapterState(c) {
  return (c.md || '').trim() ? '<span class="ch-state ok">✓ 已整理</span>' : '<span class="ch-state">未整理</span>';
}
function renderBook() {
  const d = S.doc, list = $('#bkChapters');
  const chs = d.chapters || [];
  list.innerHTML = `<div class="ch-row overview ${S.bookSel.i === -2 ? 'active' : ''}" data-ch="-2"><span>📘</span><span>00、全书总览</span>${(d.overview || '').trim() ? '<span class="ch-state ok">✓ 已生成</span>' : '<span class="ch-state">未生成</span>'}</div>` +
    chs.map((c, i) => (c.part && c.part !== (chs[i - 1] || {}).part ? `<div class="ch-part" title="原书目录里的部分（只用来分组，下面的每一篇单独整理）">${esc(c.part)}</div>` : '')
      + `<div class="ch-row ${S.bookSel.i === i ? 'active' : ''} ${c.part ? 'in-part' : ''}" data-ch="${i}">
      <input type="checkbox" data-chk ${S.bookChecked.has(i) ? 'checked' : ''} title="勾选后点「整理选中的章节」">
      <input type="text" data-f="title" value="${esc(c.title)}" title="章名（可以直接改）">
      ${chapterState(c)}
      <span class="ch-pages">PDF 第<input type="number" data-f="start" value="${c.start}" min="1" title="从 PDF 第几页（${esc(pageRef(c.start))}）">–<input type="number" data-f="end" value="${c.end}" min="1" title="到 PDF 第几页（${esc(pageRef(c.end))}）">页
        <span class="grow"></span><button data-delch title="删除这一章（不影响原文）">✕ 删除</button></span></div>`).join('');
  renderTocCount();
  renderChapterView();
  updateButtons();
}
function renderTocCount() {
  const chs = (S.doc && S.doc.chapters) || [];
  $('#bkTocCount').textContent = chs.length ? `共 ${chs.length} 章 · 已整理 ${chs.filter(c => (c.md || '').trim()).length}` : '';
  $('#bkMetaSum').textContent = S.doc ? ` · 《${S.doc.title || ''}》${bookShift() === null ? '' : ` · 书上第 1 页 = PDF 第 ${bookShift() + 1} 页`}` : '';
}
function renderChapterView() {
  const d = S.doc, i = S.bookSel.i;
  const c = i >= 0 ? (d.chapters || [])[i] : null;
  const text = i === -2 ? d.overview : (c ? c.md : '');
  if (bookReading()) TTS.stop();  // 内容要换了：朗读停下（再点朗读，从这一章上次读到的地方接着读）
  $('#bkTts').disabled = !(text || '').trim();
  $('#bkViewTitle').textContent = i === -2 ? '00、全书总览' : (c ? `${String(i + 1).padStart(2, '0')}、${c.title}（${pageRef(c.start)}–${pageRef(c.end)}）` : '');
  $('#bkView').innerHTML = (text || '').trim() ? bookMd(text)
    : `<div class="ai-empty">${i === -2 ? '章节整理好以后，点「全书总览」生成。' : c ? '这一章还没整理。点「整理选中的章节」开始。' : '先核对上面的书名、作者和页码，再看看章节分得对不对（可以直接改章名和起止页），然后整理。<br>点左边原文里的页码会跳过去。'}</div>`;
  hydrateBookFigs($('#bkView'));
  $('#bkEdit').disabled = !(i === -2 || c); $('#bkCopy').disabled = !(text || '').trim();
  $('#bkViewHint').textContent = c && c.note ? c.note : '';
  if (bookEditing) toggleBookEdit(false);
  if (S.srcRead) loadSrcText();  // 原书正显示文字版：换成这一章的原文（同一章不重读）
  if (bkChatMode) renderBookChat();  // 正在 AI 对话：换成这一章的对话
}

/* ---------- 三列的显示、隐藏、宽度（2026-10-29 用户要求：原书正文、目录、整理笔记三列，各自能隐藏；记在本机） ---------- */
const BOOK_LAYOUT_KEY = 'book-layout';
let bookLayout = { src: 0, toc: 300, hide: {}, prev: null };
try { Object.assign(bookLayout, JSON.parse(localStorage.getItem(BOOK_LAYOUT_KEY) || '{}')); } catch (_) { /* 本机存不了就用默认 */ }
function saveBookLayout() { try { localStorage.setItem(BOOK_LAYOUT_KEY, JSON.stringify(bookLayout)); } catch (_) { /* 本机存不了就不记 */ } }
function applyBookLayout() {
  const h = bookLayout.hide || {}, main = $('#bookMain');
  for (const k of ['src', 'toc', 'note']) $(`#bookMain [data-col="${k}"]`).classList.toggle('collapsed', !!h[k]);
  main.classList.toggle('note-hidden', !!h.note);
  main.classList.toggle('src-hidden', !!h.src);
  $('#bookLeft').style.width = bookLayout.src ? bookLayout.src + 'px' : '';
  $('#bookToc').style.width = (bookLayout.toc || 300) + 'px';
  $('#bookSplit').hidden = !!h.src || (!!h.toc && !!h.note);  // 左边那条：调原书宽度（笔记隐藏时调目录宽度）
  $('#bookSplit2').hidden = !!h.toc || !!h.note;               // 右边那条：调目录宽度
  $('#bkZoom').textContent = h.src && h.toc && !h.note ? '还原' : '放大';
  // 朗读的那一列被隐藏了：停下
  const t = typeof TTS !== 'undefined' && TTS.active();
  if (t && t.root && t.root.closest('.bk-col.collapsed')) TTS.stop();
}
function setBookCol(k, hidden) {
  bookLayout.hide = { ...(bookLayout.hide || {}), [k]: hidden };
  bookLayout.prev = null;
  applyBookLayout(); saveBookLayout();
}
function bookZoom() {
  const h = bookLayout.hide || {};
  if (h.src && h.toc && !h.note) bookLayout.hide = bookLayout.prev || {};
  else { bookLayout.prev = { ...h }; bookLayout.hide = { src: true, toc: true, note: false }; }
  applyBookLayout(); saveBookLayout();
}

/* ---------- 原书：PDF / 文字版；朗读原文（2026-10-29 用户选的做法：读的时候换成这一章的文字版，正在读的句子高亮） ---------- */
const srcReading = () => typeof TTS !== 'undefined' && !!TTS.active() && TTS.active().root === $('#bkSrcText');
function setSrcMode(read, auto) {
  S.srcRead = read; S.srcAuto = read && !!auto;
  $$('#bkSrcMode button').forEach(b => b.classList.toggle('on', (b.dataset.m === 'read') === read));
  $('#bkSrcText').hidden = !read;
  $(S.viewer ? '#bookPdf' : '#bookText').hidden = read;
  if (!read) { if (srcReading()) TTS.stop(); return Promise.resolve(true); }
  return loadSrcText();
}
let srcSeq = 0, figWatch = null;
async function loadSrcText() {
  const box = $('#bkSrcText'), i = S.bookSel.i, c = i >= 0 ? (S.doc.chapters || [])[i] : null;
  const a = c ? c.start : 1, b = c ? c.end : ((S.book && S.book.pages) || 1), path = S.path, key = `${path}|${a}-${b}|${c ? c.title : ''}`;
  if (box.dataset.key === key) return true;
  const my = ++srcSeq;
  if (srcReading()) TTS.stop(true);
  box.dataset.key = key;
  box.innerHTML = '<div class="muted">正在读取原文…</div>';
  try {
    const r = await api('book_read', { path, start: a, end: b, shift: bookShift() });
    if (my !== srcSeq || S.path !== path) return false;
    // 页码、「这一页有图表」的说明不读（tts-skip）；PDF 有图表的页，滚到附近时把这一页的图显示出来，朗读跳过图
    box.innerHTML = `<h3>${esc(c ? c.title : '全书')}<span class="muted small-text tts-skip">　原文 ${esc(pageRef(a))}–${esc(pageRef(b))}</span></h3>`
      + (r.pages.map(p => `<div class="bpage"><a class="pglink bref" data-n="${p.n}" title="在 PDF 里看这一页">${esc(p.ref)}</a>`
        + p.paras.map(x => `<p>${esc(x)}</p>`).join('') + p.images.map(u => `<img class="bfig-img" src="${esc(u)}" alt="" title="点开放大">`).join('')
        + (r.pdf && p.figure ? `<div class="bfig tts-skip" data-n="${p.n}"><div class="muted small-text">这一页有图表（朗读时跳过，点图放大）</div></div>` : '') + '</div>').join('')
        || '<div class="muted">这几页没有文字</div>');
    box.scrollTop = 0;
    watchFigs(box);
    return true;
  } catch (e) { if (my === srcSeq) { delete box.dataset.key; box.innerHTML = `<div class="muted">读不出原文：${esc(e.message)}</div>`; } return false; }
}
function watchFigs(box) {
  if (figWatch) figWatch.disconnect();
  figWatch = new IntersectionObserver(ents => ents.forEach(en => {
    if (!en.isIntersecting) return;
    const el = en.target; figWatch.unobserve(el);
    api('book_page_img', { path: S.path, n: +el.dataset.n }).then(r => {
      if (el.isConnected) el.insertAdjacentHTML('beforeend', `<img class="bfig-img" src="${esc(r.src)}" alt="第 ${el.dataset.n} 页" title="点开放大">`);
    }).catch(() => {});
  }), { root: box, rootMargin: '800px 0px' });
  $$('.bfig', box).forEach(el => figWatch.observe(el));
}
/* 朗读原文用的来源（tts.js 读）：关掉朗读时，如果文字版是朗读原文自动切过来的，换回 PDF */
function bookSrcSource() {
  const i = S.bookSel ? S.bookSel.i : -1;
  return { root: $('#bkSrcText'), pane: 'booksrc:' + i, name: '原文', anchor: $('#bookLeft .bk-col-head'), onClose: () => { if (S.srcAuto) setSrcMode(false); } };
}
async function bookReadSrc() {
  if (!S.srcRead) { if (!(await setSrcMode(true, true))) return; }
  else if (!(await loadSrcText())) return;
  TTS.button(bookSrcSource());
}
let bookEditing = false;
function toggleBookEdit(on) {
  bookEditing = on ?? !bookEditing;
  if (bookEditing && bookReading()) TTS.stop();
  $('#bkEditor').hidden = !bookEditing; $('#bkView').hidden = bookEditing || bkChatMode;
  $('#bkEdit').textContent = bookEditing ? '完成编辑' : '编辑';
  const i = S.bookSel.i;
  if (bookEditing) $('#bkEditor').value = i === -2 ? (S.doc.overview || '') : ((S.doc.chapters[i] || {}).md || '');
  else renderChapterViewOnly();
}
/* 原书图示：按页码和位置裁出来显示（bridge.do_book_fig，和剪藏时同一个裁法） */
function hydrateBookFigs(root) {
  const path = S.path;
  $$('.book-fig', root).forEach(fig => {
    api('book_fig', { path, n: +fig.dataset.pn, region: fig.dataset.reg || '' })
      .then(r => { const img = fig.querySelector('img'); if (img && S.path === path) img.src = r.src; })
      .catch(() => { fig.querySelector('figcaption').insertAdjacentText('beforeend', '（图裁不出来）'); });
  });
}
const bookReading = () => typeof TTS !== 'undefined' && !!TTS.active() && TTS.active().root === $('#bkView');
function renderChapterViewOnly() { const keep = bookEditing; bookEditing = false; renderChapterView(); bookEditing = keep; }
async function bookOrganize(indexes) {
  if (!indexes.length) return toast('没有要整理的章节', { error: true });
  try {
    const res = await runSteps(['organize'], '正在整理…', { opts: { chapters: indexes }, onItems: bookLive });
    const out = stepResult(res, 'organize');
    if (out && out.chapters) S.bookSel.i = indexes[indexes.length - 1];
    renderDoc();
    if (!out) return false;
    if (out.skipped !== undefined) { toast(out.skipped); return true; }
    toast(`整理好了 ${out.chapters} 章${out.added ? `，查漏补了 ${out.added} 条` : ''}。核对后可以点「全书总览」和「剪藏」`, { ms: 8000 });
    return true;
  } catch (e) { fail(e); return false; }
}
/* 整理时每存好一章，后台推一条 {kind: 'chapter'}：只更新这一章（不重画整个列表，正在改的章名、正在编辑的笔记不受影响） */
function bookLive(items) {
  for (const x of items) {
    const c = x.kind === 'chapter' && S.doc && (S.doc.chapters || [])[x.i];
    if (!c || c.title !== x.title || +c.start !== +x.start || +c.end !== +x.end) continue;  // 这期间分章改了：等做完重新读取
    Object.assign(c, { md: x.md, updated: x.updated, note: x.note });
    const st = $(`#bkChapters .ch-row[data-ch="${x.i}"] .ch-state`);
    if (st) st.outerHTML = chapterState(c);
    renderTocCount();
    if (S.bookSel.i === x.i && !bookEditing) renderChapterView();
  }
}
/* ---------- 书的 AI 对话（2026-10-29）：「整理笔记」这一列里和笔记二选一。笔记是提炼（约原文 1/5），所以 AI 读原文回答：
   问这一章读这一章的原文全文 + 笔记，问整本书读全部笔记 + 本机挑的相关原文页（bridge.do_book_chat）。
   问答由后台写进 book_chat（不走界面保存，后台可能正在整理这本书），界面不改 dirty。 ---------- */
let bkChatMode = false, bkChatScope = 'chapter';
function bookChatWhere() {
  const i = S.bookSel ? S.bookSel.i : -1, c = i >= 0 ? (S.doc.chapters || [])[i] : null;
  return bkChatScope === 'book' || !c ? { scope: 'book', key: 'book', i: -1, c: null } : { scope: 'chapter', key: `ch:${c.start}-${c.end}`, i, c };
}
function setNoteMode(chat) {
  bkChatMode = !!chat;
  if (bkChatMode && bookEditing) toggleBookEdit(false);
  if (bkChatMode && bookReading()) TTS.stop();
  $$('#bkNoteMode button').forEach(b => b.classList.toggle('on', (b.dataset.m === 'chat') === bkChatMode));
  $('#bookNote').classList.toggle('chat-mode', bkChatMode);
  $('#bkChat').hidden = !bkChatMode;
  $('#bkView').hidden = bkChatMode || bookEditing;
  if (bkChatMode) { renderBookChat(); $('#bkChatInput').focus(); }
}
function renderBookChat() {
  if (!bkChatMode || !S.doc) return;
  const w = bookChatWhere();
  $$('#bkChatScope button').forEach(b => b.classList.toggle('on', b.dataset.s === w.scope));
  $('#bkChatHint').textContent = w.scope === 'book' ? 'AI 读全部章节的笔记，再从原文里挑出和问题相关的页' : `AI 读「${w.c.title}」的原文全文和笔记`;
  $('#bkChatInput').placeholder = (w.scope === 'book' ? '问整本书…' : '问这一章…') + '（Enter 发送，Shift+Enter 换行）';
  const msgs = ((S.doc.book_chat || {})[w.key]) || [];
  const box = $('#bkChatBox');
  box.innerHTML = msgs.length
    ? msgs.map(m => `<div class="msg ${m.role}"><div class="bubble ${m.role === 'assistant' ? 'md' : ''}">${m.role === 'assistant' ? bookMd(m.content) : esc(m.content)}</div></div>`).join('')
    : `<div class="ai-empty">${w.scope === 'book' ? 'AI 读过全部章节的提炼笔记，回答时再从原文里挑出和问题相关的页。' : `AI 读过「${esc(w.c.title)}」的原文全文和提炼笔记。`}<br>回答里的页码可以点，原书那一列会跳到那一页。</div>`;
  box.scrollTop = box.scrollHeight;
}
async function sendBookChat(q) {
  q = (q || '').trim(); if (!q || !S.doc || S.chatAbort) return;
  const w = bookChatWhere(), path = S.path;
  S.doc.book_chat = S.doc.book_chat || {};
  const list = S.doc.book_chat[w.key] = S.doc.book_chat[w.key] || [];
  list.push({ role: 'user', content: q });
  $('#bkChatInput').value = '';
  renderBookChat();
  const box = $('#bkChatBox');
  box.insertAdjacentHTML('beforeend', '<div class="msg assistant"><div class="bubble md typing"></div></div>');
  const bubble = box.lastElementChild.firstElementChild;
  $('#bkChatSend').textContent = '停止';
  try {
    const text = await streamAI({ api: 'book_chat', path, scope: w.scope, i: w.i, key: w.key, messages: list },
      t => { if (bubble.isConnected) { bubble.innerHTML = bookMd(t); box.scrollTop = box.scrollHeight; } });
    list.push({ role: 'assistant', content: text.trim() });
  } catch (e) {
    if (e.name === 'AbortError') { const t = bubble.innerText.trim(); if (t) list.push({ role: 'assistant', content: t + '（已停止）' }); }
    else { fail(e); list.pop(); $('#bkChatInput').value = q; }
  }
  $('#bkChatSend').textContent = '发送';
  if (S.path === path) { renderBookChat(); syncBookFromDisk(); }  // 后台存了对话、版本号变了：没有未保存的修改就马上对上
}
/* 点回答里的页码：原书那一列跳到那一页（原书隐藏着就打开，正显示文字版就换回 PDF） */
function bookGo(n) {
  if ((bookLayout.hide || {}).src) setBookCol('src', false);
  if (S.srcRead) setSrcMode(false);
  bookJump(n);
}
/* 书被命令行或后台队列整理（不是这里前台在整理）时，每 20 秒看一次有没有新整理好的章节（文件的 rev 变了才重画）。
   不只看打开时有没有在整理：可能是先打开、后开始整理的。正在改东西、正在听朗读时不打扰 */
async function syncBookFromDisk() {  // 正在改东西、朗读、等 AI 回答时不打扰
  const idle = () => S.mode === 'book' && S.doc && !S.job && !S.dirty && !bookEditing && !bookReading() && !S.chatAbort && !$('#bkChapters').contains(document.activeElement);
  if (!idle()) return;
  const path = S.path;
  try {
    const r = await api('doc', { path });
    if (S.path !== path || !idle() || (r.doc.rev || 0) === S.rev) return;
    S.doc = r.doc; S.rev = r.doc.rev || 0; S.lastSaved = r.doc.updated || S.lastSaved; S.pending = r.pending || [];
    syncLibFromDoc(); renderBook();  // 左边媒体库的「整理 x/18」也跟着变
  } catch (e) { /* 下次再看 */ }
}
setInterval(syncBookFromDisk, 20000);
async function bookOverview() {
  if (!(S.doc.chapters || []).some(c => (c.md || '').trim())) return toast('还没有整理好的章节', { error: true });
  try {
    const res = await runSteps(['overview'], 'AI 正在写全书总览…', { opts: { allow_partial: true } });
    const out = stepResult(res, 'overview');
    if (out && out.skipped === undefined) S.bookSel.i = -2;
    renderDoc();
    if (out) toast(out.skipped !== undefined ? out.skipped : '全书总览写好了');
  } catch (e) { fail(e); }
}
/* 扫描书识别文字（2026-10-29，bridge.do_book_ocr / core/ocr.py）：做完用原来的文件名换上新的可搜索 PDF，重新打开 */
async function bookOcr() {
  if (S.job || !S.doc) return;
  const again = (S.book.chars || 0) >= (S.book.pages || 1) * 50, done = (S.doc.chapters || []).some(c => (c.md || '').trim());
  if (!confirm('用 Windows 自带的文字识别，把这本扫描书的每一页认成文字（在这台电脑上识别，不联网、不花钱，300 页大约 2 分钟）。\n\n'
    + '做完会生成带文字的新 PDF，用原来的文件名；原文件挪进同一个文件夹里的「_OCR前的原件」，不删。'
    + (again ? '\n\n这本书已经有一层文字（可能是以前别的软件识别的），会换成新识别的。' : '')
    + (done ? '\n\n已经整理好的章节不受影响。' : '\n\n做完会按新文字重新分章。') + '\n\n开始吗？')) return;
  await flushSave();
  const path = S.path;
  if (S.viewer) $('#bookPdf').src = 'about:blank';  // 放开原文件，才能挪走
  let out = null;
  try {
    const r = await api('book_ocr', { path });
    await runJob(r.job, '正在识别文字…', { onDone: job => { out = job.result; } });
  } catch (e) { fail(e); }
  await window.openPath(path);  // 换成了新的文件：重新打开
  if (out) toast(`识别好了：${out.pages} 页认出约 ${Math.round(out.chars / 1000)} 千字`
    + (out.empty.length ? `，${out.empty.length} 页几乎没字（多半是空白页或整页的图）` : '')
    + '。原文件在同一文件夹的「_OCR前的原件」里，确认没问题可以删。' + (done ? '' : '章节没分对的话，点「AI 读目录分章」。'), { ms: 14000 });
}
/* 重新分章：起止页一样的章，保留整理好的内容（2026-10-29 用户确认的方案：只重新整理变了的） */
function keepNotes(old, chs) {
  let kept = 0;
  const chapters = chs.map(c => {
    const o = old.find(x => +x.start === +c.start && +x.end === +c.end && (x.md || '').trim());
    if (!o) return { ...c, md: '' };
    kept++;
    return { ...c, md: o.md, ...(o.updated ? { updated: o.updated } : {}), ...(o.note ? { note: o.note } : {}) };
  });
  return { chapters, kept };
}
async function bookAiToc() {
  if ((S.doc.chapters || []).some(c => (c.md || '').trim()) && !confirm('重新分章：起止页没变的章保留已经整理的内容，其他的要重新整理。继续吗？')) return;
  try {
    const r = await api('book_chapters', { path: S.path, ai: true, shift: bookShift() });
    await runJob(r.job, 'AI 正在读目录…', {
      onDone: job => {
        const chs = job.result.chapters || [];
        if (!chs.length) return toast('AI 没有从目录里认出章节，可以手动改章名和起止页', { error: true });
        const { chapters, kept } = keepNotes(S.doc.chapters || [], chs);
        S.doc.chapters = chapters; S.bookSel.i = 0; S.bookChecked = new Set();
        renderBook(); markDirty();
        toast(`按原书目录分成了 ${chs.length} 篇` + (kept ? `，其中 ${kept} 篇起止页没变、保留了整理好的内容；其余的点「整理全部未整理的」` : '') + '。请核对起止页', { ms: 9000 });
      },
    });
  } catch (e) { fail(e); }
}
async function bookClip() {
  try {
    await flushSave();
    let r = await api('book_clip', { doc: S.doc });
    if (r.conflict) {
      if (!confirm(`下面这些笔记上次剪藏后在 Obsidian 里改过：\n${r.changed.join('\n')}\n\n继续剪藏会用软件里的内容更新软件写的那些小节：你在这些小节里直接改的文字会没掉。你自己加的小节、标注（> [!…]）和属性会保留。改过的笔记会先备份到：\n%APPDATA%\\猛人学习\\backups\\笔记\n\n要继续剪藏吗？`)) {
        toast('没有剪藏，Obsidian 里的笔记保持不变');
        return true;
      }
      r = await api('book_clip', { doc: S.doc, force: true });
    }
    S.doc.clip = { dir: r.dir, time: r.time, files: r.records }; markDirty(); save();
    toast(`已剪藏 ${r.files.length} 篇笔记到 Obsidian` + (r.backups ? `，${r.backups} 篇改过的旧笔记已备份` : '')
      + (r.moved ? `。有 ${r.moved} 条你写的标注找不到原来那句话，放在了那一节的最后` : ''),
      { action: { label: '在 Obsidian 中打开', run: () => api('reveal', { target: r.uri }) }, ms: 8000 });
    return true;
  } catch (e) { fail(e); return false; }
}
function bindBook() {
  $('#bkTitle').addEventListener('input', e => { if (S.doc) { S.doc.title = e.target.value.trim(); $('#fileTitle').textContent = S.doc.title; renderTocCount(); markDirty(); } });
  $('#bkAuthor').addEventListener('input', e => { if (S.doc) { S.doc.author = e.target.value.trim(); markDirty(); } });
  $('#bkFirst').addEventListener('change', e => {
    const v = e.target.value.trim();
    S.doc.shift = v === '' ? null : Math.max(0, +v - 1);
    updateShiftHint(); renderBook(); markDirty();
  });
  $('#bkChapters').addEventListener('click', e => {
    const row = e.target.closest('[data-ch]'); if (!row) return;
    const i = +row.dataset.ch;
    if (e.target.matches('[data-chk]')) { e.target.checked ? S.bookChecked.add(i) : S.bookChecked.delete(i); return; }
    if (e.target.matches('[data-delch]')) {
      const c = S.doc.chapters[i];
      if ((c.md || '').trim() && !confirm(`删除「${c.title}」？已整理的内容也会删掉。`)) return;
      S.doc.chapters.splice(i, 1); S.bookChecked = new Set(); S.bookSel.i = -1; renderBook(); markDirty(); return;
    }
    if (S.bookSel.i !== i) { S.bookSel.i = i; $$('.ch-row', $('#bkChapters')).forEach(r => r.classList.toggle('active', r === row)); renderChapterView(); }
    if (i >= 0 && !e.target.matches('input')) bookJump(S.doc.chapters[i].start);
  });
  $('#bkChapters').addEventListener('change', e => {
    const row = e.target.closest('[data-ch]'); const f = e.target.dataset.f; if (!row || !f) return;
    const c = S.doc.chapters[+row.dataset.ch];
    c[f] = f === 'title' ? e.target.value.trim() : clamp(Math.round(+e.target.value || 1), 1, (S.book && S.book.pages) || 1);
    if (c.end < c.start) c.end = c.start;
    markDirty(); renderChapterView();
  });
  $('#bkRun').onclick = () => { if (S.job) return; const sel = [...S.bookChecked].sort((a, b) => a - b); bookOrganize(sel.length ? sel : (S.bookSel.i >= 0 ? [S.bookSel.i] : [])); };
  $('#bkRunAll').onclick = () => { if (!S.job) bookOrganize(S.doc.chapters.map((c, i) => (c.md || '').trim() ? -1 : i).filter(i => i >= 0)); };
  $('#bkOverview').onclick = () => { if (!S.job) bookOverview(); };
  $('#bkAiToc').onclick = () => { if (!S.job) bookAiToc(); };
  $('#bkOcr').onclick = () => { if (!S.job) bookOcr(); };
  $('#bkAddCh').onclick = () => {
    const last = S.doc.chapters[S.doc.chapters.length - 1];
    const start = last ? Math.min(last.end + 1, S.book.pages) : 1;
    S.doc.chapters.push({ title: '新的一章', start, end: S.book.pages, md: '' });
    S.bookSel.i = S.doc.chapters.length - 1; renderBook(); markDirty();
  };
  $('#bkEdit').onclick = () => toggleBookEdit();
  $('#bkEditor').addEventListener('input', e => {
    const i = S.bookSel.i;
    if (i === -2) S.doc.overview = e.target.value; else if (S.doc.chapters[i]) S.doc.chapters[i].md = e.target.value;
    markDirty();
  });
  $('#bkCopy').onclick = () => { const i = S.bookSel.i; navigator.clipboard.writeText(i === -2 ? S.doc.overview : S.doc.chapters[i].md).then(() => toast('已复制')); };
  $('#bkView').addEventListener('click', e => {
    const fimg = e.target.closest('.book-fig img'); if (fimg && fimg.src) return showLightbox(fimg.src);
    const a = e.target.closest('.pglink'); if (!a) return;
    if (a.dataset.n) bookJump(+a.dataset.n);
    else { const s = bookShift(); bookJump(+a.dataset.p + (s === null ? 0 : s)); }
  });
  // 三列：隐藏 / 显示 / 放大；拖分隔线调宽度
  $('#bookMain').addEventListener('click', e => {
    const hide = e.target.closest('[data-hide]'), show = e.target.closest('[data-show]');
    if (hide) setBookCol(hide.dataset.hide, true);
    else if (show) setBookCol(show.dataset.show, false);
  });
  $('#bkZoom').onclick = bookZoom;
  // 书的 AI 对话
  $('#bkNoteMode').addEventListener('click', e => { const b = e.target.closest('[data-m]'); if (b && S.doc) setNoteMode(b.dataset.m === 'chat'); });
  $('#bkChatScope').addEventListener('click', e => {
    const b = e.target.closest('[data-s]'); if (!b || S.chatAbort) return;
    if (b.dataset.s === 'chapter' && !(S.bookSel.i >= 0)) return toast('先在目录里点一章');
    bkChatScope = b.dataset.s; renderBookChat();
  });
  $('#bkChatSend').onclick = () => { if (S.chatAbort) S.chatAbort.abort(); else sendBookChat($('#bkChatInput').value); };
  $('#bkChatInput').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendBookChat(e.target.value); } });
  $('#bkChips').addEventListener('click', e => { const b = e.target.closest('[data-q]'); if (b) sendBookChat(b.dataset.q); });
  $('#bkChatClear').onclick = async () => {
    const w = bookChatWhere();
    if (S.chatAbort || !((S.doc.book_chat || {})[w.key] || []).length) return;
    if (!confirm(w.scope === 'book' ? '清空整本书的 AI 对话记录？' : `清空「${w.c.title}」的 AI 对话记录？`)) return;
    try { await api('book_chat_clear', { path: S.path, key: w.key }); delete S.doc.book_chat[w.key]; renderBookChat(); syncBookFromDisk(); } catch (e) { fail(e); }
  };
  $('#bkChatBox').addEventListener('click', e => {
    const a = e.target.closest('.pglink'); if (!a) return;
    if (a.dataset.n) bookGo(+a.dataset.n); else { const s = bookShift(); bookGo(+a.dataset.p + (s === null ? 0 : s)); }
  });
  $('#bkSrcMode').addEventListener('click', e => { const b = e.target.closest('[data-m]'); if (b && S.doc) setSrcMode(b.dataset.m === 'read'); });
  $('#bkSrcTts').onclick = () => { if (S.doc) bookReadSrc(); };
  $('#bkSrcText').addEventListener('click', e => {
    const img = e.target.closest('.bfig-img'); if (img) return showLightbox(img.src);
    const a = e.target.closest('.pglink'); if (a) { setSrcMode(false); bookJump(+a.dataset.n); }  // 点页码：回到 PDF 看这一页
  });
  const W = () => $('#bookMain').getBoundingClientRect().width;
  dragger($('#bookSplit'), 'x', ev => {
    if ((bookLayout.hide || {}).note) {  // 笔记隐藏时原书占满，这条线调目录宽度
      bookLayout.toc = Math.round(clamp($('#bookToc').getBoundingClientRect().right - ev.clientX, 210, 700));
    } else {
      const left = $('#bookLeft').getBoundingClientRect().left;
      bookLayout.src = Math.round(clamp(ev.clientX - left, 240, W() - 380 - ((bookLayout.hide || {}).toc ? 34 : $('#bookToc').getBoundingClientRect().width)));
    }
    applyBookLayout();
  }, saveBookLayout);
  dragger($('#bookSplit2'), 'x', ev => {
    bookLayout.toc = Math.round(clamp(ev.clientX - $('#bookToc').getBoundingClientRect().left, 210, Math.max(210, W() - 340 - ((bookLayout.hide || {}).src ? 34 : $('#bookLeft').getBoundingClientRect().width))));
    applyBookLayout();
  }, saveBookLayout);
}

/* 播放进度：播放中每 5 秒、暂停、关闭软件时记下来 */
let posAt = 0;
function savePosition(force) {
  const v = $('#video');
  if (!S.path || S.resumePending || !v.getAttribute('src') || !isFinite(v.duration) || !v.duration) return Promise.resolve();
  const now = Date.now();
  if (!force && now - posAt < 5000) return Promise.resolve();
  posAt = now;
  const t = v.currentTime > v.duration - 3 ? 0 : v.currentTime;
  patchLib(S.path, { pos: t, duration: v.duration }, true);
  return api('position', { path: S.path, t, d: v.duration }).catch(() => {});
}

/* ------------------------------------------------------------- 字幕列表 ---- */
const ICONS = {
  align: '<svg viewBox="0 0 24 24"><circle cx="12" cy="13" r="8"/><path d="M12 9v4l2 2M9 2h6"/></svg>',
  split: '<svg viewBox="0 0 24 24"><path d="M4 12h16M12 4v4m0 8v4"/></svg>',
  merge: '<svg viewBox="0 0 24 24"><path d="M7 4v6a5 5 0 0 0 10 0V4M12 15v5"/></svg>',
  del: '<svg viewBox="0 0 24 24"><path d="M4 7h16M10 11v6m4-6v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg>',
};
function doubtOf(seg) {
  const ds = S.doc && S.doc.doubts;
  return ds && seg ? ds.find(d => Math.abs(d.t - seg.start) < 0.02) || null : null;
}
function cueHTML(seg, i) {
  const d = doubtOf(seg);
  return `<div class="cue${d ? ' doubt' : ''}" data-i="${i}">
    <button class="ts" tabindex="-1" title="跳到这里">${fmt(seg.start)}</button>
    <div class="cue-body">
      <div class="src" contenteditable="plaintext-only" spellcheck="false">${esc(seg.text)}</div>
      <div class="tr" contenteditable="plaintext-only" spellcheck="false" data-ph="${S.doc.lang === 'en' ? '（还没有译文，点「翻译」或在这里手动输入）' : ''}">${esc(seg.tr || '')}</div>
      ${d ? `<div class="doubt-tag"><span class="why" title="${esc(d.reason)}">存疑：${esc(d.reason)}</span><button data-doubt="play" title="单句循环播放这一句">回听</button><button data-doubt="ok" title="已经回听确认（或改好了），去掉标记">已确认</button></div>` : ''}
    </div>
    <div class="cue-tools">
      <button data-act="align" title="把这一句的开始时间设为当前播放位置">${ICONS.align}</button>
      <button data-act="split" title="在光标处拆成两句">${ICONS.split}</button>
      <button data-act="merge" title="与下一句合并">${ICONS.merge}</button>
      <button data-act="del" title="删除这一句">${ICONS.del}</button>
    </div>
  </div>`;
}
function renderCues() {
  const box = $('#cues');
  const segs = S.doc ? S.doc.segments : [];
  box.innerHTML = segs.length ? `<div class="cues-inner">${segs.map(cueHTML).join('')}</div>`
    : `<div class="cues-empty">这个文件还没有字幕。<br>点上方的「生成字幕」或「一键整理」开始。</div>`;
  placeSubShots();
  S.active = -1;
  $('#cueCount').textContent = segs.length ? `${segs.length} 句` : '';
  updateDoubtBtn();
  runSearch(false);
  updateActive(true);
}
function cuesInner() {
  let inner = $('.cues-inner', $('#cues'));
  if (!inner) { $('#cues').innerHTML = '<div class="cues-inner"></div>'; inner = $('.cues-inner', $('#cues')); }
  return inner;
}
function cueEl(i) { return $(`.cue[data-i="${i}"]`, $('#cues')); }
/* ---------- 字幕里手动插的截图（2026-10-30 用户要求）：S.doc.sub_shots = [{t: 截图时间, at: 挂在哪一句后面（那一句的开始时间）}]。
   位置按 at 找「开始时间不晚于它的最后一句」（findIndex），句子删了、拆了也找得到；上移 / 下移就是换挂到上一句 / 下一句
   （语音识别断句不准时，图会落在不对的句子后面）。剪藏时 doc.sub_shot_images 插进「完整字幕」对应的位置。 ---------- */
function placeSubShots() {
  const inner = $('.cues-inner', $('#cues'));
  if (!inner) return;
  $$('.sub-shot', inner).forEach(el => el.remove());
  const shots = (S.doc && S.doc.sub_shots) || [];
  shots.map((x, k) => ({ x, k, i: findIndex(x.at) })).sort((a, b) => a.i - b.i || a.x.t - b.x.t).forEach(({ x, k, i }) => {
    const html = `<div class="sub-shot${x.w ? ' sized' : ''}" data-sk="${k}"${x.w ? ` style="max-width:${x.w + 2}px"` : ''}>
      <img alt="" title="点击看大图">
      <div class="sub-shot-bar"><a class="tslink" data-t="${x.t}">[${fmt(x.t)}]</a> 截图
        <span class="grow"></span>
        <button data-ss="up" title="挪到上一句后面">↑ 上移</button><button data-ss="down" title="挪到下一句后面">↓ 下移</button><button data-ss="crop" title="只留需要的那一块">✂ 裁剪</button><button data-ss="size" title="调整这张图显示多大（拖滑块马上看到效果）">⇲ 大小</button><button data-ss="del" title="删掉这张截图">删除</button></div>
    </div>`;
    const cue = cueEl(i);
    if (cue) {  // 挂在第 i 句后面：同一句后面有几张时，按插入顺序往后排
      let after = cue;
      while (after.nextElementSibling && after.nextElementSibling.classList.contains('sub-shot')) after = after.nextElementSibling;
      after.insertAdjacentHTML('afterend', html);
    } else inner.insertAdjacentHTML('afterbegin', html);  // 挂在第一句前面
    const img = $(`.sub-shot[data-sk="${k}"] img`, inner);
    frameUrl(x.t, false, x.crop).then(u => { if (u && img) img.src = u; });
  });
}
function insertSubShot() {
  const v = $('#video');
  if (!S.doc || !v.getAttribute('src')) return;
  if (!S.info || !S.info.has_video) return toast('这是音频，没有画面可以截');
  if (!S.doc.segments.length) return toast('还没有字幕，先生成字幕再插截图');
  v.pause();
  const t = +v.currentTime.toFixed(2), i = Math.max(0, findIndex(t));
  S.doc.sub_shots = S.doc.sub_shots || [];
  if (S.doc.sub_shots.some(x => Math.abs(x.t - t) < 0.5)) return toast('这个时间已经插过一张了');
  S.doc.sub_shots.push({ t, at: S.doc.segments[i].start });
  placeSubShots(); markDirty();
  const el = $(`.sub-shot[data-sk="${S.doc.sub_shots.length - 1}"]`); if (el) el.scrollIntoView({ block: 'nearest' });
  toast('截图插在这一句后面了；位置不对可以点图下面的「上移 / 下移」', { ms: 5000 });
}
function moveSubShot(k, act) {
  const x = (S.doc.sub_shots || [])[k], segs = S.doc.segments; if (!x) return;
  if (act === 'crop') return openShotEdit(x.t, x.crop).then(r => { if (r) { setShotProp(x, 'crop', r.crop); placeSubShots(); markDirty(); } });
  if (act === 'del') {
    if (!confirm('删掉这张截图？')) return;
    S.doc.sub_shots.splice(k, 1);
  } else {
    const i = findIndex(x.at), j = act === 'up' ? i - 1 : i + 1;
    if (j < -1 || j >= segs.length) return toast(act === 'up' ? '已经在最前面了' : '已经在最后面了');
    x.at = j < 0 ? -1 : segs[j].start;  // -1：挂在第一句前面
  }
  placeSubShots(); markDirty();
  const el = $(`.sub-shot[data-sk="${k}"]`); if (el) el.scrollIntoView({ block: 'nearest' });
}
function findIndex(t) {
  const segs = S.doc ? S.doc.segments : [];
  let lo = 0, hi = segs.length - 1, ans = -1;
  while (lo <= hi) { const mid = (lo + hi) >> 1; if (segs[mid].start <= t + 0.05) { ans = mid; lo = mid + 1; } else hi = mid - 1; }
  return ans;
}
function updateActive(force) {
  if (!S.doc) return;
  const v = $('#video'), t = v.currentTime || 0;
  const segs = S.doc.segments;
  let i = findIndex(t);
  if ($('#optLoop').checked && S.loopIdx != null && segs[S.loopIdx] && !v.paused) {
    const seg = segs[S.loopIdx];
    if (t >= seg.end - 0.03 || t < seg.start - 0.5) { v.currentTime = seg.start; return; }
    i = S.loopIdx;
  }
  if (i !== S.active || force) {
    const old = cueEl(S.active); if (old) old.classList.remove('active');
    S.active = i;
    const el = cueEl(i);
    if (el) {
      el.classList.add('active');
      const recentlyScrolled = Date.now() - S.userScrollAt < 2500;
      if (S.settings.follow && !S.editing && !recentlyScrolled && !v.paused) el.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  }
  updateLoopHint();
  if (S.settings.overlay) {
    const seg = segs[i];
    const show = seg && t <= seg.end + 0.6;
    $('#overlay').innerHTML = show ? esc(seg.text) + (S.settings.show_tr && seg.tr ? `<span class="o-tr">${esc(seg.tr)}</span>` : '') : '';
  }
}
// 单句循环开着时，开关旁边写明在循环哪一句，点一下关掉
function updateLoopHint() {
  const on = $('#optLoop').checked;
  const seg = on && S.doc && S.loopIdx != null ? S.doc.segments[S.loopIdx] : null;
  const text = !on ? '' : seg ? `正在循环 ${fmt(seg.start)} 这一句` : '单句循环已打开';
  const h = $('#loopHint');
  if (h.textContent !== text) h.textContent = text;
  h.hidden = !on;
}
function seek(t, play) {
  const v = $('#video');
  if (!v.getAttribute('src')) return;
  v.currentTime = Math.max(0, t + 0.01);
  if (play) v.play().catch(() => {});
  setTimeout(() => updateActive(true), 30);
}
function doubtIndexes() {
  if (!S.doc || !(S.doc.doubts || []).length) return [];
  return S.doc.segments.map((seg, i) => doubtOf(seg) ? i : -1).filter(i => i >= 0);
}
function updateDoubtBtn() {
  const n = doubtIndexes().length;
  $('#btnDoubts').hidden = !n;
  $('#btnDoubts').textContent = `存疑 ${n}`;
}
function playLoop(i) {
  const seg = S.doc.segments[i]; if (!seg) return;
  $('#optLoop').checked = true; S.loopIdx = i;
  seek(seg.start, true);
  const el = cueEl(i); if (el) { el.scrollIntoView({ block: 'center' }); S.userScrollAt = Date.now(); }
}
function nextDoubt() {
  const list = doubtIndexes(); if (!list.length) return;
  const i = list.find(x => x > S.active) ?? list[0];
  playLoop(i);
}
function resolveDoubt(i) {
  const seg = S.doc.segments[i], d = doubtOf(seg); if (!d) return;
  S.doc.doubts = S.doc.doubts.filter(x => x !== d);
  const el = cueEl(i);
  if (el) { el.classList.remove('doubt'); const tag = $('.doubt-tag', el); if (tag) tag.remove(); }
  updateDoubtBtn(); markDirty();
}
function onCueInput(e) {
  const el = e.target.closest('.src, .tr'); if (!el) return;
  const i = +el.closest('.cue').dataset.i;
  const seg = S.doc.segments[i]; if (!seg) return;
  if (el.classList.contains('src')) seg.text = el.textContent; else seg.tr = el.textContent;
  markDirty();
}
/* 记下每句字幕从什么改成了什么（AI 校对、画面复核、手动修改），用来找这节课的识别错误、积累术语表 */
function recordFix(doc, seg, before, after, by) {
  if (!doc || !seg || !before || before === after) return;
  const list = doc.fixes || (doc.fixes = []);
  const old = list.find(f => Math.abs(f.t - seg.start) < 0.05 && f.after === before);
  if (old) { old.after = after; if (by === 'manual') old.by = 'manual'; }
  else list.push({ t: seg.start, before, after, by });
  if (list.length > 1500) list.splice(0, list.length - 1500);
}
function snapshot() {
  S.undo.push(JSON.stringify(S.doc.segments));
  if (S.undo.length > 60) S.undo.shift();
  $('#btnUndo').disabled = false;
}
function undo() {
  if (!S.undo.length) return;
  S.doc.segments = JSON.parse(S.undo.pop());
  $('#btnUndo').disabled = !S.undo.length;
  renderCues(); markDirty(); toast('已撤销');
}
function caretOffset(el) {
  const sel = getSelection();
  if (!sel.rangeCount || !el.contains(sel.anchorNode)) return null;
  const r = sel.getRangeAt(0).cloneRange();
  r.selectNodeContents(el); r.setEnd(sel.anchorNode, sel.anchorOffset);
  return r.toString().length;
}
let lastCaret = { i: -1, field: 'src', pos: null };
function cueAction(act, i) {
  const segs = S.doc.segments, seg = segs[i]; if (!seg) return;
  const v = $('#video');
  if (act === 'align') {
    snapshot();
    const t = +v.currentTime.toFixed(2);
    seg.start = t;
    if (segs[i - 1]) segs[i - 1].end = Math.min(segs[i - 1].end, t);
    if (seg.end < t) seg.end = segs[i + 1] ? segs[i + 1].start : t + 2;
    segs.sort((a, b) => a.start - b.start);
  } else if (act === 'split') {
    const pos = lastCaret.i === i && lastCaret.field === 'src' && lastCaret.pos != null ? lastCaret.pos : null;
    const text = seg.text;
    const at = pos != null && pos > 0 && pos < text.length ? pos : Math.round(text.length / 2);
    snapshot();
    const mid = +(seg.start + (seg.end - seg.start) * at / Math.max(1, text.length)).toFixed(2);
    segs.splice(i + 1, 0, { start: mid, end: seg.end, text: text.slice(at).trim(), tr: '' });
    seg.text = text.slice(0, at).trim(); seg.end = mid;
  } else if (act === 'merge') {
    const next = segs[i + 1]; if (!next) return toast('这已经是最后一句了');
    snapshot();
    const sep = /[A-Za-z0-9,.!?]$/.test(seg.text) && /^[A-Za-z0-9]/.test(next.text) ? ' ' : '';
    seg.text = seg.text + sep + next.text;
    seg.tr = [seg.tr, next.tr].filter(Boolean).join('');
    seg.end = next.end;
    segs.splice(i + 1, 1);
  } else if (act === 'del') {
    snapshot();
    segs.splice(i, 1);
  }
  renderCues(); markDirty();
  const el = cueEl(Math.min(i, segs.length - 1)); if (el) el.scrollIntoView({ block: 'nearest' });
}

/* ---------------------------------------------------------------- 保存 ---- */
function markDirty() {
  if (!S.doc) return;
  S.dirty = true; updateSaveLabel();
  clearTimeout(S.saveTimer);
  S.saveTimer = setTimeout(() => save(), 1200);
}
function updateSaveLabel() {
  const b = $('#btnSave');
  b.classList.toggle('dirty', S.dirty);
  b.title = S.dirty ? '有修改还没保存（会自动保存）' : (S.lastSaved ? `已保存 ${S.lastSaved}` : '保存（Ctrl+S）');
  $('#saveLabel').textContent = S.saving ? '保存中…' : (S.dirty ? '保存' : (S.lastSaved ? '已保存' : '保存'));
}
async function save(quiet = true) {
  if (!S.doc || !S.path) return;
  clearTimeout(S.saveTimer);
  if (S.saving) { await S.saving; if (!S.dirty) return; }
  S.dirty = false;
  let conflict = false;
  const run = (async () => {
    try {
      const r = await api('save', { path: S.path, doc: S.doc, base_rev: S.rev || 0, force: !!S.forceSave });
      if (r.conflict) { S.dirty = true; conflict = true; return; }
      S.rev = r.rev; S.lastSaved = r.updated; S.docStale = !!r.stale;
      if ('summary_changes' in r && r.summary_changes !== S.summaryChanges) { S.summaryChanges = r.summary_changes; renderSummaryStale(); }
      syncLibFromDoc();
      if (!quiet) toast('已保存');
    } catch (e) { S.dirty = true; fail(new Error('保存失败：' + e.message)); }
  })();
  S.saving = run; updateSaveLabel();
  await run; S.saving = null; updateSaveLabel();
  if (conflict) await onSaveConflict();
}
/* 保存时发现字幕文件在这期间被后台的 Agent 自动整理改过：不直接覆盖，问用户要哪一份 */
async function onSaveConflict() {
  if (S.closing) { S.dirty = false; logToFile('warn', '关闭时发现这节课被后台改过，保留后台的结果：' + S.path); return; }
  if (S.conflictAsking) return;
  S.conflictAsking = true;
  try {
    if (confirm('这节课刚刚被后台的自动整理更新了（字幕、课程重点等）。\n\n确定：重新打开，看自动整理的结果（你刚才没保存的修改会丢掉）\n取消：保留你现在的版本，覆盖自动整理的结果')) {
      S.dirty = false; clearTimeout(S.saveTimer);
      await window.openPath(S.path);
    } else {
      S.forceSave = true;
      try { await save(); } finally { S.forceSave = false; }
    }
  } finally { S.conflictAsking = false; }
}
async function flushSave() { await savePosition(true); if (S.dirty) await save(); else if (S.saving) await S.saving; return true; }
window.flushSave = flushSave;
/* 关闭窗口前由后台调用：保存完（最多等 3 秒）再通知后台关窗 */
window.prepareClose = async function () {
  S.closing = true;
  try { await Promise.race([flushSave(), sleep(3000)]); }
  catch (e) { logToFile('error', '关闭前保存失败：' + (e.message || e)); }
  finally { try { await window.pywebview.api.call('quit', {}); } catch (_) { /* 后台会自己超时关窗 */ } }
};
window.addEventListener('beforeunload', () => { if (S.dirty) save(); });

function updateButtons() {
  if (S.mode === 'book') {
    const busy = !!S.job, done = S.doc && (S.doc.chapters || []).some(c => (c.md || '').trim());
    $('#btnSave').disabled = !S.doc;
    $('#btnClip').disabled = !done || busy;
    ['#bkRun', '#bkRunAll', '#bkAiToc', '#bkAddCh'].forEach(s => $(s).disabled = busy);
    $('#bkOverview').disabled = busy || !done;
    return;
  }
  const has = S.doc && S.doc.segments.length > 0, busy = !!S.job;
  $('#btnTranscribe').disabled = !S.doc || busy;
  $('#btnPipeline').disabled = !S.doc || busy;
  $('#btnCorrect').disabled = !has || busy;
  $('#btnTranslate').disabled = !has || busy;
  $('#btnSave').disabled = !S.doc;
  $('#btnExport').disabled = !has;
  $('#btnClip').disabled = !has;
  $('#btnSummary').disabled = !has;
  const vid = !!(S.info && S.info.has_video);
  $('#btnFrames').disabled = !S.doc || busy || !vid;
  $('#btnCapture').disabled = !S.doc || busy || !vid;
  $('#btnSubShot').hidden = !vid;  // 音频没有画面
  $('#btnSubShot').disabled = !has || busy;
  $('#btnDescribe').disabled = !S.doc || busy || !((S.doc && S.doc.frames) || []).some(f => !f.note);
  $('#btnRecheck').disabled = !S.doc || busy || !vid || !doubtIndexes().length;
  $('#btnReview').disabled = !has || busy || !(S.doc && (S.doc.summary || '').trim()) || !!S.chatAbort;
  $('#btnSumShot').hidden = !vid;
  $('#btnSumShot').disabled = !(S.doc && (S.doc.summary || '').trim()) || busy || !!S.chatAbort;
  $('#btnPrint').disabled = !(S.doc && (S.doc.summary || '').trim()) || !!S.chatAbort;
  $('#btnTerms').disabled = !has || busy;
  const pend = S.doc && S.doc.terms_pending, nPend = pend ? pend.terms.length + pend.errors.length : 0;
  $('#btnTerms').textContent = nPend ? `新术语 ${nPend}` : '新术语';
  $('#btnTerms').classList.toggle('primary', !!nPend);
  $('#btnSend').disabled = !has;
}

/* ------------------------------------------------------------ 后台任务 ---- */
/* 返回 true 表示成功完成 */
/* detachable：做的时候可以切到别的课（2026-10-21 用户要求，单个按钮也不用等）。切走后任务在后台接着做，
   进度条写明是哪节课；边做边显示的内容和做完的收尾（onItems / onDone / onError、调用方后面的代码）只在那节课还开着时做，
   不能弄到新打开的课上：切走时做完就只提示，不再回到调用方（返回一个永远不结束的 Promise）。切回那节课时自动重新接上。 */
S.detached = new Map();  // 切走后还在做的任务：id → {path, title, label}
async function runJob(id, label, { onItems, onDone, onError, onExtra, detachable = false, quiet = false } = {}) {
  const path = S.path, title = (S.doc && S.doc.title) || '';
  S.job = id; S.jobInfo = { id, path, title, label, detachable }; updateButtons();
  const bar = $('#jobbar'); bar.hidden = false;
  $('#jobText').textContent = label; $('#jobFill').style.width = '0%';
  const away = () => S.path !== path;
  let since = 0, ok = false, last = null;
  try {
    for (;;) {
      const { job } = await api('job', { id, since });
      since = job.total; last = job;
      if (!away() && !S.job && S.detached.has(id)) { S.detached.delete(id); S.job = id; S.jobInfo = { id, path, title, label, detachable }; updateButtons(); }  // 切回来了：重新接上
      if (job.items.length && onItems && !away()) onItems(job.items);
      if (job.extra && onExtra && !away()) onExtra(job.extra);
      if (S.job === id || (!S.job && [...S.detached.keys()][0] === id)) {
        $('#jobFill').style.width = Math.round(job.progress * 100) + '%';
        $('#jobText').textContent = (away() ? `《${title}》` : '') + (job.message || label) + (job.progress > 0 && job.progress < 1 ? `　${Math.round(job.progress * 100)}%` : '');
      }
      if (job.status === 'done') { ok = true; if (onDone && !away()) onDone(job); break; }
      if (job.status === 'error') { if (!away()) onError ? onError(job) : fail(new Error(job.error)); break; }
      if (job.status === 'cancelled') { if (!away()) { toast('已取消'); if (onError) onError(job); } break; }
      await sleep(600);
    }
  } catch (e) { fail(e); }
  finally {
    if (S.job === id) S.job = null;
    S.detached.delete(id);
    if (!S.job && !S.detached.size) bar.hidden = true;
    updateButtons();
  }
  if (detachable && away()) {
    if (!quiet && last) {
      const name = label.replace(/[…\s]+$/, '').replace(/中$/, ''), t = last.result && last.result.task;
      if (last.status === 'cancelled') toast(`《${title}》：「${name}」已停止。已经做完的部分保存好了`);
      else if (last.status !== 'done' || (t && !t.success)) toast(`《${title}》：「${name}」没有完成：${(t && t.message) || last.error || ''}`, { error: true, ms: 12000 });
      else toast(`《${title}》：「${name}」在后台做完了，打开这节课就能看到`, { ms: 8000 });
      loadLibrary();
    }
    return new Promise(() => {});  // 不回到调用方：后面的收尾是针对那节课的
  }
  return ok;
}
$('#jobCancel').onclick = () => {
  const id = S.job || [...S.detached.keys()][0];
  if (id) api('cancel', { id }).catch(() => {});
  S.cancelPipeline = true;
};

/* ------------------------------------------------------------ 整理步骤 ---- */
/* 生成字幕、AI 校对、找关键帧、画面复核存疑句、翻译、课程重点、查漏补缺、新术语、整理章节、全书总览，以及一键整理：
   都交给后台的任务层（app/core/workflow.py），和命令行（Agent）是同一套规则。后台做完直接写进字幕文件，
   这里只显示进度和边做边出的内容，做完重新读取。一步做到一半失败，这一步什么都不写。 */
const SOFT_STEPS = new Set(['recheck', 'review']);  // 失败了不影响一键整理后面的步骤
const stageOf = (task, name) => ((task && task.stages) || []).find(s => s.name === name) || {};
async function runSteps(steps, label, { opts = {}, redo, onItems, onStart } = {}) {
  await flushSave();
  const path = S.path;
  const r = await api('steps', { path, steps, ...(redo ? { redo } : {}), ...opts });
  if (onStart) onStart(r.job);
  const res = { task: null, status: 'error', error: '' };
  await runJob(r.job, label, {
    onItems, detachable: true,
    onDone: job => { res.task = job.result.task; res.status = 'done'; },
    onError: job => { res.status = job.status; res.error = job.error || ''; },
  });
  if (S.path === path) await reloadDoc();
  return res;
}
/* 后台写完字幕文件后重新读取（界面里还没保存的修改先保存；后台期间改过会先问） */
async function reloadDoc() {
  if (S.dirty) await save();
  const r = await api('doc', { path: S.path });
  S.doc = r.doc; S.rev = r.doc.rev || 0; S.lastSaved = r.doc.updated || S.lastSaved; S.dirty = false; S.docStale = !!r.stale;
  S.summaryChanges = r.summary_changes || 0; S.pending = r.pending || [];
  syncLibFromDoc();
}
function renderDoc() {
  if (S.mode === 'book') { renderBook(); updateButtons(); updateSaveLabel(); return; }
  renderTeacherSelect(); renderCues(); renderSummary(); renderFrames(); updateButtons(); updateSaveLabel();
  $('#optReviewed').checked = !!S.doc.reviewed;
}
/* 一步的结果：失败时提示原因、返回 null；跳过时返回 {skipped: 原因}；完成时返回这一步的结果 */
function stepResult(res, name) {
  if (res.status === 'cancelled') return null;  // runJob 已经提示「已取消」
  if (res.status !== 'done' || !res.task) { fail(new Error(res.error || '没有完成')); return null; }
  const st = stageOf(res.task, name);
  if (st.status === 'cancelled') { toast('已取消'); return null; }
  if (st.status === 'failed') { fail(new Error(st.error || res.task.message || '没有完成')); return null; }
  if (st.status === 'skipped') return { skipped: st.reason || '' };
  if (st.status !== 'completed') {  // 这一步还没开始整个任务就停了，例如这节课正被后台 / 命令行整理（2026-10-29 以前显示成「整理好了 undefined 章」）
    const err = res.task.error || {};
    fail(new Error((err.message || res.task.message || '没有完成') + (err.error_code === 'busy' ? '。等那边整理完再点；那边每整理好一章，这里会自动显示出来' : '')));
    return null;
  }
  return st.result || {};
}
/* 校对、复核改了的句子标上绿色下划线（这些步骤不增删句子，按位置对得上） */
function markFixed(before) {
  S.doc.segments.forEach((s, i) => { if (before[i] !== undefined && s.text !== before[i]) { const el = cueEl(i); if (el) el.classList.add('fixed'); } });
}
function dropSnapshot() { S.undo.pop(); $('#btnUndo').disabled = !S.undo.length; }
/* 边做边显示：识别出来的字幕一句句出现、课程重点边写边显示 */
function liveView() {
  const live = { cues: [], text: '' };
  live.onItems = items => {
    const reset = items.map(x => x.kind).lastIndexOf('reset');
    if (reset >= 0) {  // 豆包额度用完或没开通，换了账号 / 模型从头识别
      live.cues.length = 0; S.doc.segments = live.cues; renderCues(); $('#cueCount').textContent = '0 句';
      items = items.slice(reset + 1);
    }
    const cues = items.filter(x => x.kind === 'cue');
    if (cues.length) {
      if (!live.cues.length) { S.doc.segments = live.cues; renderCues(); }
      for (const c of cues) live.cues.push({ start: c.start, end: c.end, text: c.text, tr: '' });
      cuesInner().insertAdjacentHTML('beforeend', cues.map((c, k) => cueHTML(c, live.cues.length - cues.length + k)).join(''));
      $('#cueCount').textContent = `${live.cues.length} 句`;
    }
    const text = items.filter(x => x.kind === 'text').map(x => x.text).join('');
    if (text) {
      if (!live.text) { if (summaryEditing) toggleSummaryEdit(false); if (S.settings.ai_hidden) savePrefs({ ai_hidden: false }); $$('.ai-tabs .tab')[0].click(); }
      live.text += text;
      const box = $('#summaryBox');
      box.innerHTML = md(live.text); if (box.lastElementChild) box.lastElementChild.classList.add('typing'); box.scrollTop = box.scrollHeight;
    }
  };
  return live;
}
/* 识别中途出错：已经识别出的部分要不要留下（后台没有写入，留不留由用户决定） */
function keepPartial(res, live, lang) {
  const st = stageOf(res.task, 'transcribe');
  if (!live.cues.length || st.status !== 'failed') return false;
  if (!confirm(`识别中途出错，已经识别了 ${live.cues.length} 句。\n要保留这部分吗？选「取消」恢复原来的字幕。`)) return false;
  pushVersion('重新识别前');
  S.doc.segments = live.cues; S.doc.lang = lang; S.doc.corrected = false; S.doc.reviewed = false;
  renderDoc(); markDirty();
  return true;
}
function reviewToast(x) {
  toast(x.added ? `查漏补缺：补了 ${x.added} 条（放进了对应的小节）。字幕被引用约 ${Math.round(x.coverage_before * 100)}% → ${Math.round(x.coverage_after * 100)}%`
    : `查漏补缺：没有发现漏掉的内容（字幕被引用约 ${Math.round(x.coverage_after * 100)}%）`, { ms: 8000 });
}

/* ------------------------------------------------------------ 生成字幕 ---- */
const PROVIDER_LABEL = { doubao: '豆包', siliconflow: '硅基流动', openai: 'OpenAI' };
function providerHint(p) {
  const s = S.settings.providers[p];
  const model = p === 'doubao' ? (S.doubaoOrder.map(k => S.doubaoModels[k].short).join(' → ') + '（自动，用完换下一个）') : s.asr_model;
  const terms = { doubao: '术语会作为热词传给豆包。', openai: '术语会作为提示词传给 Whisper。', siliconflow: '硅基流动读不了术语表，建议识别后用 AI 校对。' }[p];
  return `模型：${model || ''}${s.has_key ? '' : '　⚠ 还没有填写 API Key'}　${terms}`;
}
function teacherHint(id) {
  const t = teacherById(id);
  if (!t) return '不用术语表。可以在「设置 → 老师与术语表」里添加。';
  const n = (t.terms || '').split(/[\r\n,，、;；]+/).filter(x => x.trim()).length;
  return `${t.terms_file ? '关联了术语表文件（自动读最新内容）　' : ''}${n ? `手写术语约 ${n} 个词条` : ''}${t.about ? '　背景：' + t.about.slice(0, 40) : ''}`;
}
/* 豆包用哪种模型：自动（按顺序换）或只用一种。2026-10-17 用户要求：流式 2.0 用超了按量扣费、欠费，要自己控制每次用哪种 */
function dbPickOptions() {
  const now = S.settings.providers.doubao.pick || 'auto';
  return [['auto', `自动（${S.doubaoOrder.map(k => S.doubaoModels[k].short).join(' → ')}）`], ...S.doubaoOrder.map(k => [k, '只用 ' + S.doubaoModels[k].name])]
    .map(([v, t]) => `<option value="${v}" ${v === now ? 'selected' : ''}>${esc(t)}${v === now ? '（设置里选的）' : ''}</option>`).join('');
}
/* 填了密钥的豆包账号（和后台 asr.doubao_accounts 一样：账号 1 在前，其他按列表顺序） */
function dbAccts() {
  const p = S.settings.providers.doubao;
  const out = (p.key || (p.app_id && p.access_token)) ? [{ id: 'main', name: doubaoAccountName('main') }] : [];
  (p.more_keys || []).forEach(a => { if (a.key) out.push({ id: a.id, name: doubaoAccountName(a.id) }); });
  return out;
}
function dbAcctNow() {
  const id = S.settings.providers.doubao.pick_account || '';
  return dbAccts().some(a => a.id === id) ? id : 'auto';
}
function dbAcctOptions() {
  const now = dbAcctNow(), list = dbAccts();
  return [['auto', `自动（从${list.length ? list[0].name : '账号 1'}开始按顺序换）`], ...list.map(a => [a.id, '只用 ' + a.name])]
    .map(([v, t]) => `<option value="${esc(v)}" ${v === now ? 'selected' : ''}>${esc(t)}${v === now ? '（设置里选的）' : ''}</option>`).join('');
}
function fillDbPickers(model, acct) {
  model.innerHTML = dbPickOptions(); acct.innerHTML = dbAcctOptions();
  $$('.db-acct-pick', model.closest('.field')).forEach(el => el.hidden = dbAccts().length < 2);  // 只有一个账号时不用选
}
function openTranscribe() {
  const dlg = $('#dlgTranscribe');
  fillDbPickers($('#trDbModel'), $('#trDbAcct'));
  const tid = S.doc.teacher || '';
  $('#trTeacher').innerHTML = teacherOptions(tid);
  const lang = S.doc.lang || (teacherById(tid) || {}).lang || S.settings.last_lang || 'zh';
  $$('input[name=lang]', dlg).forEach(r => r.checked = r.value === lang);
  const setProv = () => {
    const l = $('input[name=lang]:checked', dlg).value;
    $('#trProvider').value = l === 'en' ? S.settings.asr_en : S.settings.asr_zh;
    $('#trProviderHint').textContent = providerHint($('#trProvider').value);
    $('#trDbField').hidden = $('#trProvider').value !== 'doubao';
  };
  const setTeacher = () => {
    const t = $('#trTeacher').value;
    $('#trTeacherHint').textContent = teacherHint(t);
    $('#trCorrect').checked = !!t && S.settings.auto_correct;
  };
  $$('input[name=lang]', dlg).forEach(r => r.onchange = setProv);
  $('#trProvider').onchange = () => {
    $('#trProviderHint').textContent = providerHint($('#trProvider').value);
    $('#trDbField').hidden = $('#trProvider').value !== 'doubao';
  };
  $('#trTeacher').onchange = () => {
    const t = teacherById($('#trTeacher').value);
    if (t && !S.doc.lang) { $$('input[name=lang]', dlg).forEach(r => r.checked = r.value === t.lang); setProv(); }
    setTeacher();
  };
  setProv(); setTeacher();
  $('#trWarn').hidden = !S.doc.segments.length;
  dlg.returnValue = '';
  dlg.showModal();
  dlg.onclose = async () => {
    if (dlg.returnValue !== 'ok') return;
    setDocTeacher($('#trTeacher').value);
    const ok = await startTranscribe($('input[name=lang]:checked', dlg).value, $('#trProvider').value, $('#trDbModel').value, $('#trDbAcct').value);
    if (ok && $('#trCorrect').checked) await startCorrect(true);
  };
}
function setDocTeacher(id) {
  if (!S.doc || (S.doc.teacher || '') === (id || '')) return;
  S.doc.teacher = id || ''; renderTeacherSelect(); markDirty();
}
async function startTranscribe(lang, provider, dbModel, dbAcct) {
  try {
    const had = S.doc.segments.length;
    if (had) snapshot();
    const live = liveView();
    const res = await runSteps(['transcribe'], `${PROVIDER_LABEL[provider] || ''}识别中…`, { opts: { lang, provider, doubao_model: provider === 'doubao' ? dbModel : '', doubao_account: provider === 'doubao' ? dbAcct : '' }, onItems: live.onItems });
    renderDoc();
    const out = stepResult(res, 'transcribe');
    if (!out) {
      if (keepPartial(res, live, lang)) return false;
      if (had) dropSnapshot();
      return false;
    }
    toast(`字幕生成好了，共 ${out.segments} 句${out.used ? `（${out.used}）` : ''}${out.notes && out.notes.length ? '。中途换过：' + out.notes.join('；') : ''}`,
      out.notes && out.notes.length ? { ms: 10000 } : undefined);
    return true;
  } catch (e) { fail(e); return false; }
}

/* ------------------------------------------------------------ 字幕版本 ---- */
/* 重新识别、清空、恢复之前，把当前字幕存一份（最多 5 份，存在字幕文件里） */
function pushVersion(reason) {
  const segs = S.doc.segments;
  if (!segs.length) return;
  S.doc.versions = [{ time: new Date().toLocaleString(), reason, provider: S.doc.provider || '', corrected: !!S.doc.corrected,
    count: segs.length, segments: JSON.parse(JSON.stringify(segs)) }, ...(S.doc.versions || [])].slice(0, 5);
}
function renderVersions() {
  const vs = (S.doc && S.doc.versions) || [];
  $('#menuVersions').innerHTML = '<div class="menu-label">历史版本（重新识别、清空、恢复前自动保存，最多 5 份）</div>' +
    (vs.length ? vs.map((v, i) => `<button class="menu-item" data-ver="${i}">恢复：${esc(v.reason)}的字幕<span class="vmeta">${esc(v.time)}　${v.count} 句${v.provider ? '　' + esc(v.provider) : ''}${v.corrected ? '　已校对过' : ''}</span></button>`).join('')
      : '<div class="menu-label">还没有历史版本</div>') +
    `<div class="menu-label">当前字幕</div><button class="menu-item danger" data-ver="clear" ${S.doc && S.doc.segments.length ? '' : 'disabled'}>清空当前字幕（会先存一份历史版本）</button>`;
}
function useVersion(i) {
  closeMenus();
  if (S.job) return toast('还有任务在进行，请稍后再试', { error: true });
  if (i === 'clear') {
    if (!confirm('清空当前字幕？\n清空前会自动存一份历史版本，之后还可以恢复。')) return;
    snapshot(); pushVersion('清空前');
    S.doc.segments = []; S.doc.corrected = false; S.doc.reviewed = false; $('#optReviewed').checked = false;
    renderCues(); markDirty(); save(); updateButtons();
    toast('已清空，可以重新生成字幕');
    return;
  }
  const v = S.doc.versions[+i];
  snapshot(); pushVersion('恢复旧版本前');
  S.doc.versions = S.doc.versions.filter(x => x !== v);
  S.doc.segments = v.segments; S.doc.provider = v.provider; S.doc.corrected = v.corrected;
  renderCues(); markDirty(); save(); updateButtons();
  toast(`已恢复 ${v.time} 的字幕（${v.count} 句）。刚才的字幕也存成了历史版本`);
}

/* ------------------------------------------------------------ AI 校对 ---- */
async function startCorrect(auto) {
  const t = teacherById(S.doc.teacher);
  if (!t && !auto && !confirm('这个视频没有指定老师，没有术语表可参考。\n仍然让 AI 只修正明显的错别字吗？\n（可以在顶栏选择老师）')) return false;
  try {
    const before = S.doc.segments.map(s => s.text);
    snapshot();
    const res = await runSteps(['correct'], 'AI 校对中…', { opts: { allow_no_teacher: true } });
    renderDoc();
    const out = stepResult(res, 'correct');
    if (!out || out.skipped !== undefined) { dropSnapshot(); if (out) toast(out.skipped); return !!out; }
    markFixed(before);
    toast((out.fixed ? `AI 校对完成，改了 ${out.fixed} 句（绿色下划线标出），不满意可以按「撤销」恢复` : 'AI 校对完成，没有发现需要改的地方')
      + (out.doubts ? `。另有 ${out.doubts} 句存疑（橙色），点字幕栏上方的「存疑」逐句回听` : ''), { ms: 8000 });
    return true;
  } catch (e) { fail(e); return false; }
}

/* ---------------------------------------------------------------- 翻译 ---- */
function openTranslate() {
  const segs = S.doc.segments;
  const missing = segs.filter(s => s.text.trim() && !s.tr.trim()).length;
  const dlg = $('#dlgTranslate');
  $('#tlInfo').innerHTML = (S.doc.lang === 'zh' ? '这是中文字幕，一般不需要翻译。<br>' : '') +
    `共 ${segs.length} 句，其中 ${missing} 句还没有译文。将使用 <b>${esc(S.providerNames[S.settings.translate])}</b> 翻译。`;
  $('#tlMissing').textContent = missing ? `翻译缺少的 ${missing} 句` : '没有缺少的';
  $('#tlMissing').disabled = !missing;
  $('#tlAll').hidden = missing === segs.length;
  dlg.returnValue = '';
  dlg.showModal();
  dlg.onclose = () => { if (dlg.returnValue === 'missing' || dlg.returnValue === 'all') startTranslate(dlg.returnValue === 'all'); };
}
async function startTranslate(all) {
  try {
    if (!S.doc.segments.some(s => s.text.trim() && (all || !s.tr.trim()))) return true;
    if (all) snapshot();
    const res = await runSteps(['translate'], '翻译中…', { opts: { any_lang: true, translate_all: !!all } });
    renderDoc();
    const out = stepResult(res, 'translate');
    if (!out || out.skipped !== undefined) { if (all) dropSnapshot(); if (out) toast(out.skipped); return !!out; }
    toast(`翻译完成，共 ${out.translated} 句`);
    if (!S.settings.show_tr) savePrefs({ show_tr: true });
    return true;
  } catch (e) { fail(e); return false; }
}

/* ------------------------------------------------------------ 一键整理 ---- */
function openPipeline() {
  const dlg = $('#dlgPipeline');
  const has = S.doc.segments.length > 0;
  $('#plTeacher').innerHTML = teacherOptions(S.doc.teacher || '');
  $('#plAsr').checked = !has;
  $('#plAsrLabel').textContent = has ? '重新生成字幕（会替换现有字幕）' : '生成字幕';
  const lang = S.doc.lang || (teacherById(S.doc.teacher) || {}).lang || S.settings.last_lang || 'zh';
  $$('input[name=plLang]').forEach(r => r.checked = r.value === lang);
  const sync = () => {
    $('#plCorrect').checked = !!$('#plTeacher').value && !S.doc.corrected;
    const l = $('input[name=plLang]:checked').value;
    $('#plTranslate').checked = l === 'en';
    $('#plTranslate').disabled = l !== 'en';
    syncDb();
  };
  const syncDb = () => {  // 要生成字幕、而且用豆包时，才显示「豆包用哪种模型」
    const l = $('input[name=plLang]:checked').value;
    $('#plDbField').hidden = !$('#plAsr').checked || (l === 'en' ? S.settings.asr_en : S.settings.asr_zh) !== 'doubao';
  };
  fillDbPickers($('#plDbModel'), $('#plDbAcct'));
  $('#plAsr').onchange = syncDb;
  $('#plTeacher').onchange = () => {
    const t = teacherById($('#plTeacher').value);
    if (t && !S.doc.lang) $$('input[name=plLang]').forEach(r => r.checked = r.value === t.lang);
    termsLabel();
    sync();
  };
  $$('input[name=plLang]').forEach(r => r.onchange = sync);
  sync();
  $('#plSummary').checked = !S.noSummary;  // 媒体库文件夹设了「不写课程重点」时默认不勾
  $('#plSummaryText').textContent = S.noSummary ? '生成课程重点（这个文件夹设了不写，要写这一节就勾上）' : '生成课程重点';
  $('#plClip').checked = false;
  $('#plTerms').checked = true;
  termsLabel();
  const vid = !!(S.info && S.info.has_video), nShots = (S.doc.frames || []).length;
  $('#plFrames').disabled = !vid;
  $('#plFrames').checked = vid && !nShots && providerReady(S.settings.vision);
  $('#plRecheck').disabled = !vid;
  $('#plRecheck').checked = vid && providerReady(S.settings.vision);
  $('#plFramesLabel').textContent = !vid ? '找关键帧（这是音频文件，没有画面）'
    : nShots ? `重新挑插图（现在有 ${nShots} 张，会替换自动挑的那些）`
      : `挑必要的插图${providerReady(S.settings.vision) ? '' : '（还没设置画面识别用的 AI）'}`;
  dlg.returnValue = '';
  dlg.showModal();
  dlg.onclose = () => { if (dlg.returnValue === 'ok') runPipeline(); };
}
function termsLabel() {  // 没选老师时，「新术语」= 找出这节课的专用术语、建这位老师的术语表
  const lb = $('#plTermsText');
  if (lb) lb.textContent = $('#plTeacher').value ? '找出新术语，最后弹出来让你勾选，确认的加进术语表'
    : '找出这节课的专用术语，最后弹出来让你勾选、填老师名字，建立这位老师的术语表（以后的课接着积累）';
}
async function runPipeline() {
  S.cancelPipeline = false;
  setDocTeacher($('#plTeacher').value);
  const lang = $('input[name=plLang]:checked').value;
  const steps = [];
  if ($('#plAsr').checked) steps.push('transcribe');
  if ($('#plCorrect').checked) steps.push('correct');
  if ($('#plFrames').checked && !$('#plFrames').disabled) steps.push('frames');
  if ($('#plRecheck').checked && !$('#plRecheck').disabled) steps.push('recheck');
  if ($('#plTranslate').checked && !$('#plTranslate').disabled) steps.push('translate');
  if ($('#plSummary').checked) { steps.push('summary'); if (S.settings.auto_review) steps.push('review'); }
  if ($('#plTerms').checked) steps.push('terms');
  const clip = $('#plClip').checked;
  if (!steps.includes('transcribe') && !S.doc.segments.length) { toast('没有字幕，一键整理已停止', { error: true }); return; }
  if (steps.length) {  // 交给后台整理队列：可以马上切到别的课继续看、校对（2026-10-17 用户要求）
    const provider = lang === 'en' ? S.settings.asr_en : S.settings.asr_zh;
    try {
      await flushSave();
      await bgAdd({ path: S.path, steps, lang, provider, allow_no_teacher: true, new_glossary: !S.doc.teacher,
        doubao_model: provider === 'doubao' ? $('#plDbModel').value : '', doubao_account: provider === 'doubao' ? $('#plDbAcct').value : '' },
      { clip, terms: steps.includes('terms') });
    } catch (e) { fail(e); return; }
    toast(`《${S.doc.title}》交给后台整理了。可以打开别的课继续看、校对；进度看上方的「后台整理」和媒体库，整理完会提示你`, { ms: 8000 });
    return;
  }
  if (clip && !(await doClip())) { toast('「剪藏」没有完成', { error: true }); return; }
  toast('一键整理完成');
}

/* ------------------------------------------------------------ 后台整理 ---- */
/* 一键整理、右键「排进后台整理」交给后台队列（app/core/bgqueue.py），一节一节整理，整理时可以切到别的课继续看、校对。
   整理规则还是 workflow.process（和命令行同一套），每一步直接写进那节课的字幕文件；做完时这节课正开着就重新读取。 */
const BG_ACTIVE = new Set(['waiting', 'running']);
S.bg = []; S.bgWant = {};  // bgWant：id → {clip, terms}，做完时要接着做的（剪藏、勾选新术语要在这节课开着时做）
const bgOf = path => S.bg.find(x => BG_ACTIVE.has(x.status) && x.path.toLowerCase() === (path || '').toLowerCase());
const bgPct = x => Math.round((x.progress || 0) * 100);
function bgTag(it) {
  const b = bgOf(it.path);
  if (!b) return '';
  return b.status === 'running' ? `<span class="tag bg" data-bg-pct title="${esc(b.message || '')}">整理中 ${bgPct(b)}%</span>`
    : '<span class="tag bg" title="排在后台整理队列里，前面的整理完就轮到它">排队中</span>';
}
async function bgAdd(body, want) {
  const r = await api('bg_add', body);
  if (want) S.bgWant[r.item.id] = want;
  applyBg(r.items);
  pollBg();
  return r.item;
}
let bgTimer = null;
function pollBg() {
  clearTimeout(bgTimer);
  bgTimer = setTimeout(async () => {
    try { applyBg((await api('bg_list')).items); } catch (e) { /* 下次再看 */ }
    if (S.bg.some(x => BG_ACTIVE.has(x.status))) pollBg();
  }, 1500);
}
function applyBg(items) {
  const was = new Map(S.bg.map(x => [x.id, x.status]));
  const shape = list => list.filter(x => BG_ACTIVE.has(x.status)).map(x => x.id + x.status).join();
  const before = shape(S.bg);
  S.bg = items || [];
  if (shape(S.bg) !== before) renderLibrary();
  else S.bg.filter(x => x.status === 'running').forEach(x => {  // 只是百分比变了：不重画整个媒体库
    const el = $$('#libBody .lib-item').find(e => (e.dataset.open || '').toLowerCase() === x.path.toLowerCase());
    const tag = el && $('[data-bg-pct]', el);
    if (tag) tag.textContent = `整理中 ${bgPct(x)}%`;
  });
  renderBgBar();
  const ended = S.bg.filter(x => BG_ACTIVE.has(was.get(x.id)) && !BG_ACTIVE.has(x.status));
  const stopped = ended.filter(x => x.status === 'cancelled');
  if (stopped.length > 1) {  // 「全部停下」、额度用完停下一整批：只提示一次，不一节一条
    stopped.forEach(b => delete S.bgWant[b.id]);
    if (!ended.some(x => x.batch_stopped)) toast(`后台整理停下了 ${stopped.length} 节。已经做完的步骤都保存好了`);
    loadLibrary();
    ended.filter(x => x.status !== 'cancelled').forEach(bgFinished);
  } else ended.forEach(bgFinished);
}
function renderBgBar() {
  const act = S.bg.filter(x => BG_ACTIVE.has(x.status));
  $('#bgbar').hidden = !act.length;
  if (!act.length) return;
  const run = act.find(x => x.status === 'running'), wait = act.filter(x => x.status === 'waiting').length;
  $('#bgFill').style.width = (run ? bgPct(run) : 0) + '%';
  $('#bgText').textContent = (run ? `后台整理《${run.title}》${bgPct(run)}%　${run.message || ''}` : '后台整理：马上开始…')
    + (wait ? `　（还排着 ${wait} 节）` : '');
}
function bgMenu(x, y) {
  const st = { waiting: '排队中', running: '正在整理', done: '整理完了', failed: '没有完成', cancelled: '已停止' };
  const act = S.bg.filter(b => BG_ACTIVE.has(b.status)), SHOW = 10;  // 整个文件夹排队时会有几百条：只列前面几条
  const rows = [...act.slice(0, SHOW), ...S.bg.filter(b => !BG_ACTIVE.has(b.status)).reverse().slice(0, 5)];
  showCtx(x, y, (act.length > SHOW ? `<div class="menu-label">还排着 ${act.length} 节，下面是最前面的 ${SHOW} 节</div>
    <button class="menu-item danger" data-bg-all>全部停下（正在整理的做完手上这一步就停，已经做完的保留）</button>` : '') + rows.map(b => `<div class="menu-label">《${esc(b.title)}》${st[b.status] || b.status}${b.status === 'running' ? ` ${bgPct(b)}%` : ''}${b.error ? '：' + esc(b.error) : ''}</div>
    <button class="menu-item" data-bg-open="${esc(b.path)}">打开这节课</button>
    ${BG_ACTIVE.has(b.status) ? `<button class="menu-item danger" data-bg-stop="${esc(b.id)}">${b.status === 'running' ? '停下（做完手上这一步就停，已经做完的保留）' : '不整理了（从队列里去掉）'}</button>` : ''}`).join('')
    + (rows.some(b => !BG_ACTIVE.has(b.status)) ? '<button class="menu-item" data-bg-clear>清掉已经结束的记录</button>' : ''), m => {
    $$('[data-bg-open]', m).forEach(el => el.onclick = () => { m.hidden = true; openPath(el.dataset.bgOpen); });
    $$('[data-bg-stop]', m).forEach(el => el.onclick = async () => { m.hidden = true; applyBg((await api('bg_cancel', { id: el.dataset.bgStop })).items); });
    const all = $('[data-bg-all]', m);
    if (all) all.onclick = async () => { m.hidden = true; applyBg((await api('bg_cancel', {})).items); };
    const c = $('[data-bg-clear]', m);
    if (c) c.onclick = async () => { m.hidden = true; applyBg((await api('bg_clear')).items); };
  });
}
$('#bgbar').onclick = e => { e.stopPropagation(); bgMenu(e.clientX, e.clientY); };
/* 一节整理完：这节课正开着就重新读取（期间你在这里改过的，保存时会先问你）；剪藏、勾选新术语要这节课开着才做 */
async function bgFinished(b) {
  const want = S.bgWant[b.id] || {};
  delete S.bgWant[b.id];
  const open = S.path && S.path.toLowerCase() === b.path.toLowerCase();
  if (open && !S.job) { try { await reloadDoc(); renderDoc(); } catch (e) { fail(e); } }
  loadLibrary();
  const t = b.task || {};
  if (b.status === 'cancelled') { toast(`《${b.title}》的后台整理停下了。已经做完的步骤保存好了`); return; }
  if (b.batch_stopped) {
    toast(`额度用完了：《${b.title}》没有完成（${b.error || ''}），这一批剩下的 ${b.batch_stopped} 节先停下了。`
      + '额度恢复后，在文件夹的「⋯」菜单里再点「整个文件夹排进后台整理」，会从没做完的接着排', { error: true, ms: 20000 });
    return;
  }
  const left = S.bg.filter(x => BG_ACTIVE.has(x.status)).length;
  if (b.batch && b.status === 'done' && !open) { toast(`《${b.title}》后台整理完了${left ? `，还排着 ${left} 节` : '，这一批都做完了'}`, { ms: 4000 }); return; }
  if (b.status !== 'done') { toast(`《${b.title}》后台整理没有完成：${b.error || ''}。前面做完的步骤已经保存，再点一键整理会接着做`, { error: true, ms: 12000 }); return; }
  if (stageOf(t, 'translate').status === 'completed' && !S.settings.show_tr) savePrefs({ show_tr: true });
  const used = (stageOf(t, 'transcribe').result || {}).used;
  const warn = (t.warnings || []).length ? '（' + t.warnings.join('；') + '）' : '';
  const pendingTerms = open ? !!S.doc.terms_pending : !!(t.state && t.state.terms_pending);
  const later = [];
  if (!open && want.clip) later.push('剪藏');
  if (!open && want.terms && pendingTerms) later.push('勾选新术语');
  toast(`《${b.title}》后台整理完了${used ? `（字幕用的是 ${used}）` : ''}。${later.length ? `打开这节课后再${later.join('、')}。` : ''}建议检查一遍字幕，打开「已校对」后再剪藏。${warn}`, { ms: 10000 });
  if (open && want.clip && !(await doClip())) toast('「剪藏」没有完成', { error: true });
  if (open && want.terms && S.doc.terms_pending) await openTerms();
}

/* ---------------------------------------------------------------- 导出 ---- */
async function doExport(kind) {
  closeMenus();
  const mode = $('input[name=exMode]:checked').value;
  const stamps = $('#exStamps').checked;
  const suffix = { both: '中英', src: '原文', tr: '中文' }[mode];
  const name = `${S.doc.title}-${suffix}.${kind}`;
  try {
    const { path: dest } = await api('save_dialog', { name, ext: kind });
    if (!dest) return;
    await api('export', { doc: S.doc, dest, format: kind, mode, timestamps: stamps });
    toast('已保存：' + dest, { action: { label: '打开文件夹', run: () => api('reveal', { target: dest }) } });
  } catch (e) { fail(e); }
}

/* ----------------------------------------------------------- 新术语 ---- */
/* 从这节课里找术语表里还没有的新术语和识别错误，用户勾选确认后加进老师的术语表（逐课积累） */
async function findTerms() {
  const fresh = !teacherById(S.doc.teacher);  // 新老师的第一节课：找出专用术语，勾选后填老师名字、建术语表（2026-10-28 用户要求）
  try {
    const res = await runSteps(['terms'], fresh ? 'AI 正在找这节课的专用术语…' : 'AI 正在找这节课的新术语…', fresh ? { opts: { new_glossary: true } } : {});
    renderDoc();
    const out = stepResult(res, 'terms');
    if (!out) return false;
    if (out.skipped !== undefined) { toast(out.skipped); return true; }
    if (!S.doc.terms_pending) { toast(fresh ? '这节课没有找出专用术语' : '没有发现术语表里还没有的新术语'); return true; }
    await openTerms();
    return true;
  } catch (e) { fail(e); return false; }
}
function termRow(kind, x, i) {
  const ts = `<a class="tslink" data-t="${x.t}" title="跳到这里听一下">[${fmt(x.t)}]</a>`;
  const q = x.quote ? `<span class="tm-quote" title="${esc(x.quote)}">${esc(x.quote)}</span>` : '<span class="tm-quote"></span>';
  return kind === 'term'
    ? `<div class="tm-row" data-kind="term" data-i="${i}"><input type="checkbox" checked><input class="tm-in" data-f="term" value="${esc(x.term)}">${ts}${q}</div>`
    : `<div class="tm-row" data-kind="error" data-i="${i}"><input type="checkbox" checked><span class="tm-pair"><input class="tm-in" data-f="wrong" value="${esc(x.wrong)}"><span>→</span><input class="tm-in" data-f="right" value="${esc(x.right)}"></span>${ts}${q}</div>`;
}
async function openTerms() {
  const pend = S.doc.terms_pending; if (!pend) return;
  const t = teacherById(S.doc.teacher);
  const dlg = $('#dlgTerms');
  if (t) {  // 候选是整理这节课时找的；之后前面的课可能已经把同样的加进术语表了（几节课一起整理、一节节确认时常见），再筛一遍
    try {
      const f = await api('terms_pending_filter', { teacher: t.id, terms: pend.terms, errors: pend.errors });
      if (f.removed) { pend.terms = f.terms; pend.errors = f.errors; markDirty(); save(); }
      if (!pend.terms.length && !pend.errors.length) {
        S.doc.terms_pending = null; markDirty(); save(); updateButtons();
        toast(`这节课找到的 ${f.removed} 条都已经在术语表里了（前面的课已经加过），不用再勾选`, { ms: 6000 });
        return;
      }
      if (f.removed) toast(`去掉了 ${f.removed} 条术语表里已经有的（前面的课已经加过）`, { ms: 6000 });
    } catch (_) { /* 筛不了就照原样显示 */ }
  }
  $('#tmNew').hidden = !!t;
  if (t) {
    const file = t.terms_file ? t.terms_file.split(/[\\/]/).pop() : '';
    $('#tmTitle').textContent = '这节课的新术语';
    $('#tmAdd').textContent = '加入术语表';
    $('#tmInfo').innerHTML = `加进 <b>${esc(t.name || '')}</b> 的术语表` +
      (file ? `：写在「${esc(file)}」末尾的「逐课补充（猛人学习）」一节，写之前自动备份。` : '（老师档案里的术语）。') +
      '只记录名称，不写定义。不对的去掉勾，写错的可以直接改；点时间可以回听。';
  } else {  // 新老师的第一节课：填老师名字，建术语表
    let plan = { folder: '', teachers: [], binds: [] };
    try { plan = await api('glossary_plan', { path: S.path }); } catch (e) { fail(e); }
    $('#tmTitle').textContent = '建立这位老师的术语表';
    $('#tmAdd').textContent = '建立术语表';
    $('#tmInfo').innerHTML = '这节课还没有老师的术语表。勾选这节课的专用术语（不对的去掉勾，写错的直接改，点时间可以回听），填上老师名字：'
      + (plan.folder ? `软件会在 Obsidian「02、规则体系/02、课程专用规则」里建一份「老师名课程术语表」（结构和峰级那份一样），关联到这位老师；`
        : '还没有设置 Obsidian 库，术语先记在软件的老师档案里；')
      + '再把下面选的文件夹绑定这位老师。以后这个文件夹里的课，识别时把这些术语当热词、校对时参考，每节课整理完继续找新术语让你勾选。只记录名称，不写定义。';
    $('#tmNames').innerHTML = plan.teachers.map(n => `<option value="${esc(n)}">`).join('');
    $('#tmName').value = '';
    $('#tmBind').innerHTML = plan.binds.map(b => `<option value="${esc(b.path)}" ${b.top ? 'selected' : ''}>${esc(b.name)}${b.top ? '（加进媒体库的文件夹）' : ''}</option>`).join('')
      + '<option value="">不绑定（以后每节课自己在顶栏选老师）</option>';
  }
  $('#tmList').innerHTML =
    (pend.terms.length ? `<div class="tm-head">新术语（以后识别时当热词）</div>` + pend.terms.map((x, i) => termRow('term', x, i)).join('') : '') +
    (pend.errors.length ? `<div class="tm-head">识别错误（以后校对时参考）</div>` + pend.errors.map((x, i) => termRow('error', x, i)).join('') : '');
  $('#tmAdd').onclick = e => { if (!t && !$('#tmName').value.trim()) { e.preventDefault(); toast('请填老师的名字', { error: true }); $('#tmName').focus(); } };
  dlg.returnValue = '';
  dlg.showModal();
  return new Promise(resolve => {
    dlg.onclose = async () => {
      const v = dlg.returnValue;
      if (v === 'add') await addTerms(t ? null : { name: $('#tmName').value.trim(), bind: $('#tmBind').value });
      else if (v === 'drop') { S.doc.terms_pending = null; markDirty(); save(); }
      updateButtons();
      resolve();
    };
  });
}
async function addTerms(fresh) {  // fresh：{name, bind} 新老师的第一节课，建老师档案和术语表
  const terms = [], errors = [];
  $$('#tmList .tm-row').forEach(row => {
    if (!$('input[type=checkbox]', row).checked) return;
    const pend = S.doc.terms_pending, i = +row.dataset.i;
    const val = f => ($(`[data-f="${f}"]`, row) || {}).value.trim();
    if (row.dataset.kind === 'term') { const x = pend.terms[i]; if (val('term')) terms.push({ ...x, term: val('term') }); }
    else { const x = pend.errors[i]; if (val('wrong') && val('right')) errors.push({ ...x, wrong: val('wrong'), right: val('right') }); }
  });
  if (!terms.length && !errors.length) { toast('一条都没勾，术语表没有改动；这些候选还留着，点「新术语」可以再看'); return; }
  if (fresh) {
    try {
      const r = await api('glossary_create', { name: fresh.name, bind: fresh.bind, terms, errors, title: S.doc.title });
      S.teachers = r.teachers;
      setDocTeacher(r.teacher);
      S.doc.terms_pending = null; markDirty(); save();
      if (r.groups) { S.lib = r.groups; renderLibrary(); }
      renderTeacherSelect();
      const where = r.file ? `「${r.file.split(/[\\/]/).pop()}」（Obsidian「02、规则体系/02、课程专用规则」）` : '软件的老师档案';
      toast(`${r.created ? '已建立' : '已加进'}「${r.name}」的术语表：新术语 ${r.terms} 条，识别错误 ${r.errors} 条，写在${where}。`
        + (r.bound ? `以后「${r.bound.split(/[\\/]/).pop()}」里的课都用这位老师。` : '') + '下次识别和校对就会用上', { ms: 10000 });
    } catch (e) { fail(e); }
    return;
  }
  try {
    const r = await api('terms_add', { teacher: S.doc.teacher, terms, errors, title: S.doc.title });
    S.doc.terms_pending = null; markDirty(); save();
    if (!r.file) await refreshSettingsOnly();
    toast(r.terms || r.errors ? `已加进术语表：新术语 ${r.terms} 条，识别错误 ${r.errors} 条。下次识别和校对就会用上` : '这些都已经在术语表里了', { ms: 7000 });
  } catch (e) { fail(e); }
}

/* ------------------------------------------------------------- 剪藏 ---- */
async function doClip() {
  try {
    await flushSave();
    let r = await api('clip', { doc: S.doc });
    if (r.conflict) {
      const name = r.path.split(/[\\/]/).pop();
      if (!confirm(`「${name}」上次剪藏后在 Obsidian 里改过（最后修改 ${r.modified}）。\n\n继续剪藏会用软件里的内容更新「课程重点」和「完整字幕」：你在这两块里直接改的文字会没掉。你自己加的小节、标注（> [!…]）、属性和状态会保留。旧笔记会先备份到：\n%APPDATA%\\猛人学习\\backups\\笔记\n\n要继续剪藏吗？`)) {
        toast('没有剪藏，Obsidian 里的笔记保持不变');
        return true;
      }
      r = await api('clip', { doc: S.doc, force: true });
    }
    S.doc.clip = { path: r.path, time: r.time, hash: r.hash, file_hash: r.file_hash, tags: r.tags, img_base: r.img_base }; S.docStale = false; markDirty(); save();
    toast((S.doc.reviewed ? '已剪藏到 Obsidian' : '已剪藏到 Obsidian（字幕还没标记为已校对）') + (r.backup ? '。覆盖前的旧笔记已备份' : '') + (r.moved ? `。有 ${r.moved} 条你写的标注找不到原来那句话，放在了那一节的最后` : ''),
      { action: { label: '在 Obsidian 中打开', run: () => api('reveal', { target: r.uri }) }, ms: 7000 });
    return true;
  } catch (e) { fail(e); return false; }
}

/* -------------------------------------------------------------- 画面 ---- */
/* 插图：只挑文字说不清、需要看图的画面，每张一句说明；课程重点里的 ![[画面 mm:ss]] 剪藏时换成图片 */
function frameUrl(t, small, crop) {
  const key = `${S.path}|${t}|${small ? 's' : ''}|${crop ? crop.join(',') : ''}`;
  if (!S.frameUrls[key]) S.frameUrls[key] = api('frame_url', { path: S.path, t, small, crop: crop || null }).then(r => r.url).catch(() => { delete S.frameUrls[key]; return ''; });
  return S.frameUrls[key];
}
/* 课程重点里 ![[画面 mm:ss]] 实际用哪张画面：3 秒内有挑出来的画面就用它（和剪藏时 doc.place_figures 一样） */
const figShot = t => (S.doc.frames || []).filter(f => Math.abs(f.t - t) <= 3).sort((a, b) => Math.abs(a.t - t) - Math.abs(b.t - t))[0];
const REDRAW_LABEL = 'AI辅助理解图｜根据老师讲解和画面整理，非原画面';
/* AI辅助理解图在软件里怎么显示：SVG 直接显示（剪藏前后台已清理过）；Mermaid 显示代码，剪藏到 Obsidian 后显示成图 */
function redrawHTML(r) {
  if (!r || !r.code) return '';
  if (r.kind === 'svg') return `<img class="redraw-svg" alt="" src="data:image/svg+xml;charset=utf-8,${encodeURIComponent(r.code)}">`;
  return `<pre class="redraw-code" title="流程图代码：剪藏到 Obsidian 后会显示成图">${esc(r.code)}</pre>`;
}
function hydrateFigures(root) {
  if (!S.path) return;
  $$('[data-ft]', root).forEach(fig => {
    const t = +fig.dataset.ft, img = fig.querySelector('img');
    const shot = figShot(t);
    if (shot && shot.redraw && shot.redraw.code) {
      img.outerHTML = redrawHTML(shot.redraw);
      fig.querySelector('figcaption').insertAdjacentHTML('beforeend', `　<b>${REDRAW_LABEL}</b>${shot.redraw.kind === 'mermaid' ? '（流程图，剪藏到 Obsidian 后显示成图）' : ''}`);
      return;
    }
    const e = (S.doc.fig_edits || {})[String(t)] || {};  // 编辑过的：裁剪后的图、显示宽度
    if (e.w) fig.style.maxWidth = e.w + 'px';
    frameUrl(shot ? shot.t : t, false, e.crop).then(u => { if (u) img.src = u; });
  });
}
/* ---------- 截图的「裁剪」和「大小」（2026-11-02 用户要求，是两个分开的功能）：字幕栏和课程重点里的截图都能用。
   crop = [左, 上, 右, 下]（占整张截图的比例），w = 显示宽度（像素，没有 = 自动）。原截图不改：显示时后台按 crop 另裁一张（frames.grab），
   剪藏时裁好再复制进笔记（doc._small_copy），宽度写成 Obsidian 的 ![[图|宽度]]。字幕截图记在 sub_shots 每一张上，
   课程重点的图记在 fig_edits {'秒数': {crop, w}}（课程重点重新生成后同一时间的图照样用）。
   裁剪：对话框 #dlgShotEdit。大小：图下面「大小」弹出滑块（openSizeMenu），拖动时图马上变，不用点确定。 ---------- */
const SE = { crop: [0, 0, 1, 1] };
function openShotEdit(t, crop) {
  return new Promise(resolve => {
    const dlg = $('#dlgShotEdit'), img = $('#seImg');
    SE.crop = crop ? crop.slice() : [0, 0, 1, 1];
    img.removeAttribute('src'); $('#seInfo').textContent = '正在取截图…';
    img.onload = seDraw;
    frameUrl(t, false).then(u => { if (u) img.src = u; else $('#seInfo').textContent = '截图取不到'; });
    seDraw();
    dlg.returnValue = ''; dlg.showModal();
    dlg.onclose = () => {
      if (dlg.returnValue !== 'ok') return resolve(null);
      const c = SE.crop.map(x => Math.round(x * 10000) / 10000);
      const full = c[0] <= 0.001 && c[1] <= 0.001 && c[2] >= 0.999 && c[3] >= 0.999;
      resolve({ crop: full ? null : c });
    };
  });
}
function seDraw() {
  const [l, t, r, b] = SE.crop, img = $('#seImg');
  Object.assign($('#seBox').style, { left: l * 100 + '%', top: t * 100 + '%', width: (r - l) * 100 + '%', height: (b - t) * 100 + '%' });
  if (img.naturalWidth) $('#seInfo').textContent = `留下 ${Math.round((r - l) * img.naturalWidth)} × ${Math.round((b - t) * img.naturalHeight)} 像素`;
}
function bindShotEdit() {
  const stage = $('#seStage'), MIN = 0.03;
  let drag = null;
  const pos = e => { const r = $('#seImg').getBoundingClientRect(); return [clamp((e.clientX - r.left) / r.width, 0, 1), clamp((e.clientY - r.top) / r.height, 0, 1)]; };
  stage.addEventListener('pointerdown', e => {
    if (!$('#seImg').naturalWidth) return;
    e.preventDefault();
    try { stage.setPointerCapture(e.pointerId); } catch (_) { /* 自测里的假事件没有真的指针 */ }
    const full = SE.crop[2] - SE.crop[0] > 0.98 && SE.crop[3] - SE.crop[1] > 0.98;  // 框还是整张图时，拖动是画新框
    drag = { h: e.target.dataset.h || (e.target.closest('#seBox') && !full ? 'move' : 'new'), p: pos(e), c: SE.crop.slice() };
  });
  stage.addEventListener('pointermove', e => {
    if (!drag) return;
    const [x, y] = pos(e), dx = x - drag.p[0], dy = y - drag.p[1];
    let [l, t, r, b] = drag.c;
    if (drag.h === 'new') SE.crop = [Math.min(drag.p[0], x), Math.min(drag.p[1], y), Math.max(drag.p[0], x), Math.max(drag.p[1], y)];
    else if (drag.h === 'move') { const w = r - l, h = b - t; l = clamp(l + dx, 0, 1 - w); t = clamp(t + dy, 0, 1 - h); SE.crop = [l, t, l + w, t + h]; }
    else {
      if (drag.h.includes('w')) l = clamp(l + dx, 0, r - MIN);
      if (drag.h.includes('e')) r = clamp(r + dx, l + MIN, 1);
      if (drag.h.includes('n')) t = clamp(t + dy, 0, b - MIN);
      if (drag.h.includes('s')) b = clamp(b + dy, t + MIN, 1);
      SE.crop = [l, t, r, b];
    }
    seDraw();
  });
  const end = () => {
    if (!drag) return;
    const [l, t, r, b] = SE.crop;
    if (r - l < MIN || b - t < MIN) SE.crop = drag.c;  // 只是点了一下：不改
    drag = null; seDraw();
  };
  stage.addEventListener('pointerup', end);
  stage.addEventListener('pointercancel', end);
  $('#seFull').onclick = () => { SE.crop = [0, 0, 1, 1]; seDraw(); };
}
function setShotProp(o, k, v) { if (v) o[k] = v; else delete o[k]; return o; }  // 没裁剪 / 自动大小就不记
/* 课程重点的图按秒数记在 fig_edits；什么都没改的去掉 */
function setFigEdit(t, k, v) {
  const key = String(t), all = S.doc.fig_edits = S.doc.fig_edits || {};
  const e = setShotProp(all[key] || {}, k, v);
  if (Object.keys(e).length) all[key] = e; else delete all[key];
}
const SIZE_PRESETS = [['小', 240], ['中', 400], ['大', 600], ['自动', 0]];
/* 「大小」弹出框：w 现在的宽度（没有 = 自动），cur 图现在实际显示多宽（自动时滑块从这里开始），
   set(w) 改一张图并马上显示，all(w) 这节课所有图都用这个大小 */
function openSizeMenu(btn, { w, cur, set, all }) {
  const r = btn.getBoundingClientRect();
  const val = w || clamp(Math.round((cur || 480) / 10) * 10, 120, 960);
  showCtx(r.left, r.bottom + 4, `<div class="menu-label">显示大小（拖动滑块，图马上跟着变）</div>
    <div class="size-row"><input type="range" data-a="w" min="120" max="960" step="10" value="${val}"><span data-a="lab"></span></div>
    <div class="size-presets">${SIZE_PRESETS.map(([n, v]) => `<button data-p="${v}" title="${v ? v + ' 像素宽' : '按原来的大小显示'}">${n}</button>`).join('')}</div>
    <button class="menu-item" data-a="all" title="课程重点和字幕栏里这节课的所有截图都用这个大小">这节课的图都用这个大小</button>`, m => {
    let now = w || null;
    const show = () => {
      $('[data-a=lab]', m).textContent = now ? `${now} 像素宽` : '自动';
      $$('[data-p]', m).forEach(b => b.classList.toggle('on', +b.dataset.p === (now || 0)));
    };
    const pick = v => { now = v || null; if (now) $('[data-a=w]', m).value = now; set(now); markDirty(); show(); };
    $('[data-a=w]', m).oninput = e => pick(+e.target.value);
    $$('[data-p]', m).forEach(b => b.onclick = () => pick(+b.dataset.p));
    $('[data-a=all]', m).onclick = () => {
      m.hidden = true; all(now); markDirty();
      toast(now ? `这节课的图都改成 ${now} 像素宽了` : '这节课的图都恢复成原来的大小了');
    };
    show();
  });
}
/* 这节课的图都用同一个大小：课程重点里的每张图、字幕栏的每张截图 */
function sizeAllShots(w) {
  const ts = new Set((S.doc.summary || '').split('\n').map(figTime).filter(t => t !== null));
  ts.forEach(t => setFigEdit(t, 'w', w));
  (S.doc.sub_shots || []).forEach(x => setShotProp(x, 'w', w));
  renderSummary(); placeSubShots();
}
function shotHTML(f, i) {
  return `<div class="shot" data-k="${i}">
    <div><img alt="" data-st="${f.t}" title="点击看大图"></div>
    <div class="shot-body">
      <div class="shot-head"><a class="tslink" data-t="${f.t}">[${fmt(f.t)}]</a>
        <label class="check" title="课程重点里可以插这张图"><input type="checkbox" data-fig ${f.figure ? 'checked' : ''}> 可插图</label>
        ${f.why ? `<span class="why" title="${esc(f.why)}">${esc(f.manual ? '手动截取' : f.why).slice(0, 24)}</span>` : (f.manual ? '<span class="why">手动截取</span>' : '')}
        <span class="grow"></span>
        ${f.redraw ? '<span class="redraw-tag" title="画面看不清，剪藏时用 AI 重画的图代替截图">AI 重画</span>' : ''}
        <button data-redo title="让 AI 重新写这张图的说明">重新识别</button>${f.redraw
          ? '<button data-unredraw title="不用 AI 重画的图，剪藏时插原截图">改用原截图</button>'
          : '<button data-redraw title="画面看不清时，让 AI 依据老师的讲解重画一张清楚的示意图（标注为 AI辅助理解图）">AI 重画</button>'}<button data-del title="删除这张画面">删除</button></div>
      <textarea data-note spellcheck="false" placeholder="${f.note === undefined ? '还没有说明，点「补写说明」让 AI 写一句，或者自己写' : ''}">${esc(f.note || '')}</textarea>
      ${f.caption ? `<div class="cap">画面字幕：「${esc(f.caption)}」</div>` : ''}
      ${f.redraw ? `<div class="redraw-box"><div class="cap">${REDRAW_LABEL}${f.redraw.note ? '：' + esc(f.redraw.note) : ''}</div>${redrawHTML(f.redraw)}</div>` : ''}
    </div></div>`;
}
function renderFrames() {
  const box = $('#framesBox');
  const list = (S.doc && S.doc.frames) || [];
  $('#framesCount').textContent = list.length ? list.length : '';
  if (!S.doc || !list.length) {
    box.innerHTML = `<div class="ai-empty">${S.info && !S.info.has_video ? '这是音频文件，没有画面。' : '点「找插图」：软件挑出光看文字不好理解、需要看图的画面（走势图、K 线标注、老师手画的曲线），<br>每张写一句说明。课程重点里只在文字确实说不清的地方插图，整节课最多 3 张；纯文字的课件不插。'}</div>`;
  } else {
    box.innerHTML = list.map(shotHTML).join('');
    $$('img[data-st]', box).forEach(img => frameUrl(+img.dataset.st, true).then(u => { if (u) img.src = u; }));
  }
  const missing = list.filter(f => !f.note).length;
  $('#framesHint').textContent = list.length ? `${list.length} 张${missing ? `，${missing} 张还没有记录` : ''}` : '';
  updateButtons();
}
function sortFrames() { S.doc.frames = (S.doc.frames || []).sort((a, b) => a.t - b.t); }
async function findFrames(auto) {
  if (!S.doc || !S.path) return false;
  const old = S.doc.frames || [];
  const manual = old.filter(f => f.manual);
  if (!auto && old.length > manual.length && !confirm(`重新找关键帧？\n现在自动找到的 ${old.length - manual.length} 张画面和它们的记录会被替换（你手动截取的 ${manual.length} 张保留）。`)) return false;
  if (S.settings.ai_hidden) savePrefs({ ai_hidden: false });
  $$('.ai-tabs .tab')[2].click();
  try {
    const res = await runSteps(['frames'], '正在找关键帧…');
    renderDoc();
    const out = stepResult(res, 'frames');
    if (!out) return false;
    if (out.skipped !== undefined) { toast(out.skipped, { ms: 7000 }); return true; }
    toast(out.figures ? `挑出 ${out.figures} 张需要看图才好理解的画面，课程重点里会在必要的地方插图（最多 3 张）` : '没有需要插图的画面（纯文字课件或只有老师讲话的视频很正常）', { ms: 7000 });
    return true;
  } catch (e) { fail(e); return false; }
}
async function describeFrames(list) {
  const doc = S.doc;
  if (!list.length) return false;
  list.forEach(f => { const el = $(`.shot[data-k="${doc.frames.indexOf(f)}"]`); if (el) el.classList.add('busy'); });
  try {
    await flushSave();
    const r = await api('frames_describe', { path: S.path, frames: list.map(f => ({ t: f.t })), segments: doc.segments });
    const apply = items => {
      for (const x of items) {
        const f = (doc.frames || []).find(y => Math.abs(y.t - x.t) < 0.01);
        if (f) Object.assign(f, { note: x.note || '', caption: x.caption || '', figure: !!x.figure });
      }
      if (S.doc === doc) renderFrames();
    };
    return await runJob(r.job, 'AI 正在写图片说明…', {
      onItems: apply,
      onDone: job => { apply(job.result.frames || []); markDirty(); save(); toast('图片说明写好了'); },
      onError: job => { renderFrames(); if (job.status === 'error') fail(new Error(job.error)); },
    });
  } catch (e) { renderFrames(); fail(e); return false; }
}
async function redrawFrames(list) {
  const doc = S.doc;
  if (!list.length) return false;
  list.forEach(f => { const el = $(`.shot[data-k="${doc.frames.indexOf(f)}"]`); if (el) el.classList.add('busy'); });
  try {
    await flushSave();
    const r = await api('frames_redraw', { path: S.path, frames: list.map(f => ({ t: f.t, note: f.note || '' })), segments: doc.segments });
    return await runJob(r.job, 'AI 正在重画看不清的画面…', {
      onDone: job => {
        let n = 0;
        for (const x of job.result.frames || []) {
          const f = (doc.frames || []).find(y => Math.abs(y.t - x.t) < 0.01);
          if (f && x.redraw) { f.redraw = x.redraw; f.figure = true; n++; }
        }
        if (S.doc === doc) { renderFrames(); renderSummary(); }
        if (n) { markDirty(); save(); }
        toast(n ? '重画好了，剪藏时用这张 AI辅助理解图代替截图' : 'AI 拿不准画面上是什么，没有重画（剪藏时继续用原截图）', { ms: 6000 });
      },
      onError: job => { renderFrames(); if (job.status === 'error') fail(new Error(job.error)); },
    });
  } catch (e) { renderFrames(); fail(e); return false; }
}
async function captureFrame() {
  const v = $('#video');
  if (!S.doc || !v.getAttribute('src')) return;
  v.pause();
  const t = +v.currentTime.toFixed(2);
  S.doc.frames = S.doc.frames || [];
  if (S.doc.frames.some(f => Math.abs(f.t - t) < 0.5)) return toast('这个时间已经有一张了');
  const f = { t, manual: true, why: '手动截取' };
  S.doc.frames.push(f); sortFrames();
  if (S.settings.ai_hidden) savePrefs({ ai_hidden: false });
  $$('.ai-tabs .tab')[2].click();
  renderFrames(); markDirty();
  if (providerReady(S.settings.vision)) await describeFrames([f]);
  else toast('已截取。还没设置「画面识别」用的 AI，可以自己在下面写一句说明', { ms: 6000 });
}
/* 用画面复核存疑句：AI 校对后还拿不准的句子，截下说话时的画面再核对一次 */
async function recheckDoubts(auto) {
  if (!doubtIndexes().length) { if (!auto) toast('没有存疑句'); return true; }
  try {
    const before = S.doc.segments.map(s => s.text);
    snapshot();
    const res = await runSteps(['recheck'], 'AI 正在用画面复核存疑句…');
    renderDoc();
    const out = stepResult(res, 'recheck');
    if (!out || out.skipped !== undefined) { dropSnapshot(); if (out) toast(out.skipped); return !!out || !!auto; }  // 一键整理里复核失败不影响后面的步骤
    markFixed(before);
    toast(`用画面复核了 ${out.checked} 句存疑句：确认 ${out.confirmed} 句（改了 ${out.fixed} 句，绿色下划线标出）${out.left ? `，还有 ${out.left} 句看不清，留给你回听` : ''}`, { ms: 9000 });
    return true;
  } catch (e) { fail(e); return !!auto; }
}
function shotOf(el) { const k = el.closest('.shot'); return k && S.doc.frames[+k.dataset.k]; }
function showLightbox(url) {
  if (!url) return;
  const lb = document.createElement('div'); lb.className = 'lightbox';
  lb.innerHTML = `<img src="${esc(url)}" alt="">`; lb.onclick = () => lb.remove();
  document.body.appendChild(lb);
}

/* ------------------------------------------------------------ AI 面板 ---- */
let summaryEditing = false;
/* 生成课程重点之后字幕又改了几句以上：提醒重新生成（课程重点是按当时的字幕写的，不会跟着字幕变） */
const SUMMARY_STALE_AT = 3;  // 和 core/doc.py 的 SUMMARY_STALE_AT 一致
function renderSummaryStale() {
  const n = S.summaryChanges || 0;
  const show = !!(S.doc && S.mode !== 'book' && (S.doc.summary || '').trim() && n >= SUMMARY_STALE_AT);
  $('#summaryStale').hidden = !show;
  if (show) $('#summaryStaleText').textContent = `生成课程重点之后，字幕又改了 ${n} 句，重点里可能还是改之前的说法。建议重新生成（不另花钱，约 1～2 分钟）。`;
  syncLibFromDoc();
}
function renderSummary() {
  const box = $('#summaryBox');
  const s = S.doc && S.doc.summary;
  box.innerHTML = s ? md(s) : `<div class="ai-empty">生成字幕后，点「生成课程重点」：<br>AI 按老师这节课的知识体系整理重点，每条标出处时间，<br>再自动对照字幕查漏补缺；剪藏时放在笔记的上半部分。</div>`;
  $('#btnSummary').textContent = s ? '重新生成' : '生成课程重点';
  $('#btnCopySummary').disabled = !s;
  $('#btnEditSummary').disabled = !S.doc;
  $('#summaryHint').textContent = s ? '可以编辑；剪藏时放在笔记上半部分' : '';
  renderSummaryStale();
  updateButtons();
  if (s) refreshCoverage();
  hydrateFigures(box);
  $$('.shot-fig', box).forEach((fig, n) => fig.querySelector('figcaption').insertAdjacentHTML('beforeend',
    `<span class="fig-tools tts-skip" data-n="${n}"><button data-sf="up" title="挪到上一条重点前面">↑ 上移</button><button data-sf="down" title="挪到下一条重点后面">↓ 下移</button>${fig.querySelector('.redraw-svg, .redraw-code') ? '' : '<button data-sf="crop" title="只留需要的那一块">✂ 裁剪</button>'}${fig.querySelector('.redraw-code') ? '' : '<button data-sf="size" title="调整这张图显示多大（拖滑块马上看到效果）">⇲ 大小</button>'}<button data-sf="del" title="从课程重点里删掉这张图">删除</button></span>`));
}
/* ---------- 课程重点里手动插截图（2026-11-02 用户要求，和字幕栏的「插入截图」一样）：和 AI 插的图同一种写法，一行 ![[画面 mm:ss]]
   （剪藏时 doc.place_figures 换成截图）。插在出处时间离视频现在最近（不晚于它）的那一条后面，正在编辑时插在光标那一行后面；
   图下面「上移 / 下移」= 和上一块 / 下一块（一条重点、一个标题、一张表、一段代码）换位置，「删除」去掉这一行，AI 插的图也能挪。
   手动插的时间记在 sum_shots：重新生成课程重点时由 doc.keep_sum_shots 放回去（分块和找位置的规则和这里一样，改一边要改另一边）。 ---------- */
const FIG_LINE = /^\s*!\[\[\s*画面\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*\]\]\s*$/;
const BLOCK_START = /^\s*([-*+]\s|\d+[.、)]|#{1,6}\s|>|\||```|~~~|!\[\[)/;
const figTime = line => { const m = line.match(FIG_LINE); return m ? parseTs(m[1]) : null; };
function sumBlocks(lines) {  // 非空行分成块：[第一行, 最后一行]
  const out = []; let i = 0;
  while (i < lines.length) {
    if (!lines[i].trim()) { i++; continue; }
    const st = i;
    if (/^\s*(```|~~~)/.test(lines[i])) { const f = lines[i].trim().slice(0, 3); i++; while (i < lines.length && !lines[i].trim().startsWith(f)) i++; i++; }
    else if (/^\s*\|/.test(lines[i])) { while (i < lines.length && /^\s*\|/.test(lines[i])) i++; }
    else if (FIG_LINE.test(lines[i])) i++;
    else { i++; while (i < lines.length && lines[i].trim() && !BLOCK_START.test(lines[i])) i++; }
    out.push([st, Math.min(i, lines.length) - 1]);
  }
  return out;
}
function sumTidy(lines) {  // 连着的空行只留一个（代码块里不动），去掉开头结尾的空行
  const out = []; let fence = false;
  for (const l of lines) {
    if (/^\s*(```|~~~)/.test(l)) fence = !fence;
    if (!fence && !l.trim() && (!out.length || !out[out.length - 1].trim())) continue;
    out.push(l);
  }
  while (out.length && !out[out.length - 1].trim()) out.pop();
  return out.join('\n');
}
function sumAnchor(lines, t) {  // 插在哪一行后面：出处时间不晚于 t、离 t 最近的那一块；都比 t 晚就放在出处最早的那块前面；没有出处时间放最后
  let best = null, first = null;
  for (const [a, b] of sumBlocks(lines)) {
    if (FIG_LINE.test(lines[a]) || /^\s*(```|~~~)/.test(lines[a])) continue;  // 图、代码块里的时间不算
    const ts = (lines.slice(a, b + 1).join(' ').match(/(?<![\d:])(?:\d{1,2}:)?\d{1,2}:\d{2}(?![\d:])/g) || []).map(parseTs).filter(x => x !== null);
    for (const x of ts) {
      if (x <= t && (!best || x > best.x)) best = { x, end: b };
      if (!first || x < first.x) first = { x, start: a };
    }
  }
  return best ? best.end : first ? first.start - 1 : lines.length - 1;
}
function scrollToSumFig(t) {
  const n = S.doc.summary.split('\n').filter(l => FIG_LINE.test(l)).findIndex(l => figTime(l) === t);
  const fig = $$('#summaryBox .shot-fig')[n]; if (fig) fig.scrollIntoView({ block: 'nearest' });
}
function insertSumShot() {
  const v = $('#video');
  if (!S.doc || !v.getAttribute('src')) return;
  if (!S.info || !S.info.has_video) return toast('这是音频，没有画面可以截');
  if (!(S.doc.summary || '').trim()) return toast('还没有课程重点，先生成课程重点再插截图');
  v.pause();
  const t = Math.floor(v.currentTime), lines = S.doc.summary.split('\n');
  if (lines.some(l => figTime(l) === t)) return toast('这个时间已经插过一张了');
  let at;
  if (summaryEditing) {  // 编辑时插在光标所在的那一块后面
    const ed = $('#summaryEdit'), ln = ed.value.slice(0, ed.selectionStart).split('\n').length - 1;
    const blk = sumBlocks(lines).find(([a, b]) => a <= ln && ln <= b);
    at = blk ? blk[1] : Math.min(ln, lines.length - 1);
  } else {
    at = sumAnchor(lines, t);
    for (const [a, b] of sumBlocks(lines))  // 那一条后面已经有更早的截图：排在它们后面，按时间先后
      if (a > at && FIG_LINE.test(lines[a]) && figTime(lines[a]) < t && lines.slice(at + 1, a).every(l => !l.trim())) at = b;
  }
  lines.splice(at + 1, 0, '', `![[画面 ${fmt(t)}]]`, '');
  S.doc.summary = sumTidy(lines);
  S.doc.sum_shots = (S.doc.sum_shots || []).filter(x => x !== t).concat([t]);
  markDirty();
  if (summaryEditing) { $('#summaryEdit').value = S.doc.summary; return toast('截图插在光标那一行后面了'); }
  renderSummary(); scrollToSumFig(t);
  toast('截图插在出处时间最接近的那一条后面了；位置不对可以点图下面的「上移 / 下移」', { ms: 5000 });
}
function moveSumShot(n, act) {  // n：课程重点里从上往下第几张图
  const lines = S.doc.summary.split('\n'), blocks = sumBlocks(lines);
  const figs = blocks.map((b, k) => k).filter(k => FIG_LINE.test(lines[blocks[k][0]]));
  const k = figs[n]; if (k === undefined) return;
  const li = blocks[k][0], tag = lines[li].trim(), t = figTime(tag);
  if (act === 'crop') {
    const f = figShot(t);
    return openShotEdit(f ? f.t : t, ((S.doc.fig_edits || {})[String(t)] || {}).crop).then(r => {
      if (r) { setFigEdit(t, 'crop', r.crop); markDirty(); renderSummary(); }
    });
  }
  if (act === 'del') {
    if (!confirm('从课程重点里删掉这张图？')) return;
    lines.splice(li, 1);
    S.doc.sum_shots = (S.doc.sum_shots || []).filter(x => x !== t);
    if (!lines.some(l => figTime(l) === t) && S.doc.fig_edits) delete S.doc.fig_edits[String(t)];
  } else {
    const j = act === 'up' ? k - 1 : k + 1;
    if (j < 0 || j >= blocks.length) return toast(act === 'up' ? '已经在最前面了' : '已经在最后面了');
    const at = act === 'up' ? blocks[j][0] : blocks[j][1];  // 上移：放到上一块前面；下移：删掉这一行后下一块往前挪了一行，放在它最后一行后面
    lines.splice(li, 1);
    lines.splice(at, 0, '', tag, '');
  }
  S.doc.summary = sumTidy(lines);
  markDirty(); renderSummary();
  if (act !== 'del') scrollToSumFig(t);
}
/* 打印课程重点（2026-11-02 用户要求，按钮在朗读旁边）：只印课程名和课程重点（连同插图），白底黑字；打印窗口里也能另存为 PDF */
async function printSummary() {
  if (!S.doc || !(S.doc.summary || '').trim()) return;
  $$('#printArea').forEach(el => el.remove());
  const box = document.createElement('div');
  box.id = 'printArea'; box.className = 'md';
  box.innerHTML = `<h1>${esc(S.doc.title || '课程重点')}</h1><div class="print-sub">课程重点</div>${md(S.doc.summary)}`;
  document.body.appendChild(box);
  hydrateFigures(box);
  const t0 = Date.now();  // 等插图加载好（最多 8 秒），不然打出来是空框
  while (Date.now() - t0 < 8000 && $$('img', box).some(img => !img.getAttribute('src') || !img.complete)) await sleep(100);
  document.body.classList.add('printing');
  window.addEventListener('afterprint', () => { document.body.classList.remove('printing'); box.remove(); }, { once: true });
  window.print();
}
/* 字幕有多少被课程重点的出处时间引用到（粗略估计，用来判断有没有大段漏掉） */
function refreshCoverage() {
  const doc = S.doc; if (!doc || !(doc.summary || '').trim()) return;
  api('coverage', { doc: { segments: doc.segments, summary: doc.summary } }).then(r => {
    if (S.doc !== doc || S.chatAbort) return;
    const gaps = r.gaps.length ? `，有 ${r.gaps.length} 段没有出处（如 ${r.gaps.slice(0, 3).map(g => fmt(g[0])).join('、')}）` : '';
    $('#summaryHint').textContent = `字幕被引用约 ${Math.round(r.ratio * 100)}%${gaps}；可以编辑，剪藏时放在笔记上半部分`;
  }).catch(() => {});
}
async function runReview() {
  if (!S.doc || !(S.doc.summary || '').trim()) return false;
  try {
    const res = await runSteps(['review'], 'AI 正在对照字幕查漏…');
    renderDoc();
    const out = stepResult(res, 'review');
    if (!out) return false;
    if (out.skipped !== undefined) { toast(out.skipped); return true; }
    reviewToast(out);
    return true;
  } catch (e) { fail(e); return false; }
}
function toggleSummaryEdit(on) {
  summaryEditing = on ?? !summaryEditing;
  $('#summaryEdit').hidden = !summaryEditing; $('#summaryBox').hidden = summaryEditing;
  $('#btnEditSummary').textContent = summaryEditing ? '完成编辑' : '编辑';
  if (summaryEditing) { $('#summaryEdit').value = S.doc.summary || ''; $('#summaryEdit').focus(); }
  else renderSummary();
}
function renderChat() {
  const box = $('#chatBox');
  const msgs = (S.doc && S.doc.chat) || [];
  if (!msgs.length) { box.innerHTML = `<div class="ai-empty">AI 读过这节课的全部字幕和术语表，可以问它任何内容。<br>回答里的时间点可以点击跳转。</div>`; return; }
  box.innerHTML = msgs.map(m => `<div class="msg ${m.role}"><div class="bubble ${m.role === 'assistant' ? 'md' : ''}">${m.role === 'assistant' ? md(m.content) : esc(m.content)}</div></div>`).join('');
  box.scrollTop = box.scrollHeight;
}
async function streamAI(body, onText) {
  const { job: id } = await api(body.api || 'chat', body);  // 书的 AI 对话走 book_chat
  let stopped = false;
  S.chatAbort = { abort() { stopped = true; api('cancel', { id }).catch(() => {}); } };
  let text = '', since = 0;
  try {
    for (;;) {
      const { job } = await api('job', { id, since });
      since = job.total;
      if (job.items.length) { text += job.items.join(''); onText(text); }
      if (stopped) { const e = new Error('已停止'); e.name = 'AbortError'; throw e; }
      if (job.status === 'error') throw new Error(job.error);
      if (job.status !== 'running') break;
      await sleep(150);
    }
  } finally { S.chatAbort = null; }
  return text;
}
async function doSummary() {
  if (S.chatAbort) { S.chatAbort.abort(); return false; }
  if (summaryEditing) toggleSummaryEdit(false);
  if (S.settings.ai_hidden) savePrefs({ ai_hidden: false });
  $$('.ai-tabs .tab')[0].click();
  $('#btnSummary').textContent = '停止';
  $('#summaryBox').innerHTML = '<div class="typing muted">AI 正在阅读字幕…</div>';
  const steps = S.settings.auto_review ? ['summary', 'review'] : ['summary'];  // 生成后自动查漏补缺（查漏失败不影响课程重点本身）
  const live = liveView();
  let res;
  try {
    res = await runSteps(steps, 'AI 正在整理课程重点…', {
      onItems: live.onItems,
      onStart: id => { S.chatAbort = { abort() { api('cancel', { id }).catch(() => {}); } }; },
    });
  } catch (e) { fail(e); return false; } finally { S.chatAbort = null; }
  renderDoc();
  const out = stepResult(res, 'summary');
  if (!out) return false;
  if (steps.includes('review')) {
    const rv = stageOf(res.task, 'review');
    if (rv.status === 'completed') reviewToast(rv.result || {});
    else if (rv.status === 'failed') toast('查漏补缺没有完成：' + (rv.error || '') + '（课程重点已经保存，可以再点「查漏补缺」）', { error: true, ms: 8000 });
  }
  return true;
}
async function sendChat(q) {
  q = (q || '').trim(); if (!q || !S.doc) return;
  if (S.chatAbort) return;
  S.doc.chat = S.doc.chat || [];
  S.doc.chat.push({ role: 'user', content: q });
  $('#chatInput').value = '';
  renderChat();
  const box = $('#chatBox');
  box.insertAdjacentHTML('beforeend', `<div class="msg assistant"><div class="bubble md typing"></div></div>`);
  const bubble = box.lastElementChild.firstElementChild;
  $('#btnSend').textContent = '停止';
  try {
    const text = await streamAI({ mode: 'chat', doc: S.doc, messages: S.doc.chat }, t => { bubble.innerHTML = md(t); box.scrollTop = box.scrollHeight; });
    S.doc.chat.push({ role: 'assistant', content: text.trim() });
  } catch (e) {
    if (e.name === 'AbortError') { const t = bubble.innerText.trim(); if (t) S.doc.chat.push({ role: 'assistant', content: t + '（已停止）' }); }
    else { fail(e); S.doc.chat.pop(); $('#chatInput').value = q; }
  }
  $('#btnSend').textContent = '发送';
  renderChat(); markDirty();
}

/* ------------------------------------------------------------- 搜索 ---- */
function runSearch(jump) {
  const q = $('#search').value.trim().toLowerCase();
  $$('.cue.hit', $('#cues')).forEach(el => el.classList.remove('hit', 'current'));
  S.hits = [];
  if (!q || !S.doc) { $('#searchCount').textContent = ''; return; }
  S.doc.segments.forEach((s, i) => { if ((s.text + ' ' + s.tr).toLowerCase().includes(q)) S.hits.push(i); });
  S.hits.forEach(i => { const el = cueEl(i); if (el) el.classList.add('hit'); });
  $('#searchCount').textContent = S.hits.length ? `${S.hitPos >= 0 ? S.hitPos + 1 : 0}/${S.hits.length}` : '无结果';
  if (jump && S.hits.length) {
    S.hitPos = (S.hitPos + 1) % S.hits.length;
    $$('.cue.current', $('#cues')).forEach(el => el.classList.remove('current'));
    const el = cueEl(S.hits[S.hitPos]);
    if (el) { el.classList.add('current'); el.scrollIntoView({ block: 'center' }); S.userScrollAt = Date.now(); }
    $('#searchCount').textContent = `${S.hitPos + 1}/${S.hits.length}`;
  }
}

/* ------------------------------------------------------------ 设置窗口 ---- */
function getPath(obj, path) { return path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj); }
function setPath(obj, path, val) {
  const ks = path.split('.'); let o = obj;
  ks.slice(0, -1).forEach(k => { o[k] = o[k] || {}; o = o[k]; });
  o[ks[ks.length - 1]] = val;
}
function providerReady(name) { const p = S.settings.providers[name] || {}; return !!p.has_key && !!p.chat_model; }
function fillProviderSelects() {
  $$('.provider-select').forEach(sel => {
    const cur = getPath(S.settings, sel.dataset.k);
    sel.innerHTML = S.chatProviders.map(p => `<option value="${p}" ${p === cur ? 'selected' : ''}>${esc(S.providerNames[p])}${providerReady(p) ? '　· ' + esc(S.settings.providers[p].chat_model) : '　（未设置）'}</option>`).join('');
  });
}
function markNav() {
  const P = S.settings.providers;
  const ready = { codex: P.codex.has_key, claude: P.claude.has_key, gemini: P.gemini.has_key, deepseek: P.deepseek.has_key, ark: P.ark.has_key,
    siliconflow: P.siliconflow.has_key, openai: P.openai.has_key, doubao: P.doubao.has_key, teachers: S.teachers.length > 0 };
  $$('#settingsNav button').forEach(b => {
    const st = b.querySelector('.state'); if (st) st.remove();
    if (ready[b.dataset.sec]) b.insertAdjacentHTML('beforeend', `<span class="state">${b.dataset.sec === 'teachers' ? S.teachers.length : '✓'}</span>`);
  });
}
function openSettings(section) {
  const dlg = $('#dlgSettings');
  const s = S.settings;
  dlg._draft = { theme: s.theme, accent: s.accent };
  $('#themePick').innerHTML = THEMES.map(t => `<button data-theme="${t.id}" style="background:${t.bg};color:${t.fg}" class="${s.theme === t.id ? 'active' : ''}">${t.name}</button>`).join('');
  $('#accentPick').innerHTML = ACCENTS.map(c => `<button data-accent="${c}" style="background:${c}" class="${s.accent === c ? 'active' : ''}"></button>`).join('');
  fillProviderSelects();
  $$('[data-k]', dlg).forEach(el => {
    if (el.classList.contains('provider-select')) return;
    const v = getPath(s, el.dataset.k);
    if (el.type === 'checkbox') el.checked = !!v;
    else if (el.hasAttribute('data-secret')) {
      if (el.dataset.ph0 === undefined) el.dataset.ph0 = el.placeholder;
      el.value = ''; delete el.dataset.cleared;
      el.placeholder = v ? `已保存（${v}），留空表示不修改` : el.dataset.ph0;
    } else el.value = v ?? '';
  });
  $('#summaryTemplate').value = s.summary_template || S.defaultTemplate;
  const p = s.providers.doubao;
  $$('input[name=dbAuth]').forEach(r => r.checked = r.value === (p.app_id && !p.key ? 'old' : 'new'));
  refreshDoubaoFields(); renderDoubaoAccounts();
  renderCodexBox(); $('#codexPaste').hidden = true;
  renderTeacherList(S.teachers[0] && S.teachers[0].id);
  $$('.test-result', dlg).forEach(el => { el.textContent = ''; el.className = 'test-result'; });
  markNav();
  showSection(section || 'look');
  api('about').then(a => {
    $('#aboutVersion').textContent = a.version || '开发版';
    $('#aboutLog').textContent = a.log_dir; $('#aboutData').textContent = a.data_dir; $('#aboutCache').textContent = a.cache_dir;
    $('#aboutCacheSize').textContent = mb(a.cache);
    $('#aboutRoot').textContent = a.storage_root + (a.storage_custom ? '' : '（软件所在文件夹）');
    $('#btnStorageReset').disabled = !a.storage_custom;
  }).catch(() => {});
  dlg.showModal();
}
function showHelp(sec) {
  $$('#helpNav [data-h]').forEach(b => b.classList.toggle('active', b.dataset.h === sec));
  $$('#helpBody section').forEach(el => el.hidden = el.dataset.h !== sec);
  $('#helpBody').scrollTop = 0;
}
function showSection(sec) {
  $$('#settingsNav button').forEach(b => b.classList.toggle('active', b.dataset.sec === sec));
  $$('#settingsBody section').forEach(el => el.hidden = el.dataset.sec !== sec);
}
function refreshDoubaoFields() {
  const auth = ($('input[name=dbAuth]:checked') || {}).value || 'new';
  $$('[data-auth]').forEach(el => el.hidden = el.dataset.auth !== auth);
}
/* 豆包：模型不让选，按固定顺序自动换（2026-10-14 用户实测后定的）；可以填几个账号，额度用完的记下来 */
function doubaoAccountName(id) {
  const p = S.settings.providers.doubao;
  if (id === 'main') return p.name || '账号 1';
  const k = (p.more_keys || []).findIndex(a => a.id === id);
  return k < 0 ? '（已删除的账号）' : (p.more_keys[k].name || `账号 ${k + 2}`);
}
function dbRowHTML(a, n) {
  return `<div class="key-row db-acct" data-id="${esc(a.id || '')}">
    <input class="db-name" value="${esc(a.name || '')}" placeholder="账号 ${n}">
    <input class="db-key" value="" placeholder="${a.key ? `已保存（${esc(a.key)}），留空表示不修改` : '这个账号的 API Key'}">
    <button class="btn small" data-db-del title="删掉这个账号">删除</button></div>`;
}
function renderDoubaoAccounts() {
  const p = S.settings.providers.doubao;
  const pick = p.pick || '';
  $('#doubaoOrder').innerHTML = `<div class="label strong">生成字幕用哪种模型</div>
    <label class="check"><input type="radio" name="dbPick" value="" ${pick ? '' : 'checked'}> <b>自动</b>：按下面的顺序用，额度用完换下一个（每种模型先把所有账号的用完，再换下一种）</label>
    <ol>${S.doubaoOrder.map(k => `<li><label class="check"><input type="radio" name="dbPick" value="${k}" ${pick === k ? 'checked' : ''}> 只用 <b>${esc(S.doubaoModels[k].name)}</b>：${esc(S.doubaoModels[k].note)}</label></li>`).join('')}</ol>
    ${dbAccts().length > 1 ? `<div class="label strong">用哪个账号</div>
    <label class="check"><input type="radio" name="dbAcct" value="" ${dbAcctNow() === 'auto' ? 'checked' : ''}> <b>自动</b>：从${esc(dbAccts()[0].name)}开始，按列表顺序用，额度用完换下一个</label>
    ${dbAccts().map(a => `<label class="check"><input type="radio" name="dbAcct" value="${esc(a.id)}" ${dbAcctNow() === a.id ? 'checked' : ''}> 只用 <b>${esc(a.name)}</b></label>`).join('')}` : ''}
    <p class="hint">选定一种模型或一个账号时，只用选定的，出错就停下告诉你，<b>不会换到别的模型或账号</b>；没选定的那一项照样自动换。生成字幕、一键整理时还可以临时换；命令行（AI 自动整理）按这里选的。资源包或免费额度用完后豆包可能继续按量扣费，欠费后所有模型都用不了。这几种都要先在火山引擎控制台「豆包语音 → 开通管理」里开通。</p>`;
  $('#dbMore').innerHTML = (p.more_keys || []).map((a, i) => dbRowHTML(a, i + 2)).join('');
  const spent = Object.entries(p.spent || {});
  $('#dbSpent').innerHTML = spent.length ? `<div class="label strong">额度用完了的</div>${spent.map(([k, day]) => {
    const [id, m] = k.split('|');
    return `<div class="db-spent"><span>${esc(doubaoAccountName(id))} · ${esc((S.doubaoModels[m] || {}).short || m)}：${esc(day)} 用完</span>
      <button class="btn small" data-db-revive="${esc(k)}">重新启用</button></div>`;
  }).join('')}<p class="hint">充值或买了资源包以后点「重新启用」；也可以点下面的「测试连接」，能用的自动重新启用。</p>` : '';
}
async function reviveDoubao(key) {
  const spent = Object.assign({}, S.settings.providers.doubao.spent || {});
  delete spent[key];
  try {
    const r = await api('settings', { settings: { providers: { doubao: { spent } } } });
    S.settings.providers.doubao.spent = r.settings.providers.doubao.spent;
    renderDoubaoAccounts(); toast('已重新启用，下次识别会先试它');
  } catch (e) { fail(e); }
}
function collectSettings() {
  const dlg = $('#dlgSettings');
  const patch = { theme: dlg._draft.theme, accent: dlg._draft.accent };
  $$('[data-k]', dlg).forEach(el => {
    let v;
    if (el.type === 'checkbox') v = el.checked;
    else if (el.type === 'number') v = Number(el.value) || 0;
    else v = el.value.trim();
    if (el.hasAttribute('data-secret')) { if (el.dataset.cleared) v = null; else if (!v) return; }
    setPath(patch, el.dataset.k, v);
  });
  if (patch.summary_template && patch.summary_template.trim() === S.defaultTemplate.trim()) patch.summary_template = '';
  if (($('input[name=dbAuth]:checked') || {}).value === 'old') setPath(patch, 'providers.doubao.key', null);
  // 其他豆包账号：密钥留空表示不改（后台按 id 保留原来的）；新加的没填密钥就不要
  if ($('input[name=dbPick]:checked')) setPath(patch, 'providers.doubao.pick', $('input[name=dbPick]:checked').value);
  if ($('input[name=dbAcct]:checked')) setPath(patch, 'providers.doubao.pick_account', $('input[name=dbAcct]:checked').value);
  setPath(patch, 'providers.doubao.more_keys', $$('#dbMore .db-acct').map(r => ({
    id: r.dataset.id, name: $('.db-name', r).value.trim(), key: $('.db-key', r).value.trim() })).filter(a => a.id || a.key));
  return patch;
}
async function saveSettings(close = true) {
  try {
    if (teacherFormDirty() && !(await saveTeacher())) return false;
    const r = await api('settings', { settings: collectSettings() });
    S.settings = keepLayout(r.settings); S.teachers = r.teachers;
    $$('#dlgSettings [data-secret]').forEach(el => delete el.dataset.cleared);
    applyLook(); fillProviderSelects(); markNav();
    if (close) { $('#dlgSettings').close(); toast('设置已保存'); }
    return true;
  } catch (e) { fail(e); return false; }
}
async function refreshSettingsOnly() {
  const d = await api('settings', {});
  S.settings = keepLayout(d.settings); S.teachers = d.teachers;
  fillProviderSelects(); markNav(); applyLook();
}

/* ChatGPT 账号登录 */
let codexPoll = null;
function renderCodexBox() {
  const acc = S.settings.providers.codex.account;
  $('#codexBox').innerHTML = acc
    ? `<div class="who">✓ 已登录：<b>${esc(acc.email || 'ChatGPT 账号')}</b>${acc.plan ? `（${esc(acc.plan)}）` : ''}</div><button class="btn small" id="codexLogout">退出登录</button>`
    : `<div class="who muted">还没有登录</div><button class="btn small primary" id="codexLogin">用 ChatGPT 账号登录</button>`;
  const lo = $('#codexLogout'), li = $('#codexLogin');
  if (lo) lo.onclick = async () => { await api('codex_logout'); await refreshSettingsOnly(); renderCodexBox(); toast('已退出 ChatGPT 账号'); };
  if (li) li.onclick = codexLogin;
}
async function codexLogin() {
  try {
    await api('codex_login');
    $('#codexPaste').hidden = false; $('#codexUrl').value = '';
    clearInterval(codexPoll);
    const t0 = Date.now();
    codexPoll = setInterval(async () => {
      if (!$('#dlgSettings').open || Date.now() - t0 > 600000) { clearInterval(codexPoll); return; }
      try { const r = await api('codex_finish', {}); if (r.status === 'done') codexDone(r); } catch (e) { clearInterval(codexPoll); fail(e); }
    }, 2000);
  } catch (e) { fail(e); }
}
async function codexDone(r) {
  clearInterval(codexPoll);
  $('#codexPaste').hidden = true;
  await refreshSettingsOnly(); renderCodexBox();
  toast(`ChatGPT 登录成功：${r.email || ''}`);
  const input = $('[data-k="providers.codex.chat_model"]');
  if (!input.value) {
    try {
      const { models } = await api('models', { provider: 'codex' });
      fillDatalist('codex', models);
      if (models[0]) { input.value = models[0].id; await saveSettings(false); toast('已选择模型 ' + models[0].id + '，可以在下面修改'); }
    } catch (e) { fail(e); }
  }
}
function fillDatalist(name, models) {
  const dl = $('#dl-' + name);
  if (dl) dl.innerHTML = models.map(m => `<option value="${esc(m.id)}">${esc(m.name !== m.id ? m.name : '')}</option>`).join('');
}

/* 老师档案 */
let editingTeacher = null;
function renderTeacherList(selectId) {
  $('#teacherList').innerHTML = S.teachers.map(t => `<button data-tid="${t.id}">${esc(t.name)}</button>`).join('') +
    '<button class="add" data-tid="__new">＋ 添加老师</button>';
  editTeacher(selectId ? teacherById(selectId) : null);
}
function editTeacher(t) {
  editingTeacher = t ? t.id : null;
  $$('#teacherList button').forEach(b => b.classList.toggle('active', b.dataset.tid === (t ? t.id : '__new')));
  $('#tName').value = t ? t.name : '';
  setTermsFile(t ? (t.terms_file || '') : '');
  $('#tAbout').value = t ? t.about : '';
  $('#tTerms').value = t ? t.terms : '';
  $('#tNotes').value = t ? (t.notes || '') : '';
  $('#tLang').value = t ? t.lang : 'zh';
  $('#tFolder').value = t ? t.folder : '';
  $('#tOwnTemplate').checked = !!(t && t.template);
  $('#tTemplate').value = (t && t.template) || S.settings.summary_template || S.defaultTemplate;
  $('#tTemplateField').hidden = !$('#tOwnTemplate').checked;
  $('#tDelete').hidden = !t;
  $('#tMsg').textContent = '';
  countTerms();
}
function countTerms() {
  const lines = $('#tTerms').value.split(/\n/).map(x => x.trim()).filter(x => x && !x.startsWith('#'));
  const forced = lines.filter(x => /(=>|->|→)\s*[!！]/.test(x)).length;
  const hints = lines.filter(x => /=>|->|→/.test(x)).length - forced;
  const words = lines.filter(x => !/=>|->|→/.test(x)).join(',').split(/[,，、;；]+/).filter(x => x.trim()).length;
  $('#tTermsHint').textContent = `${words} 个术语，${hints} 条识别错误参考，${forced} 条直接替换。最值得加的是：老师自创的说法、专有名词、人名和容易听错的词。`;
  const n = $('#tNotes').value.length;
  $('#tNotesHint').textContent = n ? `${n} 个字${n > 20000 ? '（超过 20000 字的部分不会发给 AI）' : ''}` : '';
}
/* 关联 Obsidian 里的术语表文件 */
async function setTermsFile(path, preview) {
  const el = $('#tFile');
  el.dataset.path = path || '';
  el.textContent = path || '没有关联';
  el.title = path || '';
  $('#tFileClear').hidden = !path;
  $('#tFileActions').hidden = !path;
  if (!path) { $('#tFileHint').textContent = TERMS_FILE_HINT; return; }
  try {
    const r = preview || await api('terms_preview', { path });
    if (($('#tFile').dataset.path || '') !== path) return;
    $('#tFileHint').textContent = `从文件里读到 ${r.words} 个术语、${r.hints} 条识别错误参考、${r.forced || 0} 条直接替换（文件 ${r.chars} 字，整份给 AI 当术语说明）。`
      + (r.dups ? `有 ${r.dups} 处重复，点「查看术语和重复检查」看在第几行。` : '没有重复的条目。') + '以后在 Obsidian 里改，软件会自动用最新的。';
  } catch (e) { $('#tFileHint').textContent = '⚠ ' + e.message; }
}
/* 「查看术语和重复检查」：软件从文件里读到的每个术语、识别错误，重复的在第几行（只看，要改在 Obsidian 里改） */
async function viewTermsFile(path) {
  try {
    const r = await api('terms_view', { path });
    const name = path.split(/[\\/]/).pop();
    const nd = r.dup_terms.length + r.dup_errors.length;
    $('#tvTitle').textContent = name.replace(/\.md$/i, '');
    $('#tvInfo').textContent = `软件从这份文件里读到 ${r.words.length} 个术语、${r.hints.length} 条识别错误参考、${r.forced.length} 条直接替换。`
      + '这里只能看；要改请点「在 Obsidian 里打开」，改完保存，软件下次就用改过的。';
    const pair = ([a, b]) => `<li>${esc(a)} → ${esc(b)}</li>`;
    $('#tvBody').innerHTML =
      `<details ${nd ? 'open' : ''}><summary>重复检查：${nd ? `${nd} 处重复` : '没有重复'}</summary>`
      + (nd ? '<p class="muted">同一个术语、同一条「错写 → 正确」在文件里出现了不止一次。软件读的时候只算一次，不影响使用；想让文件干净，可以在 Obsidian 里删掉多余的那一行。'
        + '「识别错误对照」和「直接替换」两张表都有的，留在「直接替换」里就够了（直接替换的也会作为参考给 AI）。</p><ul>'
        + r.dup_terms.map(d => `<li>术语「${esc(d.term)}」：${d.where.map(esc).join('；')}</li>`).join('')
        + r.dup_errors.map(d => `<li>「${esc(d.wrong)} → ${esc(d.right)}」：${d.where.map(esc).join('；')}</li>`).join('') + '</ul>' : '')
      + '</details>'
      + `<details><summary>术语（${r.words.length} 个，识别时当热词）</summary><p class="tv-words">${r.words.map(w => `<span class="tv-word">${esc(w)}</span>`).join('')}</p></details>`
      + `<details><summary>识别错误参考（${r.hints.length} 条，AI 校对时结合上下文判断）</summary><ul>${r.hints.map(pair).join('')}</ul></details>`
      + `<details><summary>直接替换（${r.forced.length} 条，识别完直接改）</summary><ul>${r.forced.map(pair).join('')}</ul></details>`;
    $('#tvOpen').onclick = () => openTermsFile(path, r.uri);
    $('#dlgTermsView').showModal();
  } catch (e) { fail(e); }
}
function openTermsFile(path, uri) {
  if (uri) api('reveal', { target: uri });
  else { api('reveal', { target: path }); toast('这份文件不在设置的 Obsidian 库里，在资源管理器里显示它'); }
}
const TERMS_FILE_HINT = '关联后，软件每次都读这个文件的最新内容：小节里的「正确写法 / 标准写法」和热词当术语，「ASR 常见错误」表当识别错误参考，整份文件给 AI 当术语说明。以后只在 Obsidian 里改术语表就行。';
async function pickTermsFile() {
  try {
    const r = await api('terms_preview', {});
    if (!r.path) return;
    await setTermsFile(r.path, r);
    if ($('#tNotes').value.trim() && confirm('关联文件后，文件内容会自动作为「术语说明」给 AI。\n下面「术语说明」框里已有内容（可能是旧版术语表），要清空吗？\n不清空的话，两份会一起发给 AI。')) {
      $('#tNotes').value = ''; countTerms();
    }
    toast('已选择术语表文件，记得点「保存这位老师」');
  } catch (e) { fail(e); }
}
function teacherForm() {
  return { id: editingTeacher, name: $('#tName').value, display: (teacherById(editingTeacher) || {}).display || '', terms_file: $('#tFile').dataset.path || '',
    about: $('#tAbout').value, terms: $('#tTerms').value, notes: $('#tNotes').value, lang: $('#tLang').value,
    folder: $('#tFolder').value, template: $('#tOwnTemplate').checked ? $('#tTemplate').value : '' };
}
/* 老师档案改了还没点「保存」：点「保存设置」或切换到别的档案时自动保存，免得改了名字却没生效 */
function teacherFormDirty() {
  if (!$('#tName')) return false;
  const f = teacherForm(), t = teacherById(editingTeacher);
  if (!t) return !!f.name.trim();
  return ['name', 'terms_file', 'about', 'terms', 'notes', 'lang', 'folder', 'template']
    .some(k => String(f[k] || '').trim() !== String(t[k] || '').trim());
}
async function saveTeacher() {
  const t = teacherForm();
  if (!t.name.trim()) { $('#tName').focus(); toast('请填写名称', { error: true }); return false; }
  try {
    const r = await api('teacher_save', { teacher: t });
    S.teachers = r.teachers; renderTeacherList(r.teacher.id); renderTeacherSelect(); renderLibrary(); markNav();
    $('#tMsg').textContent = '✓ 已保存'; $('#tMsg').className = 'test-result ok';
    return true;
  } catch (e) { fail(e); return false; }
}
async function deleteTeacher() {
  const t = teacherById(editingTeacher);
  if (!t || !confirm(`删除「${t.name}」和这份术语表？`)) return;
  const r = await api('teacher_delete', { id: t.id });
  S.teachers = r.teachers; renderTeacherList(S.teachers[0] && S.teachers[0].id); renderTeacherSelect(); markNav();
}

/* ------------------------------------------------------------- 菜单 ---- */
function closeMenus() { $$('.menu.open').forEach(m => m.classList.remove('open')); $('#ctxMenu').hidden = true; }
async function showRecent(target) {
  try {
    const { items } = await api('recent');
    target.innerHTML = items.length ? '<div class="menu-label">最近打开</div>' + items.slice(0, 6).map(r => `<button class="menu-item" data-open="${esc(r.path)}" title="${esc(r.path)}">${esc(r.title)}<small>${esc(r.path)}</small></button>`).join('') : '';
  } catch (e) { fail(e); }
}

/* 拖动分隔条 */
function dragger(el, axis, onMove, onEnd) {
  el.addEventListener('pointerdown', e => {
    e.preventDefault();
    try { el.setPointerCapture(e.pointerId); } catch (_) { /* 模拟事件没有真实指针 */ }
    el.classList.add('dragging');
    document.body.classList.add(axis === 'x' ? 'dragging-col' : 'dragging-row');
    const move = ev => onMove(ev);
    const up = () => { el.removeEventListener('pointermove', move); el.classList.remove('dragging'); document.body.classList.remove('dragging-col', 'dragging-row'); onEnd(); };
    el.addEventListener('pointermove', move); el.addEventListener('pointerup', up, { once: true });
  });
}

/* ------------------------------------------------------------ 事件绑定 ---- */
function bind() {
  const v = $('#video');
  bindBook();
  $('#btnOpen').onclick = pickAndOpen;
  $('#btnOpen2').onclick = pickAndOpen;
  $('#btnAddFolder2').onclick = addFolder;
  $('#btnLibrary').onclick = () => savePrefs({ sidebar: !S.settings.sidebar });
  $('#libAddFolder').onclick = addFolder;
  $('#libAddFiles').onclick = addFiles;
  $('#libRefresh').onclick = () => loadLibrary();
  $('#libSearch').oninput = renderLibrary;
  $('#libBody').addEventListener('click', e => {
    const more = e.target.closest('[data-more]');
    if (more) { e.stopPropagation(); const r = more.getBoundingClientRect(); folderMenu(more.dataset.more, r.left, r.bottom + 4); return; }
    const head = e.target.closest('[data-folder]');
    if (head) {  // 展开 / 收起，记住
      const n = libNode(head.dataset.folder); if (!n) return;
      const open = !head.parentElement.classList.contains('collapsed');
      n.collapsed = open; renderLibrary();
      api('library_set', { path: n.path, collapsed: n.collapsed }).catch(() => {});
    }
  });
  $('#libBody').addEventListener('contextmenu', e => {
    const it = e.target.closest('[data-open]'); const gh = e.target.closest('[data-folder]');
    if (it) { e.preventDefault(); itemMenu(it.dataset.open, e.clientX, e.clientY); }
    else if (gh) { e.preventDefault(); folderMenu(gh.dataset.folder, e.clientX, e.clientY); }
  });
  libDrag();
  document.addEventListener('click', e => {
    const item = e.target.closest('[data-open]');
    if (item && !e.target.closest('#ctxMenu')) { closeMenus(); openPath(item.dataset.open); return; }
    if (!e.target.closest('.menu') && !e.target.closest('#ctxMenu')) closeMenus();
    const ts = e.target.closest('.tslink');
    if (ts) seek(+ts.dataset.t, true);
  });
  $('#teacherSelect').onchange = e => {
    if (e.target.value === '__manage') { renderTeacherSelect(); openSettings('teachers'); return; }
    setDocTeacher(e.target.value);
    toast(e.target.value ? `这个视频用「${teacherById(e.target.value).name}」的术语表` : '这个视频不用术语表');
  };
  $('#optReviewed').onchange = e => { if (S.doc) { S.doc.reviewed = e.target.checked; markDirty(); } };
  $('#btnExport').onclick = e => { e.stopPropagation(); const m = $('#menuExport'); const o = !m.classList.contains('open'); closeMenus(); if (o) m.classList.add('open'); };
  $('#btnVersions').onclick = e => { e.stopPropagation(); const m = $('#menuVersions'); const o = !m.classList.contains('open'); closeMenus(); if (o) { renderVersions(); m.classList.add('open'); } };
  $('#menuVersions').addEventListener('click', e => { e.stopPropagation(); const b = e.target.closest('[data-ver]'); if (b && !b.disabled) useVersion(b.dataset.ver); });
  $('#btnText').onclick = e => { e.stopPropagation(); const m = $('#menuText'); const o = !m.classList.contains('open'); closeMenus(); if (o) m.classList.add('open'); };
  $$('[data-export]').forEach(b => b.onclick = () => doExport(b.dataset.export));
  $('#btnTranscribe').onclick = openTranscribe;
  $('#btnCorrect').onclick = () => startCorrect(false);
  $('#btnTranslate').onclick = openTranslate;
  $('#btnPipeline').onclick = openPipeline;
  $('#btnSave').onclick = () => { S.dirty = true; save(false); };
  $('#btnClip').onclick = () => (S.mode === 'book' ? bookClip() : doClip());
  $('#btnSettings').onclick = () => openSettings();
  $('#btnTheme').onclick = () => {
    const order = THEMES.map(t => t.id);
    const next = order[(order.indexOf(S.settings.theme) + 1) % order.length];
    savePrefs({ theme: next }); toast('主题：' + THEMES.find(t => t.id === next).name, { ms: 1200 });
  };
  $('#btnUndo').onclick = undo;
  const openHelp = sec => { showHelp(sec || 'start'); $('#dlgHelp').showModal(); };
  $('#btnHelp').onclick = () => openHelp('edit');
  $('#btnHelpTop').onclick = () => openHelp(S.mode === 'book' ? 'book' : 'start');
  $('#btnHelp2').onclick = () => openHelp('start');
  $('#helpNav').onclick = e => { const b = e.target.closest('[data-h]'); if (b) showHelp(b.dataset.h); };
  $('#helpClose').onclick = () => $('#dlgHelp').close();

  // 文字设置
  $('#txTheme').innerHTML = THEMES.map(t => `<option value="${t.id}">${t.name}</option>`).join('');
  $$('[data-tx]').forEach(b => b.onclick = () => {
    const [k, d] = b.dataset.tx.split(':'); const delta = +d; const s = S.settings;
    if (k === 'font_size') savePrefs({ font_size: clamp(s.font_size + delta, 12, 36) });
    if (k === 'line_height') savePrefs({ line_height: +clamp(s.line_height + delta, 1.2, 2.6).toFixed(1) });
    if (k === 'cue_gap') savePrefs({ cue_gap: clamp(s.cue_gap + delta, 2, 30) });
    if (k === 'cue_width') {
      const full = $('#cues').clientWidth;
      const next = clamp((s.cue_width || full) + delta, 360, 2400);
      savePrefs({ cue_width: next >= full ? 0 : next });
    }
  });
  $('#txFont').onchange = e => savePrefs({ font_family: e.target.value });
  $('#txTheme').onchange = e => savePrefs({ theme: e.target.value });
  $('#menuText').addEventListener('click', e => e.stopPropagation());
  // 书的文字设置：整理笔记、原书两列各有一个「Aa」，打开同一个菜单（挪到点的那个按钮下面）
  $('#bkTxTheme').innerHTML = $('#txTheme').innerHTML;
  $$('[data-bktx]').forEach(b => b.onclick = e => {
    e.stopPropagation();
    const m = $('#menuBookText'), wrap = b.closest('.menu-wrap'), o = !(m.classList.contains('open') && m.parentElement === wrap);
    closeMenus();
    if (o) { wrap.appendChild(m); m.classList.add('open'); }
  });
  $$('[data-btx]').forEach(b => b.onclick = () => {
    const [k, d] = b.dataset.btx.split(':'); const delta = +d; const s = S.settings;
    if (k === 'book_font_size') savePrefs({ book_font_size: clamp((s.book_font_size || 15) + delta, 12, 32) });
    if (k === 'book_line_height') savePrefs({ book_line_height: +clamp((s.book_line_height || 1.8) + delta, 1.2, 2.8).toFixed(1) });
    if (k === 'book_gap') savePrefs({ book_gap: clamp((s.book_gap ?? 8) + delta, 0, 40) });
    if (k === 'book_width') {
      const col = b.closest('.bk-col'), full = ((col && col.querySelector('.bk-text:not([hidden])')) || $('#bkView')).clientWidth - 32;
      const next = clamp((s.book_width || full) + delta, 360, 2400);
      savePrefs({ book_width: next >= full ? 0 : next });
    }
  });
  $('#bkTxFont').onchange = e => savePrefs({ book_font_family: e.target.value });
  $('#bkTxTheme').onchange = e => savePrefs({ theme: e.target.value });
  $('#menuBookText').addEventListener('click', e => e.stopPropagation());

  // 播放器
  v.addEventListener('timeupdate', () => { updateActive(); if (!v.paused) savePosition(false); });
  v.addEventListener('pause', () => savePosition(true));
  v.addEventListener('ended', () => savePosition(true));
  document.addEventListener('visibilitychange', () => { if (document.hidden) savePosition(true); });
  v.addEventListener('seeked', () => updateActive(true));
  v.addEventListener('play', () => { if (S.loopIdx == null) S.loopIdx = S.active >= 0 ? S.active : null; tick(); });
  v.addEventListener('error', () => { if (v.getAttribute('src')) fail(new Error('这个文件无法在软件里播放，字幕功能仍然可以使用')); });
  let raf = null;
  function tick() { cancelAnimationFrame(raf); if (!v.paused) { updateActive(); raf = requestAnimationFrame(tick); } }
  $('#speed').onchange = () => v.playbackRate = +$('#speed').value;
  $('#optFollow').onchange = e => savePrefs({ follow: e.target.checked });
  $('#optTr').onchange = e => savePrefs({ show_tr: e.target.checked });
  $('#optOverlay').onchange = e => { savePrefs({ overlay: e.target.checked }); updateActive(true); };
  $('#optLoop').onchange = e => { S.loopIdx = e.target.checked ? (S.active >= 0 ? S.active : null) : null; updateLoopHint(); };
  $('#loopHint').onclick = () => { $('#optLoop').checked = false; S.loopIdx = null; updateLoopHint(); };
  $('#playerWrap').addEventListener('dblclick', e => {
    if (e.target === v && S.settings.overlay) { e.preventDefault(); document.fullscreenElement ? document.exitFullscreen() : $('#playerWrap').requestFullscreen(); }
  });

  // 字幕列表
  const box = $('#cues');
  box.addEventListener('input', onCueInput);
  box.addEventListener('wheel', () => S.userScrollAt = Date.now(), { passive: true });
  box.addEventListener('mousedown', () => S.userScrollAt = Date.now());
  box.addEventListener('click', e => {
    const ss = e.target.closest('.sub-shot');  // 字幕里插的截图：上移 / 下移 / 删除、点图看大图
    if (ss) {
      const b = e.target.closest('[data-ss]');
      if (b && b.dataset.ss === 'size') {  // 不能冒泡到 document：那里的点击会把刚打开的弹出框关掉
        e.stopPropagation();
        const k = +ss.dataset.sk, x = S.doc.sub_shots[k];
        openSizeMenu(b, { w: x.w, cur: ss.querySelector('img').clientWidth, all: sizeAllShots, set: w => {
          setShotProp(x, 'w', w);
          ss.classList.toggle('sized', !!w); ss.style.maxWidth = w ? `${w + 2}px` : '';
        } });
      } else if (b) moveSubShot(+ss.dataset.sk, b.dataset.ss);
      else if (e.target.matches('img') && e.target.src) showLightbox(e.target.src);
      return;
    }
    const tool = e.target.closest('.cue-tools button');
    const cue = e.target.closest('.cue'); if (!cue) return;
    const i = +cue.dataset.i;
    const db = e.target.closest('[data-doubt]');
    if (db) { if (db.dataset.doubt === 'play') playLoop(i); else resolveDoubt(i); return; }
    if (tool) { cueAction(tool.dataset.act, i); return; }
    if (e.target.closest('.ts')) { seek(S.doc.segments[i].start, true); S.loopIdx = i; return; }
    if (e.target.closest('.src, .tr')) {
      const seg = S.doc.segments[i];
      const t = v.currentTime;
      if (t < seg.start - 0.2 || t > seg.end + 0.2) seek(seg.start, false);
      S.loopIdx = i;
    }
  });
  box.addEventListener('focusin', e => { if (e.target.matches('.src, .tr')) { S.editing = e.target; e.target._orig = e.target.textContent; } });
  box.addEventListener('focusout', e => {
    if (e.target.matches('.src, .tr')) {
      const cue = e.target.closest('.cue');
      if (e.target.matches('.src') && S.doc) recordFix(S.doc, S.doc.segments[+cue.dataset.i], e.target._orig, e.target.textContent, 'manual');
      lastCaret = { i: +cue.dataset.i, field: e.target.classList.contains('src') ? 'src' : 'tr', pos: e.target._caret ?? null };
      S.editing = null;
    }
  });
  document.addEventListener('selectionchange', () => { const el = S.editing; if (el) el._caret = caretOffset(el); });
  box.addEventListener('keydown', e => {
    if (!e.target.matches('.src, .tr')) return;
    if (e.key === 'Enter' || e.key === 'Escape') { e.preventDefault(); e.target.blur(); }
  });

  // 搜索
  $('#search').addEventListener('input', () => { S.hitPos = -1; runSearch(false); });
  $('#search').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); runSearch(true); } if (e.key === 'Escape') { e.target.value = ''; runSearch(false); e.target.blur(); } });

  // 分隔条
  dragger($('#splitter'), 'x', ev => {
    const L = $('#stageMain').getBoundingClientRect();
    const sideW = S.settings.ai_dock === 'side' && !S.settings.ai_hidden ? $('#aiDockSide').offsetWidth : 0;
    S.settings.split = clamp((ev.clientX - L.left) / (L.width - sideW - 20), 0.2, 0.8);
    applyLayout();
  }, () => savePrefs({ split: +S.settings.split.toFixed(3) }));
  dragger($('#libSplit'), 'x', ev => {
    const w = clamp(ev.clientX - $('#library').getBoundingClientRect().left, 180, 520);
    $('#library').style.width = w + 'px'; S.settings.lib_w = w;
  }, () => savePrefs({ lib_w: Math.round(S.settings.lib_w) }));
  dragger($('#vSplit'), 'y', ev => {
    const L = $('#left').getBoundingClientRect();
    S.settings.video_h = clamp((ev.clientY - L.top - 12) / (L.height - 24), 0.12, 0.88); applyLayout();
  }, () => savePrefs({ video_h: +S.settings.video_h.toFixed(3) }));
  dragger($('#aiSplit'), 'x', ev => {
    const M = $('#stageMain').getBoundingClientRect();
    S.settings.ai_w = clamp(M.right - ev.clientX - 6, 280, M.width * 0.65);
    applyLayout();
  }, () => savePrefs({ ai_w: Math.round(S.settings.ai_w) }));

  // AI 区
  $$('.ai-tabs .tab').forEach(t => t.onclick = () => {
    $$('.ai-tabs .tab').forEach(x => x.classList.toggle('active', x === t));
    $$('.ai-body').forEach(p => p.hidden = p.dataset.pane !== t.dataset.tab);
    $('#btnPrint').hidden = t.dataset.tab !== 'summary';  // 只打印课程重点
  });
  $('#btnAiDock').onclick = () => savePrefs({ ai_dock: S.settings.ai_dock === 'side' ? 'bottom' : 'side' });
  $('#btnAiHide').onclick = () => savePrefs({ ai_hidden: true });
  $('#btnShowAi').onclick = () => savePrefs({ ai_hidden: false });
  $('#btnAiMax').onclick = () => {
    const s = S.settings;
    if (s.ai_dock === 'side') {
      const W = $('#stageMain').getBoundingClientRect().width;
      savePrefs({ ai_w: s.ai_w > W * 0.5 ? 440 : Math.round(W * 0.6) });
    } else savePrefs({ video_h: s.video_h > 0.2 ? 0.14 : 0.55 });
  };
  $('#btnSummary').onclick = doSummary;
  $('#btnSummaryStale').onclick = () => { if (!S.job) doSummary(); };
  $('#btnEditSummary').onclick = () => toggleSummaryEdit();
  $('#summaryEdit').addEventListener('input', e => { if (S.doc) { S.doc.summary = e.target.value; markDirty(); } });
  $('#btnCopySummary').onclick = () => navigator.clipboard.writeText(S.doc.summary || '').then(() => toast('已复制'));
  $('#btnSend').onclick = () => { if (S.chatAbort) S.chatAbort.abort(); else sendChat($('#chatInput').value); };
  $('#chatInput').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendChat(e.target.value); } });
  $('#btnClearChat').onclick = () => { if (S.doc && S.doc.chat && S.doc.chat.length && confirm('清空这节课的 AI 对话记录？')) { S.doc.chat = []; renderChat(); markDirty(); } };
  $$('#chips button').forEach(b => b.onclick = () => { $$('.ai-tabs .tab')[1].click(); sendChat(b.dataset.q); });

  // 设置
  $('#settingsNav').onclick = e => { const b = e.target.closest('button'); if (b) showSection(b.dataset.sec); };
  $('#themePick').onclick = e => { const b = e.target.closest('button'); if (!b) return; $('#dlgSettings')._draft.theme = b.dataset.theme; $$('#themePick button').forEach(x => x.classList.toggle('active', x === b)); document.documentElement.dataset.theme = b.dataset.theme; };
  $('#accentPick').onclick = e => { const b = e.target.closest('button'); if (!b) return; $('#dlgSettings')._draft.accent = b.dataset.accent; $$('#accentPick button').forEach(x => x.classList.toggle('active', x === b)); document.documentElement.style.setProperty('--accent', b.dataset.accent); };
  $('#dbAdd').onclick = () => {
    $('#dbMore').insertAdjacentHTML('beforeend', dbRowHTML({}, $$('#dbMore .db-acct').length + 2));
    $('#dbMore .db-acct:last-child .db-name').focus();
  };
  $('#dbMore').addEventListener('click', e => { const b = e.target.closest('[data-db-del]'); if (b) b.closest('.db-acct').remove(); });
  $('#dbSpent').addEventListener('click', e => { const b = e.target.closest('[data-db-revive]'); if (b) reviveDoubao(b.dataset.dbRevive); });
  $$('input[name=dbAuth]').forEach(r => r.onchange = refreshDoubaoFields);
  $$('[data-clear]').forEach(b => b.onclick = () => { const el = $(`[data-k="${b.dataset.clear}"]`); el.value = ''; el.dataset.cleared = '1'; el.placeholder = '保存后清除'; });
  $('#setCancel').onclick = () => $('#dlgSettings').close();
  $('#dlgSettings').addEventListener('close', () => { clearInterval(codexPoll); applyLook(); });
  $('#setSave').onclick = () => saveSettings(true);
  $('#resetTemplate').onclick = () => { $('#summaryTemplate').value = S.defaultTemplate; };
  $$('[data-dir]').forEach(b => b.onclick = () => api('open_dir', { which: b.dataset.dir }).catch(fail));
  $('#aboutHome').onclick = e => { e.preventDefault(); api('reveal', { target: 'https://github.com/longpeng1413/mengren-learning' }).catch(fail); };
  $$('[data-test]').forEach(b => b.onclick = async () => {
    const out = b.parentElement.querySelector('.test-result');
    out.className = 'test-result'; out.textContent = '先保存设置，再测试…';
    if (!(await saveSettings(false))) return;
    out.textContent = '测试中…';
    try {
      const r = await api('test', { provider: b.dataset.test, kind: b.dataset.kind }); out.textContent = '✓ ' + r.message; out.classList.add('ok');
      if (b.dataset.test === 'doubao') { await refreshSettingsOnly(); renderDoubaoAccounts(); }
    }
    catch (e) { out.textContent = '✗ ' + e.message; out.classList.add('bad'); }
  });
  $$('[data-models]').forEach(b => b.onclick = async () => {
    const name = b.dataset.models;
    if (!(await saveSettings(false))) return;
    b.disabled = true; const label = b.textContent; b.textContent = '获取中…';
    try {
      const { models } = await api('models', { provider: name });
      fillDatalist(name, models);
      const input = $(`[data-k="providers.${name}.chat_model"]`);
      toast(`找到 ${models.length} 个模型，点输入框可以选择`);
      if (!input.value && models[0]) input.value = models[0].id;
      input.focus();
    } catch (e) { fail(e); }
    b.disabled = false; b.textContent = label;
  });
  $('#codexDone').onclick = async () => {
    try { const r = await api('codex_finish', { url: $('#codexUrl').value }); if (r.status === 'done') codexDone(r); else toast('还没有拿到登录结果'); }
    catch (e) { fail(e); }
  };
  $('#teacherList').onclick = async e => {
    const b = e.target.closest('button'); if (!b) return;
    if (teacherFormDirty() && !(await saveTeacher())) return;
    if (b.dataset.tid !== '__new' && !teacherById(b.dataset.tid)) return;
    editTeacher(b.dataset.tid === '__new' ? null : teacherById(b.dataset.tid)); if (b.dataset.tid === '__new') $('#tName').focus(); };
  $('#tSave').onclick = saveTeacher;
  $('#tFilePick').onclick = pickTermsFile;
  $('#tFileClear').onclick = () => setTermsFile('');
  $('#tFileView').onclick = () => { const p = $('#tFile').dataset.path; if (p) viewTermsFile(p); };
  $('#tFileOpen').onclick = async () => {
    const p = $('#tFile').dataset.path;
    if (!p) return;
    try { openTermsFile(p, (await api('terms_view', { path: p })).uri); } catch (e) { fail(e); }
  };
  $('#btnDoubts').onclick = nextDoubt;
  $('#btnFrames').onclick = () => { if (!S.job) findFrames(false); };
  $('#btnCapture').onclick = () => { if (!S.job) captureFrame(); };
  $('#btnSubShot').onclick = () => { if (!S.job) insertSubShot(); };
  $('#btnDescribe').onclick = () => { if (!S.job) describeFrames((S.doc.frames || []).filter(f => !f.note)); };
  $('#btnRecheck').onclick = () => { if (!S.job) recheckDoubts(false); };
  const fb = $('#framesBox');
  fb.addEventListener('input', e => { const f = e.target.matches('[data-note]') && shotOf(e.target); if (f) { f.note = e.target.value; markDirty(); } });
  fb.addEventListener('change', e => { const f = e.target.matches('[data-fig]') && shotOf(e.target); if (f) { f.figure = e.target.checked; markDirty(); } });
  fb.addEventListener('click', async e => {
    const f = shotOf(e.target); if (!f) return;
    if (e.target.matches('.redraw-svg')) { showLightbox(e.target.src); return; }
    if (e.target.matches('img')) { showLightbox(await frameUrl(f.t, false)); return; }
    if (e.target.matches('[data-del]')) { S.doc.frames = S.doc.frames.filter(x => x !== f); renderFrames(); markDirty(); return; }
    if (e.target.matches('[data-redo]') && !S.job) describeFrames([f]);
    if (e.target.matches('[data-redraw]') && !S.job) redrawFrames([f]);
    if (e.target.matches('[data-unredraw]')) { delete f.redraw; delete f.unclear; renderFrames(); renderSummary(); markDirty(); }
  });
  $('#summaryBox').addEventListener('click', async e => {
    const b = e.target.closest('.fig-tools [data-sf]');
    if (b && b.dataset.sf === 'size') {  // 不能冒泡到 document：那里的点击会把刚打开的弹出框关掉
      e.stopPropagation();
      const fig = b.closest('.shot-fig'), t = +fig.dataset.ft;
      openSizeMenu(b, { w: ((S.doc.fig_edits || {})[String(t)] || {}).w, cur: (fig.querySelector('img') || fig).clientWidth, all: sizeAllShots,
        set: w => { setFigEdit(t, 'w', w); $$(`#summaryBox .shot-fig[data-ft="${t}"]`).forEach(f => f.style.maxWidth = w ? w + 'px' : ''); } });
      return;
    }
    if (b) { if (!S.chatAbort) moveSumShot(+b.parentElement.dataset.n, b.dataset.sf); return; }
    const img = e.target.closest('.shot-fig img'); if (img && img.src) showLightbox(img.src);
  });
  $('#btnSumShot').onclick = () => { if (!S.chatAbort) insertSumShot(); };
  $('#btnPrint').onclick = printSummary;
  bindShotEdit();
  $('#btnReview').onclick = () => { if (!S.job) runReview(); };
  $('#btnTerms').onclick = () => { if (S.job) return; if (S.doc.terms_pending) openTerms(); else findTerms(false); };
  $('#tDelete').onclick = deleteTeacher;
  $('#tTerms').oninput = countTerms;
  $('#tNotes').oninput = countTerms;
  $('#btnExportSettings').onclick = async () => {
    try {
      if ($('#dlgSettings').open) await saveSettings(false);
      const r = await api('export_settings', { keys: $('#exKeys').checked, library: $('#exLibrary').checked });
      if (r.path) { $('#ioMsg').textContent = '✓ 已导出：' + r.path; $('#ioMsg').className = 'test-result ok'; }
    } catch (e) { fail(e); }
  };
  $('#btnImportSettings').onclick = async () => {
    try {
      const r = await api('import_settings');
      if (!r.imported) return;
      await loadSettings(); loadLibrary(); $('#dlgSettings').close(); openSettings('about');
      toast(`已导入设置${r.teachers ? `、${r.teachers} 位老师` : ''}${r.folders ? `、${r.folders} 个媒体库文件夹` : ''}${r.with_keys ? '' : '。这个文件里没有密钥，请重新填写'}`, { ms: 7000 });
    } catch (e) { fail(e); }
  };
  const setStorage = async body => {
    try {
      const r = await api('set_storage', body);
      if (r.changed) { $('#aboutRoot').textContent = r.root + '（重启软件后生效）'; toast('已更改，重启软件后缓存和日志会放到新位置。旧位置的文件夹可以手动删除。', { ms: 7000 }); }
    } catch (e) { fail(e); }
  };
  $('#btnStorage').onclick = () => setStorage({ pick: true });
  $('#btnStorageReset').onclick = () => setStorage({ path: '' });
  $('#tImport').onclick = async () => {
    try {
      const r = await api('pick_text');
      if (r.text == null) return;
      if ($('#tNotes').value.trim() && !confirm('用这个文件替换现在的术语说明？')) return;
      $('#tNotes').value = r.text.trim(); countTerms();
      toast(`已导入「${r.name}」，记得点「保存这位老师」`);
    } catch (e) { fail(e); }
  };
  $('#clearCache').onclick = async () => {
    if (!confirm('清空缓存？\n会删除转换后的视频和抽出的音频，下次打开或识别时会重新生成。字幕、设置和你的视频都不受影响。')) return;
    try { const r = await api('cache_clear'); $('#aboutCacheSize').textContent = mb(r.cache); toast(`已清空，释放 ${mb(r.freed)}`); } catch (e) { fail(e); }
  };
  $('#tOwnTemplate').onchange = e => { $('#tTemplateField').hidden = !e.target.checked; };
  $('#pickBookFolder').onclick = async () => {
    const vault = $('[data-k="obsidian.vault"]').value.replace(/[\\/]+$/, '');
    const { path: p } = await api('pick_folder', { start: vault });
    if (!p) return;
    if (!p.toLowerCase().startsWith(vault.toLowerCase())) return toast('请选择库里面的文件夹', { error: true });
    $('[data-k="obsidian.book_folder"]').value = p.slice(vault.length).replace(/^[\\/]+/, '').replace(/\\/g, '/');
  };
  $('#pickBookRules').onclick = async () => { const { path: p } = await api('pick_file', { kind: 'text' }); if (p) $('[data-k="book_rules_file"]').value = p; };
  $('#pickVault').onclick = async () => { const { path: p } = await api('pick_folder', { start: $('[data-k="obsidian.vault"]').value }); if (p) $('[data-k="obsidian.vault"]').value = p; };
  $('#pickFolder').onclick = async () => {
    const vault = $('[data-k="obsidian.vault"]').value.replace(/[\\/]+$/, '');
    const { path: p } = await api('pick_folder', { start: vault });
    if (!p) return;
    if (!p.toLowerCase().startsWith(vault.toLowerCase())) return toast('请选择库里面的文件夹', { error: true });
    $('[data-k="obsidian.folder"]').value = p.slice(vault.length).replace(/^[\\/]+/, '').replace(/\\/g, '/');
  };

  // 拖放
  const dz = $('#dropZone');
  const hasFiles = e => !!e.dataTransfer && [...(e.dataTransfer.types || [])].includes('Files');  // 从资源管理器拖进来的（媒体库里拖动排序不是）
  document.addEventListener('dragover', e => { e.preventDefault(); if (hasFiles(e)) dz.classList.add('drag'); });
  document.addEventListener('dragleave', e => { if (!e.relatedTarget) dz.classList.remove('drag'); });
  document.addEventListener('drop', e => {
    e.preventDefault(); dz.classList.remove('drag');
    const files = e.dataTransfer && e.dataTransfer.files;
    if (!files || !files.length || !(window.chrome && chrome.webview && chrome.webview.postMessageWithAdditionalObjects)) return;
    // 完整路径只有浏览器内核知道：先把文件交给它（后台记下路径），再按文件名取回来打开（见 main.py on_loaded）
    chrome.webview.postMessageWithAdditionalObjects('FilesDropped', files);
    api('dropped', { names: [...files].map(f => f.name) }).then(r => { if (r.path) openPath(r.path); }).catch(fail);
  });

  // 键盘
  document.addEventListener('keydown', e => {
    const inField = e.target.closest('input, textarea, select, [contenteditable]');
    const ctrl = e.ctrlKey || e.metaKey;
    if (ctrl && e.key.toLowerCase() === 's') { e.preventDefault(); if (S.doc) { S.dirty = true; save(false); } return; }
    if (ctrl && e.key.toLowerCase() === 'o') { e.preventDefault(); pickAndOpen(); return; }
    if (!S.doc) return;
    if (ctrl && e.key.toLowerCase() === 'k') { e.preventDefault(); v.paused ? v.play() : v.pause(); return; }
    if (ctrl && e.key === 'ArrowLeft') { e.preventDefault(); v.currentTime = Math.max(0, v.currentTime - 3); return; }
    if (ctrl && e.key === 'ArrowRight') { e.preventDefault(); v.currentTime += 3; return; }
    if (ctrl && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
      e.preventDefault();
      const i = clamp(S.active + (e.key === 'ArrowUp' ? -1 : 1), 0, S.doc.segments.length - 1);
      if (S.doc.segments[i]) { seek(S.doc.segments[i].start, !v.paused); S.loopIdx = i; const el = cueEl(i); if (el) el.scrollIntoView({ block: 'center' }); }
      return;
    }
    if (ctrl && e.key.toLowerCase() === 'z' && !inField) { e.preventDefault(); undo(); return; }
    if (ctrl && e.key.toLowerCase() === 'f') { e.preventDefault(); $('#search').focus(); return; }
    if (e.key === ' ' && !inField && !e.target.closest('button')) { e.preventDefault(); v.paused ? v.play() : v.pause(); }
  });
  window.addEventListener('resize', () => applyLayout());
}

/* ---------------------------------------------------------------- 启动 ---- */
(async function init() {
  bind();
  await bridgeReady();
  try { await loadSettings(); } catch (e) { fail(e); }
  showRecent($('#recentList'));
  loadLibrary();
  api('about').then(a => {
    if (a.bundled) toast('已自动导入软件文件夹里附带的设置（AI 密钥、ChatGPT 登录、老师和术语表等）。那个设置文件已改名为「…（已导入，可删除）」，建议删掉它。', { ms: 12000 });
  }).catch(() => {});
  $('#stageMain').hidden = true; $('#empty').hidden = false;
  updateButtons();
})();
