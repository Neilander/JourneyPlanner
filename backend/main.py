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

def get_access_token():
    r = requests.get("https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                     params={"corpid": CORP_ID, "corpsecret": KF_SECRET}).json()
    return r.get("access_token", "")

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
        print("=== 消息类型:", m.get("msgtype"))
        if m.get("msgtype") == "miniprogram":
            print("★【小程序卡片】", json.dumps(m["miniprogram"], ensure_ascii=False, indent=2))
        else:
            print(json.dumps(m, ensure_ascii=False, indent=2))
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
