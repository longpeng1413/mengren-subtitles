"""Agent 自动整理（任务层 workflow.py、任务记录 tasks.py、命令行 cli.py）的自测。
不联网、不花钱：识别和 AI 都换成假的，检查步骤顺序、合并规则、断点续做、错误分类、保存冲突、命令行 JSON。
运行：.venv\\Scripts\\python tests\\test_agent.py"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path

for _old in Path(tempfile.gettempdir()).glob('mengren-agent-test-*'):
    shutil.rmtree(_old, ignore_errors=True)
TMP = Path(tempfile.mkdtemp(prefix='mengren-agent-test-'))
os.environ['APPDATA'] = str(TMP / 'appdata')
os.environ['LOCALAPPDATA'] = str(TMP / 'local')
ROOT = Path(__file__).resolve().parents[1]
if not (ROOT / 'test-media' / '演示视频.mp4').exists():
    subprocess.run([sys.executable, str(ROOT / 'tests' / 'make_test_media.py')], check=True)
if not (ROOT / 'test-media' / '测试书籍.pdf').exists():
    subprocess.run([sys.executable, str(ROOT / 'tests' / 'make_test_book.py')], check=True)
(TMP / 'appdata' / '猛人学习').mkdir(parents=True)
(TMP / 'appdata' / '猛人学习' / 'settings.json').write_text(json.dumps({'storage_dir': str(TMP / 'store')}), encoding='utf-8')
sys.path.insert(0, str(ROOT / 'app'))

import cli  # noqa: E402
from bridge import Api  # noqa: E402
from core import asr, book, config, doc as docs, frames, library, llm, tasks, workflow as wf  # noqa: E402

passed = 0


def check(name, cond, detail=''):
    global passed
    if not cond:
        print('✗', name, detail)
        raise SystemExit(1)
    passed += 1
    print('✓', name)


# ------------------------------------------------------------ 准备数据 ----
COURSE = TMP / '峰级分析'
(COURSE / '1、基础').mkdir(parents=True)
for n in ('1、第一课', '2、第二课', '3、第三课'):
    shutil.copy(ROOT / 'test-media' / '演示视频.mp4', COURSE / '1、基础' / f'{n}.mp4')
ENG = TMP / 'English Course'
ENG.mkdir()
shutil.copy(ROOT / 'test-media' / '演示视频.mp4', ENG / 'Lesson 1.mp4')
BOOKS = TMP / '书'
BOOKS.mkdir()
shutil.copy(ROOT / 'test-media' / '测试书籍.pdf', BOOKS / '《测试书籍》测试作者.pdf')
OUTSIDE = TMP / '不在库里.mp4'
shutil.copy(ROOT / 'test-media' / '演示视频.mp4', OUTSIDE)

config.update({'providers': {'deepseek': {'key': 'sk-test-deepseek'}, 'openai': {'key': 'sk-test-openai'}, 'doubao': {'key': 'test-doubao'}},
               'chat': 'deepseek', 'correct': 'deepseek', 'translate': 'deepseek', 'vision': 'openai', 'asr_zh': 'doubao', 'asr_en': 'openai',
               'show_tr': False, 'auto_review': True})
teacher = config.save_teacher({'name': '峰级', 'terms': '峰级, 拐点, 三段结构', 'lang': 'zh'})
teacher_en = config.save_teacher({'name': 'English', 'terms': 'trend', 'lang': 'en'})
library.add_folder(str(COURSE))
library.set_folder(str(COURSE), teacher=teacher['id'])
library.add_folder(str(ENG))
library.set_folder(str(ENG), teacher=teacher_en['id'])
library.add_folder(str(BOOKS))
L1 = str(COURSE / '1、基础' / '1、第一课.mp4')
L2 = str(COURSE / '1、基础' / '2、第二课.mp4')
L3 = str(COURSE / '1、基础' / '3、第三课.mp4')
EN = str(ENG / 'Lesson 1.mp4')
BK = str(BOOKS / '《测试书籍》测试作者.pdf')

# ------------------------------------------------------------ 假的识别和 AI ----
calls = Counter()
FAIL = {}  # 步骤 → [还要失败几次, 异常]
seen = {}


def maybe_fail(stage):
    f = FAIL.get(stage)
    if f and f[0] > 0:
        f[0] -= 1
        raise f[1]


def fake_recognise(path, duration, provider, lang, s, cancel, progress, on_cues, teacher=None, title=''):
    calls['asr'] += 1
    seen['asr_provider'] = provider
    seen['asr_pick'] = s['providers']['doubao'].get('pick')
    seen['asr_account'] = s['providers']['doubao'].get('pick_account')
    maybe_fail('transcribe')
    progress(0.5, '识别中…')
    if lang == 'en':
        cues = [{'start': 0.0, 'end': 2.0, 'text': 'Hello everyone.'}, {'start': 2.0, 'end': 4.0, 'text': 'Today we talk about trend.'}]
    else:
        cues = [{'start': 0.0, 'end': 2.0, 'text': '大家好，今天讲峰级。'}, {'start': 2.0, 'end': 4.0, 'text': '风级的入门很重要。'},
                {'start': 4.0, 'end': 6.0, 'text': '这是一个关于峰级的讲论文。'}, {'start': 6.0, 'end': 9.0, 'text': '这就是三段结构。'}]
    on_cues(cues)
    return cues


def fake_complete(settings, name, messages, json_mode=False):
    sys_text = str(messages[0].get('content')) if messages else ''
    if sys_text == llm.CORRECT_SYSTEM:
        calls['correct'] += 1
        maybe_fail('correct')
        return json.dumps({'改动': {'1': '峰级的入门很重要。'}, '存疑': {'2': '疑似识别错误，可能是「讲论」'}}, ensure_ascii=False)
    if sys_text == llm.TRANSLATE_SYSTEM:
        calls['translate'] += 1
        payload = json.loads(messages[-1]['content'].split('\n')[-1])
        return json.dumps({'译文': {k: '中文：' + v for k, v in payload.items()}}, ensure_ascii=False)
    if sys_text == llm.REVIEW_SYSTEM:
        calls['review'] += 1
        maybe_fail('review')
        return json.dumps({'补充': [{'放在': '二、结构', '内容': '- 老师自述：讲了很多年 [00:06]'}]}, ensure_ascii=False)
    if sys_text == llm.TERMS_SYSTEM:
        calls['terms'] += 1
        maybe_fail('terms')
        return json.dumps({'新术语': [{'术语': '再推进', '时间': '00:06', '原话': '这就是三段结构'}, {'术语': '峰级', '时间': '00:00'}],
                           '识别错误': [{'错写': '风级', '正确': '峰级', '时间': '00:02', '原话': '风级的入门'}]}, ensure_ascii=False)
    raise AssertionError('没想到的 AI 调用：' + sys_text[:40])


def fake_stream(settings, name, messages, cancel=None, json_mode=False):
    calls['summary'] += 1
    maybe_fail('summary')
    for piece in ['这节课讲峰级。\n\n## 一、峰级\n\n- 峰级的入门很重要 [00:02]\n\n', '## 二、结构\n\n- 三段结构 [00:06]\n']:
        yield piece


def fake_extract(settings, name, path, duration, segments, cancel=None, progress=lambda f, m=None: None, on_part=None):
    calls['frames'] += 1
    maybe_fail('frames')
    return [{'t': 5.0, 'why': '老师说请看', 'note': '三段结构示意图', 'figure': True}]


def fake_recheck(settings, name, path, items, frames_list, cancel=None, progress=lambda f, m=None: None, on_part=None):
    calls['recheck'] += 1
    maybe_fail('recheck')
    seen['recheck_items'] = items
    return [{'i': it['i'], 'text': it['text'].replace('讲论文', '讲论')} for it in items]


asr.recognise = fake_recognise
llm.complete = fake_complete
llm.stream = fake_stream
frames.extract = fake_extract
frames.recheck_doubts = fake_recheck
RETRY_REAL = wf.RETRY_WAIT
wf.RETRY_WAIT = (0.05, 0.05)

# ------------------------------------------------------------ 查找 ----
items = wf.find(course='峰级')
check('按课程找：媒体库里的文件夹名', [x['path'] for x in items] == [L1, L2, L3], items)
check('按课程 + 第几个找', [x['path'] for x in wf.find(course='峰级', index=2)] == [L2])
check('按子文件夹名找课程', [x['path'] for x in wf.find(course='基础', index=3)] == [L3])
check('按关键词找', [x['path'] for x in wf.find('第三课')] == [L3])
vid = wf.video_id(L2)
check('video_id 稳定，能用它找回文件', vid == wf.video_id(L2.upper()) and wf.resolve(vid) == L2 and len(vid) == 12)
check('找到的结果带课程、编号、时长、状态', {'course_id', 'video_id', 'name', 'path', 'duration', 'state', 'index', 'kind'} <= set(items[0])
      and items[0]['state'] == '未处理' and items[0]['kind'] == 'video')
check('只找还没整理的', len(wf.find(todo=True)) == 5 and len(wf.find(todo=True, kind='book')) == 1)
check('按新增日期找', len(wf.find(since='today')) == 5 and wf.find(since='2999-01-01') == [])
try:
    wf.resolve(str(OUTSIDE))
    check('媒体库以外的文件不处理', False)
except wf.WorkError as e:
    check('媒体库以外的文件不处理', e.code == 'not_in_library' and not e.retryable)

# ------------------------------------------------------------ 加载 ----
inf = wf.info(L1)
check('加载：返回文件、时长、字幕状态，不改任何东西', inf['metadata']['duration'] > 0 and inf['state'] == {'exists': False}
      and not inf['processed'] and inf['teacher'] == teacher['id'] and docs.load(L1) is None, inf)

# ------------------------------------------------------------ 整条流程 ----
progress_seen = []
t1 = wf.process(L1, on_progress=lambda d: progress_seen.append(d.get('stage')))
st = {s['name']: s['status'] for s in t1['stages']}
check('一键整理：全部完成', t1['status'] == 'completed' and t1['error'] is None, t1)
check('步骤顺序和界面的一键整理一样', [s['name'] for s in t1['stages']] == ['transcribe', 'correct', 'frames', 'recheck', 'translate', 'summary', 'review', 'terms'])
check('中文视频不翻译，其余都做了', st == {'transcribe': 'completed', 'correct': 'completed', 'frames': 'completed', 'recheck': 'completed',
                                     'translate': 'skipped', 'summary': 'completed', 'review': 'completed', 'terms': 'completed'}, st)
check('进度会报告', 'correct' in progress_seen and 'summary' in progress_seen)
d = docs.load(L1)
check('字幕写进原来的字幕文件', len(d['segments']) == 4 and d['lang'] == 'zh' and d['provider'].startswith('doubao') and d['teacher'] == teacher['id'])
check('AI 校对：改了的句子记进 fixes（积累术语表用）', d['segments'][1]['text'] == '峰级的入门很重要。'
      and any(f['before'] == '风级的入门很重要。' and f['by'] == 'ai' for f in d['fixes']) and d['corrected'] is True)
check('AI 校对前存了一份历史版本（可以在软件里恢复）', d['versions'][0]['reason'] == 'AI 校对前' and d['versions'][0]['segments'][1]['text'] == '风级的入门很重要。')
check('画面复核：存疑句改正、去掉存疑标记', d['segments'][2]['text'] == '这是一个关于峰级的讲论。' and d['doubts'] == []
      and any(f['by'] == 'frames' for f in d['fixes']) and seen['recheck_items'][0]['before'])
check('找关键帧：插图存进 frames', [f['t'] for f in d['frames']] == [5.0] and d['frames'][0]['figure'])
check('课程重点 + 查漏补缺', '## 一、峰级' in d['summary'] and '老师自述：讲了很多年 [00:06]' in d['summary'])
check('新术语只放进「待勾选」，不写术语表', [x['term'] for x in d['terms_pending']['terms']] == ['再推进']
      and d['terms_pending']['errors'][0]['wrong'] == '风级' and '再推进' not in (config.get_teacher(teacher['id'])['terms']))
check('不剪藏、不替用户标「已校对」', not d.get('clip') and not d.get('reviewed'))
check('每一步记下完成时间', set(d['steps']) >= {'transcribe', 'correct', 'frames', 'recheck', 'summary', 'review', 'terms'})
rec = tasks.load(t1['task_id'])
check('任务记录存到磁盘：每步状态、时间、模型', rec['status'] == 'completed' and rec['stages'][1]['model'].startswith('DeepSeek')
      and rec['stages'][0]['started'] and rec['stages'][0]['finished'] and rec['video_id'] == wf.video_id(L1)
      and rec['options']['steps'] and rec['outputs']['subtitle_file'].endswith('.猛人学习.json'))
check('任务记录里没有密钥', 'sk-test' not in (tasks.TASKS_DIR / f'{t1["task_id"]}.json').read_text(encoding='utf-8'))
check('媒体库能看到整理结果', next(x for x in wf.find(course='峰级') if x['path'] == L1)['state'] == '已整理')

# ------------------------------------------------------------ 再运行 ----
before = calls.copy()
t1b = wf.process(L1)
check('已完成的课再运行：所有步骤跳过，不再调用识别和 AI', t1b['status'] == 'completed' and calls == before
      and all(s['status'] == 'skipped' and s['reason'] for s in t1b['stages']), [(s['name'], s['status'], s['reason']) for s in t1b['stages']])
t1c = wf.run_step(L1, 'summary', force=True)
check('force：指定的步骤重做', t1c['status'] == 'completed' and calls['summary'] == before['summary'] + 1 and calls['asr'] == before['asr'])

# ------------------------------------------------------------ 中途失败、接着做 ----
FAIL['summary'] = [99, llm.LlmError('连接DeepSeek失败：Read timed out')]
c0 = calls.copy()
t2 = wf.process(L2)
st2 = {s['name']: s for s in t2['stages']}
check('中途失败：停在出错的那一步', t2['status'] == 'failed' and t2['error']['stage'] == 'summary' and st2['summary']['status'] == 'failed'
      and st2['terms']['status'] == 'pending', t2['error'])
check('网络错误：可以重试，自动重试了 3 次', t2['error']['error_code'] == 'network' and t2['error']['retryable'] and st2['summary']['retries'] == 3
      and calls['summary'] - c0['summary'] == 4)
d2 = docs.load(L2)
check('失败前完成的步骤已经保存', d2['corrected'] and len(d2['segments']) == 4 and not d2.get('summary'))
FAIL.pop('summary')
c1 = calls.copy()
t2r = wf.resume(t2['task_id'])
st2r = {s['name']: s['status'] for s in t2r['stages']}
check('接着做：从失败的那一步继续，不重新识别、不重新校对', t2r['status'] == 'completed' and calls['asr'] == c1['asr']
      and calls['correct'] == c1['correct'] and calls['summary'] == c1['summary'] + 1
      and st2r['transcribe'] == 'skipped' and st2r['summary'] == 'completed', st2r)

FAIL['correct'] = [1, llm.LlmError('DeepSeek出错（HTTP 503）：busy')]
t3 = wf.process(L3, steps=['transcribe', 'correct'])
check('临时出错：自动重试后成功', t3['status'] == 'completed' and t3['stages'][1]['retries'] == 1 and docs.load(L3)['corrected'])
FAIL.pop('correct', None)

# ChatGPT「太忙」（数据流里的错误）：等一会儿自动重试，进度里写「X 秒后第 N 次重试」
check('重试等待逐步加长：10 秒、1 分钟、3 分钟', wf.RETRIES == 3 and tuple(RETRY_REAL) == (10, 60, 180))
FAIL['correct'] = [2, llm.LlmError('ChatGPT 出错：Our servers are currently overloaded. Please try again later.')]
msgs = []
t3b = wf.run_step(L3, 'correct', force=True, on_progress=lambda d: msgs.append(d.get('message') or ''))
check('ChatGPT 太忙：自动重试 2 次后成功', t3b['status'] == 'completed' and t3b['stages'][0]['retries'] == 2, t3b.get('error'))
check('重试时进度写清楚「X 秒后第 N 次重试」', any('秒后第 1 次重试' in m for m in msgs)
      and any('第 2 次重试' in m for m in msgs), msgs[-6:])
FAIL.pop('correct', None)

# 额度用完（ChatGPT 5 小时用量限额）：马上停，不重试
for i, quota_err in enumerate([llm.LlmError('ChatGPT 额度不足：You have hit your usage limit（usage_limit_reached）'),
                               asr.QuotaError('豆包额度不足或账户欠费：所有账号的几种识别模型都标记成用完了')]):
    stage_ = 'correct' if i == 0 else 'transcribe'
    FAIL[stage_] = [99, quota_err]
    c3 = calls.copy()
    tq = wf.run_step(L3, stage_, force=True)
    n_calls = calls['correct' if i == 0 else 'asr'] - c3['correct' if i == 0 else 'asr']
    check(f'额度用完（{"ChatGPT" if i == 0 else "豆包"}）：马上停、不重试', tq['status'] == 'failed' and tq['error']['error_code'] == 'quota'
          and not tq['error']['retryable'] and tq['stages'][0]['retries'] == 0 and n_calls == 1, (tq.get('error'), n_calls))
    FAIL.pop(stage_)

# 等着重试的时候取消：不再试
wf.RETRY_WAIT = (30,)
FAIL['correct'] = [99, llm.LlmError('ChatGPT 出错：Our servers are currently overloaded. Please try again later.')]
stop = threading.Event()
threading.Timer(0.5, stop.set).start()
c4, t_0 = calls.copy(), time.time()
tc = wf.run_step(L3, 'correct', force=True, cancel=stop)
check('等着重试时取消：马上停，不会再多试一次', tc['status'] == 'cancelled' and calls['correct'] - c4['correct'] == 1
      and time.time() - t_0 < 10, (tc['status'], calls['correct'] - c4['correct'], time.time() - t_0))
FAIL.pop('correct')
wf.RETRY_WAIT = (0.05, 0.05)

# AI 校对失败：什么都不写（不会出现「报错了，结果却出现了一半」）
seg_before = json.dumps(docs.load(L3)['segments'], ensure_ascii=False)
ver_before = len(docs.load(L3).get('versions') or [])
FAIL['correct'] = [99, llm.LlmError('校对结果格式不对，请重试或换一个 AI 服务')]
t4 = wf.run_step(L3, 'correct', force=True)
check('AI 校对失败：字幕一句都没改，也没多出历史版本', t4['status'] == 'failed' and t4['error']['error_code'] == 'bad_output'
      and json.dumps(docs.load(L3)['segments'], ensure_ascii=False) == seg_before and len(docs.load(L3).get('versions') or []) == ver_before)
FAIL.pop('correct')

# 不可重试的错误
config.update({'chat': 'claude'})  # Claude 没填 Key（设置里空的 Key 不会覆盖已有的，所以换一个没填的服务）
c2 = calls.copy()
t5 = wf.run_step(L3, 'summary')
check('缺 API Key：不重试，错误码 config', t5['status'] == 'failed' and t5['error']['error_code'] == 'config' and not t5['error']['retryable']
      and t5['stages'][0]['retries'] == 0 and calls['summary'] == c2['summary'])
config.update({'chat': 'deepseek'})
check('错误分类：服务方太忙、临时出错可以重试', all(tasks.classify(e)[:2] == ('overloaded', True) for e in [
    llm.LlmError('ChatGPT 出错：Our servers are currently overloaded. Please try again later.'),
    llm.LlmError('ChatGPT 出错：Service temporarily unavailable'),
    llm.LlmError('DeepSeek出错（HTTP 503）：busy'), llm.LlmError('Claude 出错（529）：Overloaded'), llm.LlmError('OpenAI出错（HTTP 502）：x'),
    asr.AsrError('豆包识别失败（55000000）：{"error":"kiteX processing ... remote or network error[remote]: error_code=1204"}'),
    asr.AsrError('豆包识别失败（55000031）：server busy')]), [tasks.classify(e)[:2] for e in [llm.LlmError('Claude 出错（529）：Overloaded')]])
check('错误分类：额度问题不重试（即使也说了「稍后再试」）', all(tasks.classify(e)[:2] == ('quota', False) for e in [
    llm.LlmError('ChatGPT 额度不足：You have hit your usage limit. Try again later.'),
    llm.LlmError('ChatGPT 出错：usage_limit_reached'),
    asr.QuotaError('豆包额度不足或账户欠费：x')]))
check('错误分类', tasks.classify(llm.LlmError('DeepSeek鉴权失败：bad key'))[:2] == ('auth', False)
      and tasks.classify(asr.AsrError('豆包额度不足或账户欠费：x'))[:2] == ('quota', False)
      and tasks.classify(asr.AsrError('连接豆包失败：timeout'))[:2] == ('network', True)
      and tasks.classify(__import__('core.media', fromlist=['x']).MediaError('无法读取这个文件：moov atom not found'))[:2] == ('media', False)
      and tasks.classify(FileNotFoundError('x'))[0] == 'not_found' and tasks.classify(KeyError('x'))[:2] == ('internal', False))

# 「软」步骤失败不影响后面
FAIL['review'] = [99, llm.LlmError('查漏结果格式不对，请重试或换一个 AI 服务')]
t6 = wf.process(L3)
check('查漏补缺失败：不影响后面的新术语，写进 warnings', t6['status'] == 'completed' and {s['name']: s['status'] for s in t6['stages']}['review'] == 'failed'
      and {s['name']: s['status'] for s in t6['stages']}['terms'] == 'completed' and t6['warnings'], t6['warnings'])
FAIL.pop('review')

# ------------------------------------------------------------ 换模型 ----
t7 = wf.process(EN, provider='siliconflow', steps=['transcribe'])
check('换识别服务：用指定的', t7['status'] == 'failed' and t7['error']['error_code'] == 'config')  # 硅基流动没填 Key
t7 = wf.process(EN, provider='openai', steps=['transcribe'])
check('换识别服务：英文用 OpenAI', t7['status'] == 'completed' and seen['asr_provider'] == 'openai' and docs.load(EN)['lang'] == 'en')
PICK = TMP / '选模型.mp4'
shutil.copy(ROOT / 'test-media' / '演示视频.mp4', PICK)
tp = wf.run_step(str(PICK), 'transcribe', force=True, check_library=False, doubao_model='flash')
check('这次指定豆包用哪种模型（界面的下拉框、命令行 --doubao-model）', tp['status'] == 'completed' and seen['asr_pick'] == 'flash')
wf.run_step(str(PICK), 'transcribe', force=True, check_library=False, doubao_model='auto')
check('这次选「自动」：不管设置里选了什么，都按顺序自动换', seen['asr_pick'] == '')
wf.run_step(str(PICK), 'transcribe', force=True, check_library=False)
check('没指定：按设置里选的（默认自动）', seen['asr_pick'] == '')
tp = wf.run_step(str(PICK), 'transcribe', force=True, check_library=False, doubao_model='nosuch')
tp = wf.run_step(str(PICK), 'transcribe', force=True, check_library=False, doubao_account='1')
check('这次指定豆包用哪个账号（第几个、名字都行）', tp['status'] == 'completed' and seen['asr_account'] == 'main')
tp = wf.run_step(str(PICK), 'transcribe', force=True, check_library=False, doubao_account='没有这个账号')
check('没有这个豆包账号：直接报错，列出有哪些', tp['status'] == 'failed' and tp['error']['error_code'] == 'not_supported'
      and '1（账号 1）' in tp['error']['message'], tp['error'])
check('不认识的豆包模型：直接报错', tp['status'] == 'failed' and tp['error']['error_code'] == 'not_supported')

# 后台整理队列（界面一键整理时可以切到别的课）：一节一节做，规则还是 workflow.process
from core import bgqueue  # noqa: E402
PICK2 = TMP / '后台第二节.mp4'
shutil.copy(PICK, PICK2)
shutil.copy(docs.sidecar(str(PICK)), docs.sidecar(str(PICK2)))
cb = calls.copy()
a1 = Api().call('bg_add', {'path': str(PICK), 'steps': ['summary'], 'lang': 'zh'})
a2 = Api().call('bg_add', {'path': str(PICK2), 'steps': ['summary']})
try:
    bgqueue.add(str(PICK), ['summary'])
    dup = False
except ValueError:
    dup = True
check('后台整理：同一节课不能重复排', a1['ok'] and a2['ok'] and dup)
PICK3 = TMP / '后台第三节.mp4'
shutil.copy(PICK, PICK3)
a3 = Api().call('bg_add', {'path': str(PICK3)})
check('排着还没开始的可以取消', Api().call('bg_cancel', {'id': a3['item']['id']})['stopped'] == 1)
t_end = time.time() + 30
while bgqueue.busy() and time.time() < t_end:
    time.sleep(0.2)
bq = {Path(x['path']).name: x for x in Api().call('bg_list', {})['items']}
check('后台整理：两节一节一节做完，结果写进各自的字幕文件，取消的没做',
      bq[PICK.name]['status'] == 'done' and bq[PICK2.name]['status'] == 'done' and bq[PICK3.name]['status'] == 'cancelled'
      and bq[PICK2.name]['task']['success'] and calls['summary'] - cb['summary'] == 2
      and bq[PICK.name]['finished'] <= bq[PICK2.name]['finished'] and docs.load(str(PICK2))['summary'], bq)
check('后台整理：没指定步骤就按一键整理默认的、只做还没做完的', Api().call('bg_add', {'path': str(PICK3)})['item']['steps'] is None)
bgqueue.cancel()
t_end = time.time() + 30
while bgqueue.busy() and time.time() < t_end:  # 等它停下，免得后面数调用次数的测试被它打扰
    time.sleep(0.2)
check('全部停下', not bgqueue.busy())
Api().call('bg_clear', {})


def wait_bg():
    t_end = time.time() + 60
    while bgqueue.busy() and time.time() < t_end:
        time.sleep(0.2)


# 整个文件夹排进后台整理（2026-10-28，几百节课的文件夹）：规则记在文件夹上，只排还没整理完的
BATCH = TMP / '灵修课'
(BATCH / '第一季').mkdir(parents=True)
for n in ('1、觉察.mp4', '2、臣服.mp4', '第一季/3、喜悦.mp4'):
    shutil.copy(ROOT / 'test-media' / '演示视频.mp4', BATCH / n)
B1, B2, B3 = (str(BATCH / n) for n in ('1、觉察.mp4', '2、臣服.mp4', '第一季/3、喜悦.mp4'))
pre = docs.new(B3, {'duration': 9})
pre.update(segments=[{'start': 0.0, 'end': 2.0, 'text': '大家好', 'tr': ''}], corrected=True, steps={'transcribe': 1, 'correct': 1, 'terms': 1})
docs.save(B3, pre)
library.add_folder(str(BATCH))
info = Api().call('folder_rule', {'path': str(BATCH)})
check('整个文件夹排队：对话框知道里面有几节课，还没有规则', info['lessons'] == 3 and info['books'] == 0 and info['rule'] is None, info)
rule_req = {'path': str(BATCH), 'steps': ['transcribe', 'correct', 'terms'], 'lang': 'zh', 'teacher': teacher['id'],
            'doubao_model': 'flash', 'doubao_account': ''}
FAIL['transcribe'] = [1, asr.QuotaError('豆包额度用完了（测试）')]
r1 = Api().call('bg_add_folder', rule_req)
check('整个文件夹排队：只排还没整理完的（已经按规则做完的跳过）', r1['ok'] and r1['added'] == 2 and r1['done'] == 1, r1)
check('整个文件夹排队：规则、老师、不写课程重点记在文件夹上，下层沿用',
      library.folder_rule(B3)['steps'] == ['transcribe', 'correct', 'terms'] and library.folder_teacher(B3) == teacher['id']
      and library.folder_no_summary(B3) and Api().call('folder_rule', {'path': str(BATCH)})['rule']['doubao_model'] == 'flash')
wait_bg()
bq = {Path(x['path']).name: x for x in Api().call('bg_list', {})['items']}
check('额度用完：这一节没做完，同一批剩下的停下（不一节接一节地失败）', bq['1、觉察.mp4']['status'] == 'failed'
      and bq['1、觉察.mp4']['batch_stopped'] == 1 and bq['2、臣服.mp4']['status'] == 'cancelled', bq)
FAIL.pop('transcribe', None)
cb = calls.copy()
r2 = Api().call('bg_add_folder', rule_req)
wait_bg()
check('额度恢复后再排一次：从没做完的接着做，用规则里选的豆包模型，不写课程重点', r2['added'] == 2 and calls['asr'] - cb['asr'] == 2
      and seen['asr_pick'] == 'flash' and calls['summary'] == cb['summary'] and all(docs.load(p)['corrected'] for p in (B1, B2))
      and not docs.load(B1).get('summary'), (r2, calls - cb))
r3 = Api().call('bg_add_folder', rule_req)
check('都整理完了：再排一次没有要排的', r3['added'] == 0 and r3['done'] == 3, r3)
B4 = str(BATCH / '第一季' / '4、新加的.mp4')
shutil.copy(ROOT / 'test-media' / '演示视频.mp4', B4)
a4 = Api().call('bg_add', {'path': B4})
check('文件夹定了规则：单节课右键「排进后台整理」也按它', a4['item']['steps'] == ['transcribe', 'correct', 'terms'], a4['item'])
bgqueue.cancel()
wait_bg()
check('整个文件夹排队：没勾步骤不排', not Api().call('bg_add_folder', dict(rule_req, steps=[]))['ok'])
Api().call('bg_clear', {})
library.remove('folder', str(BATCH))
t8 = wf.process(EN, model='local', steps=['transcribe'], redo=['transcribe'])
check('本地模型：接口留着，明确报「还不支持」', t8['status'] == 'failed' and t8['error']['error_code'] == 'not_supported')
t9 = wf.process(EN)
de = docs.load(EN)
check('英文视频：自动翻译，中英同时显示', {s['name']: s['status'] for s in t9['stages']}['translate'] == 'completed'
      and all(s['tr'].startswith('中文：') for s in de['segments']) and config.load()['show_tr'] is True)

# ------------------------------------------------------------ 书 ----
book_calls = Counter()
book.organize_chapter = lambda s, n, path, info, c, meta, *a, **k: (book_calls.update(['org']), f'## 一、本章主题\n\n{c["title"]}（P1）')[1]
book.review_chapter = lambda s, n, info, c, md, meta, teacher=None: (md, 0, 0)
book.verify_doubts = lambda s, n, path, info, c, md, meta, teacher=None, vision=True: (md, 0)
book.overview = lambda s, n, meta, chs, files, info=None: (book_calls.update(['overview']), '## 全书总览\n\n两章。')[1]
tb = wf.process(BK)
db = docs.load(BK)
check('书：逐章整理 + 全书总览，不剪藏', tb['status'] == 'completed' and all(c['md'] for c in db['chapters']) and db['overview']
      and not db.get('clip') and book_calls['org'] == len(db['chapters']), tb)
tb2 = wf.process(BK)
check('书：再运行全部跳过', tb2['status'] == 'completed' and book_calls['org'] == len(db['chapters']) and book_calls['overview'] == 1)

# ------------------------------------------------------------ 课程重点比字幕旧 ----
d = docs.load(L1)
check('生成课程重点时记下用的哪一版字幕：刚生成时不提醒', isinstance(d.get('summary_src'), list) and docs.summary_changes(d) == 0
      and wf.summarize(L1)['summary_changes'] == 0)
for i in range(3):
    d['segments'][i]['text'] += '（人工改）'
docs.save(L1, d)
d = docs.load(L1)
check('之后改了 3 句：提醒重新生成（媒体库也标出来）', docs.summary_changes(d) == 3 and wf.summarize(L1)['summary_changes'] == 3
      and next(x for x in wf.find(course='峰级') if x['path'] == L1)['summary_outdated'])
check('保存时把「改了几句」告诉界面', docs.save(L1, d)['summary_changes'] == 3)
wf.run_step(L1, 'summary', force=True)
check('重新生成后不再提醒', docs.summary_changes(docs.load(L1)) == 0 and not next(x for x in wf.find(course='峰级') if x['path'] == L1)['summary_outdated'])
# 课程重点里手动插的截图（2026-11-02）：重新生成后放回去，按出处时间找位置；用户已经删掉的不放回
d = docs.load(L1)
d['summary'] = d['summary'].replace('## 一、峰级', '![[画面 00:05]]\n\n## 一、峰级') + '\n\n![[画面 00:03]]'
d['sum_shots'] = [3, 7, 5]  # 00:07 用户插过又删掉了
docs.save(L1, d)
wf.run_step(L1, 'summary', force=True)
d = docs.load(L1)
check('重新生成课程重点：手动插的截图放回出处时间最接近的那条后面，删掉的不放回', d['sum_shots'] == [3, 5]
      and '- 峰级的入门很重要 [00:02]\n\n![[画面 00:03]]\n\n![[画面 00:05]]\n\n## 二、结构' in d['summary'] and '00:07' not in d['summary'], d['summary'])
old = {'summary': '以前生成的重点', 'segments': [{'start': 0, 'end': 1, 'text': '甲改', 'tr': ''}, {'start': 1, 'end': 2, 'text': '乙改', 'tr': ''}, {'start': 2, 'end': 3, 'text': '丙', 'tr': ''}],
       'fixes': [{'t': 0, 'before': '甲', 'after': '甲改', 'by': 'manual'}, {'t': 1, 'before': '乙', 'after': '乙改', 'by': 'manual'},
                 {'t': 2, 'before': '丁', 'after': '丙', 'by': 'ai'}, {'t': 3, 'before': '戊', 'after': '撤销掉的', 'by': 'manual'}]}
check('以前生成的课程重点（没记字幕版本）：按手动改过的句子估算', docs.summary_changes(old) == 2)
check('没有课程重点、书：不提醒', docs.summary_changes(dict(old, summary='')) == 0 and docs.summary_changes({'kind': 'book', 'summary': 'x'}) == 0)

# ------------------------------------------------------------ 界面调用 ----
# 界面上的整理按钮也走 workflow.process（caller='gui'）：不限媒体库、边做边显示、用户自己选的选项
shown = []
tg = wf.process(str(OUTSIDE), steps=['transcribe', 'correct', 'terms'], caller='gui', check_library=False, on_items=shown.extend)
stg = {s['name']: s for s in tg['stages']}
check('界面：媒体库以外打开的文件也能整理', tg['status'] == 'completed' and docs.load(str(OUTSIDE))['segments'])
check('界面：识别出的字幕边做边显示', [x['text'] for x in shown if x['kind'] == 'cue'] == [s['text'] for s in docs.load(str(OUTSIDE))['segments']])
check('没选老师：不做 AI 校对、不找新术语（跳过并写明原因）', stg['correct']['status'] == 'skipped' and stg['terms']['status'] == 'skipped'
      and '没有指定老师' in stg['terms']['reason'])
tg = wf.process(str(OUTSIDE), steps=['correct'], caller='gui', check_library=False, allow_no_teacher=True)
check('界面：用户确认后没选老师也校对', tg['status'] == 'completed' and docs.load(str(OUTSIDE))['corrected'])
tg = wf.process(str(OUTSIDE), steps=['translate'], caller='gui', check_library=False)
check('中文字幕默认不翻译', tg['stages'][0]['status'] == 'skipped')
tg = wf.process(str(OUTSIDE), steps=['translate'], caller='gui', check_library=False, any_lang=True)
check('界面：用户自己选了翻译，中文也翻', tg['status'] == 'completed' and all(s['tr'] for s in docs.load(str(OUTSIDE))['segments']))
c4 = calls.copy()
tg = wf.process(str(OUTSIDE), steps=['translate'], caller='gui', check_library=False, any_lang=True)
check('已经都有译文：跳过', tg['stages'][0]['status'] == 'skipped' and calls['translate'] == c4['translate'])
tg = wf.process(str(OUTSIDE), steps=['translate'], caller='gui', check_library=False, any_lang=True, translate_all=True)
check('界面：「全部重新翻译」', tg['status'] == 'completed' and calls['translate'] > c4['translate'])
live = []
llm.stream = fake_stream
tg = wf.process(str(OUTSIDE), steps=['summary'], caller='gui', check_library=False, on_items=live.extend)
check('界面：课程重点边写边显示', ''.join(x['text'] for x in live if x['kind'] == 'text') == docs.load(str(OUTSIDE))['summary'].strip() + '\n'
      or ''.join(x['text'] for x in live if x['kind'] == 'text').strip() == docs.load(str(OUTSIDE))['summary'])
BK2 = str(BOOKS / '《第二本》.pdf')
shutil.copy(ROOT / 'test-media' / '测试书籍.pdf', BK2)
live = []
tg = wf.process(BK2, steps=['organize'], chapters=[1], on_items=live.extend)
d2b = docs.load(BK2)
check('界面：只整理勾选的章节', tg['status'] == 'completed' and not d2b['chapters'][0]['md'] and d2b['chapters'][1]['md'])
chi = [x for x in live if x['kind'] == 'chapter']
check('界面：每整理好一章就推给界面（不用等全部做完）', len(chi) == 1 and chi[0]['i'] == 1 and chi[0]['md'] == d2b['chapters'][1]['md']
      and (chi[0]['title'], chi[0]['start'], chi[0]['end']) == (d2b['chapters'][1]['title'], d2b['chapters'][1]['start'], d2b['chapters'][1]['end']), chi)
tg = wf.process(BK2, steps=['overview'])
check('Agent：章节没整理完不写总览（可以接着做）', tg['status'] == 'failed' and tg['error']['retryable'])
tg = wf.process(BK2, steps=['overview'], allow_partial=True)
check('界面：用户可以先写全书总览', tg['status'] == 'completed' and docs.load(BK2)['overview'])
try:
    wf.process(L1, nosuch=True)
    check('不认识的参数直接报错', False)
except TypeError:
    check('不认识的参数直接报错', True)
r = Api().call('steps', {'path': str(OUTSIDE), 'steps': ['nosuchstep']})
check('界面入口：步骤名写错直接报错', not r['ok'] and 'nosuchstep' in r['error'])
r = Api().call('doc', {'path': str(OUTSIDE)})
check('界面入口：重新读取字幕文件', r['ok'] and r['doc']['segments'] and r['busy'] is None)

# ------------------------------------------------------------ 保存冲突 ----
d = docs.load(L1)
rev = d['rev']
d2 = docs.load(L1)
d2['summary'] += '\n- Agent 后来加的'
docs.save(L1, d2, base_rev=rev)
d['summary'] = '界面里的旧版本'
r = docs.save(L1, d, base_rev=rev)
check('保存冲突：界面拿着旧版本，不覆盖 Agent 的结果', r.get('conflict') and 'Agent 后来加的' in docs.load(L1)['summary'])
api = Api()
r = api.call('save', {'path': L1, 'doc': d, 'base_rev': rev})
check('界面保存：返回 conflict 让界面问用户', r['ok'] and r.get('conflict'))
r = api.call('save', {'path': L1, 'doc': d, 'base_rev': rev, 'force': True})
check('用户选择覆盖：force 保存', r['ok'] and not r.get('conflict') and docs.load(L1)['summary'] == '界面里的旧版本' and r['rev'] == rev + 2)
r = api.call('save', {'path': L1, 'doc': d})
check('以前的调用方式（不带版本号）照样能保存', r['ok'] and not r.get('conflict'))

# ------------------------------------------------------------ 正在整理 ----
key = docs.media_key(L2)
tasks.claim(key, 'other-task')
t10 = wf.process(L2)
check('同一节课正在被别的任务整理：不重复处理', t10['status'] == 'failed' and t10['error']['error_code'] == 'busy')
r = api.call('open', {'path': L2})
check('界面打开正在整理的课：会提示', r['ok'] and r['busy'] and r['busy']['task_id'] == 'other-task')
tasks.release(key, 'other-task')
(tasks.BUSY_DIR / f'{key}.busy').write_text(json.dumps({'task_id': 'dead', 'pid': 999999, 'since': 'x'}), encoding='utf-8')
check('留下来的旧标记（程序已经不在了）不算', tasks.busy(key) is None and wf.process(L2)['status'] == 'completed')
r = api.call('open', {'path': L2})
check('整理完以后界面不再提示', r['ok'] and r['busy'] is None)

# 中途退出的任务
fake = {'task_id': '20000101-000000000-dead', 'status': 'running', 'pid': 999999, 'path': L2, 'stages': [{'name': 'summary', 'status': 'running'}]}
(tasks.TASKS_DIR / f'{fake["task_id"]}.json').write_text(json.dumps(fake), encoding='utf-8')
check('程序中途退出的任务：显示为失败（被打断），可以接着做', tasks.load(fake['task_id'])['status'] == 'failed'
      and tasks.load(fake['task_id'])['error']['error_code'] == 'interrupted')

# 取消
started = threading.Event()


def slow_stream(*a, **k):
    started.set()
    for _ in range(100):
        time.sleep(0.1)
        yield 'x'


llm.stream = slow_stream
holder = {}
th = threading.Thread(target=lambda: holder.setdefault('r', wf.run_step(L3, 'summary', force=True)))
th.start()
started.wait(5)
time.sleep(0.3)
tid = next(t['task_id'] for t in tasks.recent(5) if t['status'] == 'running')
check('取消：通知正在运行的任务', tasks.request_cancel(tid))
th.join(15)
check('取消：任务停下，状态 cancelled，课程重点没被改', holder['r']['status'] == 'cancelled' and docs.load(L3)['summary'] != 'x' * 100)
llm.stream = fake_stream


# ------------------------------------------------------------ 清理 ----
from core import cache  # noqa: E402
old_lock = tasks.BUSY_DIR / 'oldlesson.lock'
old_lock.write_bytes(b'')
os.utime(old_lock, (time.time() - 3 * 86400, time.time() - 3 * 86400))
dead_busy = tasks.BUSY_DIR / 'deadlesson.busy'
dead_busy.write_text(json.dumps({'task_id': 'x', 'pid': 999999, 'since': 'x'}), encoding='utf-8')
fresh_lock = tasks.BUSY_DIR / 'fresh.lock'
fresh_lock.write_bytes(b'')
with docs.write_lock(L1):  # 正被占着的锁文件删不掉，跳过
    held = tasks.BUSY_DIR / f'{docs.media_key(L1)}.lock'
    os.utime(held, (time.time() - 3 * 86400, time.time() - 3 * 86400))
    cache.clean_marks()
    check('清理：旧的锁文件、程序已经不在的「正在整理」标记删掉；新的、正被占着的保留',
          not old_lock.exists() and not dead_busy.exists() and fresh_lock.exists() and held.exists())
(config.CACHE_DIR / 'audio').mkdir(parents=True, exist_ok=True)
old_wav = config.CACHE_DIR / 'audio' / 'old.wav'
old_wav.write_bytes(b'0' * 100)
os.utime(old_wav, (time.time() - 5 * 86400, time.time() - 5 * 86400))
buf = io.StringIO()
with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
    cli.main(['run', 'summary', L1, '--json', '--quiet'])
check('命令行整理时也清理缓存（以前只有软件启动时清）', not old_wav.exists())

# ------------------------------------------------------------ 还没整理完的课 ----
# 2026-10-16：以前「还没整理的课」只看有没有课程重点。「37、三角形突破应用逻辑」写完课程重点后 ChatGPT 额度用完，
# 查漏、新术语都没做，--todo 却不再列出它。现在按一键整理默认的步骤逐步判断（和真正整理时跳过的规则是同一个函数）。
def lesson(name):
    p = COURSE / '1、基础' / name
    shutil.copy(ROOT / 'test-media' / '演示视频.mp4', p)
    return str(p)


def item_of(path):
    return next(x for x in wf.find(course='峰级') if x['path'] == path)


L5 = lesson('5、第五课.mp4')
FAIL['review'] = [99, llm.LlmError('ChatGPT 额度不足：You have hit your usage limit（usage_limit_reached）')]
FAIL['terms'] = [99, llm.LlmError('ChatGPT 额度不足：You have hit your usage limit（usage_limit_reached）')]
t11 = wf.process(L5)
FAIL.pop('review')
FAIL.pop('terms')
check('额度用完：课程重点写好了，查漏、新术语没做', t11['status'] == 'failed' and t11['error']['stage'] == 'terms'
      and (docs.load(L5).get('summary') or '').strip() and 'review' not in docs.load(L5)['steps'])
it5 = item_of(L5)
check('有课程重点、但查漏和新术语没做完：算没整理完（--todo 会选上）', it5['todo'] and it5['pending_steps'] == ['review', 'terms']
      and it5['state'] == '未整理完' and L5 in [x['path'] for x in wf.find(course='峰级', todo=True)], it5)
inf5 = wf.info(L5)
check('info：只列还要做的步骤，做过的、不用做的写明原因', not inf5['processed'] and inf5['next_steps'] == ['review', 'terms']
      and inf5['skipped_steps']['summary'] == '已经有课程重点' and '不用翻译' in inf5['skipped_steps']['translate'], inf5['next_steps'])
check('预览（--dry-run 用的）：同样只列查漏、新术语；指定重做的照样列', wf.plan(L5) == (inf5['next_steps'], inf5['skipped_steps'])
      and wf.plan(L5, steps=['summary', 'review'], redo=['summary'])[0] == ['summary', 'review'])
c5 = calls.copy()
t12 = wf.process(L5)
st12 = {s['name']: s['status'] for s in t12['stages']}
check('再整理：只做没做完的查漏和新术语，不重新识别、校对、写重点', t12['status'] == 'completed'
      and calls - c5 == Counter({'review': 1, 'terms': 1})
      and [n for n, v in st12.items() if v == 'completed'] == ['review', 'terms'], (calls - c5, st12))
it5 = item_of(L5)
check('补完以后：不再算没整理完', not it5['todo'] and it5['pending_steps'] == [] and it5['state'] == '已整理' and wf.info(L5)['processed'])

L6 = lesson('6、第六课.mp4')
FAIL['review'] = [99, llm.LlmError('查漏结果格式不对，请重试或换一个 AI 服务')]
t13 = wf.process(L6)
FAIL.pop('review')
it6 = item_of(L6)
check('只有「查漏」这种失败了不影响后面的步骤失败：任务算完成，但这节课仍算没整理完', t13['status'] == 'completed' and t13['warnings']
      and it6['todo'] and it6['pending_steps'] == ['review'] and it6['state'] == '未整理完', it6)
c6 = calls.copy()
wf.process(L6)
check('再整理只做查漏', calls - c6 == Counter({'review': 1}) and not item_of(L6)['todo'])

# 以前整理过的旧字幕文件（没有 steps 记录）：不能全部误判成没整理完
L7 = lesson('7、第七课.mp4')
old7 = docs.new(L7, {'duration': 9})
old7.update(segments=[{'start': 0.0, 'end': 2.0, 'text': '大家好', 'tr': ''}], corrected=True, teacher=teacher['id'],
            summary='## 一、以前写的重点\n\n- 大家好 [00:00]', doubts=[{'t': 0.0, 'reason': '以前标的', 'text': '大家好'}])
docs.save(L7, old7)
check('旧字幕文件：有课程重点、没有 steps 记录 → 算整理完了（不重新找插图、复核、查漏、找新术语）', 'steps' not in docs.load(L7)
      and not item_of(L7)['todo'] and item_of(L7)['state'] == '已整理' and wf.info(L7)['next_steps'] == [], item_of(L7))
c7 = calls.copy()
check('旧字幕文件：整理时同样全部跳过', wf.process(L7)['status'] == 'completed' and calls == c7)
L8 = lesson('8、第八课.mp4')
old8 = docs.new(L8, {'duration': 9})
old8.update(segments=[{'start': 0.0, 'end': 2.0, 'text': '大家好', 'tr': ''}], corrected=True, teacher=teacher['id'])
docs.save(L8, old8)
check('旧字幕文件：只校对过、没写课程重点 → 没整理完，从找插图往后做（没有存疑句不用复核）',
      item_of(L8)['pending_steps'] == ['frames', 'summary', 'review', 'terms'] and item_of(L8)['state'] == '有字幕', item_of(L8))
# 媒体库文件夹设了「不写课程重点」（灵修类逐字稿课程，2026-10-28）
library.set_folder(str(COURSE), no_summary=True)
check('文件夹设了不写课程重点：课程重点、查漏不算没做', item_of(L8)['pending_steps'] == ['frames', 'terms']
      and wf.info(L8)['skipped_steps'].get('summary') == wf.NO_SUMMARY, (item_of(L8), wf.info(L8)['skipped_steps']))
c8 = calls.copy()
wf.process(L8, steps=['summary'])
check('文件夹设了不写课程重点：只做还没做完的步骤时跳过，不调 AI', calls == c8 and not docs.load(L8).get('summary'))
wf.process(L8, steps=['summary'], redo=['summary'])
check('文件夹设了不写课程重点：界面上勾选（重做）时照样写', (docs.load(L8).get('summary') or '').strip())
library.set_folder(str(COURSE), no_summary=False)

# 不该做的步骤不算没做：中文不翻译、音频没有画面、没有老师不校对不找新术语
S0 = config.load()
done_zh = {'segments': [{'start': 0, 'end': 1, 'text': '你好', 'tr': ''}], 'lang': 'zh', 'summary': 'x', 'steps': {'summary': 1, 'review': 1}}
audio_no_teacher = wf.Facts(S0, teacher=None, has_video=False, vision=('openai', ''))
check('音频、没有老师、中文：课程重点和查漏做完就算整理完', wf.pending_steps(done_zh, 'video', audio_no_teacher)[0] == []
      and set(wf.pending_steps(done_zh, 'video', audio_no_teacher)[1]) == set(wf.VIDEO_STAGES))
check('还没有任何结果的音频（没有老师）：生成字幕 → 课程重点 → 查漏', wf.pending_steps({}, 'video', audio_no_teacher)[0] == ['transcribe', 'summary', 'review'])
video_teacher_en = wf.Facts(S0, teacher={'id': 'x', 'lang': 'en'}, has_video=True, vision=('openai', ''))
check('英文视频、有老师：全部步骤都要做（校对完可能有存疑句，复核也算上）', wf.pending_steps({}, 'video', video_teacher_en)[0] == wf.VIDEO_STAGES)
check('能看图的 AI 没设好：找插图、复核跳过，不算没做', wf.pending_steps({}, 'video', wf.Facts(S0, teacher={'id': 'x'}, vision=('', '没设好')))[0]
      == ['transcribe', 'correct', 'summary', 'review', 'terms'])
check('设置里关掉「自动查漏补缺」：查漏不算没做', wf.pending_steps(dict(done_zh, steps={'summary': 1}), 'video',
      wf.Facts(dict(S0, auto_review=False), teacher=None, has_video=False, vision=('', 'x')))[0] == [])
check('不写课程重点：还没整理的音频只差生成字幕', wf.pending_steps({}, 'video', wf.Facts(S0, has_video=False, no_summary=True))[0] == ['transcribe'])
check('书：章节都整理完、没写全书总览 → 没整理完', wf.pending_steps({'kind': 'book', 'chapters': [{'title': '第一章', 'md': '内容'}]}, 'book',
      wf.Facts(S0))[0] == ['overview'])


def tree_files(nodes):
    for n in nodes:
        if n['type'] == 'folder':
            yield from tree_files(n['children'])
        else:
            yield n


r = Api().call('library', {})
lib_items = {n['path']: n for n in tree_files(r['groups'])}
check('媒体库界面的列表也带「还差哪几步」', r['ok'] and lib_items[L6]['pending'] == [] and lib_items[L8]['pending'][0] == 'frames')
for p in (L5, L6, L7, L8):  # 后面的命令行测试不用它们
    os.remove(p)
    docs.sidecar(p).unlink(missing_ok=True)


# ------------------------------------------------------------ 命令行 ----
def run_cli(*argv):
    buf, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, json.loads(buf.getvalue()) if '--json' in argv else buf.getvalue(), err.getvalue()


code, out, _ = run_cli('find', '--course', '峰级', '--index', '2', '--json')
check('命令行 find：JSON', code == 0 and out['success'] and out['count'] == 1 and out['items'][0]['path'] == L2)
code, out, _ = run_cli('info', wf.video_id(L1), '--json')
check('命令行 info：JSON', code == 0 and out['success'] and out['state']['segments'] == 4 and 'metadata' in out)
shutil.copy(ROOT / 'test-media' / '演示视频.mp4', COURSE / '1、基础' / '4、第四课.mp4')
c3 = calls.copy()
code, out, _ = run_cli('process', '--course', '峰级', '--todo', '--dry-run', '--json')
plan_ = {Path(x['path']).name: x['steps'] for x in out['plan']}
# 第一课：前面用 force 重新生成过课程重点（只做了这一步），第三课：查漏失败过，都只差查漏；第四课是新加的
check('命令行 process --dry-run：只看不做，只列真正还要做的步骤', code == 0 and out['dry_run'] and calls == c3
      and plan_ == {'1、第一课.mp4': ['review'], '3、第三课.mp4': ['review'],
                    '4、第四课.mp4': ['transcribe', 'correct', 'frames', 'recheck', 'summary', 'review', 'terms']}, plan_)
code, text, _ = run_cli('process', '--course', '峰级', '--todo', '--dry-run')
check('命令行 --dry-run 中文输出', '1、第一课.mp4：查漏补缺' in text, text)
code, text, _ = run_cli('list', '--course', '峰级', '--todo')
check('命令行 list --todo：写明「未整理完」和还差哪几步', '第一课  [未整理完]' in text and '还差：查漏补缺' in text and '共 3 个' in text, text)
code, out, err = run_cli('process', '--course', '峰级', '--todo', '--json')
r0 = out['results'][0] if out.get('results') else {}
check('命令行 process --todo：新课从头整理，只差查漏的只做查漏', code == 0 and out['success'] and out['count'] == 3 and r0['success']
      and calls - c3 == Counter({'review': 3, 'asr': 1, 'correct': 1, 'frames': 1, 'recheck': 1, 'summary': 1, 'terms': 1}), (out, calls - c3))
check('JSON 结构：success / task_id / stage / error_code / retryable / stages / state',
      {'success', 'task_id', 'video_id', 'stage', 'error_code', 'message', 'retryable', 'stages', 'state', 'warnings'} <= set(r0)
      and {'name', 'label', 'status', 'retries', 'model', 'result'} <= set(r0['stages'][0]))
check('进度写在 stderr（不混进 JSON）', '生成字幕' in err or '%' in err)
code, out, _ = run_cli('process', str(OUTSIDE), '--json')
check('命令行：失败时也是结构化 JSON', code == 1 and out['success'] is False and out['error_code'] == 'not_in_library' and out['retryable'] is False)
code, out, _ = run_cli('process', L1, '--force', '--json')
check('命令行：--force 必须写明重做哪几步（防止重复花钱）', code == 1 and out['error_code'] == 'not_supported')
code, out, _ = run_cli('run', 'nosuchstep', L1, '--json')
check('命令行：不认识的步骤', code == 1 and not out['success'] and 'nosuchstep' in json.dumps(out, ensure_ascii=False))
code, out, _ = run_cli('tasks', '--limit', '3', '--json')
check('命令行 tasks', code == 0 and len(out['tasks']) == 3 and out['tasks'][0]['task_id'])
code, out, _ = run_cli('task', out['tasks'][0]['task_id'], '--json')
check('命令行 task：完整记录', code == 0 and out['record']['stages'])
code, out, _ = run_cli('steps', '--json')
check('命令行 steps', code == 0 and [s['name'] for s in out['video']][:2] == ['transcribe', 'correct'])
code, text, _ = run_cli('find', '第一课')
check('命令行：不加 --json 输出中文', code == 0 and '第一课' in text and '已整理' in text)
check('转存检测：C 盘路径换成 \\\\localhost\\C$', cli.unc_of(r'C:\Users\x\AppData\Roaming') == r'\\localhost\C$\Users\x\AppData\Roaming')

# 真的起一个进程（不用假的 AI，只查找）
env = dict(os.environ)
r = subprocess.run([sys.executable, str(ROOT / 'app' / 'cli.py'), 'list', '--kind', 'book', '--json'], capture_output=True, env=env, timeout=120)
outp = json.loads(r.stdout.decode('utf-8'))
check('命令行进程：退出码 0、stdout 是 UTF-8 的 JSON', r.returncode == 0 and outp['count'] == 2 and '《测试书籍》测试作者' in [x['name'] for x in outp['items']])
r = subprocess.run(['cmd', '/c', str(ROOT / 'mengren.cmd'), 'steps', '--json'], capture_output=True, env=env, timeout=120)
check('mengren.cmd 能直接调用', r.returncode == 0 and json.loads(r.stdout.decode('utf-8'))['success'], r.stderr.decode('utf-8', 'replace')[-300:])

# ------------------------------------------------------------ 收尾 ----
r = subprocess.run([sys.executable, '-m', 'pyflakes', str(ROOT / 'app' / 'cli.py'), str(ROOT / 'app' / 'core' / 'workflow.py'),
                    str(ROOT / 'app' / 'core' / 'tasks.py')], capture_output=True, text=True)
check('pyflakes：没有用了没定义的变量', r.returncode == 0, r.stdout)
print(f'\n全部通过：{passed} 项')
import logging  # noqa: E402
logging.shutdown()
shutil.rmtree(TMP, ignore_errors=True)
