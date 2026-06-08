import os, json, re, sqlite3
import xml.etree.ElementTree as ET
import requests
from fastapi import FastAPI, Request, Response, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from wechatpy.enterprise.crypto import WeChatCrypto

load_dotenv()
CORP_ID      = os.environ["WECOM_CORP_ID"]
KF_SECRET    = os.environ["WECOM_KF_SECRET"]
TOKEN        = os.environ["WECOM_TOKEN"]
AES_KEY      = os.environ["WECOM_AES_KEY"]
AMAP_WEB_KEY = os.environ["AMAP_WEB_KEY"]
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

H5_URL = "https://trip.neiland.xyz"
DB_PATH = os.path.join(os.path.dirname(__file__), "journeyplanner.db")

# ── 数据库 ────────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            wecom_id    TEXT UNIQUE NOT NULL,
            bot_state   INTEGER DEFAULT 1,  -- 1=onboarding 2=chitchat 3=import 4=delivery
            city        TEXT DEFAULT '西安',
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS hotels (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            name        TEXT NOT NULL,
            source_url  TEXT,
            hotel_id    TEXT,
            lat         REAL,
            lng         REAL,
            rating      TEXT,
            raw_text    TEXT,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS kv (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        """)

init_db()

def kv_get(key: str) -> str | None:
    with get_db() as conn:
        row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

def kv_set(key: str, value: str):
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, value))
        conn.commit()

def get_or_create_user(wecom_id: str) -> sqlite3.Row:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE wecom_id=?", (wecom_id,)).fetchone()
        if not row:
            conn.execute("INSERT INTO users (wecom_id, bot_state) VALUES (?, 1)", (wecom_id,))
            conn.commit()
            row = conn.execute("SELECT * FROM users WHERE wecom_id=?", (wecom_id,)).fetchone()
        return dict(row)

def set_bot_state(wecom_id: str, state: int):
    with get_db() as conn:
        conn.execute("UPDATE users SET bot_state=? WHERE wecom_id=?", (state, wecom_id))
        conn.commit()

def save_hotel(user_id: int, name: str, source_url: str = "", hotel_id: str = "",
               lat: float = None, lng: float = None, rating: str = "", raw_text: str = ""):
    with get_db() as conn:
        conn.execute("""
            INSERT INTO hotels (user_id, name, source_url, hotel_id, lat, lng, rating, raw_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, name, source_url, hotel_id, lat, lng, rating, raw_text))
        conn.commit()

def get_hotel_count(user_id: int) -> int:
    with get_db() as conn:
        return conn.execute("SELECT COUNT(*) FROM hotels WHERE user_id=?", (user_id,)).fetchone()[0]

def get_hotels(user_id: int) -> list:
    with get_db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM hotels WHERE user_id=? ORDER BY created_at DESC", (user_id,)
        ).fetchall()]

# ── 携程链接解析 ───────────────────────────────────────────────────────────────

CTRIP_TEXT_RE = re.compile(
    r'[<＜]([^<>＜＞]+)[>＞]\s*[<＜]([^<>＜＞]+)[>＞]'  # <城市><酒店名>
)
CTRIP_RATING_RE = re.compile(r'([0-9](?:\.[0-9])?)\s*分')
CTRIP_ID_RE     = re.compile(r'hotelid=(\d+)', re.I)
CTRIP_URL_RE    = re.compile(r'https?://[^\s]+')

def parse_ctrip_text(text: str) -> dict | None:
    """解析携程分享文本，返回 {city, name, hotel_id, rating, url} 或 None"""
    pair = CTRIP_TEXT_RE.search(text)
    if not pair:
        return None
    city  = pair.group(1).strip()
    name  = pair.group(2).strip()
    hid   = (CTRIP_ID_RE.search(text) or [None])[0]
    hid   = CTRIP_ID_RE.search(text)
    rating = CTRIP_RATING_RE.search(text)
    url    = CTRIP_URL_RE.search(text)
    return {
        "city":     city,
        "name":     name,
        "hotel_id": hid.group(1) if hid else "",
        "rating":   rating.group(1) if rating else "",
        "url":      url.group(0) if url else "",
    }

def amap_geocode(name: str, city: str) -> tuple[float, float] | tuple[None, None]:
    """用高德POI搜索拿经纬度"""
    try:
        r = requests.get("https://restapi.amap.com/v3/place/text", params={
            "key": AMAP_WEB_KEY, "keywords": name, "city": city,
            "citylimit": "true", "offset": 1, "output": "json",
        }, timeout=5).json()
        pois = r.get("pois", [])
        if pois:
            lng, lat = pois[0]["location"].split(",")
            return float(lat), float(lng)
    except Exception as e:
        print("geocode error:", e)
    return None, None

# ── 意图识别 ──────────────────────────────────────────────────────────────────

DONE_KEYWORDS  = ["好了", "看结果", "没了", "完了", "结果", "看看", "对比", "比较"]
HOTEL_KEYWORDS = ["酒店", "民宿", "旅馆", "客栈", "住", "房间", "携程", "去哪儿", "美团"]

def classify_intent(text: str, msgtype: str) -> str:
    """返回 intent: onboarding | import | done | chitchat"""
    if msgtype in ("image", "miniprogram"):
        return "import"
    if parse_ctrip_text(text):
        return "import"
    if re.search(r'https?://', text) and any(k in text for k in ["ctrip", "trip", "hotel", "酒店", "qunar", "meituan"]):
        return "import"
    if any(k in text for k in DONE_KEYWORDS):
        return "done"
    if any(k in text for k in HOTEL_KEYWORDS):
        return "import_hint"  # 提到酒店但没发链接
    return "chitchat"

# ── DeepSeek 闲聊 ─────────────────────────────────────────────────────────────

PERSONA_PROMPT = """你是「旅途向导」，一个专注西安旅行规划的AI助手，风格：亲切、风趣、简洁。
你帮用户规划西安行程——找酒店、看景点、统筹通勤距离。
如果用户问旅行相关问题就认真回答；如果用户闲聊就顺着说几句然后引导回旅行话题。
回复控制在100字以内，不用加emoji堆砌。"""

def deepseek_chat(user_msg: str) -> str:
    if not DEEPSEEK_KEY:
        return "有什么关于西安旅行的问题都可以问我～发酒店链接或截图，我帮你整理候选名单！"
    try:
        r = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_KEY}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "max_tokens": 200,
                  "messages": [{"role": "system", "content": PERSONA_PROMPT},
                                {"role": "user",   "content": user_msg}]},
            timeout=15
        ).json()
        return r["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print("deepseek error:", e)
        return "西安的旅行话题我都能聊～有酒店想加进候选名单吗？把链接发给我就行！"

# ── Bot 状态机 ────────────────────────────────────────────────────────────────

def handle_user_message(open_kfid: str, user_id: str, text: str, msgtype: str):
    user = get_or_create_user(user_id)
    bot_state = user["bot_state"]
    hotel_count = get_hotel_count(user["id"])
    intent = classify_intent(text, msgtype)

    # ── 状态1：Onboarding（首次进入）────────────────────────────────────────
    if bot_state == 1:
        set_bot_state(user_id, 2)
        send_text(open_kfid, user_id,
            "你好！我是西安旅行规划助手 🗺️\n\n"
            "使用方式很简单：\n"
            "① 把携程/去哪儿的酒店分享文本或链接发给我\n"
            "② 我帮你存好候选名单\n"
            "③ 发「看结果」打开地图，按景点距离排酒店\n\n"
            f"现在可以直接发酒店链接开始～或者打开规划页面：{H5_URL}")
        return

    # ── 状态2：闲聊（中枢）──────────────────────────────────────────────────
    # 分支A：导入意图
    if intent == "import":
        ctrip = parse_ctrip_text(text)
        if ctrip:
            lat, lng = amap_geocode(ctrip["name"], ctrip["city"])
            save_hotel(
                user_id=user["id"],
                name=ctrip["name"],
                source_url=ctrip["url"],
                hotel_id=ctrip["hotel_id"],
                lat=lat, lng=lng,
                rating=ctrip["rating"],
                raw_text=text[:500]
            )
            hotel_count += 1
            loc_str = f"📍 已定位到地图" if lat else "（坐标定位失败，后续补）"
            send_text(open_kfid, user_id,
                f"✅ 已记录：{ctrip['name']}"
                + (f"（{ctrip['rating']}分）" if ctrip["rating"] else "")
                + f"\n{loc_str}\n\n"
                f"当前候选酒店：{hotel_count} 家\n"
                f"继续发酒店，或发「看结果」打开对比页面")
        else:
            send_text(open_kfid, user_id,
                "收到！不过我没识别出携程格式。\n\n"
                "请发**携程分享文本**（在携程 App 里点分享→复制文字），格式像这样：\n"
                "「#携程旅行# <城市><酒店名> X.X分...」\n\n"
                "或者直接把链接粘过来也行～")
        return

    # 分支A-hint：提到酒店但没发数据
    if intent == "import_hint":
        send_text(open_kfid, user_id,
            "想加酒店到候选名单吗？\n\n"
            "把携程/去哪儿里的酒店分享文本或链接发给我，我来记录 👇")
        return

    # 分支B：完成导入，看结果
    if intent == "done":
        if hotel_count == 0:
            send_text(open_kfid, user_id,
                "还没有候选酒店呢～\n\n"
                "先把想考虑的酒店链接或携程分享文本发给我，我帮你存好，再来看对比结果！")
        else:
            send_text(open_kfid, user_id,
                f"已收录 {hotel_count} 家候选酒店 🏨\n\n"
                f"点击下方链接，在地图上看各酒店到景点的通勤距离 👇\n{H5_URL}\n\n"
                "选好景点后会自动按距离排名～")
        return

    # 分支C：普通闲聊
    reply = deepseek_chat(text)
    send_text(open_kfid, user_id, reply)

# ── WeChat KF 基础设施 ────────────────────────────────────────────────────────

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://trip.neiland.xyz"],
    allow_methods=["*"],
    allow_headers=["*"],
)
crypto = WeChatCrypto(TOKEN, AES_KEY, CORP_ID)
_processed_msg_ids: set = set()

def get_access_token():
    r = requests.get("https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                     params={"corpid": CORP_ID, "corpsecret": KF_SECRET}).json()
    return r.get("access_token", "")

def ensure_session(open_kfid: str, user_id: str, token: str) -> bool:
    r = requests.post("https://qyapi.weixin.qq.com/cgi-bin/kf/service_state/get",
                      params={"access_token": token},
                      json={"open_kfid": open_kfid, "external_userid": user_id})
    state = r.json()
    service_state = state.get("service_state", -1)
    if service_state == 0:
        r2 = requests.post("https://qyapi.weixin.qq.com/cgi-bin/kf/service_state/trans",
                           params={"access_token": token},
                           json={"open_kfid": open_kfid, "external_userid": user_id,
                                 "service_state": 1})
        return r2.json().get("errcode", -1) == 0
    elif service_state == 1:
        return True
    else:
        print(f"skip send: session in state {service_state}")
        return False

def send_text(open_kfid: str, user_id: str, text: str):
    token = get_access_token()
    if not ensure_session(open_kfid, user_id, token):
        return
    r = requests.post("https://qyapi.weixin.qq.com/cgi-bin/kf/send_msg",
                      params={"access_token": token},
                      json={"touser": user_id, "open_kfid": open_kfid,
                            "msgtype": "text", "text": {"content": text}})
    print("send_msg:", r.json())

def process_messages(sync_token: str, open_kf_id: str):
    cursor = kv_get("sync_cursor")
    payload = {"token": sync_token, "open_kfid": open_kf_id, "limit": 1000}
    if cursor:
        payload["cursor"] = cursor
    resp = requests.post("https://qyapi.weixin.qq.com/cgi-bin/kf/sync_msg",
                         params={"access_token": get_access_token()}, json=payload).json()
    print("sync_msg:", json.dumps(resp, ensure_ascii=False))
    next_cursor = resp.get("next_cursor")
    if next_cursor:
        kv_set("sync_cursor", next_cursor)
    for m in resp.get("msg_list", []):
        msg_id = m.get("msgid", "")
        if msg_id and msg_id in _processed_msg_ids:
            continue
        if msg_id:
            _processed_msg_ids.add(msg_id)
        msgtype = m.get("msgtype", "")
        user_id = m.get("external_userid", "")
        if not user_id:
            continue
        print("=== msg:", msgtype, json.dumps(m, ensure_ascii=False))
        if msgtype == "text":
            text = m.get("text", {}).get("content", "")
        elif msgtype == "miniprogram":
            text = json.dumps(m.get("miniprogram", {}), ensure_ascii=False)
        elif msgtype == "image":
            text = ""
        else:
            continue
        handle_user_message(open_kf_id, user_id, text, msgtype)

@app.get("/wecom/callback")
async def verify(msg_signature: str, timestamp: str, nonce: str, echostr: str):
    return Response(content=crypto.check_signature(msg_signature, timestamp, nonce, echostr))

@app.post("/wecom/callback")
async def receive(request: Request, background_tasks: BackgroundTasks,
                  msg_signature: str, timestamp: str, nonce: str):
    body = await request.body()
    xml = crypto.decrypt_message(body, msg_signature, timestamp, nonce)
    root = ET.fromstring(xml)
    sync_token = root.findtext("Token")
    open_kf_id = root.findtext("OpenKfId")
    if sync_token:
        background_tasks.add_task(process_messages, sync_token, open_kf_id)
    return Response(content="success")

# ── REST API ──────────────────────────────────────────────────────────────────

@app.get("/api/poi/search")
async def poi_search(keyword: str, city: str = "西安"):
    r = requests.get("https://restapi.amap.com/v3/place/text", params={
        "key": AMAP_WEB_KEY, "keywords": keyword, "city": city,
        "citylimit": "true", "types": "床和早餐|旅馆|酒店", "offset": 10, "output": "json",
    }).json()
    pois = []
    for p in r.get("pois", []):
        lng, lat = p["location"].split(",")
        pois.append({"id": p["id"], "name": p["name"],
                     "address": p.get("address", ""), "lng": float(lng), "lat": float(lat)})
    return {"pois": pois}

@app.get("/api/user/hotels")
async def user_hotels(wecom_id: str):
    user = get_or_create_user(wecom_id)
    return {"hotels": get_hotels(user["id"])}
