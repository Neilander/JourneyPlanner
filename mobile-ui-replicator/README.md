# UI Replicator Skill

把一张 **UI 截图** + 一张 **风格参考图** 喂给 AI agent，输出一个**painted in that style 的可交互单页 HTML demo**（手机壳 + 真素材 + 真交互）。

不是 Claude Code 的安装式 skill，是个**自带说明书的便携文件夹**——丢给 agent 一句"读 SKILL.md 干活"就行。

## 最简单的使用方法

把压缩包放进项目里解压，让 AI 自己说该怎么 setup。

📦 **[下载最新 zip](https://github.com/Neilander/mobile-ui-replicator/releases/latest/download/mobile-ui-replicator.zip)**

## 它能干什么

输入：
- `ui.png` —— 你想复刻的 app 截图（任何手机 UI）
- `style.jpg` —— 你想要的画风（水彩 / 像素 / 油画 / 任意视觉语言）
- **功能描述** —— 用一段话或几行 bullet 告诉 agent：这是什么 app、屏幕上每个区块是干嘛的、哪些能交互、有什么特定文案要保留。**必填**——光看截图 agent 经常会猜错图标含义、卡片分类、小字内容，给段描述准确率立刻拉满。
- `name` —— 项目名

输出：
```
output/<name>/
├── index.html          ← 单文件 HTML，浏览器直接打开
├── assets/*.png        ← Gemini 生成的所有素材
└── *.json / *.txt      ← 中间产物，方便单张重生
```

总耗时 ~1-3 分钟，主要花在素材生成。

## 准备工作（一次性）

### 1. 装依赖

```bash
pip install pillow scipy numpy
```

只有 `flood_fill.py`（mascot 抠图）需要这些。Gemini 调用走 `urllib`，不需要 SDK。

### 2. 填 key

打开 `.env`，把占位值替换成真 key：

```
GEMINI_API_KEY=PASTE_YOUR_GEMINI_KEY_HERE   ← 替换
```

- Gemini key: https://aistudio.google.com/apikey

**只需要这一个 key。** Agent 自己看图、写 HTML 用的是它本身的能力（你跟 Claude / Cursor / 任何 agent 对话时已经在用了），不需要单独的 Anthropic key。Gemini key 只给 `gen_image.py` 用来出图。

`.env` 已经 gitignore 了，安全。

### 3. （强烈推荐）装 `frontend-design` skill

写 HTML 那步会自动调它做 polish——动效、间距、微交互这些细节差距很大。**不装也能跑，但 HTML 质量明显差一档。**

> TODO: `frontend-design` skill 的具体安装方式回头补

### 4. 验证

```bash
python3 scripts/load_keys.py
```

应该输出两行 `loaded`。如果报"placeholder values"就是 key 没填。

## 使用方式

把这段话喂给你的 agent（Claude Code 或任何能跑 Bash + 看图的 agent），**带上 3 个东西：UI 截图、风格图、功能描述**：

```
读 /<absolute-path>/ui-replicator-skill/SKILL.md
帮我把 ./my-ui.png + ./watercolor.jpg 做成 demo，名字叫 cafe-finder

功能描述：
- 这是个咖啡馆发现 app，主屏首页
- 顶部 hero 是一张大的咖啡馆插画 + 标题"今日推荐"
- hero 下面是分类瓷砖（手冲/拿铁/甜品/外带），点了进二级页
- 再下面是横向滚动的"附近的店"卡片列表，每张卡片有店名+距离
- 底部 tab 栏：发现 / 收藏 / 我的（当前在"发现"）
- 没有吉祥物
```

如果你没给功能描述，agent 会先停下来问你要——光看截图它经常猜错。

Agent 拿到 3 个输入后接管 5 步流程：

1. 看一眼参考 HTML（`references/forest-journal.html`）了解输出长什么样
2. 看风格图，填 `style-preamble.txt`
3. 看 UI 截图 + 读你的功能描述，出 `layout.json` + `assets.json`
4. 并发调 `gen_image.py` 出所有素材，mascot 跑 `flood_fill.py` 抠图
5. 调 `frontend-design` skill（如果装了）写 `index.html`，开浏览器自检

完事打开 `output/cafe-finder/index.html` 看结果。

## 单张重生

跑完一遍如果某张图不满意：

```
agent，重新生成 cafe-finder 的 hero
```

Agent 会从 `assets.json` 读 hero 的 prompt，只调 `gen_image.py` 重生那一张。整套流程不重跑。

## 文件结构

```
ui-replicator-skill/
├── SKILL.md                      ← agent 入口，5 步流程
├── README.md                     ← 本文档
├── .env                          ← 填 key
├── .gitignore                    ← 锁住 .env / output
├── prompts/
│   ├── style-extraction.md       ← Step 2 prompt
│   ├── analyze-ui.md             ← Step 3 prompt + JSON schema
│   └── scaffold-html.md          ← Step 5 prompt + 硬约束
├── scripts/
│   ├── load_keys.py              ← .env 加载器
│   ├── gen_image.py              ← Gemini 单张生图 CLI
│   └── flood_fill.py             ← PIL+scipy 抠图 CLI
└── references/
    └── forest-journal.html       ← 质量基准（agent 必看）
```

## 已知限制

- **单屏 app only**——不做多屏跳转、登录流、设置页
- **iOS 竖屏壳固定 390×844**——不做 Android、平板、横屏
- **素材数量**约束在 ~10-15 张以内——超了 Gemini 限流，agent 会重试但拖时间
- **map 类素材会拒绝在底图上画文字**——所有街道名、POI 标签走 CSS 叠加层（这是 feature 不是 bug，Gemini 写中文会乱码）
