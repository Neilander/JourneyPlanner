# 前端部署与开发交接

> 给协作者:前端怎么改、怎么上线、有哪些坑。

## 一句话工作流
**改 `frontend/` 代码 → `git push` 到 `main` → Cloudflare 自动 build & 部署到 `trip.neiland.xyz`**(约 2–4 分钟)。
和个人网站一样的体验,不用手动操作。

## 部署机制
- 前端 = **Next.js 16 静态导出**,托管在 **Cloudflare Pages** 项目 `journeyplanner`。
- 仓库 `Neilander/JourneyPlanner`,**Root directory = `frontend`**。
- 生产域名 `trip.neiland.xyz`,预览域名 `journeyplanner-dy1.pages.dev`。
- Build 配置(已在 Cloudflare 设好):command `npm run build`,output `out`,`NODE_VERSION=20`。

## ⚠️ 静态导出约束(最重要,违反会 build 失败)
`next.config.ts` 设了 `output: 'export'` —— **没有服务端运行时**。
- ❌ 不能用:SSR / `getServerSideProps` / `app/api` 路由 / server actions / 动态 middleware。
- ✅ 能用:纯客户端组件(`'use client'`)+ `fetch` 调外部后端 API。
- 任何用 `window` / `document` / 浏览器专属库(如高德)的代码,**必须放进 `useEffect` 或用动态 `await import(...)`**,否则预渲染阶段报 `window is not defined`。

## 本次(交接前)已做的改动
- `next.config.ts`:加 `output: 'export'` + `images.unoptimized`。
- `app/page.tsx`:高德 `amap-jsapi-loader` 从顶层 import 改为 `useEffect` 内动态 import(功能不变,只为静态导出能 build)。

## 「一人一页」实现规格(后端为主)

目标:每个微信用户有专属链接 `trip.neiland.xyz/?u=<userId>`,打开看到自己导入的酒店。
**不要**用 `/u/[id]` 动态路由(静态导出对无限用户不友好),统一用查询参数 `?u=`。

### 链路
```
用户在微信客服发酒店（携程卡片/截图/搜索）
  → 后端解析出酒店，存到该用户名下（userId）
  → 后端回复链接：trip.neiland.xyz/?u=<userId>
  → 用户点开 → 前端读 ?u → GET 该用户酒店 → 渲染到地图
```

### userId 怎么定
- 微信客服消息里带 `external_userid`(微信用户在本企业下的唯一 id)。
- 建议存一张 `users` 表做映射:`external_userid ↔ 短 userId`(如 8 位 nanoid),**链接里用短 userId**(更短、不暴露 external_userid)。
- 同一个用户多次发酒店 → 复用同一个 userId,酒店往他名下累加。

### 接口契约(前端依赖这个,先定死)
**`GET /api/users/{userId}/hotels`**
```json
{
  "city": "西安",
  "center": [108.94, 34.34],
  "hotels": [
    { "id": "h1", "name": "西安钟楼亚朵", "address": "碑林区…",
      "lng": 108.94, "lat": 34.26, "rating": 4.7,
      "source": "携程", "source_url": "https://…" }
  ]
}
```
- 找不到 userId → 返回 `404` 或 `{ "hotels": [] }`。
- 🔴 **必须开 CORS**:响应头 `Access-Control-Allow-Origin: https://trip.neiland.xyz`(否则前端跨域被拦)。
- 🔴 **必须走 https**:前端是 https,这个接口也得 https(见下方"待修的坑"第 2 条)。

### 数据存储(最小表)
```
users:  userId(PK, 短id) / external_userid / created_at
hotels: id(PK) / user_id(FK→users) / name / address / lng / lat / rating / source / source_url / created_at
```

### 写入时机
微信客服收到酒店消息 → 解析出酒店字段 → 若该 `external_userid` 还没 userId 就建一个 → 插入 hotel 关联到 user_id → 回复链接 `trip.neiland.xyz/?u=<userId>`。

### 前端对接(已约定,前端按此调用)
```js
const u = new URLSearchParams(location.search).get('u')
if (u) {
  fetch(`${API_BASE}/api/users/${u}/hotels`)
    .then(r => r.json())
    .then(d => { setHotels(d.hotels) /* + 地图中心移到 d.center */ })
}
// 不带 ?u= 时保持现有手动搜索模式不变
```

## 本地开发
```bash
cd frontend
npm install
npm run dev        # 本地开发 localhost:3000（dev 不受静态导出限制）
npm run build      # push 前自测，能过再 push
```

## 待修的坑(按优先级)
1. 🔴 **高德密钥泄露**:`page.tsx` 里 `AMAP_SECRET` 明文,前端可见 → **轮换密钥** + 走后端代理或域名白名单。
2. 🟠 **mixed content**:前端是 `https`,但调的后端是 `http://119.45.235.102`,浏览器会拦(搜索功能现在就是因此失效)→ 后端要上 `https`(建议给后端绑 `api.neiland.xyz` 走 Cloudflare)。
3. 🟠 **仓库后端 ≠ 线上后端**:`backend/main.py` 只有微信客服回调,但线上 `119.45.235.102` 有 `/api/poi/search` → 把线上后端代码提交进仓库,前后端对齐。
4. ⚠️ **备案/微信内打开**:`trip.neiland.xyz` 是 Cloudflare 海外、未备案,**在微信内打开会被拦/很慢**。产品形态待定(结果在客服会话回图文 vs 备案后微信内开 H5)。
