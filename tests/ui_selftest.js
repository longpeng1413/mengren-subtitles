// 在真实窗口里跑的界面自测（由 main.py --selftest= 执行）
Promise.race([new Promise(r => setTimeout(() => r((window.__R || []).concat(['TIMEOUT'])), 240000)), (async () => {
  const R = window.__R = [];
  const ok = (name, cond, info) => {
    const line = (cond ? 'PASS ' : 'FAIL ') + name + (cond || info === undefined ? '' : ' :: ' + JSON.stringify(info));
    R.push(line);
    api('log', { level: 'info', message: '自测 ' + line }).catch(() => {});  // 程序中途退出时，日志里能看到测到哪一步
  };
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const until = async (fn, ms = 8000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { try { if (await fn()) return true; } catch (e) {} await wait(100); } return false; };
  const v = document.querySelector('#video');
  const toasts = () => [...document.querySelectorAll('.toast')].map(t => t.innerText);
  const base = window.__TEST_BASE;  // 项目里的 test-media\ui\（由 main.py 传进来）
  try {
    await until(() => S.settings);
    ok('后台已连接', !!(window.pywebview && window.pywebview.api));
    ok('空状态页', !document.querySelector('#empty').hidden);
    ok('媒体库默认显示', !document.querySelector('#library').hidden);

    // 媒体库
    const lib = await api('library_add_folder', { path: base });
    await loadLibrary(lib.groups);
    // 左上角「书籍 / 课程 / 老师」（2026-10-29）：书和音视频分开列
    ok('左上角有「书籍」「课程」「老师」三个按钮', [...document.querySelectorAll('#libTabs .lib-tab')].map(b => b.textContent).join('|') === '书籍|课程|老师');
    ok('添加文件夹到媒体库：「课程」里只列视频、音频', document.querySelectorAll('.lib-item').length === 2
      && ![...document.querySelectorAll('.lib-item')].some(el => el.textContent.includes('测试书籍')), document.querySelectorAll('.lib-item').length);
    document.querySelector('#libTabs [data-view="book"]').click();
    ok('「书籍」里只列书和文档，文件夹后面的数字只数书', document.querySelectorAll('.lib-item').length === 1 && document.querySelector('.lib-item').textContent.includes('测试书籍')
      && document.querySelector('#libBody .lib-gcount').textContent === '1' && document.querySelector('#libSearch').placeholder === '筛选书名');
    document.querySelector('#libTabs [data-view="course"]').click();
    const item = [...document.querySelectorAll('.lib-item')].find(el => el.textContent.includes('演示视频'));
    ok('媒体库显示字幕数', item && item.textContent.includes('字幕 8'), item && item.textContent);
    item.click();
    ok('点媒体库里的视频就打开', await until(() => S.doc && S.doc.title === '演示视频' && document.querySelectorAll('.cue').length === 8));
    ok('当前视频在列表里高亮', await until(() => document.querySelector('.lib-item.active')));
    ok('视频可以加载', await until(() => v.readyState >= 1 && v.duration > 39), { rs: v.readyState, d: v.duration });
    // 字幕里手动插截图（2026-10-30）：插在正在播放的这一句后面，可以上移 / 下移 / 删除
    const ssSegs = S.doc.segments, ssK = 2;
    v.currentTime = (ssSegs[ssK].start + ssSegs[ssK].end) / 2; await wait(300);
    ok('字幕栏有「插入截图」按钮（视频才有）', !document.querySelector('#btnSubShot').hidden);
    document.querySelector('#btnSubShot').click();
    const ssAfter = () => { const el = document.querySelector('#cues .sub-shot'); const prev = el && el.previousElementSibling; return prev && prev.classList.contains('cue') ? +prev.dataset.i : -1; };
    ok('插入截图：插在正在播放的这一句后面，图片加载出来', (S.doc.sub_shots || []).length === 1 && ssAfter() === ssK
      && await until(() => (document.querySelector('#cues .sub-shot img') || {}).src), [S.doc.sub_shots, ssAfter()]);
    document.querySelector('#cues .sub-shot [data-ss="down"]').click();
    ok('截图下移：挪到下一句后面', ssAfter() === ssK + 1 && S.doc.sub_shots[0].at === ssSegs[ssK + 1].start, ssAfter());
    document.querySelector('#cues .sub-shot [data-ss="up"]').click(); document.querySelector('#cues .sub-shot [data-ss="up"]').click();
    ok('截图上移两次：回到上上一句后面', ssAfter() === ssK - 1, ssAfter());
    renderCues();
    ok('字幕重画后截图还在原来的位置', ssAfter() === ssK - 1);
    // 编辑截图：裁剪、调整大小（2026-11-02）。seDrag 在编辑框的图上按比例位置拖动
    window.seDrag = (x0, y0, x1, y1, el) => {
      const r = $('#seImg').getBoundingClientRect();
      const ev = (type, x, y, target) => (target || $('#seStage')).dispatchEvent(new PointerEvent(type, { bubbles: true, pointerId: 1, clientX: r.left + r.width * x, clientY: r.top + r.height * y }));
      ev('pointerdown', x0, y0, el); ev('pointermove', x1, y1); ev('pointerup', x1, y1);
    };
    const near = (a, b) => a && b && a.every((x, i) => Math.abs(x - b[i]) < 0.02);
    document.querySelector('#cues .sub-shot [data-ss="crop"]').click();
    ok('字幕截图点「裁剪」打开裁剪截图，显示整张截图', await until(() => $('#dlgShotEdit').open && $('#seImg').naturalWidth > 0));
    seDrag(0.2, 0.2, 0.8, 0.7, $('#seBox'));
    ok('一开始框是整张图：拖动画出新框', near(SE.crop, [0.2, 0.2, 0.8, 0.7]), SE.crop);
    seDrag(0.8, 0.7, 0.7, 0.6, $('#seBox [data-h="se"]'));
    ok('拖右下角缩小框', near(SE.crop, [0.2, 0.2, 0.7, 0.6]), SE.crop);
    seDrag(0.4, 0.4, 0.3, 0.45, $('#seBox'));
    ok('拖框里面移动框', near(SE.crop, [0.1, 0.25, 0.6, 0.65]), SE.crop);
    $('#seOk').click(); await until(() => !$('#dlgShotEdit').open); await wait(50);
    const subX = S.doc.sub_shots[0], subEl = () => document.querySelector('#cues .sub-shot');
    ok('确定后记下裁剪范围，字幕栏显示裁剪后的图', near(subX.crop, [0.1, 0.25, 0.6, 0.65]) && !subX.w
      && await until(() => /_c[0-9a-f]{8}\.jpg/.test((subEl().querySelector('img') || {}).src || '')), [subX, (subEl().querySelector('img') || {}).src]);
    // 「大小」是单独的按钮（2026-11-02 用户：裁剪和调整大小是两个功能）：拖滑块马上变，不用点确定
    subEl().querySelector('[data-ss="size"]').click();
    const ctxM = $('#ctxMenu');
    ok('字幕截图点「大小」弹出滑块（没被马上关掉）', !ctxM.hidden && ctxM.querySelector('input[type=range]'));
    const rg = ctxM.querySelector('input[type=range]'); rg.value = 400; rg.dispatchEvent(new Event('input'));
    ok('拖滑块：图马上变成 400 像素宽', subX.w === 400 && subEl().style.maxWidth === '402px' && subEl().classList.contains('sized')
      && ctxM.querySelector('[data-a=lab]').textContent === '400 像素宽', subX);
    ctxM.querySelector('[data-p="240"]').click();
    ok('点「小」', subX.w === 240 && subEl().style.maxWidth === '242px');
    ctxM.querySelector('[data-p="0"]').click();
    ok('点「自动」：恢复原来的大小', !subX.w && !subEl().style.maxWidth && !subEl().classList.contains('sized'), subX);
    closeMenus();
    document.querySelector('#cues .sub-shot [data-ss="crop"]').click();
    await until(() => $('#dlgShotEdit').open);
    ok('再打开裁剪：框是上次的', near(SE.crop, subX.crop));
    $('#seFull').click(); $('#seOk').click(); await until(() => !$('#dlgShotEdit').open); await wait(50);
    ok('点「整张」：恢复成整张截图', !subX.crop, subX);
    const keepConfirm = window.confirm; window.confirm = () => true;
    document.querySelector('#cues .sub-shot [data-ss="del"]').click();
    window.confirm = keepConfirm;
    ok('删除截图', !(S.doc.sub_shots || []).length && !document.querySelector('#cues .sub-shot'));

    // 老师与术语表
    const tr = await api('teacher_save', { teacher: { name: '测试老师', terms: '底层逻辑\n风级 => 峰级', lang: 'en' } });
    S.teachers = tr.teachers; renderTeacherSelect();
    ok('顶栏可以选老师', [...document.querySelectorAll('#teacherSelect option')].some(o => o.textContent === '测试老师'));
    const sel = document.querySelector('#teacherSelect');
    sel.value = tr.teacher.id; sel.dispatchEvent(new Event('change'));
    ok('选了老师写进字幕文件', S.doc.teacher === tr.teacher.id);

    // 点字幕跳转、编辑和自动保存
    const cue3 = document.querySelectorAll('.cue')[3];
    cue3.querySelector('.src').dispatchEvent(new MouseEvent('click', { bubbles: true }));
    ok('点字幕跳到对应时间', await until(() => Math.abs(v.currentTime - S.doc.segments[3].start) < 0.3), v.currentTime);
    const src0 = document.querySelectorAll('.cue')[0].querySelector('.src');
    src0.focus(); document.getSelection().selectAllChildren(src0);
    document.execCommand('insertText', false, '改过的第一句');
    src0.blur();
    ok('自动保存', await until(() => !S.dirty && !S.saving, 5000));
    ok('手动改字幕记下改动（用来积累术语表）', (S.doc.fixes || []).some(f => f.after === '改过的第一句' && f.by === 'manual' && f.before && f.before !== f.after), S.doc.fixes);
    const reread = await api('open', { path: base + '演示视频.mp4' });
    ok('修改和老师都保存到磁盘', reread.doc.segments[0].text === '改过的第一句' && reread.doc.teacher === tr.teacher.id);
    ok('媒体库里的状态跟着更新', await until(() => [...document.querySelectorAll('.lib-item.active .tag')].length >= 1));

    // 后台的 Agent 自动整理改过这节课：界面保存时不直接覆盖，先问
    ok('打开时记下字幕文件的版本号', S.rev > 0 && S.rev === reread.doc.rev, [S.rev, reread.doc.rev]);
    const origConfirm = window.confirm;
    const bg1 = (await api('open', { path: S.path })).doc;
    bg1.summary = '后台整理的课程重点'; await api('save', { path: S.path, doc: bg1 });  // 模拟 Agent 写入（不带版本号）
    window.confirm = () => true;  // 选「重新打开，看自动整理的结果」
    S.doc.segments[2].text = '界面里改的第三句'; markDirty(); await save();
    ok('后台改过：选「重新打开」就看到后台的结果，不覆盖', await until(() => S.doc.summary === '后台整理的课程重点' && !S.dirty) && S.doc.segments[2].text !== '界面里改的第三句', S.doc.summary);
    const bg2 = (await api('open', { path: S.path })).doc;
    bg2.summary = '后台又改了一次'; await api('save', { path: S.path, doc: bg2 });
    window.confirm = () => false;  // 选「保留我的版本」
    S.doc.segments[2].text = '界面里改的第三句'; markDirty(); await save();
    const kept = (await api('open', { path: S.path })).doc;
    ok('后台改过：选「保留我的」就用界面的版本覆盖', kept.segments[2].text === '界面里改的第三句' && kept.summary === '后台整理的课程重点' && !S.dirty, kept.summary);
    window.confirm = origConfirm;
    S.doc.summary = ''; markDirty(); await save();

    // 工具按钮：正在编辑的一行，下排的「合并」按钮超出这一行时，也要点得到它自己（不能被下一句盖住）
    const cue1 = document.querySelectorAll('.cue')[1];
    cue1.scrollIntoView({ block: 'center' });
    cue1.querySelector('.src').focus();
    const mb = cue1.querySelector('[data-act="merge"]').getBoundingClientRect();
    const hitEl = document.elementFromPoint(mb.left + mb.width / 2, mb.top + mb.height / 2);
    const mouseOnCues = !!document.querySelector('.cues-inner:hover');  // 真实鼠标正好停在字幕上时，悬停状态会干扰这项检查
    if (mouseOnCues) console.log('鼠标停在字幕上，跳过「合并」按钮的点击位置检查');
    ok('工具按钮排成两列，「合并」点得到、属于这一句', mouseOnCues || (mb.width > 0 && hitEl && hitEl.closest('[data-act="merge"]') && hitEl.closest('.cue') === cue1),
       [mb.top, mb.height, cue1.getBoundingClientRect().bottom, hitEl && hitEl.outerHTML.slice(0, 80)]);
    cue1.querySelector('.src').blur();

    // 拆分、合并、撤销
    const n0 = S.doc.segments.length;
    cueAction('split', 1); cueAction('merge', 1); cueAction('del', 7);
    ok('拆分合并删除', S.doc.segments.length === n0 - 1);
    undo(); undo(); undo();
    ok('撤销回到原样', S.doc.segments.length === n0);

    // 文字设置（Aa）
    document.querySelector('#btnText').click();
    ok('文字设置面板打开', document.querySelector('#menuText').classList.contains('open'));
    // 「－」「＋」一样大、标签不竖排（「新术语」对话框的 .tm-row 样式以前会套到这里）
    const txRow = document.querySelector('#menuText .tm-row');
    const [txMinus, txPlus] = txRow.querySelectorAll('button');
    const txLabel = txRow.querySelector('span').getBoundingClientRect();
    ok('文字设置：左右按钮一样宽、标签一行', Math.abs(txMinus.getBoundingClientRect().width - txPlus.getBoundingClientRect().width) < 1 && txLabel.height < 24,
       [txMinus.getBoundingClientRect().width, txPlus.getBoundingClientRect().width, txLabel.height]);
    // 中文课开着「译文」、这句没有译文：不留空行（以前每句下面多一行高）
    const trShown = S.settings.show_tr;
    savePrefs({ show_tr: true });
    const emptyTr = [...document.querySelectorAll('.cue .tr')].find(x => !x.textContent && x.dataset.ph === '');
    ok('中文课没有译文的句子不留空行', S.doc.lang === 'en' || !emptyTr || emptyTr.getBoundingClientRect().height === 0);
    savePrefs({ show_tr: trShown });
    const cs = () => getComputedStyle(document.documentElement);
    const fs0 = S.settings.font_size, lh0 = S.settings.line_height;
    document.querySelector('[data-tx="font_size:1"]').click();
    document.querySelector('[data-tx="line_height:0.1"]').click();
    document.querySelector('[data-tx="cue_gap:2"]').click();
    document.querySelector('[data-tx="cue_width:-60"]').click();
    const f = document.querySelector('#txFont'); f.value = 'kai'; f.dispatchEvent(new Event('change'));
    ok('字号', cs().getPropertyValue('--cue-size').trim() === (fs0 + 1) + 'px');
    ok('行距', Math.abs(parseFloat(cs().getPropertyValue('--cue-line')) - (lh0 + 0.1)) < 0.01);
    ok('句间距', cs().getPropertyValue('--cue-gap').trim() === '11px');
    ok('字幕栏宽度', S.settings.cue_width > 0 && document.querySelector('.cues-inner').getBoundingClientRect().width <= S.settings.cue_width + 1);
    ok('字体', getComputedStyle(document.querySelector('.src')).fontFamily.includes('KaiTi'));
    savePrefs({ font_size: fs0, line_height: lh0, cue_gap: 9, cue_width: 0, font_family: 'yahei' });
    closeMenus();

    // AI 区：上下拖动、放大、移到右侧、隐藏
    savePrefs({ ai_dock: 'bottom', ai_hidden: false, video_h: 0.3 });
    await wait(100);
    const ph = document.querySelector('#playerArea').getBoundingClientRect().height;
    const lh = document.querySelector('#left').getBoundingClientRect().height;
    ok('视频高度可以调', Math.abs(ph / lh - 0.3) < 0.06, { ph, lh });
    const aiH = document.querySelector('#aiPanel').getBoundingClientRect().height;
    ok('AI 区跟着变高', aiH > lh * 0.55, { aiH, lh });
    document.querySelector('#btnAiMax').click();
    ok('放大 AI 区', S.settings.video_h < 0.2);
    document.querySelector('#btnAiDock').click(); await wait(100);
    ok('AI 区移到右侧', document.querySelector('#aiPanel').parentElement.id === 'aiDockSide' && document.querySelector('#aiDockSide').getBoundingClientRect().width > 250);
    const sp = document.querySelector('#aiSplit');
    const stageRect = document.querySelector('#stageMain').getBoundingClientRect();
    ok('右侧 AI 区的拖动条可见', sp.getBoundingClientRect().width >= 10 && getComputedStyle(sp).display !== 'none');
    for (const target of [600, 380]) {
      sp.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerId: 1, clientX: stageRect.right - 450 }));
      sp.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, pointerId: 1, clientX: stageRect.right - target }));
      sp.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 1 }));
      await wait(50);
      const w = document.querySelector('#aiDockSide').getBoundingClientRect().width;
      ok(`拖动把右侧 AI 区调到约 ${target - 6} 像素`, Math.abs(w - (target - 6)) < 12, w);
    }
    const subW = document.querySelector('#right').getBoundingClientRect().width;
    ok('字幕栏没有被挤掉', subW >= 250, subW);
    document.querySelector('#btnAiHide').click(); await wait(50);
    ok('隐藏 AI 区', !document.querySelector('#aiPanel').classList.contains('docked') && !document.querySelector('#btnShowAi').hidden);
    document.querySelector('#btnShowAi').click();
    savePrefs({ ai_dock: 'bottom', video_h: 0.55 });
    ok('移回视频下方', document.querySelector('#aiPanel').parentElement.id === 'aiDockBottom');

    // 课程重点可以编辑
    document.querySelector('#btnEditSummary').click();
    const ta = document.querySelector('#summaryEdit');
    ta.value = '### 一句话主题\n测试重点 [00:10]'; ta.dispatchEvent(new Event('input'));
    document.querySelector('#btnEditSummary').click();
    ok('编辑课程重点', S.doc.summary.includes('测试重点') && document.querySelector('#summaryBox .tslink'));
    const tb = md('前文\n\n| 模型 | 比例 |\n|---|---|\n| 金字塔 | 40% [01:02] |\n\n后文');
    ok('Markdown 表格显示成表格', tb.includes('<table>') && tb.includes('<td>金字塔</td>') && tb.includes('tslink') && tb.includes('<p>后文</p>'), tb);

    // 字幕历史版本、清空、恢复
    window.confirm = () => true;
    const before = S.doc.segments.length;
    useVersion('clear');
    ok('清空字幕', S.doc.segments.length === 0 && document.querySelector('.cues-empty'));
    ok('清空前自动存了历史版本', S.doc.versions.length === 1 && S.doc.versions[0].count === before);
    document.querySelector('#btnVersions').click();
    ok('历史版本菜单', document.querySelectorAll('#menuVersions [data-ver]').length === 2, document.querySelector('#menuVersions').innerText);
    document.querySelector('#menuVersions [data-ver="0"]').click();
    ok('恢复历史版本', S.doc.segments.length === before && document.querySelectorAll('.cue').length === before);
    await until(() => !S.dirty && !S.saving, 5000);
    const v2 = await api('open', { path: base + '演示视频.mp4' });
    ok('历史版本存进字幕文件', v2.doc.segments.length === before);

    // 已校对
    document.querySelector('#optReviewed').click();
    ok('标记已校对', S.doc.reviewed === true);

    // 存疑句（AI 校对时标出、需要回听的句子）
    S.doc.doubts = [{ t: S.doc.segments[2].start, reason: '测试存疑原因' }];
    renderCues();
    ok('存疑句用橙色标出并写明原因', document.querySelectorAll('.cue.doubt').length === 1 && document.querySelector('.cue.doubt .doubt-tag').textContent.includes('测试存疑原因'));
    updateButtons();
    ok('有存疑句时可以用画面复核', !document.querySelector('#btnRecheck').disabled);
    const plOrder = [...document.querySelectorAll('#dlgPipeline input[type=checkbox]')].map(x => x.id);
    ok('一键整理：最后一步找新术语', plOrder[plOrder.length - 1] === 'plTerms', plOrder);
    ok('一键整理：先校对，再找关键帧、用画面复核存疑句', plOrder.indexOf('plCorrect') < plOrder.indexOf('plFrames') && plOrder.indexOf('plFrames') < plOrder.indexOf('plRecheck'), plOrder);
    ok('字幕栏上方显示存疑数量',!document.querySelector('#btnDoubts').hidden && document.querySelector('#btnDoubts').textContent === '存疑 1');
    document.querySelector('#btnDoubts').click();
    ok('点「存疑」跳到那一句并单句循环', S.loopIdx === 2 && document.querySelector('#optLoop').checked);
    const loopHint = document.querySelector('#loopHint');
    ok('单句循环时开关旁边写明在循环哪一句', await until(() => !loopHint.hidden && loopHint.textContent === `正在循环 ${fmt(S.doc.segments[2].start)} 这一句`), loopHint.textContent);
    v.pause();
    document.querySelector('.cue.doubt [data-doubt="ok"]').click();
    ok('「已确认」去掉存疑标记', !S.doc.doubts.length && !document.querySelector('.cue.doubt') && document.querySelector('#btnDoubts').hidden);
    loopHint.click();
    ok('点循环提示关掉单句循环', !document.querySelector('#optLoop').checked && S.loopIdx === null && loopHint.hidden);

    // 画面：关键帧和画面记录、课程重点里的插图
    S.doc.frames = [{ t: 5, note: '课件标题：交易心理学', caption: '欢迎来到', figure: true }, { t: 20, note: '' }];
    renderFrames();
    document.querySelectorAll('.ai-tabs .tab')[2].click();
    ok('画面页列出画面和记录', document.querySelectorAll('.shot').length === 2 && document.querySelector('.shot textarea').value.includes('交易心理学') && document.querySelector('#framesCount').textContent === '2');
    ok('画面缩略图能显示', await until(() => { const im = document.querySelector('.shot img'); return im && im.src.startsWith('file:') && im.complete && im.naturalWidth > 0; }, 15000));
    ok('有没写记录的画面时可以补写', !document.querySelector('#btnDescribe').disabled && document.querySelector('#framesHint').textContent.includes('1 张还没有记录'));
    const ta2 = document.querySelectorAll('.shot textarea')[1]; ta2.value = '我自己写的画面记录'; ta2.dispatchEvent(new Event('input', { bubbles: true }));
    ok('可以自己写画面记录', S.doc.frames[1].note === '我自己写的画面记录' && S.dirty);
    document.querySelectorAll('.shot [data-del]')[1].click();
    ok('删除画面', S.doc.frames.length === 1);
    v.currentTime = 12; await wait(300);
    await captureFrame();
    ok('截取当前画面（没设置画面识别 AI 时先留空）', S.doc.frames.length === 2 && S.doc.frames.some(f => f.manual && Math.abs(f.t - 12) < 0.5));
    S.doc.summary = '## 一、开头\n- 要点 [00:05]\n![[画面 00:05]]';
    renderSummary();
    ok('课程重点里的插图能显示', await until(() => { const im = document.querySelector('#summaryBox .shot-fig img'); return im && im.complete && im.naturalWidth > 0; }, 15000));
    document.querySelectorAll('.ai-tabs .tab')[0].click();
    // 课程重点里手动插截图、上移 / 下移 / 删除、打印（2026-11-02）
    const sumKeep = S.doc.summary;
    S.doc.summary = '## 一、开头\n\n- 要点甲 [00:03]\n- 要点乙 [00:20]\n\n## 二、后面\n\n- 要点丙 [00:30]';
    S.doc.sum_shots = []; renderSummary(); updateButtons();
    ok('课程重点有「插入截图」和「打印」按钮', !$('#btnSumShot').hidden && !$('#btnSumShot').disabled && !$('#btnPrint').hidden && !$('#btnPrint').disabled);
    v.currentTime = 25; await wait(300);
    $('#btnSumShot').click();
    ok('插入截图：插在出处时间不晚于现在、最接近的那一条后面，图片加载出来', S.doc.summary.includes('- 要点乙 [00:20]\n\n![[画面 00:25]]\n\n## 二、后面')
      && S.doc.sum_shots.includes(25) && await until(() => (document.querySelector('#summaryBox .shot-fig img') || {}).src), S.doc.summary);
    $('#summaryBox .fig-tools [data-sf="up"]').click();
    ok('截图上移：挪到上一条前面', S.doc.summary.includes('- 要点甲 [00:03]\n\n![[画面 00:25]]\n\n- 要点乙'), S.doc.summary);
    $('#summaryBox .fig-tools [data-sf="down"]').click(); $('#summaryBox .fig-tools [data-sf="down"]').click();
    ok('截图下移两次：越过小标题', S.doc.summary.includes('## 二、后面\n\n![[画面 00:25]]\n\n- 要点丙'), S.doc.summary);
    ok('图下面的按钮不朗读', !TTS.segments($('#summaryBox')).some(x => x.text.includes('上移')));
    $('#summaryBox .fig-tools [data-sf="crop"]').click();
    ok('课程重点的图也能「裁剪」', await until(() => $('#dlgShotEdit').open && $('#seImg').naturalWidth > 0));
    seDrag(0.1, 0.1, 0.5, 0.5, $('#seBox'));
    $('#seOk').click(); await until(() => !$('#dlgShotEdit').open); await wait(50);
    const sumFig = () => document.querySelector('#summaryBox .shot-fig');
    $('#summaryBox .fig-tools [data-sf="size"]').click();
    const rg2 = $('#ctxMenu input[type=range]'); rg2.value = 300; rg2.dispatchEvent(new Event('input'));
    const fe = (S.doc.fig_edits || {})['25'];
    ok('课程重点的图：按时间记下裁剪和宽度，显示裁剪后的图、马上变成 300 像素宽', fe && fe.w === 300 && fe.crop && Math.abs(fe.crop[2] - 0.5) < 0.02 && sumFig().style.maxWidth === '300px'
      && await until(() => /_c[0-9a-f]{8}\.jpg/.test((sumFig().querySelector('img') || {}).src || '')), [fe, S.doc.summary]);
    S.doc.sub_shots = [{ t: 10, at: S.doc.segments[1].start }]; placeSubShots();
    $('#ctxMenu [data-a=all]').click();
    ok('「这节课的图都用这个大小」：课程重点和字幕栏的截图一起改', S.doc.sub_shots[0].w === 300 && S.doc.fig_edits['25'].w === 300 && $('#ctxMenu').hidden
      && document.querySelector('#cues .sub-shot').style.maxWidth === '302px', [S.doc.sub_shots, S.doc.fig_edits]);
    S.doc.sub_shots = []; placeSubShots();
    toggleSummaryEdit(true);
    const sed = $('#summaryEdit'); sed.selectionStart = sed.selectionEnd = sed.value.indexOf('要点甲');
    v.currentTime = 33; await wait(300);
    $('#btnSumShot').click();
    ok('编辑时插在光标那一行后面', S.doc.summary.includes('- 要点甲 [00:03]\n\n![[画面 00:33]]\n\n- 要点乙') && sed.value === S.doc.summary, S.doc.summary);
    toggleSummaryEdit(false);
    const keepConfirm2 = window.confirm; window.confirm = () => true;
    $('#summaryBox .fig-tools [data-sf="del"]').click();
    window.confirm = keepConfirm2;
    ok('删除课程重点里的截图', !S.doc.summary.includes('00:33]]') && S.doc.sum_shots.join() === '25' && document.querySelectorAll('#summaryBox .shot-fig').length === 1, S.doc.summary);
    let printed = null; const keepPrint = window.print;
    window.print = () => { const pa = $('#printArea'); printed = { cls: document.body.classList.contains('printing'), html: pa ? pa.innerHTML : '', imgs: pa ? [...pa.querySelectorAll('img')].filter(im => im.complete && im.naturalWidth > 0).length : 0 }; };
    $('#btnPrint').click();
    ok('打印：只印课程名和课程重点，插图加载好了，没有上移下移按钮', await until(() => printed) && printed.cls && printed.html.includes(S.doc.title)
      && printed.html.includes('要点丙') && printed.imgs === 1 && !printed.html.includes('上移') && getComputedStyle($('#printArea')).display === 'none', printed);
    window.print = keepPrint;
    window.dispatchEvent(new Event('afterprint'));
    ok('打印完恢复原样', !document.body.classList.contains('printing') && !$('#printArea'));
    document.querySelectorAll('.ai-tabs .tab')[1].click();
    ok('「打印」只在课程重点页显示', $('#btnPrint').hidden);
    document.querySelectorAll('.ai-tabs .tab')[0].click();
    S.doc.summary = sumKeep; delete S.doc.sum_shots; delete S.doc.fig_edits; renderSummary();

    // 查漏补缺按钮和覆盖率
    renderSummary();
    ok('有课程重点时可以查漏补缺', !document.querySelector('#btnReview').disabled);
    ok('显示字幕被引用的比例', await until(() => document.querySelector('#summaryHint').textContent.includes('字幕被引用约')), document.querySelector('#summaryHint').textContent);

    // 新术语：勾选确认后加进术语表
    ok('「新术语」按钮可用', !document.querySelector('#btnTerms').disabled);
    S.doc.terms_pending = { terms: [{ term: '测试新术语', t: 1, quote: '原话' }, { term: '不要的词', t: 2, quote: '' }, { term: '底层逻辑', t: 4, quote: '' }], errors: [{ wrong: '风机', right: '峰级', t: 3, quote: '' }] };
    updateButtons();
    ok('有待确认的新术语时按钮显示数量', document.querySelector('#btnTerms').textContent === '新术语 4');
    const termsShown = openTerms();
    ok('新术语对话框：列出新术语和识别错误，术语表里已经有的（底层逻辑）先去掉', await until(() => document.querySelector('#dlgTerms').open)
      && document.querySelectorAll('#tmList .tm-row').length === 3 && !document.querySelector('#tmList').textContent.includes('底层逻辑')
      && !$$('#tmList .tm-in').some(x => x.value === '底层逻辑'));
    document.querySelectorAll('#tmList .tm-row')[1].querySelector('input[type=checkbox]').checked = false;
    document.querySelector('#dlgTerms').close('add');
    await termsShown;
    const tTerms = (teacherById(S.doc.teacher) || {}).terms || '';
    ok('勾选的加进老师术语表，没勾的不加', tTerms.includes('测试新术语') && !tTerms.includes('不要的词') && tTerms.includes('风机 => 峰级') && !S.doc.terms_pending, tTerms);

    // 新老师的第一节课：没选老师时也找专用术语，勾选后填老师名字、建术语表（这里没设 Obsidian 库，记在老师档案里）
    const keepTeacher = S.doc.teacher;
    setDocTeacher('');
    S.doc.terms_pending = { terms: [{ term: '觉知', t: 1, quote: '保持觉知' }], errors: [] };
    const freshShown = openTerms();
    ok('没选老师：对话框变成「建立这位老师的术语表」，要填老师名字、选以后用这位老师的文件夹', await until(() => document.querySelector('#dlgTerms').open
      && !document.querySelector('#tmNew').hidden && document.querySelector('#tmAdd').textContent === '建立术语表' && document.querySelectorAll('#tmBind option').length >= 2));
    const bindPath = document.querySelector('#tmBind').value, bindWas = (libNode(bindPath) || {}).teacher || '';
    document.querySelector('#tmAdd').click();
    ok('没填老师名字不能建', document.querySelector('#dlgTerms').open);
    document.querySelector('#tmName').value = '界面新老师';
    document.querySelector('#tmAdd').click();
    await freshShown;
    const freshT = S.teachers.find(x => x.name === '界面新老师');
    ok('建立术语表：有了新老师档案，这节课用上他，勾选的术语写进去，文件夹绑定了他', !!freshT && S.doc.teacher === freshT.id && (freshT.terms || '').includes('觉知')
      && !S.doc.terms_pending && (libNode(bindPath) || {}).teacher === freshT.id, [freshT, bindPath]);
    await api('library_set', { path: bindPath, teacher: bindWas });
    S.teachers = (await api('teacher_delete', { id: freshT.id })).teachers;
    setDocTeacher(keepTeacher);
    await loadLibrary();

    // 媒体库读不出来时显示原因，不显示“还没有添加文件”
    const libSaved = S.lib; S.lib = []; S.libError = '读不出「library.json」'; renderLibrary();
    ok('媒体库读不出来：显示原因和重试，不显示“还没有添加文件”', $('#libBody').textContent.includes('读不出') && !$('#libBody').textContent.includes('还没有添加文件'));
    S.lib = libSaved; S.libError = ''; renderLibrary();
    // 收起媒体库
    document.querySelector('#btnLibrary').click();
    ok('收起媒体库', document.querySelector('#library').hidden);
    document.querySelector('#btnLibrary').click();

    // 没设置 AI 时的提示
    await startCorrect(true);
    ok('AI 校对没设置时提示', toasts().some(t => t.includes('还没有填写')), toasts());

    // 整理按钮和一键整理都交给后台任务层（和命令行 / Agent 同一套规则）。识别和 AI 是假的（--fake-ai），不联网
    const aiBase = base.replace(/ui\\$/, 'ui-ai\\');
    const keepPath = S.path;
    await api('settings', { settings: { providers: { deepseek: { key: 'sk-fake' }, openai: { key: 'sk-fake' }, doubao: { key: 'fake' } },
      vision: 'openai', chat: 'deepseek', correct: 'deepseek', translate: 'deepseek', asr_zh: 'doubao', asr_en: 'openai', auto_review: true } });
    await loadSettings();
    await window.openPath(aiBase + 'AI整理测试.mp4');
    ok('整理：打开还没有字幕的视频', await until(() => S.doc && S.doc.title === 'AI整理测试' && !S.doc.segments.length));
    setDocTeacher(tr.teacher.id);
    ok('生成字幕：后台识别并写进字幕文件，界面重新读取', await startTranscribe('zh', 'doubao') && S.doc.segments.length === 4 && S.doc.lang === 'zh'
      && S.rev > 0 && S.doc.steps && S.doc.steps.transcribe && S.doc.teacher === tr.teacher.id && document.querySelectorAll('.cue').length === 4, S.doc);
    ok('AI 校对：改的句子绿色下划线、存疑标橙色', await startCorrect(true) && S.doc.segments[0].text.startsWith('（AI 校对过）')
      && !!document.querySelector('.cue.fixed') && document.querySelectorAll('.cue.doubt').length === 1 && S.doc.corrected, S.doc.segments);
    ok('AI 校对：校对前存了历史版本，改动记进 fixes（积累术语表用）', S.doc.versions[0].reason === 'AI 校对前' && S.doc.fixes.some(f => f.by === 'ai'));
    undo();
    ok('AI 校对：可以撤销', S.doc.segments[0].text === '大家好，今天讲峰级。' && await until(() => !S.dirty && !S.saving));
    await startCorrect(true);
    ok('画面复核存疑句：确认后去掉存疑标记', await recheckDoubts(false) && !document.querySelector('.cue.doubt') && S.doc.segments[1].text.endsWith('（画面确认）'), S.doc.segments);
    ok('找关键帧', await findFrames(true) && S.doc.frames.length === 1 && S.doc.frames[0].note === '假 AI 挑的图');
    {  // AI辅助理解图：画面看不清时 AI 重画
      ok('AI辅助理解图：画面页标「AI 重画」，显示重画的流程图代码', !!document.querySelector('.shot .redraw-tag')
        && document.querySelector('.shot .redraw-code').textContent.includes('破级') && !!document.querySelector('[data-unredraw]'));
      const keep = S.doc.summary;
      S.doc.summary = '## 一、开头\n- 要点 [00:03]\n![[画面 00:03]]'; renderSummary();
      ok('AI辅助理解图：课程重点预览显示重画的图和标注，不显示截图', await until(() => {
        const fig = document.querySelector('#summaryBox .shot-fig');
        return fig && fig.querySelector('.redraw-code') && fig.textContent.includes('AI辅助理解图｜根据老师讲解和画面整理，非原画面');
      }));
      document.querySelector('[data-unredraw]').click();
      ok('改用原截图：去掉重画的图', !S.doc.frames[0].redraw && !!document.querySelector('[data-redraw]') && !document.querySelector('.shot .redraw-tag'));
      document.querySelector('[data-redraw]').click();
      ok('点「AI 重画」：重画一张（假 AI 画 SVG），画面页和课程重点预览都显示', await until(() => S.doc.frames[0].redraw && S.doc.frames[0].redraw.kind === 'svg'
        && document.querySelector('.shot .redraw-svg') && document.querySelector('#summaryBox .redraw-svg'), 15000));
      S.doc.summary = keep; renderSummary();
    }
    ok('课程重点：后台写好，自动查漏补缺', await doSummary() && S.doc.summary.includes('## 一、风险') && S.doc.summary.includes('查漏补上的一条')
      && $('#summaryBox').textContent.includes('截断亏损') && $('#btnSummary').textContent === '重新生成', S.doc.summary);
    ok('课程重点刚生成：不提醒重新生成', document.querySelector('#summaryStale').hidden && S.summaryChanges === 0);
    S.doc.segments.slice(0, 3).forEach(s => { s.text += '（人工改）'; }); markDirty(); await save();
    ok('生成后字幕改了 3 句：课程重点上方提醒重新生成', !document.querySelector('#summaryStale').hidden && S.summaryChanges === 3
      && document.querySelector('#summaryStaleText').textContent.includes('改了 3 句'));
    document.querySelector('#btnSummaryStale').click();
    ok('点提醒里的「重新生成」：用改好的字幕重新写，提醒消失', await until(() => !S.job && S.summaryChanges === 0 && document.querySelector('#summaryStale').hidden, 15000));
    ok('媒体库标「重点待更新」', itemHTML({ path: 'x.mp4', name: 'x', summary: true, summary_changes: 5 }).includes('重点待更新')
      && !itemHTML({ path: 'x.mp4', name: 'x', summary: true, summary_changes: 2 }).includes('重点待更新'));
    const dragT0 = performance.now();
    for (let i = 0; i < 40; i++) document.querySelector('#libBody').dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: new DataTransfer() }));
    document.querySelector('#libBody').dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: new DataTransfer() }));
    ok('媒体库里拖动：不再把整个页面打包发给后台（2026-10-19 拖文件夹排序时卡死），也不弹「拖进来打开」', performance.now() - dragT0 < 500
      && !document.querySelector('#dropZone').classList.contains('drag'), performance.now() - dragT0);
    ok('媒体库标「未整理完」：有课程重点但一键整理还差几步（悬停写明差哪几步）',
      itemHTML({ path: 'x.mp4', name: 'x', summary: true, pending: ['review', 'terms'] }).includes('未整理完')
      && itemHTML({ path: 'x.mp4', name: 'x', summary: true, pending: ['review', 'terms'] }).includes('查漏补缺、新术语')
      && !itemHTML({ path: 'x.mp4', name: 'x', summary: true, pending: [] }).includes('未整理完')
      && !itemHTML({ path: 'x.mp4', name: 'x', subs: 5, pending: ['correct', 'summary'] }).includes('未整理完')
      && itemHTML({ path: 'x.pdf', name: 'x', book: { done: 2, total: 2 }, pending: ['overview'] }).includes('未整理完')
      && !itemHTML({ path: 'x.pdf', name: 'x', book: { done: 1, total: 2 }, pending: ['organize', 'overview'] }).includes('未整理完'));
    ok('媒体库标「存疑 N」：还有没确认的存疑句；打开「已校对」后不再标',
      itemHTML({ path: 'x.mp4', name: 'x', subs: 9, doubts: 3 }).includes('存疑 3')
      && !itemHTML({ path: 'x.mp4', name: 'x', subs: 9, doubts: 0 }).includes('存疑')
      && !itemHTML({ path: 'x.mp4', name: 'x', subs: 9, doubts: 3, reviewed: true }).includes('存疑'));
    ok('媒体库「未整理完」：不写课程重点的文件夹里，有字幕就算整理过', itemHTML({ path: 'x.mp4', name: 'x', subs: 5, no_summary: true, pending: ['terms'] }).includes('未整理完')
      && !itemHTML({ path: 'x.mp4', name: 'x', subs: 5, no_summary: true, pending: [] }).includes('未整理完'));
    ok('翻译：中文字幕自己选了翻译也照样翻', await startTranslate(false) && S.doc.segments.every(s => s.tr.startsWith('中文：')));
    const termsP = findTerms(false);
    ok('新术语：放进待勾选并打开对话框', await until(() => document.querySelector('#dlgTerms').open) && S.doc.terms_pending.terms[0].term === '假AI新术语');
    document.querySelector('#dlgTerms').close('drop'); await termsP;
    ok('新术语：不要就清掉', !S.doc.terms_pending);
    openPipeline();
    ok('一键整理：可以选这次豆包用哪种模型（默认是设置里选的「自动」）', document.querySelectorAll('#plDbModel option').length === 5
      && document.querySelector('#plDbModel').value === 'auto');
    ['plAsr', 'plCorrect', 'plFrames', 'plRecheck', 'plSummary', 'plTerms'].forEach(id => { document.getElementById(id).checked = true; });
    document.getElementById('plClip').checked = false;
    const bgSaved = S.bg;
    S.bg = [{ id: 'x1', path: 'D:\\课\\甲.mp4', title: '甲', status: 'running', progress: 0.45 }, { id: 'x2', path: 'D:\\课\\乙.mp4', title: '乙', status: 'waiting', progress: 0 }];
    ok('媒体库：后台正在整理的标「整理中 45%」，排着的标「排队中」', itemHTML({ path: 'd:\\课\\甲.mp4', name: '甲' }).includes('整理中 45%')
      && itemHTML({ path: 'D:\\课\\乙.mp4', name: '乙' }).includes('排队中') && !itemHTML({ path: 'D:\\课\\丙.mp4', name: '丙' }).includes('整理中'));
    S.bg = bgSaved;
    document.querySelector('#dlgPipeline').close('ok');
    ok('一键整理：交给后台整理，界面不用等（可以切到别的课）', await until(() => toasts().some(t => t.includes('交给后台整理了')), 10000) && !S.job, toasts());
    ok('一键整理：做完打开新术语对话框', await until(() => document.querySelector('#dlgTerms').open, 30000));
    document.querySelector('#dlgTerms').close('drop');
    ok('一键整理：完成，重新识别前的字幕存了历史版本', await until(() => toasts().some(t => t.includes('后台整理完了')), 10000)
      && S.doc.versions.some(v => v.reason === '重新识别前') && S.doc.corrected && S.doc.summary.includes('## 一、风险') && !S.job, toasts());
    const tasksNow = await api('doc', { path: S.path });
    ok('一键整理：整理结果和磁盘上的一致', tasksNow.doc.rev === S.rev && tasksNow.doc.summary === S.doc.summary);
    ok('一键整理后：「还差哪几步」跟着更新（媒体库的「未整理完」用它）', Array.isArray(S.pending) && JSON.stringify(S.pending) === JSON.stringify(tasksNow.pending)
      && !S.pending.includes('summary') && !S.pending.includes('review'), [S.pending, tasksNow.pending]);
    await api('settings', { settings: { chat: 'claude' } });  // Claude 没填 Key：课程重点会失败
    await loadSettings();
    const sumBefore = S.doc.summary;
    ok('失败时：提示原因，课程重点保持原样', !(await doSummary()) && toasts().some(t => t.includes('Claude')) && S.doc.summary === sumBefore, toasts());
    await api('settings', { settings: { chat: 'deepseek' } });
    await loadSettings();
    // 单个按钮在前台做的时候也能切课（2026-10-21）：任务在后台接着做，做完提示，结果写进原来那节课
    const aiPath = S.path, aiRev = S.rev;
    doSummary();
    await until(() => S.job);
    await window.openPath(aiBase + '《AI整理测试书》.pdf');
    ok('前台整理时可以切到别的课：任务转到后台接着做', S.mode === 'book' && !S.job && S.detached.size === 1 && toasts().some(t => t.includes('会在后台接着做')), toasts());
    ok('切走的任务做完：提示「在后台做完了」，结果写进原来那节课', await until(() => !S.detached.size && toasts().some(t => t.includes('在后台做完了')), 20000)
      && (await api('doc', { path: aiPath })).doc.rev > aiRev, toasts());
    await window.openPath(aiBase + '《AI整理测试书》.pdf');
    ok('书：打开', await until(() => S.mode === 'book' && (S.doc.chapters || []).length === 2));
    ok('书：整理选中的章节', await bookOrganize([0]) && S.doc.chapters[0].md.includes('假 AI 整理') && !S.doc.chapters[1].md);
    await bookOverview();
    ok('书：章节没全部整理完也可以先写全书总览', S.doc.overview.includes('假 AI 写的总览'));
    await window.openPath(keepPath);
    ok('回到原来的视频', await until(() => S.path === keepPath && S.mode === 'media'));
    const olHtml = md(['1. 甲 [05:53]', '', '2. 乙', '', '3. 丙'].join('\n')), olStart = md(['3. 丙', '4. 丁'].join('\n'));
    ok('排版：编号列表每条之间空一行，也是同一个列表，编号 1、2、3（以前全是 1.）', (olHtml.match(/<ol/g) || []).length === 1 && (olHtml.match(/<li>/g) || []).length === 3
      && olStart.includes('<ol start="3">'), [olHtml, olStart]);
    // 朗读（tts.js）：切句、跳过时间、播放条（用系统声音，不联网）
    $$('.ai-tabs .tab')[0].click();
    const ttsBox = document.createElement('div');
    ttsBox.innerHTML = md('- 先看结构 [01:42]-[02:23]，再看位置。第二句 12:30 也是时间！');
    document.body.append(ttsBox);
    const ttsText = TTS.segments(ttsBox).map(x => x.text);
    ttsBox.remove();
    ok('朗读：按句子切；中括号里的时间、两个时间中间的横杠都不读', ttsText.length === 2 && !/[-\d]/.test(ttsText.join('')) && ttsText[0].startsWith('先看结构'), ttsText);
    if ((S.doc.summary || '').trim()) {
      savePrefs({ tts_engine: 'system', tts_rate: 2 });
      $('#btnTts').click();
      ok('朗读：点「朗读」出现播放条，正在读的句子高亮', await until(() => TTS.active() && document.querySelector('.tts-bar')
        && (!window.CSS.highlights || CSS.highlights.has('tts-speaking'))), TTS._state());
      document.querySelector('.tts-bar [data-t=more]').click();
      ok('朗读：点「更多」菜单真的弹出来（以前一弹出就被关掉）', !document.querySelector('#ctxMenu').hidden && !!document.querySelector('#ctxMenu [data-from="here"]'));
      document.querySelector('.tts-bar [data-t=rate]').click();
      ok('朗读：点倍速菜单真的弹出来', !document.querySelector('#ctxMenu').hidden && !!document.querySelector('#ctxMenu [data-rate="1.5"]'));
      document.querySelector('#ctxMenu [data-rate="1.5"]').click();
      ok('朗读：可以调语速，会记住', S.settings.tts_rate === 1.5 && document.querySelector('.tts-bar [data-t=rate]').textContent === '1.5×');
      const segs2 = TTS.segments(document.querySelector('#summaryBox')), rg = document.createRange();
      rg.setStart(segs2[1].a[0], segs2[1].a[1]); rg.setEnd(segs2[1].b[0], segs2[1].b[1]);
      const bx = rg.getClientRects()[0];
      document.querySelector('#summaryBox').dispatchEvent(new MouseEvent('dblclick', { bubbles: true, clientX: bx.left + 3, clientY: bx.top + bx.height / 2 }));
      ok('朗读：双击第二句，从第二句开始读', TTS._state().i === 1 && TTS._state().state === 'playing', TTS._state());
      document.querySelector('.tts-bar [data-t=play]').click();
      ok('朗读：暂停', TTS._state().state === 'paused');
      document.querySelector('.tts-bar [data-t=close]').click();
      ok('朗读：关闭后播放条消失', !TTS.active() && !document.querySelector('.tts-bar'));
      savePrefs({ tts_engine: 'edge', tts_rate: 1 });
    }

    // 老师工作台（mentor.js）：导入测试老师 → 提问（假 AI）→ 出处变成可点的标签 → 收藏 → 点出处跳回课程
    const coursePath = S.path;
    document.querySelector('#libTabs [data-view="mentor"]').click();
    ok('老师工作台：切到「老师」，显示对话区、隐藏媒体库和课程', S.view === 'mentor' && !document.querySelector('#mentorMain').hidden
      && document.querySelector('#libBody').hidden && !document.querySelector('#mentorList').hidden && document.querySelector('#stageMain').hidden);
    const added = await api('mentor_add', { folder: base + 'kb\\04、外部知识转化库\\测试老师' });
    await loadMentors();
    await openMentor(added.mentor.id);
    ok('导入老师：列表里有他，找到了课程的视频，开场介绍列出课程和示例问题', added.mentor.sources[0].found
      && document.querySelector('#mentorList').textContent.includes('测试老师') && document.querySelectorAll('#mentorMsgs [data-eg]').length === 4
      && document.querySelector('#mentorMsgs').textContent.includes('演示课'), added.mentor.sources);
    const ans = document.createElement('div');
    ans.innerHTML = answerHTML('先算风险［S01 00:02］，见［S01-01］；还有［S09 00:01］。\n\n> 【AI 推测：测试老师可能的看法，不是课程内容】这里是推测。');
    markLabels(ans);
    ok('回答里的出处变成可点的标签，观点库和课程里没有的标红，「AI 推测」单独一个框',
      ans.querySelectorAll('a.cite:not(.bad)').length === 2 && ans.querySelectorAll('a.cite.bad').length === 1
      && ans.querySelector('a.cite').dataset.t === '2' && !!ans.querySelector('.ai-guess') && !ans.querySelector('a.tslink'), ans.innerHTML);
    document.querySelector('#mentorMsgs [data-eg]').click();
    ok('点示例问题就提问；回答写完存进对话记录，列表里出现这个对话（标题是问题）', await until(() => !M.job && M.chat && M.chat.messages.length === 2
      && M.chat.messages[1].role === 'assistant' && ((document.querySelector('#mentorList .mt-chat.active') || {}).textContent || '').includes('交易系统包括哪些方面'), 20000),
      [String(M.chat), M.chat && M.chat.messages.length, M.seq, M.cur && M.cur.mentor.id, M.job, toasts().slice(-3), (document.querySelector('#mentorList') || {}).textContent]);
    document.querySelector('#mentorMsgs [data-save="1"]').click();
    ok('收藏到 Obsidian：按钮变成「已收藏」', await until(() => M.chat.messages[1].saved && document.querySelector('#mentorMsgs [data-save="1"]').disabled));
    const cite = document.createElement('a');
    cite.className = 'cite'; cite.dataset.sid = 'S01'; cite.dataset.t = '2';
    document.querySelector('#mentorMsgs .msg.assistant .bubble').append(cite);
    cite.click();
    ok('点出处：切回课程，打开那节课', await until(() => S.view === 'course' && S.path && S.path.toLowerCase().endsWith('演示视频.mp4') && !document.querySelector('#stageMain').hidden, 10000), [S.view, S.path]);
    document.querySelector('#libTabs [data-view="mentor"]').click();
    ok('再切回「老师」：刚才的对话还在', await until(() => S.view === 'mentor' && document.querySelectorAll('#mentorMsgs .msg').length === 2));
    await api('mentor_remove', { id: added.mentor.id });

    // 老师蒸馏（后台 distill.py，假 AI）：从媒体库的课新建老师 → 后台蒸馏 → 三份文件写进 Obsidian → 能对话 → 一键收藏整个对话
    await api('settings', { settings: { obsidian: { vault: base + 'kb' } } });
    document.querySelector('#mentorAdd').click();
    ok('加老师按钮：弹出「从媒体库蒸馏 / 导入」两个选项（不会一点就关掉）', !document.querySelector('#ctxMenu').hidden
      && !!document.querySelector('#ctxMenu [data-a=distill]') && !!document.querySelector('#ctxMenu [data-a=import]'));
    document.querySelector('#ctxMenu [data-a=distill]').click();
    ok('蒸馏窗口：列出媒体库文件夹里的课', await until(() => document.querySelector('#dlgDistill').open && document.querySelectorAll('#dsList input').length > 0));
    document.querySelector('#dsName').value = '蒸馏测试老师';
    document.querySelector('#dsName').dispatchEvent(new Event('input'));
    ok('蒸馏窗口：显示结果写到 Obsidian 的哪个文件夹', await until(() => document.querySelector('#dsTarget').textContent.includes('04、外部知识转化库\\01、老师分身蒸馏资料\\蒸馏测试老师')),
      document.querySelector('#dsTarget').textContent);
    const dBoxes = [...document.querySelectorAll('#dsList input')];
    const dDemo = dBoxes.find(b => b.dataset.path.toLowerCase().endsWith('演示视频.mp4'));
    ok('蒸馏窗口：有字幕的课默认勾上，书和文档不列出来', dDemo && dDemo.checked && !dDemo.disabled && !dBoxes.some(b => /\.(pdf|md)$/i.test(b.dataset.path)),
      dBoxes.map(b => b.dataset.path));
    dBoxes.forEach(b => { if (!b.disabled) b.checked = b === dDemo; });
    document.querySelector('#dsOk').click();
    ok('开始蒸馏：后台做完后提示「蒸馏好了」', await until(() => toasts().some(t => t.includes('「蒸馏测试老师」蒸馏好了')), 30000), toasts().slice(-3));
    const dm = M.list.find(x => x.name === '蒸馏测试老师') || {};
    ok('蒸馏完：左栏没有「未蒸馏完」，打开就能对话（开场列出课程和示例问题）', await until(() => M.cur && M.cur.mentor.id === dm.id
      && !document.querySelector('#mentorInput').disabled && document.querySelectorAll('#mentorMsgs [data-eg]').length === 4
      && !document.querySelector('#mentorList').textContent.includes('未蒸馏完') && document.querySelector('#mentorDistill').hidden, 10000),
      [M.cur && M.cur.files, document.querySelector('#mentorList').textContent]);
    const dFiles = M.cur.files;
    ok('蒸馏完：三份文件写进「04、外部知识转化库/蒸馏测试老师」', ['atoms', 'distill', 'persona'].every(k => (dFiles[k] || '').includes('蒸馏测试老师')), dFiles);
    document.querySelector('#mentorMsgs [data-eg]').click();
    await until(() => !M.job && M.chat && M.chat.messages.length === 2, 20000);
    await mentorAsk('第二个问题');
    ok('提了两个问题，「收藏整个对话」能点', M.chat.messages.length === 4 && !document.querySelector('#mentorSaveAll').disabled
      && document.querySelector('#mentorMsgs [data-save="1"]').textContent.includes('☆ 收藏'));
    document.querySelector('#mentorSaveAll').click();
    ok('一键收藏整个对话：两条回答都收藏，按钮变成「★ 已收藏」，「收藏整个对话」变灰', await until(() => M.chat.messages.filter(x => x.role === 'assistant').every(x => x.saved)
      && document.querySelector('#mentorSaveAll').disabled && document.querySelector('#mentorMsgs [data-save="3"]').textContent.includes('★ 已收藏')),
      toasts().slice(-2));
    const dFav = await api('mentor_favorites', { id: dm.id });
    ok('对话精选：有了，能在 Obsidian 里打开', dFav.exists && dFav.uri.startsWith('obsidian://open?') && !document.querySelector('#mentorFav').disabled, dFav);
    // 圆桌讨论、主题蒸馏、提示词（round.js，假 AI）。软件的 R 被这个脚本里的 R（结果列表）挡住了，用间接 eval 取全局的
    const RT = (0, eval)('R');
    const t2 = await api('mentor_add', { folder: base + 'kb\\04、外部知识转化库\\测试老师' });
    await loadMentors();
    ok('左栏老师下面有「圆桌讨论」「主题蒸馏」两组', !!document.querySelector('#mentorList .mt-section[data-new=round]') && !!document.querySelector('#mentorList .mt-section[data-new=topic]'));
    document.querySelector('#mentorList .mt-section[data-new=round]').click();
    ok('新建圆桌：列出蒸馏好的老师，默认都勾上', await until(() => document.querySelector('#dlgMulti').open && document.querySelectorAll('#mlList input:checked').length === 2
      && document.querySelector('.topic-only').hidden));
    document.querySelector('#mlOk').click();
    ok('开始讨论：顶上显示两位老师和圆桌的按钮，和一位老师对话的按钮藏起来', await until(() => RT.mode === 'round' && RT.round
      && document.querySelector('#mentorChatTitle').textContent.includes('测试老师') && !document.querySelector('#roundTools').hidden && document.querySelector('#mentorSaveAll').hidden));
    await roundAsk('风险怎么定？');
    ok('圆桌提问：两位老师各自回答，再 AI 总结', RT.round.messages.map(x => x.role).join() === 'user,teacher,teacher,summary'
      && document.querySelectorAll('#mentorMsgs .rt-teacher').length === 2 && !!document.querySelector('#mentorMsgs .rt-summary') && !RT.job, RT.round.messages.map(x => x.role));
    const cdiv = document.createElement('div');
    cdiv.innerHTML = answerHTML('见［测试老师 S01-01］和［蒸馏测试老师 S01-01］，还有［别人 S01-01］。', { names: roundNames() });
    ok('总结里带老师名的出处：认得出是哪位老师的，认不出的标红', cdiv.querySelectorAll('a.cite:not(.bad)').length === 2 && cdiv.querySelectorAll('a.cite.bad').length === 1
      && cdiv.querySelector('a.cite').dataset.mid === t2.mentor.id && cdiv.textContent.includes('别人 S01-01'), cdiv.innerHTML);
    await roundAction('round_reply');
    ok('让老师们互相回应：多了两段回应和新的总结', RT.round.messages.filter(x => x.round === 2).length === 2 && document.querySelectorAll('#mentorMsgs .rt-teacher').length === 4
      && RT.round.messages[RT.round.messages.length - 1].role === 'summary');
    document.querySelector('#rtSave').click();
    ok('收藏菜单列出六个主题文件夹', await until(() => document.querySelectorAll('#ctxMenu [data-f]').length === 6));
    document.querySelector('#ctxMenu [data-f]').click();
    ok('收藏到 Obsidian：记下文件，按钮变成「更新」', await until(() => RT.round.saved && document.querySelector('#rtSave').textContent.includes('更新')));
    document.querySelector('#mentorList .mt-section[data-new=topic]').click();
    await until(() => document.querySelector('#dlgMulti').open);
    document.querySelector('#mlTopic').value = '风险';
    document.querySelector('#mlOk').click();
    ok('主题蒸馏：后台做完，软件里显示结果，左栏有这一条', await until(() => RT.mode === 'topic' && RT.topic && RT.topic.text && !!document.querySelector('#mentorMsgs .mentor-doc')
      && !Object.keys(RT.tjobs).length && !!document.querySelector('#mentorList .tp-item'), 30000), [RT.mode, RT.topic && RT.topic.topic, toasts().slice(-2)]);
    ok('主题蒸馏的页面：不能对话，有「在 Obsidian 里打开」', document.querySelector('#mentorInput').disabled && !document.querySelector('#topicTools').hidden && !document.querySelector('#tpOpen').disabled);
    document.querySelector('#mentorPrompts').click();
    ok('提示词：显示 11 种，第一种是逐节提取观点库', await until(() => document.querySelector('#dlgPrompts').open && document.querySelectorAll('#prList details').length === 11
      && document.querySelector('#prList pre').textContent.includes('观点库')));
    document.querySelector('#dlgPrompts').close();
    document.querySelector('#mentorList .mt-teacher').click();
    ok('点老师：回到和一位老师对话，圆桌的按钮藏起来', await until(() => !RT.mode && document.querySelector('#roundTools').hidden && document.querySelector('#topicTools').hidden
      && !document.querySelector('#mentorSaveAll').hidden));
    for (const r of RT.rounds) await api('round_delete', { cid: r.id });
    for (const t of RT.topics) await api('topic_delete', { id: t.id });
    await api('mentor_remove', { id: t2.mentor.id });

    await api('mentor_remove', { id: dm.id });
    await api('settings', { settings: { obsidian: { vault: '' } } });
    document.querySelector('#libTabs [data-view="course"]').click();
    if (coursePath && S.path !== coursePath) await window.openPath(coursePath);

    // 媒体库树形显示、展开收起、拖动排序、改名、新格式（test-media\ui-lib：1、第一讲\{2、乙,1、甲}.avi、资料\补充.epub、预告.avi）
    const libBase = base.replace(/ui\\$/, 'ui-lib\\');
    const rl = await api('library_add_folder', { path: libBase });
    await loadLibrary(rl.groups);
    const top = S.lib.find(n => n.path.toLowerCase() === libBase.replace(/\\$/, '').toLowerCase());
    ok('媒体库按实际层级显示：带序号的在前，子文件夹里也按序号', top && top.children.map(c => c.name).join('|') === '1、第一讲|资料|预告'
      && top.children[0].children.map(c => c.name).join('|') === '1、甲|2、乙', top && top.children.map(c => c.name));
    const headOf = name => [...document.querySelectorAll('#libBody [data-folder]')].find(h => h.querySelector('.lib-gname').textContent === name);
    ok('「课程」里不显示只有书的文件夹', !headOf('资料') && !!headOf('1、第一讲'));
    switchView('book');
    ok('子文件夹显示成可以展开的一层，往里缩进（「书籍」里只显示有书的文件夹）', !!headOf('资料') && getComputedStyle(headOf('资料')).getPropertyValue('--depth').trim() === '1'
      && !headOf('1、第一讲') && !document.querySelector('#libBody [data-fname="预告.avi"]'));
    headOf('资料').click();
    ok('点文件夹收起，记住', headOf('资料').parentElement.classList.contains('collapsed') && libNode(top.path + '\\资料').collapsed);
    headOf('资料').click();
    switchView('course');
    const sib = () => siblings0(top.path).map(el => el.dataset.fname || el.dataset.drag.split('\\').pop());
    const elOf = name => siblings0(top.path).find(el => (el.dataset.fname || '') === name);
    let list = [elOf('预告.avi'), elOf('1、第一讲')];
    await libReorder({ el: elOf('预告.avi'), path: elOf('预告.avi').dataset.drag, parent: top.path }, list);
    ok('拖动没序号的：放到哪里就排在哪里；这一层看不见的书（「资料」）不丢', await until(() => sib().join('|') === '预告.avi|1、第一讲')
      && libNode(top.path).children.some(c => c.name === '资料'), [sib(), libNode(top.path).children.map(c => c.name)]);
    const origPrompt = window.prompt;
    window.prompt = () => null;  // 提示改名时不改
    const inner = top.path + '\\1、第一讲';
    const elsIn = () => siblings0(inner);
    list = elsIn().slice().reverse();
    await libReorder({ el: list[0], path: list[0].dataset.drag, parent: inner }, list);
    ok('拖动带序号的打乱序号：提示改名，不改名就不动', toasts().some(t => t.includes('按序号排列')) && elsIn().map(el => el.dataset.fname).join('|') === '1、甲.avi|2、乙.avi');
    window.prompt = () => '预告（改过名）';
    ok('改名：电脑上的文件和媒体库一起改', await renameNode(libNode(top.path + '\\预告.avi')) && !!libNode(top.path + '\\预告（改过名）.avi')
      && !libNode(top.path + '\\预告.avi'));
    window.prompt = origPrompt;
    const isShut = name => headOf(name).parentElement.classList.contains('collapsed');
    const topName = top.name;
    switchView('book');
    headOf('资料').click();  // 先收起，再打开里面的书
    switchView('course');
    await window.openPath(top.path + '\\资料\\补充.epub');
    ok('打开电子书（EPUB）：分出章节，显示类型；左上角自动切到「书籍」', await until(() => S.mode === 'book' && (S.doc.chapters || []).length === 2) && $('#bkInfo').textContent.includes('电子书（EPUB）')
      && S.view === 'book' && document.querySelector('#libTabs .lib-tab.active').dataset.view === 'book', $('#bkInfo').textContent);
    ok('打开的课所在的文件夹自动展开', !isShut('资料') && !libNode(top.path + '\\资料').collapsed);
    headOf('资料').click();
    ok('正在看的课所在的文件夹也能收起', isShut('资料'));
    headOf(topName).click();
    ok('正在看的课所在的最上层文件夹也能收起', isShut(topName));
    headOf(topName).click(); headOf('资料').click();
    ok('再点又展开', !isShut(topName) && !isShut('资料'));
    await window.openPath(keepPath);
    ok('打开视频：左上角自动切回「课程」', S.view === 'course');
    await loadLibrary((await api('library_remove', { kind: 'folder', path: top.path })).groups);

    // 设置窗口
    openSettings('codex');
    ok('设置：ChatGPT 账号登录', document.querySelector('#codexBox').textContent.includes('用 ChatGPT 账号登录'));
    showSection('teachers');
    ok('设置：老师列表', document.querySelectorAll('#teacherList button').length === 2);
    document.querySelectorAll('#teacherList button')[0].click();
    const tEdit = teacherById(editingTeacher), tOld = tEdit.name;
    document.querySelector('#tName').value = tOld + '改名';
    ok('改了名称还没点「保存这位老师」：认得出有改动', teacherFormDirty());
    await saveSettings(false);
    ok('点「保存设置」时把改名一起保存，顶栏下拉框跟着变', teacherById(tEdit.id).name === tOld + '改名'
       && [...document.querySelectorAll('#teacherSelect option')].some(o => o.textContent.includes(tOld + '改名')) && !teacherFormDirty());
    document.querySelector('#tName').value = tOld; await saveTeacher();
    ok('设置：可以关联术语表文件；没有「笔记里的称呼」（笔记不写老师）', document.querySelector('#tFile').textContent === '没有关联' && !!document.querySelector('#tFilePick') && !document.querySelector('#tDisplay'));
    ok('设置：没关联文件时不显示「查看术语」「在 Obsidian 里打开」', document.querySelector('#tFileActions').hidden);
    const tvPath = base + 'kb\\04、外部知识转化库\\测试老师\\01、观点库（逐节提取）.md';
    await setTermsFile(tvPath);
    ok('设置：关联了文件就能「查看术语和重复检查」「在 Obsidian 里打开」，提示里说有没有重复', !document.querySelector('#tFileActions').hidden
      && /重复/.test(document.querySelector('#tFileHint').textContent), document.querySelector('#tFileHint').textContent);
    await viewTermsFile(tvPath);
    ok('查看术语：列出重复检查、术语、识别错误参考、直接替换', document.querySelector('#dlgTermsView').open && document.querySelectorAll('#tvBody details').length === 4
      && document.querySelector('#tvBody').textContent.includes('没有重复'));
    document.querySelector('#dlgTermsView').close();
    await setTermsFile('');
    showSection('doubao');
    ok('设置：豆包只列四种模型，可以选「自动」或只用一种；没有对象存储', document.querySelectorAll('#doubaoOrder li').length === 4
       && document.querySelector('#doubaoOrder li').textContent.includes('流式识别 2.0') && !document.querySelector('#doubaoModel')
       && document.querySelectorAll('input[name=dbPick]').length === 5 && document.querySelector('input[name=dbPick][value=""]').checked
       && !document.querySelector('[data-k="providers.doubao.tos_ak"]'));
    document.querySelector('input[name=dbPick][value=std1]').checked = true;
    ok('设置：选「只用录音文件识别 1.0 标准版」会保存', collectSettings().providers.doubao.pick === 'std1', collectSettings().providers.doubao);
    document.querySelector('input[name=dbPick][value=""]').checked = true;
    document.querySelector('#dbAdd').click();
    const dbRow = document.querySelector('#dbMore .db-acct:last-child');
    dbRow.querySelector('.db-name').value = '小号'; dbRow.querySelector('.db-key').value = 'ui-test-key-98765';
    await saveSettings(false);
    const dbMk = S.settings.providers.doubao.more_keys;
    renderDoubaoAccounts();
    ok('设置：可以添加别的豆包账号，保存后密钥只显示后四位', dbMk.length === 1 && dbMk[0].name === '小号' && dbMk[0].key === '••••8765'
       && document.querySelector('#dbMore .db-key').placeholder.includes('••••8765') && document.querySelector('#dbMore .db-key').value === '', dbMk);
    const acctRadio = document.querySelector(`input[name=dbAcct][value="${dbMk[0].id}"]`);
    if (dbAccts().length > 1) {
      acctRadio.checked = true;
      ok('设置：有几个豆包账号时可以选「只用小号」', document.querySelector('input[name=dbAcct][value=""]')
        && collectSettings().providers.doubao.pick_account === dbMk[0].id);
      document.querySelector('input[name=dbAcct][value=""]').checked = true;
    } else {
      ok('设置：只有一个豆包账号时不用选账号', dbAccts()[0].name === '小号' && !document.querySelector('input[name=dbAcct]'));
    }
    fillDbPickers(document.querySelector('#trDbModel'), document.querySelector('#trDbAcct'));
    ok('生成字幕对话框：可以临时选这次用哪个账号', [...document.querySelectorAll('#trDbAcct option')].some(o => o.textContent.includes('小号'))
      && document.querySelector('#trDbAcct').value === 'auto');
    await api('settings', { settings: { providers: { doubao: { spent: { [dbMk[0].id + '|stream2']: '2026-10-14' } } } } });
    await refreshSettingsOnly(); renderDoubaoAccounts();
    ok('设置：列出哪个账号的哪种模型额度用完了', document.querySelector('#dbSpent').textContent.includes('小号 · 流式识别 2.0：2026-10-14 用完'),
       document.querySelector('#dbSpent').textContent);
    document.querySelector('[data-db-revive]').click();
    ok('点「重新启用」去掉用完的记录', await until(() => !Object.keys(S.settings.providers.doubao.spent || {}).length && !document.querySelector('[data-db-revive]')));
    document.querySelector('#dbMore [data-db-del]').click();
    await saveSettings(false);
    ok('设置：删掉豆包账号', !S.settings.providers.doubao.more_keys.length);
    showSection('obsidian');
    ok('设置：笔记文件名格式', document.querySelector('[data-k="obsidian.name_pattern"]').value === '{title}');
    showSection('tasks');
    ok('设置：任务分配可选 7 个 AI 服务', document.querySelectorAll('select[data-k="chat"] option').length === 7);
    showSection('template');
    showSection('about');
    ok('设置：导入导出和存放位置', !!document.querySelector('#btnExportSettings') && await until(() => document.querySelector('#aboutRoot').textContent.length > 3));
    ok('设置：课程重点模板', document.querySelector('#summaryTemplate').value.includes('不要套用固定栏目'));
    ok('关于：开源地址已改名', document.querySelector('#aboutHome').textContent.includes('mengren-learning'));
    ok('关于：点 × 时收到右下角，默认打开，可以关掉', document.querySelector('[data-k="close_to_tray"]').checked === true);
    document.querySelector('#dlgSettings').close();

    // 识别中途豆包额度用完或没开通、换了账号 / 模型从头识别：已经显示的半截字幕清掉
    const segs0 = S.doc.segments, live0 = liveView();
    live0.onItems([{ kind: 'cue', start: 0, end: 1, text: '半截一' }, { kind: 'cue', start: 1, end: 2, text: '半截二' }]);
    live0.onItems([{ kind: 'reset' }, { kind: 'cue', start: 0, end: 1, text: '重新识别' }]);
    ok('识别中途换了账号或模型：先清掉已经显示的半截字幕', S.doc.segments.length === 1 && S.doc.segments[0].text === '重新识别'
       && document.querySelectorAll('#cues .cue').length === 1, S.doc.segments.length);
    S.doc.segments = segs0; renderCues();

    // 使用说明
    document.querySelector('#btnHelpTop').click();
    ok('顶栏的帮助按钮打开使用说明', document.querySelector('#dlgHelp').open && !document.querySelector('#helpBody section[data-h="start"]').hidden);
    document.querySelector('#helpNav [data-h="book"]').click();
    ok('使用说明可以切换章节', !document.querySelector('#helpBody section[data-h="book"]').hidden && document.querySelector('#helpBody section[data-h="start"]').hidden
      && document.querySelectorAll('#helpNav [data-h]').length >= 17);
    document.querySelector('#helpNav [data-h="ui"]').click();
    const uiHelp = document.querySelector('#helpBody section[data-h="ui"]');
    ok('使用说明：有「界面上的标签和按钮」，讲到媒体库的未整理完、字幕的存疑标记、课程重点的按钮', !uiHelp.hidden
      && ['未整理完', '重点待更新', '待更新剪藏', '整理中', '存疑', '绿色下划线', '查漏补缺', '字幕被引用'].every(w => uiHelp.textContent.includes(w)));
    document.querySelector('#helpClose').click();
    ok('关闭使用说明', !document.querySelector('#dlgHelp').open);

    // 书籍和文档
    await flushSave();
    await window.openPath(base + '测试书籍.pdf');
    ok('打开 PDF 进入书籍界面', await until(() => S.mode === 'book' && !document.querySelector('#bookMain').hidden && document.querySelector('#stageMain').hidden && document.body.classList.contains('mode-book')));
    ok('左边显示 PDF 原文', document.querySelector('#bookPdf').src.startsWith('file:') && !document.querySelector('#bookPdf').hidden);
    ok('普通的电子版 PDF 不显示「识别文字（OCR）」（只有扫描书才有）', document.querySelector('#bkOcr').hidden);
    ok('视频相关按钮在书籍界面隐藏', getComputedStyle(document.querySelector('#btnTranscribe')).display === 'none');
    ok('列出章节和全书总览', document.querySelectorAll('#bkChapters .ch-row').length === 3 && document.querySelector('#bkChapters').textContent.includes('00、全书总览'));
    ok('认出「书上第 1 页是 PDF 第 3 页」', document.querySelector('#bkFirst').value === '3' && document.querySelector('#bkTitle').value === '测试书籍');
    document.querySelector('#bkChapters .ch-row[data-ch="0"] span').click();
    ok('点章节：选中并跳到这一章的第一页', S.bookSel.i === 0 && document.querySelector('#bookPdf').src.endsWith('#page=3'));
    S.doc.chapters[0].md = '## 一、本章主题\n\n讲趋势。（P2）（PDF 第 5 页）';
    renderBook();
    ok('整理稿里的页码可以点', document.querySelectorAll('#bkView .pglink').length === 2);
    // 原书图示在软件里直接显示（2026-10-30，以前要剪藏到 Obsidian 才变成图）
    const mdKeep = S.doc.chapters[0].md;
    S.doc.chapters[0].md = mdKeep + '\n\n![[原书图示 第4页 图1-1 三段结构 @0.1,0.3,0.9,0.6]]\n*原书图示｜图 1-1 三段结构（P2）*';
    renderBook();
    ok('整理笔记里的原书图示显示成图片，图的说明不朗读', await until(() => (document.querySelector('#bkView .book-fig img') || {}).src)
      && !TTS.segments(document.querySelector('#bkView')).some(x => x.text.includes('第 4 页')), document.querySelector('#bkView').innerHTML.slice(-300));
    S.doc.chapters[0].md = mdKeep; renderBook();
    document.querySelector('#bkView .pglink[data-p]').click();
    ok('点 P2 跳到 PDF 第 4 页', document.querySelector('#bookPdf').src.endsWith('#page=4'));
    // 书籍朗读（2026-10-29）：和课程重点一样，页码不读；换一章朗读停下
    const bkSegs = TTS.segments(document.querySelector('#bkView')).map(x => x.text);
    // 书的文字设置（Aa）：整理笔记和原文文字版共用、和字幕分开；两列的「Aa」打开同一个菜单
    const bkFs0 = parseFloat(getComputedStyle(document.querySelector('#bkView')).fontSize), cueFs0 = S.settings.font_size;
    document.querySelector('#bookNote [data-bktx]').click();
    ok('书的文字设置：整理笔记的「Aa」打开菜单', document.querySelector('#menuBookText').classList.contains('open') && document.querySelector('#bookNote').contains(document.querySelector('#menuBookText')));
    document.querySelector('[data-btx="book_font_size:1"]').click();
    document.querySelector('[data-btx="book_line_height:0.1"]').click();
    ok('书的文字设置：字号、行距变大，字幕的字号不变', parseFloat(getComputedStyle(document.querySelector('#bkView')).fontSize) === bkFs0 + 1
      && S.settings.book_line_height === 1.9 && S.settings.font_size === cueFs0 && document.querySelector('#bkTxSize').textContent === String(bkFs0 + 1));
    document.querySelector('#bookLeft [data-bktx]').click();
    ok('书的文字设置：原书的「Aa」打开同一个菜单', document.querySelector('#menuBookText').classList.contains('open') && document.querySelector('#bookLeft').contains(document.querySelector('#menuBookText')));
    document.querySelector('#bkTxFont').value = 'kai'; document.querySelector('#bkTxFont').dispatchEvent(new Event('change'));
    ok('书的文字设置：换字体，原文文字版也跟着变', getComputedStyle(document.querySelector('#bkSrcText')).fontFamily.includes('Kai'), getComputedStyle(document.querySelector('#bkSrcText')).fontFamily);
    savePrefs({ book_font_size: 15, book_line_height: 1.8, book_font_family: 'yahei' }); closeMenus();
    ok('朗读：英文句子自动换英文声音，中文句子夹英文词还是中文声音', TTS.isEnglish('Research is a craft.') && !TTS.isEnglish('研究是一门 craft。'));
    ok('书籍朗读：按句子切，页码（P2、PDF 第 5 页）不读', bkSegs.join('|').includes('讲趋势') && !/P2|PDF|第 5 页/.test(bkSegs.join('')), bkSegs);
    savePrefs({ tts_engine: 'system', tts_rate: 2 });
    ok('书籍朗读：有内容时朗读按钮可以点', !document.querySelector('#bkTts').disabled);
    document.querySelector('#bkTts').click();
    ok('书籍朗读：点「朗读」读这一章，播放条出现在这一章上面', await until(() => TTS.active() && TTS.active().root === document.querySelector('#bkView')
      && document.querySelector('.chapter-view .tts-bar')), TTS._state());
    document.querySelector('#bkChapters .ch-row[data-ch="1"] span').click();
    ok('书籍朗读：换一章，朗读停下；没整理的章朗读按钮是灰的', !TTS.active() && !document.querySelector('.tts-bar') && document.querySelector('#bkTts').disabled);
    savePrefs({ tts_engine: 'edge', tts_rate: 1 });
    // 整理时每做好一章，界面马上显示（bookLive）；分章这期间改过的不动
    const c1 = S.doc.chapters[1];
    bookLive([{ kind: 'chapter', i: 1, title: c1.title, start: c1.start, end: c1.end, md: '## 一、本章主题\n\n边整理边显示。', note: '提炼：12 字' },
              { kind: 'chapter', i: 0, title: '别的章名', start: 1, end: 2, md: '不该写进来' }]);
    ok('整理时每做好一章，列表马上标「已整理」、正在看的这一章马上显示', S.doc.chapters[1].md.includes('边整理边显示')
      && document.querySelector('#bkChapters .ch-row[data-ch="1"] .ch-state.ok') && document.querySelector('#bkView').textContent.includes('边整理边显示')
      && !document.querySelector('#bkTts').disabled && S.doc.chapters[0].md.includes('讲趋势'));
    // 这本书正被后台 / 命令行整理时点「整理全部」：整个任务没开始就停了，要说清楚原因（2026-10-29 以前显示「整理好了 undefined 章」）
    const busyOut = stepResult({ status: 'done', task: { status: 'failed', message: '失败', error: { error_code: 'busy', message: '这节课正在被另一个任务整理（测试，15:08 开始）' },
      stages: [{ name: 'organize', status: 'pending' }] } }, 'organize');
    ok('正被别的任务整理时：说清楚原因，不显示「undefined 章」', busyOut === null && toasts().some(t => t.includes('正在被另一个任务整理') && t.includes('等那边整理完'))
      && !toasts().some(t => t.includes('undefined')), toasts());
    S.doc.chapters[1].md = ''; renderBook();
    document.querySelector('#bkChapters .ch-row[data-ch="0"] span').click();
    // 三列（2026-10-29 用户要求）：原书、目录、整理笔记，各有标题和边框，能单独隐藏；放大；拖宽度；记在本机
    const colName = k => document.querySelector(`#bookMain [data-col="${k}"] .bk-col-name`).textContent;
    const box = sel => document.querySelector(sel).getBoundingClientRect();
    ok('书：三列，标题是「原书」「目录」「整理笔记」，目录栏写着共几章、整理了几章', colName('src') === '原书' && colName('toc') === '目录' && colName('note') === '整理笔记'
      && document.querySelector('#bkTocCount').textContent.includes('共 2 章 · 已整理 1'), document.querySelector('#bkTocCount').textContent);
    const nb0 = box('#bookNote');
    ok('书：三列从左到右排开，章节竖着排在目录这一列里', box('#bookLeft').right <= box('#bookToc').left + 1 && box('#bookToc').right <= nb0.left + 1
      && [...document.querySelectorAll('#bkChapters .ch-row')].every(r => r.getBoundingClientRect().right <= box('#bookToc').right + 1), [box('#bookLeft'), box('#bookToc'), nb0]);
    document.querySelector('#bookLeft [data-hide="src"]').click();
    ok('书：隐藏原书，缩成一条竖条，笔记变宽，记在本机', document.querySelector('#bookLeft').classList.contains('collapsed') && box('#bookLeft').width < 40
      && box('#bookNote').width > nb0.width + 100 && JSON.parse(localStorage.getItem('book-layout')).hide.src === true);
    document.querySelector('#bookLeft .bk-rail').click();
    ok('书：点竖条，原书恢复', !document.querySelector('#bookLeft').classList.contains('collapsed') && box('#bookLeft').width > 200);
    document.querySelector('#bkZoom').click();
    ok('书：放大，原书和目录收起，笔记占满', document.querySelector('#bookLeft').classList.contains('collapsed') && document.querySelector('#bookToc').classList.contains('collapsed')
      && document.querySelector('#bkZoom').textContent === '还原' && box('#bookNote').width > nb0.width + 300);
    document.querySelector('#bkZoom').click();
    ok('书：还原', !document.querySelector('#bookLeft').classList.contains('collapsed') && !document.querySelector('#bookToc').classList.contains('collapsed') && document.querySelector('#bkZoom').textContent === '放大');
    const sp2 = document.querySelector('#bookSplit2'), tl = box('#bookToc').left;
    sp2.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, clientX: tl + 300, pointerId: 1 }));
    sp2.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, clientX: tl + 230, pointerId: 1 }));
    sp2.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, clientX: tl + 230, pointerId: 1 }));
    ok('书：拖分隔线调目录宽度，记住', box('#bookToc').width < 290 && Math.abs(JSON.parse(localStorage.getItem('book-layout')).toc - box('#bookToc').width) < 3, box('#bookToc'));
    bookLayout.toc = 300; bookLayout.src = 0; applyBookLayout(); saveBookLayout();
    // 和原书目录一致（2026-10-29）：按部分分组显示；重新分章时起止页没变的保留整理好的内容
    const kn = keepNotes([{ title: 'a', start: 1, end: 2, md: '甲' }, { title: 'b', start: 3, end: 9, md: '乙' }],
      [{ title: 'a', start: 1, end: 2, part: 'P1' }, { title: 'b2', start: 3, end: 5, part: 'P1' }, { title: 'c', start: 6, end: 9 }]);
    ok('重新分章：起止页没变的保留整理好的内容，变了的、新的要重新整理', kn.kept === 1 && kn.chapters[0].md === '甲' && kn.chapters[0].part === 'P1'
      && !kn.chapters[1].md && !kn.chapters[2].md, kn);
    const saveChs = S.doc.chapters.map(c => ({ ...c }));
    S.doc.chapters.forEach(c => { c.part = '第一部分 测试'; }); renderBook();
    ok('目录按原书的部分分组：部分标题下面缩进列出各篇', document.querySelectorAll('#bkChapters .ch-part').length === 1
      && document.querySelector('#bkChapters .ch-part').textContent === '第一部分 测试' && document.querySelectorAll('#bkChapters .ch-row.in-part').length === S.doc.chapters.length);
    S.doc.chapters = saveChs; renderBook();
    // 原书：朗读原文（换成这一章的文字版，正在读的句子高亮，页码不读；关掉朗读换回 PDF）；也可以手动切文字版
    savePrefs({ tts_engine: 'system', tts_rate: 2 });
    document.querySelector('#bkSrcTts').click();
    ok('原书：点「朗读原文」，换成这一章的文字版开始读，播放条在原书这一列', await until(() => TTS.active() && TTS.active().root === document.querySelector('#bkSrcText')
      && document.querySelector('#bookPdf').hidden && !document.querySelector('#bkSrcText').hidden && document.querySelector('#bkSrcText .bpage') && document.querySelector('#bookLeft .tts-bar')), TTS._state());
    const ref1 = document.querySelector('#bkSrcText .bref').textContent;
    ok('原书：页码不读', ref1 && !TTS.segments(document.querySelector('#bkSrcText')).some(x => x.text.includes(ref1)), ref1);
    ok('原书：朗读原文时，整理笔记的朗读按钮不亮', !document.querySelector('#bkTts').classList.contains('primary'));
    document.querySelector('#bookLeft .tts-bar [data-t=close]').click();
    ok('原书：关掉朗读，换回 PDF', !TTS.active() && !document.querySelector('#bookPdf').hidden && document.querySelector('#bkSrcText').hidden);
    document.querySelector('#bkSrcMode [data-m=read]').click();
    ok('原书：可以手动切到文字版', await until(() => !document.querySelector('#bkSrcText').hidden && document.querySelector('#bkSrcText .bpage'))
      && document.querySelector('#bkSrcMode [data-m=read]').classList.contains('on') && document.querySelector('#bookPdf').hidden);
    document.querySelector('#bkSrcMode [data-m=orig]').click();
    ok('原书：切回 PDF', !document.querySelector('#bookPdf').hidden && document.querySelector('#bkSrcText').hidden);
    savePrefs({ tts_engine: 'edge', tts_rate: 1 });
    const ti = document.querySelector('#bkChapters .ch-row[data-ch="0"] input[data-f="title"]');
    ti.value = '第一章 改过的名字'; ti.dispatchEvent(new Event('change', { bubbles: true }));
    ok('可以改章名', S.doc.chapters[0].title === '第一章 改过的名字');
    const fp = document.querySelector('#bkFirst'); fp.value = '4'; fp.dispatchEvent(new Event('change'));
    ok('可以改页码对应关系', S.doc.shift === 3 && document.querySelector('#bkShiftHint').textContent.includes('PDF 第 4 页 = P1'));
    ok('有整理好的章节时可以剪藏', !document.querySelector('#btnClip').disabled && !document.querySelector('#bkOverview').disabled);
    await flushSave();
    const reb = await api('open', { path: base + '测试书籍.pdf' });
    ok('书籍整理结果保存到磁盘', reb.doc.chapters[0].title === '第一章 改过的名字' && reb.doc.shift === 3 && reb.doc.chapters[0].md.includes('讲趋势'));
    // 书的 AI 对话（2026-10-29）：「笔记 / AI 对话」二选一；问这一章、问整本书各是一段对话；问答由后台存；回答里的页码能点
    document.querySelector('#bkChapters .ch-row[data-ch="0"] span').click();
    document.querySelector('#bkNoteMode [data-m="chat"]').click();
    ok('书的 AI 对话：切到「AI 对话」，默认问这一章', !document.querySelector('#bkChat').hidden && document.querySelector('#bkView').hidden
      && document.querySelector('#bkChatScope [data-s="chapter"]').classList.contains('on') && document.querySelector('#bkChatHint').textContent.includes('原文全文')
      && getComputedStyle(document.querySelector('#bkEdit')).display === 'none');
    const chKey = `ch:${S.doc.chapters[0].start}-${S.doc.chapters[0].end}`;
    document.querySelector('#bkChips [data-q]').click();
    ok('书的 AI 对话：点常用问题，回答显示出来', await until(() => !S.chatAbort && ((S.doc.book_chat || {})[chKey] || []).length === 2, 15000)
      && document.querySelector('#bkChatBox .msg.assistant').textContent.includes('假 AI'), S.doc.book_chat);
    ok('书的 AI 对话：问答由后台存进书的整理结果', (((await api('doc', { path: S.path })).doc.book_chat || {})[chKey] || []).length === 2);
    document.querySelector('#bookPdf').src = S.viewer;
    document.querySelector('#bkChatBox .msg.assistant .pglink').click();
    ok('书的 AI 对话：点回答里的页码，原书跳到那一页', document.querySelector('#bookPdf').src.endsWith('#page=5'), document.querySelector('#bookPdf').src);
    document.querySelector('#bkChatScope [data-s="book"]').click();
    ok('书的 AI 对话：切到问整本书，是另一段对话', document.querySelector('#bkChatHint').textContent.includes('全部章节') && !!document.querySelector('#bkChatBox .ai-empty'));
    document.querySelector('#bkNoteMode [data-m="note"]').click();
    ok('书的 AI 对话：切回笔记', document.querySelector('#bkChat').hidden && !document.querySelector('#bkView').hidden);
    bkChatScope = 'chapter';
    await api('book_chat_clear', { path: S.path, key: chKey });
    await syncBookFromDisk();
    await window.openPath(base + '演示视频.mp4');
    ok('再打开视频回到视频界面', await until(() => S.mode === 'media' && !document.querySelector('#stageMain').hidden && document.querySelector('#bookMain').hidden && v.readyState >= 1 && !S.resumePending));

    // 播放进度续播
    v.currentTime = 21.3; await wait(300);
    await flushSave();
    document.querySelector('#optLoop').click();
    ok('打开单句循环开关时显示提示', document.querySelector('#optLoop').checked && !document.querySelector('#loopHint').hidden);
    await window.openPath(base + '旧格式.avi');
    ok('换一节课时自动关掉单句循环', !document.querySelector('#optLoop').checked && S.loopIdx === null && document.querySelector('#loopHint').hidden);
    ok('avi 自动转换后可播放', await until(() => v.readyState >= 1 && v.duration > 19, 60000));
    ok('打开 avi 时自动用文件夹没有绑定的老师（空）', S.doc.teacher === '');
    await window.openPath(base + '演示视频.mp4');
    ok('再次打开从上次进度继续', await until(() => Math.abs(v.currentTime - 21.3) < 0.6, 5000), v.currentTime);

    // 文件夹菜单「这里的课不写课程重点」（2026-10-28，灵修类逐字稿课程直接蒸馏）
    const nsTop = S.lib.find(n => n.type === 'folder' && insideDir(S.path, n.path));
    ok('打开的课在媒体库文件夹里，刚打开时照常写课程重点', !!nsTop && S.noSummary === false, S.path);
    folderMenu(nsTop.path, 20, 20);
    ok('文件夹菜单有「这里的课不写课程重点」', !!document.querySelector('#ctxMenu [data-a=nosum]') && !document.querySelector('#ctxMenu [data-a=nosum]').textContent.includes('✓'));
    document.querySelector('#ctxMenu [data-a=nosum]').click();
    ok('设了不写课程重点：文件夹名旁边标「不写重点」，正开着的课跟着知道', await until(() => S.noSummary === true && (libNode(nsTop.path) || {}).no_summary)
      && document.querySelector(`.lib-ghead[data-folder="${CSS.escape(nsTop.path)}"]`).textContent.includes('不写重点'));
    openPipeline();
    ok('设了不写课程重点：一键整理默认不勾「生成课程重点」，旁边写明原因', !document.querySelector('#plSummary').checked
      && document.querySelector('#plSummaryText').textContent.includes('设了不写'));
    document.querySelector('#dlgPipeline').close();
    ok('重新打开这节课：后台也说是不写课程重点', (await api('open', { path: S.path })).no_summary === true);
    folderMenu(nsTop.path, 20, 20);
    ok('菜单里显示已经打开（✓）', document.querySelector('#ctxMenu [data-a=nosum]').textContent.includes('✓'));
    document.querySelector('#ctxMenu [data-a=nosum]').click();
    ok('再点一次改回要写', await until(() => S.noSummary === false && !(libNode(nsTop.path) || {}).no_summary));
    // 整个文件夹排进后台整理（2026-10-28）
    folderMenu(nsTop.path, 20, 20);
    document.querySelector('#ctxMenu [data-a=bgall]').click();
    ok('文件夹菜单「整个文件夹排进后台整理…」：弹出规则对话框，写明里面有几节课，默认步骤勾上', await until(() => document.querySelector('#dlgFolderBg').open)
      && /有 \d+ 节音视频/.test(document.querySelector('#fbInfo').textContent) && document.querySelector('#fbAsr').checked
      && document.querySelector('#fbSummary').checked && document.querySelector('#fbTeacher').options.length > 1, document.querySelector('#fbInfo').textContent);
    ['fbFrames', 'fbSummary', 'fbTranslate'].forEach(id => { document.getElementById(id).checked = false; });
    // 真的排队会整理后面测试要用的课：截下发给后台的请求（后台排队在 test_agent.py 里测）
    const realApi = window.api;
    let sent = null;
    window.api = async (name, body) => name === 'bg_add_folder' ? (sent = body, { added: 0, done: 2, busy: 0, books: 1, errors: 0, items: S.bg }) : realApi(name, body);
    document.querySelector('#dlgFolderBg').close('ok');
    ok('开始排队：按勾选的步骤、语言、老师、豆包模型交给后台，提示结果', await until(() => sent && toasts().some(t => t.includes('没有要整理的课')), 10000)
      && JSON.stringify(sent.steps) === '["transcribe","correct","terms"]' && sent.path === nsTop.path && sent.lang && 'teacher' in sent && 'doubao_model' in sent,
      [sent, toasts()]);
    window.api = realApi;
    await api('library_set', { path: nsTop.path, rule: { steps: ['translate'], lang: 'zh', doubao_model: '', doubao_account: '' }, no_summary: true });
    folderMenu(nsTop.path, 20, 20);
    document.querySelector('#ctxMenu [data-a=bgall]').click();
    ok('再打开：带出上次定的规则', await until(() => document.querySelector('#dlgFolderBg').open) && document.querySelector('#fbInfo').textContent.includes('上次定的规则')
      && document.querySelector('#fbTranslate').checked && !document.querySelector('#fbAsr').checked && !document.querySelector('#fbSummary').checked);
    document.querySelector('#dlgFolderBg').close();
    await api('library_set', { path: nsTop.path, rule: null, no_summary: false });
    await loadLibrary();
    ok('还原文件夹设置', !(libNode(nsTop.path) || {}).no_summary && !(await api('folder_rule', { path: nsTop.path })).rule);
    const bgSaved2 = S.bg;
    S.bg = Array.from({ length: 30 }, (_, i) => ({ id: 'q' + i, path: `D:\\课\\${i}.mp4`, title: '课' + i, status: i ? 'waiting' : 'running', progress: 0 }));
    bgMenu(20, 20);
    ok('后台整理列表：几百节排着时只列前 10 节，有「全部停下」', document.querySelectorAll('#ctxMenu [data-bg-open]').length === 10
      && !!document.querySelector('#ctxMenu [data-bg-all]') && document.querySelector('#ctxMenu').textContent.includes('还排着 30 节'));
    closeMenus(); S.bg = bgSaved2;

    // 剪藏
    await api('settings', { settings: { obsidian: { vault: base, folder: 'clip-test', teacher_subfolder: true } } });
    const okClip = await doClip();
    ok('剪藏到老师子文件夹', okClip && S.doc.clip.path.includes('clip-test\\测试老师\\'), S.doc.clip);
    const libTag = () => (document.querySelector('#libBody .lib-item.active .lib-meta') || {}).textContent || '';
    ok('剪藏后媒体库显示「已剪藏」', await until(() => libTag().includes('已剪藏') && !libTag().includes('待更新')), libTag());
    S.doc.summary = (S.doc.summary || '') + '\n- 剪藏后又加的一条 [00:01]'; markDirty(); await save();
    ok('剪藏后又改了课程重点：显示「待更新剪藏」', await until(() => libTag().includes('待更新剪藏')), libTag());
    ok('字幕标签写明是几句', /字幕 \d+ 句/.test(libTag()), libTag());
    await doClip();
    ok('重新剪藏后恢复「已剪藏」', await until(() => libTag().includes('已剪藏') && !libTag().includes('待更新')), libTag());
    ok('剪藏记下笔记文件的指纹（用来判断 Obsidian 里改过没有）', !!S.doc.clip.file_hash, S.doc.clip);

    await api('teacher_delete', { id: tr.teacher.id });

    // 改完马上关窗（--selftest-close 时会接着走关闭流程，检查这句有没有存下来）
    S.doc.segments[1].text = '关闭前最后一改'; markDirty();
    ok('关闭前有未保存的修改', S.dirty === true);
  } catch (e) {
    R.push('ERROR ' + (e.stack || e));
  }
  return R;
})()])
