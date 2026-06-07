import os, json
import xml.etree.ElementTree as ET
import requests
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from wechatpy.enterprise.crypto import WeChatCrypto

load_dotenv()
CORP_ID      = os.environ["WECOM_CORP_ID"]
KF_SECRET    = os.environ["WECOM_KF_SECRET"]
TOKEN        = os.environ["WECOM_TOKEN"]
AES_KEY      = os.environ["WECOM_AES_KEY"]
AMAP_WEB_KEY = os.environ["AMAP_WEB_KEY"]

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://trip.neiland.xyz"],
    allow_methods=["*"],
    allow_headers=["*"],
)
crypto = WeChatCrypto(TOKEN, AES_KEY, CORP_ID)
_cursor = None

PLAN_KEYWORDS = ["酒店", "住", "规划", "行程", "景点", "旅游", "旅行", "攻略", "住哪", "去哪", "西安"]
H5_URL = "https://trip.neiland.xyz"

def get_access_token():
    r = requests.get("https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                     params={"corpid": CORP_ID, "corpsecret": KF_SECRET}).json()
    return r.get("access_token", "")

def accept_session(open_kfid: str, user_id: str, token: str):
    r = requests.post("https://qyapi.weixin.qq.com/cgi-bin/kf/session/accept",
                      params={"access_token": token},
                      json={"open_kfid": open_kfid, "external_userid": user_id})
    print("accept_session:", r.json())

def send_text(open_kfid: str, user_id: str, text: str):
    token = get_access_token()
    accept_session(open_kfid, user_id, token)
    r = requests.post("https://qyapi.weixin.qq.com/cgi-bin/kf/send_msg",
                      params={"access_token": token},
                      json={"touser": user_id, "open_kfid": open_kfid,
                            "msgtype": "text", "text": {"content": text}})
    print("send_msg:", r.json())

@app.get("/wecom/callback")
async def verify(msg_signature: str, timestamp: str, nonce: str, echostr: str):
    return Response(content=crypto.check_signature(msg_signature, timestamp, nonce, echostr))

@app.post("/wecom/callback")
async def receive(request: Request, msg_signature: str, timestamp: str, nonce: str):
    global _cursor
    body = await request.body()
    xml = crypto.decrypt_message(body, msg_signature, timestamp, nonce)
    root = ET.fromstring(xml)
    sync_token = root.findtext("Token")
    open_kf_id = root.findtext("OpenKfId")
    if not sync_token:
        return Response(content="success")
    payload = {"token": sync_token, "open_kfid": open_kf_id, "limit": 1000}
    if _cursor:
        payload["cursor"] = _cursor
    resp = requests.post("https://qyapi.weixin.qq.com/cgi-bin/kf/sync_msg",
                         params={"access_token": get_access_token()}, json=payload).json()
    print("sync_msg响应:", json.dumps(resp, ensure_ascii=False))
    _cursor = resp.get("next_cursor", _cursor)
    for m in resp.get("msg_list", []):
        print("=== 消息类型:", m.get("msgtype"), json.dumps(m, ensure_ascii=False))
        if m.get("msgtype") != "text":
            continue
        user_id = m.get("external_userid") or m.get("open_id", "")
        text = m.get("text", {}).get("content", "")
        if any(kw in text for kw in PLAN_KEYWORDS):
            send_text(open_kf_id, user_id,
                      f"你好！点击下方链接开始规划西安行程 👇\n{H5_URL}\n\n"
                      "可以搜索候选酒店，选择想去的景点，查看通勤时间排行～")
        else:
            send_text(open_kf_id, user_id,
                      "你好！我是西安旅行规划助手 🗺️\n"
                      "告诉我你想去哪些景点或者想找什么酒店，我来帮你规划行程！")
    return Response(content="success")


@app.get("/api/poi/search")
async def poi_search(keyword: str, city: str = "西安"):
    r = requests.get("https://restapi.amap.com/v3/place/text", params={
        "key": AMAP_WEB_KEY,
        "keywords": keyword,
        "city": city,
        "citylimit": "true",
        "types": "床和早餐|旅馆|酒店",
        "offset": 10,
        "output": "json",
    }).json()
    pois = []
    for p in r.get("pois", []):
        lng, lat = p["location"].split(",")
        pois.append({
            "id": p["id"],
            "name": p["name"],
            "address": p.get("address", ""),
            "lng": float(lng),
            "lat": float(lat),
        })
    return {"pois": pois}
