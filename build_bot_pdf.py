# -*- coding: utf-8 -*-
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, KeepTogether
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

# 中文字体（reportlab 内置 CID 字体，无需外部文件）
pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
CN = 'STSong-Light'

# 配色
ORANGE = colors.HexColor('#f97316')
DARK = colors.HexColor('#1f2937')
GRAY = colors.HexColor('#6b7280')
LIGHT = colors.HexColor('#fff3e9')
LINE = colors.HexColor('#e5e7eb')
BLUEBG = colors.HexColor('#eff6ff')

styles = getSampleStyleSheet()

def S(name, **kw):
    base = dict(fontName=CN, leading=16, textColor=DARK)
    base.update(kw)
    return ParagraphStyle(name, **base)

title_s    = S('t', fontSize=22, leading=28, textColor=ORANGE, spaceAfter=2)
sub_s      = S('sub', fontSize=10.5, leading=15, textColor=GRAY, spaceAfter=4)
h1_s       = S('h1', fontSize=15, leading=20, textColor=ORANGE, spaceBefore=14, spaceAfter=6)
h2_s       = S('h2', fontSize=12, leading=17, textColor=DARK, spaceBefore=8, spaceAfter=3)
body_s     = S('b', fontSize=10, leading=16, spaceAfter=3)
small_s    = S('sm', fontSize=8.6, leading=12.5, textColor=DARK)
small_w    = S('smw', fontSize=8.6, leading=12.5, textColor=colors.white)
cell_s     = S('c', fontSize=9, leading=13)
note_s     = S('n', fontSize=9.5, leading=15, textColor=GRAY)

doc = SimpleDocTemplate(
    '/Users/a23544/Documents/GitHub/JourneyPlanner/docs/云端AIBot设计.pdf',
    pagesize=A4, topMargin=18*mm, bottomMargin=16*mm,
    leftMargin=16*mm, rightMargin=16*mm,
    title='云端 AI Bot 设计', author='JourneyPlanner'
)
story = []

def hr():
    story.append(Spacer(1, 4))
    story.append(HRFlowable(width='100%', thickness=0.6, color=LINE))
    story.append(Spacer(1, 4))

# ---------- 封面标题 ----------
story.append(Paragraph('云端 AI Bot 设计', title_s))
story.append(Paragraph('JourneyPlanner ｜ 客服机器人 ｜ 状态机 / 决策树 / AI 实现链路 / Prompt 表', sub_s))
hr()

# ---------- 1. 核心状态定义 ----------
story.append(Paragraph('1 ｜ 核心状态定义', h1_s))
story.append(Paragraph('Bot 以「状态机」运行，任何时刻处于以下四个状态之一。<b>闲聊为默认常驻态</b>，其余三态执行完即回到闲聊。', body_s))

state_rows = [
    [Paragraph('状态', small_w), Paragraph('名称', small_w), Paragraph('职责', small_w)],
    [Paragraph('状态 1', cell_s), Paragraph('引入 Onboarding', cell_s),
     Paragraph('用户首次进入，告知功能与操作。<b>不强迫选城市</b>，仅做引导。', cell_s)],
    [Paragraph('状态 2', cell_s), Paragraph('闲聊 Chitchat', cell_s),
     Paragraph('<b>默认常驻状态</b>。负责语义判断与人设对话，是所有流转的中枢。', cell_s)],
    [Paragraph('状态 3', cell_s), Paragraph('导入 Import', cell_s),
     Paragraph('功能执行状态。处理链接解析与图片识别，解析存库后回到闲聊。', cell_s)],
    [Paragraph('状态 4', cell_s), Paragraph('交付 Delivery', cell_s),
     Paragraph('结果产出状态。发送 H5 决策页链接，发送后回到闲聊。', cell_s)],
]
t = Table(state_rows, colWidths=[20*mm, 38*mm, 120*mm])
t.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), ORANGE),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, LIGHT]),
    ('GRID', (0,0), (-1,-1), 0.5, LINE),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('LEFTPADDING', (0,0), (-1,-1), 6),
    ('RIGHTPADDING', (0,0), (-1,-1), 6),
    ('TOPPADDING', (0,0), (-1,-1), 5),
    ('BOTTOMPADDING', (0,0), (-1,-1), 5),
]))
story.append(t)

# ---------- 2. 状态2决策树 ----------
story.append(Paragraph('2 ｜ 状态 2（闲聊）下的决策树', h1_s))
story.append(Paragraph('每收到一条消息，Bot 优先做<b>语义判断</b>，归入下面三个分支之一：', body_s))

def branch(title, lines, bg):
    items = [[Paragraph(title, S('bt', fontSize=10.5, leading=15, textColor=DARK))]]
    for ln in lines:
        items.append([Paragraph(ln, S('bl', fontSize=9.3, leading=14))])
    tb = Table(items, colWidths=[178*mm])
    tb.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), bg),
        ('LEFTPADDING', (0,0), (-1,-1), 9),
        ('RIGHTPADDING', (0,0), (-1,-1), 9),
        ('TOPPADDING', (0,0), (0,0), 7),
        ('TOPPADDING', (0,1), (-1,-1), 1),
        ('BOTTOMPADDING', (0,-1), (-1,-1), 7),
        ('LINEBEFORE', (0,0), (0,-1), 2.5, ORANGE),
        ('BOX', (0,0), (-1,-1), 0.4, LINE),
    ]))
    return KeepTogether([tb, Spacer(1,6)])

story.append(branch(
    '分支 A ｜ 识别为「导入酒店」意图（发了链接 / 图片 / 提到某酒店）',
    ['判断：是否已选城市？',
     '　- 否 → 触发引导文案，告知需先选城市。',
     '　- 是 → 进入【状态 3 导入】，解析并存库，完成后回到【状态 2】。'],
    LIGHT))

story.append(branch(
    '分支 B ｜ 识别为「完成导入」意图（如「好了」「看结果」「没了」）',
    ['判断 1：是否已选城市？　否 → 引导先选城市。',
     '判断 2：是否已有导入酒店？',
     '　- 否 → 告知目前还没酒店，建议发送截图或链接。',
     '　- 是 → 进入【状态 4 交付】，发送 H5 链接，完成后回到【状态 2】。'],
    BLUEBG))

story.append(branch(
    '分支 C ｜ 识别为「普通对话」意图（非导入、非完成）',
    ['执行：以「向导人设」风趣回复，维持在【状态 2】。'],
    colors.HexColor('#f3f4f6')))

# ---------- 3. 逻辑关键点 ----------
story.append(Paragraph('3 ｜ 逻辑关键点对齐', h1_s))
kp = [
    ('非强制性', '用户在「引入」后拥有高度自由：可以先闲聊，也可以直接喂数据，不被流程绑死。'),
    ('前置依赖', '所有功能执行（导入、看结果）都<b>强依赖于「已选城市」</b>——城市是地图上计算通勤的锚点。'),
    ('容错弥补', '在「看结果」阶段若缺数据（城市或酒店），Bot 以<b>积极态度</b>指出缺失项并引导补全，不是冷冰冰报错。'),
]
kp_rows = []
for k, v in kp:
    kp_rows.append([Paragraph(k, S('kk', fontSize=9.5, leading=14, textColor=ORANGE)),
                    Paragraph(v, cell_s)])
t = Table(kp_rows, colWidths=[26*mm, 152*mm])
t.setStyle(TableStyle([
    ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ('ROWBACKGROUNDS', (0,0), (-1,-1), [colors.white, LIGHT]),
    ('GRID', (0,0), (-1,-1), 0.5, LINE),
    ('LEFTPADDING', (0,0), (-1,-1), 6),
    ('TOPPADDING', (0,0), (-1,-1), 5),
    ('BOTTOMPADDING', (0,0), (-1,-1), 5),
]))
story.append(t)

# ---------- 4. AI 实现链路 ----------
story.append(Paragraph('4 ｜ 云端 AI 实现链路', h1_s))
story.append(Paragraph(
    '收到消息后，Bot 不直接把整段对话丢给大模型，而是先<b>判断意图</b>拿到一个 <b>prompt_key</b>，'
    '用 key 取出对应的 Prompt 模板，再把「Prompt + 上下文」一起发给 DeepSeek API。', body_s))

flow = ['收到消息', '判断模块\n得到 prompt_key', 'prompt_key\n→ 取 Prompt 模板', 'Prompt + 上下文\n→ DeepSeek API', '回复用户']
flow_cells = [[Paragraph(x.replace('\n','<br/>'), S('fl', fontSize=9, leading=12, alignment=1, textColor=DARK)) for x in flow]]
ft = Table(flow_cells, colWidths=[34*mm]*5)
ft.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,-1), LIGHT),
    ('BACKGROUND', (1,0), (1,0), colors.HexColor('#ffe4cc')),
    ('BOX', (0,0), (-1,-1), 0.4, ORANGE),
    ('INNERGRID', (0,0), (-1,-1), 0.4, colors.white),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('TOPPADDING', (0,0), (-1,-1), 9),
    ('BOTTOMPADDING', (0,0), (-1,-1), 9),
]))
story.append(ft)
story.append(Spacer(1, 3))
story.append(Paragraph(
    '判断模块是核心：它把「自然语言意图」收敛成有限个 prompt_key，'
    '每个 key 对应一段可单独维护、可灰度调整的 Prompt，使大模型行为可控、可测、可迭代。', note_s))

# ---------- 5. Prompt Table ----------
story.append(Paragraph('5 ｜ Prompt 表（草案，具体 Prompt 待填）', h1_s))
story.append(Paragraph('每个场景对应一个 prompt_key 与一段 Prompt 模板。下表为场景清单与设计理由。', body_s))

prompt_data = [
    ('1', '问候与上手指南', '不只是打招呼，要立刻教用户怎么「发卡片 / 截图」。'),
    ('2', '初始化引导（选城市 / 景点）', '新增。确保地图上有计算通勤的「锚点」。'),
    ('3', '数据输入实时反馈（处理中）', '合并。体现「正在扫描 / 解析」的进度感。'),
    ('4', '解析结果反馈（含纠错入口）', '强化。成功报喜；失败体现「积极弥补」态度并引导重发。'),
    ('5', '对比决策触发（Nudge）', '新增。当数据足够时，推用户去网页看结果。'),
    ('6', '咨询地点 / 酒店详情', '保持现状，体现「整合者」的专业。'),
    ('7', '返回规划网址（H5 入口）', '核心入口，文案要诱人。'),
    ('8', '能力边界与付费引导', '合并。把「我不能订房」和「请付费开启高级工具」逻辑连贯起来。'),
    ('9', '计费相关（支付 / 到期）', '保持现状，确保商业闭环。'),
    ('10', '向导式闲聊（含认错弥补）', '强化人设。体现「可靠但会犯错」的亲切感。'),
    ('11', '分享与反馈引导', '增加传播属性。'),
]
rows = [[Paragraph('#', small_w), Paragraph('需求场景', small_w), Paragraph('设计 / 修改理由', small_w)]]
for n, scene, reason in prompt_data:
    rows.append([Paragraph(n, cell_s), Paragraph(scene, cell_s), Paragraph(reason, cell_s)])
t = Table(rows, colWidths=[10*mm, 56*mm, 112*mm], repeatRows=1)
t.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), ORANGE),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, LIGHT]),
    ('GRID', (0,0), (-1,-1), 0.5, LINE),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('ALIGN', (0,0), (0,-1), 'CENTER'),
    ('LEFTPADDING', (0,0), (-1,-1), 6),
    ('RIGHTPADDING', (0,0), (-1,-1), 6),
    ('TOPPADDING', (0,0), (-1,-1), 5),
    ('BOTTOMPADDING', (0,0), (-1,-1), 5),
]))
story.append(t)

story.append(Spacer(1, 10))
story.append(HRFlowable(width='100%', thickness=0.6, color=LINE))
story.append(Spacer(1, 3))
story.append(Paragraph('JourneyPlanner ｜ 云端 AI Bot 设计草案 ｜ 待填具体 Prompt 文案', S('foot', fontSize=8, textColor=GRAY)))

doc.build(story)
print('OK')
