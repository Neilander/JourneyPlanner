# 阶段一搭建指南：打通「微信客服 ↔ 云端」+ 实测携程小程序卡片

> **本阶段目标（两件事）**
> 1. C 端用户在**微信**里加你的**微信客服**（云端机器人）→ 发消息 → 云端能收到、解密、打印。
> 2. **实测命门**：让用户转发一张**携程酒店小程序卡片**，把回调里的 `pagepath` 打印出来——
>    这是后续"抠 hotelId → 拼 URL → 爬数据"整条链路能不能成的关键，必须先验证。

```
微信用户 ──加微信客服、发消息──▶ 微信客服 ──回调"有新消息"──▶ [腾讯云:80]
                                                              FastAPI /wecom/callback
                                                              ↓ 收到事件，拿 Token
                                                              调 sync_msg 拉取真实消息
                                                              ↓ 打印 miniprogram.pagepath ★
```

> ⚠️ 微信客服和"自建应用"不一样：回调只是通知"有新消息"，**真正的消息内容要再调 `sync_msg` 接口拉取**。

云厂商：腾讯云轻量。预计 1–2 天（不含备案，备案见 Step 0）。

---

## Step 0　备案：本阶段不需要，降级为"以后升级时再办"

- **MVP 0 备案**：结果都在**微信客服会话里**回（避雷图文 + 高德静态地图图片），全程不出微信、不做自有域名 H5 → **不触发备案**。
- 阶段一回调走 `http://IP/...`，是服务器间通信，更不需要备案。
- **以后**要做"微信内可交互地图 H5"时再备：主体用**个体工商户**（凭身份证 1–3 天出证、免费）走单位备案，比个人备案过审顺。**现在不用管。**

---

## Step 1　买腾讯云轻量服务器
1. [腾讯云轻量控制台](https://console.cloud.tencent.com/lighthouse) → 新建实例。
2. **地域**：国内（上海/广州/北京），别选海外。
3. **镜像**：纯系统 → **Ubuntu Server 22.04 LTS**。
4. **套餐**：2核2G 起步。创建后记下**公网 IP**。

## Step 2　开放端口（最容易踩坑）
轻量控制台 → 防火墙 → 放行 `22` / `80` / `443`。
> 80 不开，微信客服回调验证必失败。

## Step 3　装环境
```bash
sudo apt update
sudo apt install -y python3.11 python3-pip python3.11-venv git
mkdir -p ~/journeyplanner && cd ~/journeyplanner
python3.11 -m venv .venv && source .venv/bin/activate
pip install fastapi "uvicorn[standard]" wechatpy pycryptodome python-dotenv requests
```

---

## Step 4　开通微信客服 + 拿 4 个凭证
在企业微信管理后台：
1. **应用管理 → 微信客服**，开通，创建一个客服账号（拿到 **OpenKfId**，也可代码里动态取）。
2. 拿到 **微信客服的 Secret**（微信客服页面里，不是自建应用的 Secret）。
3. **接收消息配置**里设置回调，生成 **Token** 和 **EncodingAESKey**。
4. 企业 ID **CorpID**：我的企业 → 企业信息。

把它们写进 `~/journeyplanner/.env`：
```ini
WECOM_CORP_ID=企业ID
WECOM_KF_SECRET=微信客服的Secret
WECOM_TOKEN=回调配置的Token
WECOM_AES_KEY=回调配置的43位EncodingAESKey
```

---

## Step 5　最小回调服务（核心：打印小程序卡片 pagepath）
新建 `~/journeyplanner/main.py`：
```python
import os, json
import xml.etree.ElementTree as ET
import requests
from fastapi import FastAPI, Request, Response
from dotenv import load_dotenv
from wechatpy.work.crypto import WeChatCrypto   # 版本报错则改 wechatpy.enterprise.crypto

load_dotenv()
CORP_ID   = os.environ["WECOM_CORP_ID"]
KF_SECRET = os.environ["WECOM_KF_SECRET"]
TOKEN     = os.environ["WECOM_TOKEN"]
AES_KEY   = os.environ["WECOM_AES_KEY"]

app = FastAPI()
crypto = WeChatCrypto(TOKEN, AES_KEY, CORP_ID)
_cursor = None   # 实测期用内存存游标即可


def get_access_token():
    r = requests.get("https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                     params={"corpid": CORP_ID, "corpsecret": KF_SECRET}).json()
    return r["access_token"]


@app.get("/wecom/callback")
async def verify(msg_signature: str, timestamp: str, nonce: str, echostr: str):
    # 配置回调 URL 时企微发 GET 验证，解密 echostr 原样返回
    return Response(content=crypto.check_signature(msg_signature, timestamp, nonce, echostr))


@app.post("/wecom/callback")
async def receive(request: Request, msg_signature: str, timestamp: str, nonce: str):
    global _cursor
    body = await request.body()
    xml = crypto.decrypt_message(body, msg_signature, timestamp, nonce)
    sync_token = ET.fromstring(xml).findtext("Token")   # 事件里带的拉取凭证
    if not sync_token:
        return Response(content="success")

    # 微信客服：拿 Token 调 sync_msg 拉真实消息
    payload = {"token": sync_token, "limit": 1000}
    if _cursor:
        payload["cursor"] = _cursor
    resp = requests.post("https://qyapi.weixin.qq.com/cgi-bin/kf/sync_msg",
                         params={"access_token": get_access_token()}, json=payload).json()
    _cursor = resp.get("next_cursor", _cursor)

    for m in resp.get("msg_list", []):
        print("=== 消息类型:", m.get("msgtype"))
        if m.get("msgtype") == "miniprogram":
            print("★【小程序卡片】", json.dumps(m["miniprogram"], ensure_ascii=False, indent=2))
        else:
            print(json.dumps(m, ensure_ascii=False, indent=2))
    return Response(content="success")   # 必须 5 秒内返回
```
裸跑：
```bash
sudo -E .venv/bin/uvicorn main:app --host 0.0.0.0 --port 80
```

## Step 6　配置回调 URL + 验证
微信客服 → 接收消息配置：
| 字段 | 填什么 |
| --- | --- |
| URL | `http://你的公网IP/wecom/callback` |
| Token / EncodingAESKey | 和 `.env` 完全一致 |

保存 → 企微发 GET 验证 → 服务返回解密 echostr → 显示"验证成功"。

---

## Step 7　★ 实测 pagepath（本阶段真正的目标）
1. 用**个人微信**扫码/搜索加上你的**微信客服**。
2. 在携程 App 打开任意酒店 → **分享 → 微信** → 发给这个客服。
3. 看服务器日志里 `★【小程序卡片】` 打印的内容，重点看 **`pagepath`** 长啥样（里面有没有 `hotelId` / `id` / `masterhotelid` 之类）。
4. **把这段 `pagepath` 原文发出来** → 据此定"抠 hotelId → 拼携程 URL → 爬数据"的解析规则。

> 顺手也各转一张**小红书 / 去哪儿**的卡片或链接，一起记录字段格式。

---

## 排错速查
| 现象 | 多半是 |
| --- | --- |
| 回调"验证失败" | 80 没开 / 服务没起 / Token·AESKey·CorpID 对不上 |
| 验证过了但收不到消息 | 微信客服没开"接收消息"，或 `sync_msg` 的 Secret 用错（要用微信客服的 Secret） |
| `sync_msg` 返回 errcode | access_token 取错 / Secret 不是微信客服的 |
| 同条消息反复推送 | 没在 5 秒内返回 `success` |
| `WeChatCrypto` import 报错 | 改 `from wechatpy.enterprise.crypto import WeChatCrypto` |
