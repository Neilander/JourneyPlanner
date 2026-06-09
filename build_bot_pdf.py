# -*- coding: utf-8 -*-
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, KeepTogether, XPreformatted
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
CN = 'STSong-Light'

ORANGE = colors.HexColor('#f97316')
DARK = colors.HexColor('#1f2937')
GRAY = colors.HexColor('#6b7280')
LIGHT = colors.HexColor('#fff3e9')
LINE = colors.HexColor('#e5e7eb')
BLUEBG = colors.HexColor('#eff6ff')
GREENBG = colors.HexColor('#ecfdf5')
GRAYBG = colors.HexColor('#f3f4f6')

def S(name, **kw):
    base = dict(fontName=CN, leading=16, textColor=DARK)
    base.update(kw)
    return ParagraphStyle(name, **base)

title_s = S('t', fontSize=22, leading=28, textColor=ORANGE, spaceAfter=2)
sub_s   = S('sub', fontSize=10.5, leading=15, textColor=GRAY, spaceAfter=4)
h1_s    = S('h1', fontSize=15, leading=20, textColor=ORANGE, spaceBefore=14, spaceAfter=6)
h2_s    = S('h2', fontSize=11.5, leading=16, textColor=DARK, spaceBefore=8, spaceAfter=3)
body_s  = S('b', fontSize=10, leading=16, spaceAfter=3)
small_w = S('smw', fontSize=8.6, leading=12.5, textColor=colors.white)
cell_s  = S('c', fontSize=9, leading=13)
mono_s  = S('m', fontSize=8.4, leading=12.5, textColor=DARK)
note_s  = S('n', fontSize=9.5, leading=15, textColor=GRAY)

doc = SimpleDocTemplate(
    '/Users/a23544/Documents/GitHub/JourneyPlanner/docs/云端AIBot设计.pdf',
    pagesize=A4, topMargin=16*mm, bottomMargin=14*mm,
    leftMargin=15*mm, rightMargin=15*mm,
    title='云端 AI Bot 设计', author='JourneyPlanner'
)
story = []
W = 180*mm

def hr():
    story.append(Spacer(1, 4)); story.append(HRFlowable(width='100%', thickness=0.6, color=LINE)); story.append(Spacer(1, 4))

def htable(header, rows, widths):
    data = [[Paragraph(h, small_w) for h in header]]
    for r in rows:
        data.append([Paragraph(c, cell_s) for c in r])
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), ORANGE),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, LIGHT]),
        ('GRID', (0,0), (-1,-1), 0.5, LINE),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('LEFTPADDING', (0,0), (-1,-1), 5), ('RIGHTPADDING', (0,0), (-1,-1), 5),
        ('TOPPADDING', (0,0), (-1,-1), 4.5), ('BOTTOMPADDING', (0,0), (-1,-1), 4.5),
    ]))
    return t

def card(title, lines, bg):
    items = [[Paragraph(title, S('ct', fontSize=10.3, leading=14.5, textColor=DARK))]]
    for ln in lines:
        items.append([Paragraph(ln, S('cl', fontSize=9, leading=13.5))])
    tb = Table(items, colWidths=[W])
    tb.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), bg),
        ('LEFTPADDING', (0,0), (-1,-1), 9), ('RIGHTPADDING', (0,0), (-1,-1), 9),
        ('TOPPADDING', (0,0), (0,0), 6), ('TOPPADDING', (0,1), (-1,-1), 1),
        ('BOTTOMPADDING', (0,-1), (-1,-1), 6),
        ('LINEBEFORE', (0,0), (0,-1), 2.5, ORANGE), ('BOX', (0,0), (-1,-1), 0.4, LINE),
    ]))
    return KeepTogether([tb, Spacer(1,5)])

def codeblock(text):
    # XPreformatted 保留缩进与换行，用真实空格（STSong 支持），不用 &nbsp;
    style = S('mw', fontSize=8.4, leading=12.8, textColor=colors.white)
    p = XPreformatted(text, style)
    tb = Table([[p]], colWidths=[W])
    tb.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#0f172a')),
        ('LEFTPADDING', (0,0), (-1,-1), 9), ('RIGHTPADDING', (0,0), (-1,-1), 9),
        ('TOPPADDING', (0,0), (-1,-1), 7), ('BOTTOMPADDING', (0,0), (-1,-1), 7),
    ]))
    return KeepTogether([tb, Spacer(1,4)])

# ===== 标题 =====
story.append(Paragraph('云端 AI Bot 设计', title_s))
story.append(Paragraph('JourneyPlanner ｜ 客服机器人 ｜ 5 状态机 / 三模块 / 删除子状态机 / Prompt 表', sub_s))
hr()

# ===== 1. 五状态 =====
story.append(Paragraph('1 ｜ 核心状态定义（5 态）', h1_s))
story.append(Paragraph('Bot 以「状态机」运行。<b>「闲聊 idle」为默认常驻态、是中枢</b>，其余四态执行完即回 idle。', body_s))
story.append(htable(
    ['状态', '名称', '职责'],
    [['状态 1', '引入 Onboarding', '首次进入，告知功能与操作（不强迫选城市）。承载「功能介绍模块」。'],
     ['状态 2', '闲聊 Chitchat (idle)', '默认常驻态。语义判断 + 人设对话，所有流转的中枢。'],
     ['状态 3', '导入 Import', '解析链接 / 图片识别，存库后回 idle。'],
     ['状态 4', '交付 Delivery', '发送 H5 决策页链接，发送后回 idle。'],
     ['状态 5', '删除 Delete', '带二次确认地移除已导入酒店。含 2 个子状态。']],
    [18*mm, 40*mm, 122*mm]))

# ===== 2. 三个新增模块 =====
story.append(Paragraph('2 ｜ 三个新增模块', h1_s))
story.append(Paragraph('<b>模块 A ｜ 功能介绍</b>（轻，挂状态 1）：引入时给「什么话 → 触发什么功能」对照表。<b>只引导链接 / 截图，不让用户发酒店名</b>（歧义大、定位不准）。', body_s))
story.append(htable(
    ['用户这样说', '触发'],
    [['发<b>链接</b> / <b>截图</b>', '导入酒店（状态 3）'],
     ['「看结果」「好了」「没了」', '出对比网页 H5（状态 4）'],
     ['「删掉 XX」「把贵的去掉」', '删除酒店（状态 5）'],
     ['选哪个城市', '设定通勤锚点']],
    [90*mm, 90*mm]))
story.append(Spacer(1,4))
story.append(card('模块 B ｜ 安全引导（轻，闲聊里一个意图分支）',
    ['触发：用户点开 H5 见「不可信 / 未备案」，回来质疑「安全吗 / 打不开」。',
     '回复：「链接可信，放心点开，我们正在提交备案，通过后即可直接打开。」',
     '⚠️ 过渡方案：备案通过后应能一键关掉此分支。'], BLUEBG))
story.append(card('模块 C ｜ 智能删除（重，独立状态 5）',
    ['用户说「把 XX 删了」时不能直接删，走带二次确认的子状态机。详见第 4 节。'], GREENBG))

# ===== 3. 闲聊决策树 =====
story.append(Paragraph('3 ｜ 状态 2（闲聊 idle）决策树', h1_s))
story.append(Paragraph('每收到一条消息，先做<b>语义判断（LLM）</b>，归入五个分支之一：', body_s))
story.append(card('分支 A ｜ 导入酒店（发链接 / 截图，不引导酒店名）',
    ['已选城市？ 否 → 引导先选城市；是 → 进【状态 3 导入】→ 存库 → 回 idle。'], LIGHT))
story.append(card('分支 B ｜ 完成导入（「好了」「看结果」「没了」）',
    ['未选城市 → 引导；无酒店 → 提示发截图/链接；都满足 → 进【状态 4 交付】发 H5 → 回 idle。'], BLUEBG))
story.append(card('分支 C ｜ 普通闲聊',
    ['以「向导人设」风趣回复 → 维持 idle。'], GRAYBG))
story.append(card('分支 D ｜ 安全质疑（模块 B）',
    ['回「链接可信，正在备案」安抚 → 维持 idle。'], BLUEBG))
story.append(card('分支 E ｜ 删除酒店（模块 C）',
    ['进入【状态 5 删除】。'], GREENBG))

# ===== 4. 状态5 子状态机 =====
story.append(Paragraph('4 ｜ 状态 5 ｜ 删除酒店（子状态机）', h1_s))
story.append(Paragraph('<b>进入</b>：idle 中用户表达删除意图。<b>入口动作</b>：匹配函数(LLM) 把「用户语义 + 已导入酒店」→ 命中 x 个，按 x 分流。', body_s))
story.append(codeblock(
"""进入状态 5
  匹配函数(LLM): (用户语义, 已导入酒店) -> 命中 x 个
  |
  ├─ x = 0  →  子状态 5.1 空匹配
  |     回复:"没匹配到酒店,还删吗?换个角度描述一下?"
  |     ├─ 想继续 → 重新进状态5入口(重新描述)
  |     ├─ 不删了 → 回 idle
  |     └─ 闲聊   → 计入闲聊缓冲
  |
  └─ x > 0  →  子状态 5.2 确认删除
        展示 x 个酒店,问:"是否删除这些?"
        子意图判断(LLM, 5选1):
        ├─ 是         → 移除 → 回执"已移出X家,还剩Y家" → 回 idle
        ├─ 进一步需求 → refine: [待删+全部已导入+用户新prompt] → LLM
        |               → 新待删list → 回 5.2 重新确认
        ├─ 重新来     → 清空待删 → 回状态5入口
        ├─ 不删了     → 回 idle
        └─ 闲聊       → 计入闲聊缓冲"""))
story.append(card('闲聊缓冲（5.1 与 5.2 都有）',
    ['删除流程中说无关闲聊 → 正常回复但累计计数；连续 3 轮纯闲聊 → 超时回 idle。',
     '任何删除相关有效动作（重新匹配 / refine / 确认）→ 闲聊计数清零。'], LIGHT))
story.append(Paragraph('<b>关键</b>：状态 5 有 2 处 LLM —— ① 入口/refine 的<b>匹配函数</b>；② 5.2 的<b>子意图判断</b>（5 选 1）。须跨消息保留：待删 list + 缓冲计数。', note_s))

# ===== 5. 逻辑关键点 =====
story.append(Paragraph('5 ｜ 逻辑关键点', h1_s))
kp = [['非强制性', '引入后用户高度自由：可先闲聊也可直接喂数据，不被流程绑死。'],
      ['前置依赖', '导入/看结果都强依赖「已选城市」——城市是算通勤的锚点。'],
      ['容错弥补', '缺数据时积极指出并引导补全，不冷冰冰报错。'],
      ['删除必确认', '删除是破坏性操作，必须二次确认，绝不静默删除。'],
      ['匹配/判断皆 LLM', '语义→酒店子集的匹配、各处意图判断，全由 LLM 完成，不写死规则。']]
t = Table([[Paragraph(k, S('kk', fontSize=9.3, leading=13, textColor=ORANGE)), Paragraph(v, cell_s)] for k,v in kp],
          colWidths=[30*mm, 150*mm])
t.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),
    ('ROWBACKGROUNDS',(0,0),(-1,-1),[colors.white, LIGHT]),('GRID',(0,0),(-1,-1),0.5,LINE),
    ('LEFTPADDING',(0,0),(-1,-1),5),('TOPPADDING',(0,0),(-1,-1),4.5),('BOTTOMPADDING',(0,0),(-1,-1),4.5)]))
story.append(t)

# ===== 6. AI 实现链路 =====
story.append(Paragraph('6 ｜ 云端 AI 实现链路', h1_s))
story.append(Paragraph('收到消息后先<b>判断当前状态</b>，再<b>意图判断(LLM)</b>拿 prompt_key / 子意图，取 Prompt 模板 + 上下文发 DeepSeek。', body_s))
story.append(codeblock(
"""def handle_message(msg, ctx):
    if ctx.state == "delete":
        return handle_delete_state(msg, ctx)        # 状态5 子状态机
    intent = classify_intent(msg, ctx)              # LLM → 分支 A~E
    if intent == "delete":
        ctx.state = "delete"; return enter_delete(msg, ctx)
    prompt = PROMPT_TABLE[intent_to_key(intent)]
    return deepseek(prompt=prompt, context=ctx)"""))

# ===== 7. Prompt 表 =====
story.append(Paragraph('7 ｜ Prompt 表（草案 ｜ 具体 Prompt 待填）', h1_s))
prows = [
    ['1','greeting_guide','问候 + 功能介绍（模块A）'],
    ['2','init_city_setup','初始化引导（选城市/景点）'],
    ['3','import_processing','导入实时反馈（处理中）'],
    ['4','import_result','解析结果反馈（含纠错）'],
    ['5','compare_nudge','对比决策触发 Nudge'],
    ['6','query_detail','咨询地点/酒店详情'],
    ['7','h5_entry','返回规划网址 H5 入口'],
    ['8','boundary_paywall','能力边界 + 付费引导'],
    ['9','billing','计费（支付/到期）'],
    ['10','chitchat_persona','向导式闲聊（含认错）'],
    ['11','share_feedback','分享与反馈引导'],
    ['12','safety_reassure','安全引导（模块B，备案后可关）'],
    ['13','intent_classify','闲聊意图判断（LLM 函数，分 A~E）'],
    ['14','delete_match','删除匹配函数（LLM，语义→酒店子集）'],
    ['15','delete_empty','删除｜空匹配（子状态5.1）'],
    ['16','delete_confirm','删除｜确认（子状态5.2，展示待删）'],
    ['17','delete_subintent','删除子意图（LLM，5选1）'],
    ['18','delete_done','删除｜回执「已移出X家,还剩Y家」'],
]
story.append(htable(['#','prompt_key','场景'], prows, [10*mm, 52*mm, 118*mm]))
story.append(Spacer(1, 6))
story.append(HRFlowable(width='100%', thickness=0.6, color=LINE)); story.append(Spacer(1,2))
story.append(Paragraph('JourneyPlanner ｜ 云端 AI Bot 设计 v2（5 状态）｜ 待填具体 Prompt 文案', S('foot', fontSize=8, textColor=GRAY)))

doc.build(story)
print('OK')
