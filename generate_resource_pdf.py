"""生成旅途向导资源需求表 PDF"""

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import os, sys

# ── 字体注册（使用系统中文字体）──────────────────────────────────────────────
def find_cjk_font():
    candidates = [
        "C:/Windows/Fonts/msyh.ttc",       # 微软雅黑
        "C:/Windows/Fonts/simhei.ttf",      # 黑体
        "C:/Windows/Fonts/simsun.ttc",      # 宋体
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None

font_path = find_cjk_font()
if font_path:
    pdfmetrics.registerFont(TTFont("CJK", font_path))
    FONT = "CJK"
else:
    print("WARNING: CJK font not found, falling back to Helvetica (Chinese may not render)")
    FONT = "Helvetica"

# ── 配色（与设计稿一致）──────────────────────────────────────────────────────
SAND   = colors.HexColor("#e8dec3")
PAPER  = colors.HexColor("#f4eedb")
PAPER2 = colors.HexColor("#fbf7ea")
SAGE   = colors.HexColor("#8aa564")
SAGEDARK = colors.HexColor("#5f7e44")
MOSS   = colors.HexColor("#445b39")
SKY    = colors.HexColor("#9fb6bf")
CLAY   = colors.HexColor("#c0855a")
INK    = colors.HexColor("#403a2a")
STONE  = colors.HexColor("#cdbf9d")
WHITE  = colors.white
RED    = colors.HexColor("#c0392b")
ORANGE = colors.HexColor("#d4804a")
GRAY   = colors.HexColor("#8a7c6a")

# ── 样式 ─────────────────────────────────────────────────────────────────────
styles = getSampleStyleSheet()

def S(name, **kw):
    kw.setdefault("fontName", FONT)
    kw.setdefault("textColor", INK)
    return ParagraphStyle(name, **kw)

sTitle   = S("sTitle",   fontSize=22, leading=28, textColor=MOSS, spaceAfter=4)
sSubtitle= S("sSub",     fontSize=11, leading=16, textColor=GRAY, spaceAfter=16)
sH1      = S("sH1",      fontSize=14, leading=20, textColor=MOSS, spaceBefore=14, spaceAfter=6, fontName=FONT)
sH2      = S("sH2",      fontSize=11, leading=16, textColor=SAGEDARK, spaceBefore=10, spaceAfter=4)
sBody    = S("sBody",    fontSize=9,  leading=14, textColor=INK)
sNote    = S("sNote",    fontSize=8,  leading=12, textColor=GRAY, leftIndent=4)
sBadge   = S("sBadge",   fontSize=8,  leading=12, textColor=CLAY)

def P(text, style=None):
    return Paragraph(text, style or sBody)

def badge(text, color=CLAY):
    return Paragraph(f'<font color="#{color.hexval()[2:] if hasattr(color,"hexval") else "c0855a"}">{text}</font>', sBadge)

# ── 表格通用样式 ──────────────────────────────────────────────────────────────
def tbl_style(header_bg=SAGE, row_bg=PAPER2, alt_bg=PAPER):
    return TableStyle([
        # 表头
        ("BACKGROUND",  (0,0), (-1,0),  header_bg),
        ("TEXTCOLOR",   (0,0), (-1,0),  WHITE),
        ("FONTNAME",    (0,0), (-1,0),  FONT),
        ("FONTSIZE",    (0,0), (-1,0),  9),
        ("BOTTOMPADDING",(0,0),(-1,0),  6),
        ("TOPPADDING",  (0,0), (-1,0),  6),
        # 内容行
        ("FONTNAME",    (0,1), (-1,-1), FONT),
        ("FONTSIZE",    (0,1), (-1,-1), 8.5),
        ("LEADING",     (0,1), (-1,-1), 13),
        ("TOPPADDING",  (0,1), (-1,-1), 5),
        ("BOTTOMPADDING",(0,1),(-1,-1), 5),
        ("LEFTPADDING", (0,0), (-1,-1), 7),
        ("RIGHTPADDING",(0,0), (-1,-1), 7),
        # 交替行背景
        *[("BACKGROUND",(0,i),(-1,i), row_bg if i%2==1 else alt_bg) for i in range(1, 30)],
        # 网格
        ("GRID",        (0,0), (-1,-1), 0.4, STONE),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[row_bg, alt_bg]),
        ("VALIGN",      (0,0), (-1,-1), "TOP"),
    ])

def priority_cell(p, desc=""):
    colors_map = {"P0": RED, "P1": CLAY, "P2": GRAY}
    c = colors_map.get(p, GRAY)
    hex_c = {"P0":"c0392b","P1":"d4804a","P2":"8a7c6a"}.get(p,"8a7c6a")
    txt = f'<font color="#{hex_c}"><b>{p}</b></font>'
    if desc:
        txt += f'\n<font color="#8a7c6a" size="7">{desc}</font>'
    return Paragraph(txt, sBody)

# ── 文档构建 ──────────────────────────────────────────────────────────────────
W, H = A4
MARGIN = 18*mm
doc = SimpleDocTemplate(
    "旅途向导_资源需求表.pdf",
    pagesize=A4,
    leftMargin=MARGIN, rightMargin=MARGIN,
    topMargin=20*mm, bottomMargin=18*mm,
)

story = []

# ── 封面区 ────────────────────────────────────────────────────────────────────
story.append(Spacer(1, 8*mm))
story.append(P("旅途向导  ·  资源需求表", sTitle))
story.append(P("UI 重设计配套 · 2026 年 6 月", sSubtitle))
story.append(HRFlowable(width="100%", thickness=1.5, color=SAGE, spaceAfter=12))

# 风格说明
story.append(P("设计风格定义", sH1))
style_text = (
    "Minimalist naive illustration · Paper-cut flat color shapes · Solid pastel color blocks without outlines · "
    "Large simple geometric forms · Details suggested by only a few dry-brush strokes · "
    "Expressive doodle marks instead of contour lines · Childlike hand-drawn accents · "
    "Imperfect organic silhouettes · Lots of negative space · Warm muted palette · "
    "Japanese stationery aesthetic · Collage feeling · Visual hierarchy driven by color shapes rather than line art."
)
story.append(P(style_text, sNote))
story.append(Spacer(1, 6))

# 配色板
story.append(P("设计配色板", sH2))
palette_data = [
    ["变量", "色值", "用途"],
    ["--sand",   "#e8dec3", "暖米黄 · 次级背景、按钮底"],
    ["--paper",  "#f4eedb", "纸面白 · 主背景、卡片底"],
    ["--paper2", "#fbf7ea", "最浅卡面 · 排行榜卡片"],
    ["--sage",   "#8aa564", "柔草绿 · 主强调色、选中态、进度条"],
    ["--sageDk", "#5f7e44", "深苔绿 · 强调文字"],
    ["--moss",   "#445b39", "深绿 · 标题文字"],
    ["--sky",    "#9fb6bf", "灰蓝 · 地铁线、辅助色"],
    ["--slate",  "#5d7a86", "深灰蓝 · 候选酒店 marker"],
    ["--clay",   "#c0855a", "暖陶土 · CTA 按钮、No.1 标记"],
    ["--ink",    "#403a2a", "暖深褐 · 正文"],
    ["--stone",  "#cdbf9d", "石色 · 分割线、边框"],
]
pt = Table(palette_data, colWidths=[38*mm, 32*mm, None])
pt.setStyle(tbl_style(MOSS))
story.append(pt)
story.append(Spacer(1, 10))

# ── 1. 地图样式 ───────────────────────────────────────────────────────────────
story.append(HRFlowable(width="100%", thickness=0.6, color=STONE, spaceAfter=8))
story.append(P("1 · 地图样式", sH1))

map_data = [
    ["资源", "规格 / 说明", "优先级", "交付格式"],
    [
        P("高德自定义地图样式 JSON"),
        P("暖米黄底色（#f4eedb）、弱化道路标注、关闭 POI、水体改为灰蓝、建筑改为暖米色。\n已生成初稿：frontend/public/amap-style.json，在高德控制台「个性化地图」导入微调。"),
        priority_cell("P0","阻塞上线"),
        P("JSON 文件"),
    ],
    [
        P("高德地图控制台调优"),
        P("将 amap-style.json 上传至高德开放平台 → 个性化地图，预览后导出 styleId，替换代码中的 mapStyle 参数。"),
        priority_cell("P0"),
        P("styleId 字符串"),
    ],
]
mt = Table(map_data, colWidths=[38*mm, 90*mm, 18*mm, 24*mm])
mt.setStyle(tbl_style(SAGE))
story.append(mt)
story.append(Spacer(1, 10))

# ── 2. 地图 Marker ────────────────────────────────────────────────────────────
story.append(HRFlowable(width="100%", thickness=0.6, color=STONE, spaceAfter=8))
story.append(P("2 · 地图 Marker（SVG 组件）", sH1))
story.append(P("所有 marker 用 SVG 实现，以 AMap CustomOverlay 方式注入，避免默认蓝色图钉。", sNote))
story.append(Spacer(1, 4))

marker_data = [
    ["Marker 类型", "视觉描述", "优先级", "尺寸"],
    [P("酒店 — 最优（#1 排名）"),  P("陶土色（clay #c0855a）圆角气泡 + 白色酒店名 + 下方圆点尾巴，投影"), priority_cell("P0"), P("宽≤120px")],
    [P("酒店 — 候选"),             P("石板蓝（slate #5d7a86）气泡，样式同上"), priority_cell("P0"), P("宽≤120px")],
    [P("景点 Marker"),             P("草绿（sage）外圈光晕脉冲环 + 白点 + 景点名小标签（paper 底）"), priority_cell("P0"), P("40×40px")],
    [P("酒店图钉（无排名时）"),    P("moss 色小方块 + 白色床铺图标，圆角 8px"), priority_cell("P1"), P("32×32px")],
]
mkt = Table(marker_data, colWidths=[42*mm, 86*mm, 18*mm, 24*mm])
mkt.setStyle(tbl_style(SAGE))
story.append(mkt)
story.append(Spacer(1, 10))

# ── 3. 景点插画（核心）───────────────────────────────────────────────────────
story.append(HRFlowable(width="100%", thickness=0.6, color=STONE, spaceAfter=8))
story.append(P("3 · 景点插画（核心资源）", sH1))
story.append(P(
    "底部景点选择区为「杂志卡」风格，每张卡需要一幅竖版插画。"
    "风格：剪纸扁平，纯色块无轮廓线，少量干笔触细节，暖柔色板，负空间充足。",
    sNote
))
story.append(Spacer(1, 4))
story.append(P("规格：400 × 560 px（2×）· PNG 透明底 或 WebP · 单文件 ≤ 150 KB", sNote))
story.append(Spacer(1, 6))

story.append(P("西安（P0 · 优先出图）", sH2))
xian_data = [
    ["景点", "画面内容建议", "主色调", "优先级"],
    [P("大唐不夜城"), P("唐风灯楼剪影 + 夜晚暖光色块，前景人群轮廓"), P("暖橙 #c0855a + 深棕"), priority_cell("P0","首批")],
    [P("钟楼"),       P("钟楼正面几何化，青绿屋顶，蓝天负空间"),         P("青绿 #8aa564 + 米白"), priority_cell("P0","首批")],
    [P("兵马俑"),     P("1-2 个陶俑局部，土黄色块为主，极简细节"),       P("土黄 #c4a56a + 沙色"), priority_cell("P0","首批")],
    [P("城墙"),       P("城墙剖面几何，远山负空间，暮色渐变"),           P("砖红 #b05a3a + 暮橙"), priority_cell("P0","首批")],
    [P("陕西历史博物馆"), P("仿唐建筑顶部轮廓，米色天空，简洁"),         P("米色 #f4eedb + 苔绿"), priority_cell("P0","首批")],
    [P("回民街"),     P("清真寺圆顶 + 食物摊位色块，街道负空间"),        P("白 + 青蓝 + 暖土"),  priority_cell("P0","首批")],
]
xt = Table(xian_data, colWidths=[28*mm, 80*mm, 42*mm, 20*mm])
xt.setStyle(tbl_style(SAGEDARK))
story.append(xt)
story.append(Spacer(1, 8))

story.append(P("其他城市（P2 · 按需扩展，每城市 6 张）", sH2))
city_data = [
    ["城市", "景点列表（各 6 张）", "优先级"],
    [P("杭州"), P("西湖 · 雷峰塔 · 三潭印月 · 断桥残雪 · 灵隐寺 · 河坊街"), priority_cell("P2")],
    [P("成都"), P("宽窄巷子 · 武侯祠 · 锦里 · 都江堰 · 大熊猫基地 · 春熙路"), priority_cell("P2")],
    [P("北京"), P("故宫 · 天坛 · 颐和园 · 南锣鼓巷 · 长城 · 三里屯"), priority_cell("P2")],
    [P("上海"), P("外滩 · 豫园 · 田子坊 · 迪士尼 · 新天地 · 武康路"), priority_cell("P2")],
    [P("其余城市"), P("按开通顺序补充，每城市 6 张"), priority_cell("P2")],
]
ct = Table(city_data, colWidths=[22*mm, 126*mm, 22*mm])
ct.setStyle(tbl_style(SKY))
story.append(ct)
story.append(Spacer(1, 10))

# ── 4. UI 图标 ────────────────────────────────────────────────────────────────
story.append(HRFlowable(width="100%", thickness=0.6, color=STONE, spaceAfter=8))
story.append(P("4 · UI 图标", sH1))
story.append(P("规格：SVG 矢量 · 24×24 dp · 线条粗细 1.5–2px · 风格与插画一致（有机感，略不规则）", sNote))
story.append(Spacer(1, 4))

icon_data = [
    ["图标名", "位置", "视觉描述", "优先级"],
    [P("地图 tab"),    P("顶部导航栏"),       P("折叠地图形，圆角，有一条路径"), priority_cell("P0")],
    [P("排行榜 tab"),  P("顶部导航栏"),       P("三根粗细不等的柱状图，底部对齐"), priority_cell("P0")],
    [P("设置"),        P("顶部导航栏"),       P("圆形齿轮，6 齿，中心镂空圆"), priority_cell("P0")],
    [P("添加 / 导入"), P("顶部导航栏 CTA"),   P("「+」加号在左 + 小箭头指向框内"), priority_cell("P0")],
    [P("步行"),        P("通勤模式选择"),     P("小人走路剪影，略歪斜有动感"), priority_cell("P1")],
    [P("公共交通"),    P("通勤模式选择"),     P("地铁车厢正面简笔，圆角矩形窗"),  priority_cell("P1")],
    [P("驾车"),        P("通勤模式选择"),     P("汽车侧面简笔，圆顶轿车轮廓"),    priority_cell("P1")],
    [P("位置 pin"),    P("城市名旁边"),       P("泪滴形，内有小圆点，底部尖"),    priority_cell("P1")],
    [P("⚠ 避雷徽章"), P("酒店警告标签"),     P("手绘风感叹号，不规则六边形外框"), priority_cell("P1")],
    [P("刷新"),        P("酒店管理弹窗"),     P("圆弧箭头，略不对称，手绘感"),    priority_cell("P2")],
]
it = Table(icon_data, colWidths=[30*mm, 34*mm, 76*mm, 18*mm])
it.setStyle(tbl_style(SAGE))
story.append(it)
story.append(Spacer(1, 10))

# ── 5. 字体 ───────────────────────────────────────────────────────────────────
story.append(HRFlowable(width="100%", thickness=0.6, color=STONE, spaceAfter=8))
story.append(P("5 · 字体", sH1))

font_data = [
    ["字体", "用途", "来源", "优先级"],
    [P("Fraunces（英文斜体衬线）"), P("排行榜序号大字（1 2 3）、数字强调、logo"),         P("Google Fonts · 已在 HTML 稿引用"), priority_cell("P0")],
    [P("Noto Sans SC"),            P("中文 UI 正文、按钮、标签"),                         P("Google Fonts · next/font 接入"),   priority_cell("P0")],
    [P("Noto Serif SC（可选）"),   P("城市名、卡片标题（更有质感）"),                     P("Google Fonts"),                    priority_cell("P2")],
]
ft = Table(font_data, colWidths=[46*mm, 68*mm, 42*mm, 18*mm])
ft.setStyle(tbl_style(MOSS))
story.append(ft)
story.append(Spacer(1, 10))

# ── 6. 优先级总览 ─────────────────────────────────────────────────────────────
story.append(HRFlowable(width="100%", thickness=0.6, color=STONE, spaceAfter=8))
story.append(P("6 · 优先级总览", sH1))

summary_data = [
    ["优先级", "资源项", "数量", "备注"],
    [priority_cell("P0","阻塞上线"), P("高德地图自定义样式 JSON\n酒店 / 景点 SVG Marker（3 种）\n西安景点插画\n导航栏 4 个图标\nFraunces + Noto Sans SC 字体接入"), P("2 个\n3 种\n6 张\n4 个\n2 款"), P("样式 JSON 已初稿完成")],
    [priority_cell("P1","完善体验"), P("通勤模式图标（步行 / 公交 / 驾车）\n位置 pin 图标\n避雷 ⚠ 徽章图标"), P("3 个\n1 个\n1 个"), P("可先用 emoji 占位")],
    [priority_cell("P2","扩展"),    P("其他城市景点插画（每城 6 张）\nNoto Serif SC 字体\n刷新图标"), P("按需\n1 款\n1 个"), P("按城市开通顺序补充")],
]
st = Table(summary_data, colWidths=[28*mm, 100*mm, 22*mm, 22*mm])
st.setStyle(tbl_style(CLAY))
story.append(st)
story.append(Spacer(1, 8))

# ── 7. 插画外包简报 ───────────────────────────────────────────────────────────
story.append(HRFlowable(width="100%", thickness=0.6, color=STONE, spaceAfter=8))
story.append(P("7 · 插画外包 / AI 生图 Prompt 参考", sH1))
story.append(P("可直接发给设计师 / Midjourney / Stable Diffusion：", sNote))
story.append(Spacer(1, 4))

prompt_text = (
    "Paper-cut flat illustration of [LANDMARK NAME], "
    "solid pastel color blocks without outlines, large simple geometric forms, "
    "minimal dry-brush stroke details, childlike hand-drawn style, "
    "imperfect organic silhouettes, warm muted palette "
    "(sand #e8dec3, sage green #8aa564, clay #c0855a, sky blue #9fb6bf), "
    "lots of negative space, Japanese stationery aesthetic, "
    "portrait orientation 400x560px, no text, no border, PNG transparent background."
)
story.append(P(prompt_text, ParagraphStyle(
    "prompt", fontName=FONT, fontSize=8, leading=13,
    textColor=MOSS, backColor=SAND,
    leftIndent=8, rightIndent=8, spaceBefore=4, spaceAfter=4,
    borderPad=8, borderRadius=6,
)))

story.append(Spacer(1, 4))
story.append(P("将 [LANDMARK NAME] 替换为：Bell Tower Xi'an / Tang Paradise Xi'an / Terracotta Warriors / Xi'an City Wall / Shaanxi History Museum / Muslim Quarter Xi'an", sNote))

# ── 页脚 ──────────────────────────────────────────────────────────────────────
story.append(Spacer(1, 12))
story.append(HRFlowable(width="100%", thickness=0.6, color=STONE, spaceAfter=6))
story.append(P("旅途向导 · 内部文档 · 2026.06", sNote))

# ── 构建 PDF ──────────────────────────────────────────────────────────────────
doc.build(story)
print("PDF 已生成：旅途向导_资源需求表.pdf")
