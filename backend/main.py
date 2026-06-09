import os, json, re, sqlite3, threading, base64
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
DEEPSEEK_KEY     = os.environ.get("DEEPSEEK_API_KEY", "")
BAIDU_OCR_API_KEY    = os.environ.get("BAIDU_OCR_API_KEY", "")
BAIDU_OCR_SECRET_KEY = os.environ.get("BAIDU_OCR_SECRET_KEY", "")

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
            city        TEXT DEFAULT '',
            source_url  TEXT,
            hotel_id    TEXT,
            lat         REAL,
            lng         REAL,
            rating      TEXT,
            raw_text    TEXT,
            platform    TEXT DEFAULT '',
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS kv (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS processed_msgs (
            msgid TEXT PRIMARY KEY,
            ts    DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """)

init_db()

# ── 数据库迁移（新增列兼容旧库）────────────────────────────────────────────────
def migrate_db():
    with get_db() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(hotels)").fetchall()]
        if "city" not in cols:
            conn.execute("ALTER TABLE hotels ADD COLUMN city TEXT DEFAULT ''")
            print("DB migration: added city column to hotels")
        if "platform" not in cols:
            conn.execute("ALTER TABLE hotels ADD COLUMN platform TEXT DEFAULT ''")
            print("DB migration: added platform column to hotels")
        conn.commit()

migrate_db()

def kv_get(key: str) -> str | None:
    with get_db() as conn:
        row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

def kv_set(key: str, value: str):
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, value))
        conn.commit()

def is_processed(msgid: str) -> bool:
    """检查msgid是否已处理过，是则返回True，否则写入并返回False"""
    try:
        with get_db() as conn:
            conn.execute("INSERT INTO processed_msgs (msgid) VALUES (?)", (msgid,))
            conn.commit()
            return False
    except sqlite3.IntegrityError:
        return True

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

def set_user_city(wecom_id: str, city: str):
    with get_db() as conn:
        conn.execute("UPDATE users SET city=? WHERE wecom_id=?", (city, wecom_id))
        conn.commit()

def save_hotel(user_id: int, name: str, source_url: str = "", hotel_id: str = "",
               lat: float = None, lng: float = None, rating: str = "", raw_text: str = "",
               city: str = "", platform: str = ""):
    with get_db() as conn:
        conn.execute("""
            INSERT INTO hotels (user_id, name, city, source_url, hotel_id, lat, lng, rating, raw_text, platform)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, name, city, source_url, hotel_id, lat, lng, rating, raw_text, platform))
        conn.commit()

def get_hotel_count(user_id: int) -> int:
    with get_db() as conn:
        return conn.execute("SELECT COUNT(*) FROM hotels WHERE user_id=?", (user_id,)).fetchone()[0]

def get_hotels(user_id: int) -> list:
    with get_db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM hotels WHERE user_id=? ORDER BY created_at DESC", (user_id,)
        ).fetchall()]

# ── 多平台酒店解析 ────────────────────────────────────────────────────────────

URL_RE     = re.compile(r'https?://[^\s]+')
RATING_RE  = re.compile(r'([0-9](?:\.[0-9])?)\s*分')
HOTEL_ID_RE = re.compile(r'hotelid=(\d+)', re.I)

EXTRACT_PROMPT = """从下面的酒店分享文本中提取信息，以JSON格式返回：
{"name": "酒店全名", "city": "城市名（只要城市，不要省份）", "rating": "评分数字或空字符串"}
如果无法识别酒店信息，返回 null。
只返回JSON，不要其他内容。"""

PLATFORM_PATTERNS = [
    ("携程",   ["ctrip.com", "trip.com", "携程"]),
    ("去哪儿", ["qunar.com", "去哪儿", "去哪网"]),
    ("美团",   ["meituan.com", "美团"]),
    ("飞猪",   ["fliggy.com", "alitrip.com", "飞猪"]),
    ("同程",   ["tongcheng.com", "ly.com", "同程"]),
    ("大众点评", ["dianping.com", "大众点评"]),
    ("小红书", ["xiaohongshu.com", "xhslink.com", "小红书"]),
]

def detect_platform(text: str) -> str:
    t = text.lower()
    for name, signals in PLATFORM_PATTERNS:
        if any(s.lower() in t for s in signals):
            return name
    return "其他"

def parse_hotel_text(text: str) -> dict | None:
    """通用多平台酒店分享文本解析，支持携程/去哪儿/飞猪/美团/同程/大众点评等"""
    # 先用规则快速判断：含酒店关键词 OR 含已知平台短链
    hotel_signals = ["酒店", "民宿", "宾馆", "旅馆", "客栈", "hotelid", "hotel",
                     "分享酒店", "发现了", "入住", "住宿"]
    has_hotel_signal = any(s in text.lower() for s in hotel_signals)
    has_hotel_url = re.search(r'https?://', text) and any(d in text for d in HOTEL_DOMAINS)
    if not has_hotel_signal and not has_hotel_url:
        return None

    url     = URL_RE.search(text)
    hotel_id = HOTEL_ID_RE.search(text)

    # 用DeepSeek提取结构化信息
    if not DEEPSEEK_KEY:
        # 没有key时fallback到携程格式
        from re import compile as rc
        pair = rc(r'[<＜]([^<>＜＞]{1,10})[>＞]\s*[<＜]([^<>＜＞]+)[>＞]').search(text)
        if not pair:
            return None
        rating = RATING_RE.search(text)
        return {"city": pair.group(1).strip(), "name": pair.group(2).strip(),
                "rating": rating.group(1) if rating else "",
                "url": url.group(0) if url else "",
                "hotel_id": hotel_id.group(1) if hotel_id else "",
                "platform": detect_platform(text)}
    last_err = None
    for attempt in range(2):   # 最多重试一次
        try:
            r = requests.post(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {DEEPSEEK_KEY}", "Content-Type": "application/json"},
                json={"model": "deepseek-chat", "max_tokens": 100,
                      "messages": [{"role": "system", "content": EXTRACT_PROMPT},
                                    {"role": "user", "content": text[:500]}]},
                timeout=12
            ).json()
            content = r["choices"][0]["message"]["content"].strip()
            # 清理可能的markdown代码块
            content = re.sub(r'^```[a-z]*\n?|\n?```$', '', content).strip()
            result = json.loads(content)
            if not result or not result.get("name") or not result.get("city"):
                return None
            return {
                "name":     result["name"],
                "city":     result["city"],
                "rating":   str(result.get("rating", "")),
                "url":      url.group(0) if url else "",
                "hotel_id": hotel_id.group(1) if hotel_id else "",
                "platform": detect_platform(text),
            }
        except Exception as e:
            last_err = e
            print(f"parse_hotel_text error (attempt {attempt+1}):", e)
    return None

MINIPROGRAM_PROMPT = """从酒店小程序的标题和页面路径中提取酒店信息，以JSON格式返回：
{"name": "酒店全名", "city": "城市名（只要城市，不要省份）", "rating": "评分数字或空字符串"}
页面路径(pagepath)中可能包含 city/cityId/cityName/hotelName 等参数，请尽量利用。
城市ID常见映射：1=北京, 2=上海, 3=广州, 4=深圳, 5=成都, 6=杭州, 7=西安, 15=南京。
如果无法识别，返回 null。只返回JSON，不要其他内容。"""

# 已知小程序 appid → 平台名
MINIPROGRAM_APPIDS = {
    "wx1e26394c80c8d22f": "携程",
    "wx6afdd3f3b2c97cb3": "携程",
    "wxb5b36a1c26a74b0c": "去哪儿",
    "wx04a2dc5ae23c8b81": "美团",
    "wx1eeff9b4be0da58a": "飞猪",
    "wx8148f685bc9b1e97": "同程",
    "wx4868444bf58aad45": "同程",
    "wx18e2e1e7e52be9e2": "大众点评",
}

def parse_miniprogram(mp: dict) -> dict | None:
    """解析微信小程序卡片，提取酒店信息"""
    if not mp:
        return None
    title    = mp.get("title", "")
    pagepath = mp.get("pagepath", "")
    appid    = mp.get("appid", "")
    platform = MINIPROGRAM_APPIDS.get(appid) or detect_platform(title + " " + pagepath)

    # 尝试从 pagepath 提取酒店ID
    hotel_id = ""
    for param in ["hotelId", "hotelid", "hotel_id", "id", "masterId"]:
        m = re.search(rf'[?&]{param}=(\d+)', pagepath, re.I)
        if m:
            hotel_id = m.group(1)
            break

    if not title and not pagepath:
        return None

    if not DEEPSEEK_KEY:
        # 无 key：仅靠 title 做酒店名，城市未知
        if "酒店" in title or "民宿" in title or "宾馆" in title:
            return {"name": title, "city": "", "rating": "", "url": "", "hotel_id": hotel_id, "platform": platform}
        return None

    prompt_text = f"标题：{title}\n页面路径：{pagepath}"
    for attempt in range(2):
        try:
            r = requests.post(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {DEEPSEEK_KEY}", "Content-Type": "application/json"},
                json={"model": "deepseek-chat", "max_tokens": 100,
                      "messages": [{"role": "system", "content": MINIPROGRAM_PROMPT},
                                    {"role": "user", "content": prompt_text}]},
                timeout=12
            ).json()
            content = r["choices"][0]["message"]["content"].strip()
            content = re.sub(r'^```[a-z]*\n?|\n?```$', '', content).strip()
            result = json.loads(content)
            if not result or not result.get("name"):
                return None
            return {
                "name":     result["name"],
                "city":     result.get("city", ""),
                "rating":   str(result.get("rating", "")),
                "url":      "",
                "hotel_id": hotel_id,
                "platform": platform,
            }
        except Exception as e:
            print(f"parse_miniprogram error (attempt {attempt+1}):", e)
    return None


def download_media(media_id: str, token: str) -> bytes | None:
    """从微信KF接口下载图片"""
    try:
        r = requests.get(
            "https://qyapi.weixin.qq.com/cgi-bin/media/get",
            params={"access_token": token, "media_id": media_id},
            timeout=10
        )
        if r.status_code == 200 and r.headers.get("Content-Type", "").startswith("image"):
            return r.content
    except Exception as e:
        print("download_media error:", e)
    return None

_baidu_ocr_token = {"token": "", "expires": 0}

def get_baidu_ocr_token() -> str:
    import time
    if _baidu_ocr_token["token"] and time.time() < _baidu_ocr_token["expires"]:
        return _baidu_ocr_token["token"]
    r = requests.post(
        "https://aip.baidubce.com/oauth/2.0/token",
        params={"grant_type": "client_credentials",
                "client_id": BAIDU_OCR_API_KEY,
                "client_secret": BAIDU_OCR_SECRET_KEY},
        timeout=5
    ).json()
    token = r.get("access_token", "")
    _baidu_ocr_token["token"] = token
    _baidu_ocr_token["expires"] = time.time() + r.get("expires_in", 2592000) - 60
    return token

def baidu_ocr(image_bytes: bytes) -> str:
    """百度通用文字识别，返回识别出的全部文字（按行拼接）"""
    if not BAIDU_OCR_API_KEY or not BAIDU_OCR_SECRET_KEY:
        return ""
    try:
        token = get_baidu_ocr_token()
        if not token:
            return ""
        r = requests.post(
            "https://aip.baidubce.com/rest/2.0/ocr/v1/general_basic",
            params={"access_token": token},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={"image": base64.b64encode(image_bytes).decode()},
            timeout=10
        ).json()
        lines = [w["words"] for w in r.get("words_result", [])]
        return "\n".join(lines)
    except Exception as e:
        print("baidu_ocr error:", e)
        return ""

def parse_hotel_image(image_bytes: bytes) -> dict | None:
    """OCR截图文字 → DeepSeek提取酒店结构化信息"""
    if not image_bytes:
        return None
    ocr_text = baidu_ocr(image_bytes)
    if not ocr_text:
        print("parse_hotel_image: OCR returned empty")
        return None
    print("OCR text:", ocr_text[:200])
    # 复用文本解析流程
    result = parse_hotel_text(ocr_text)
    if result:
        result["platform"] = "截图"
    return result

def amap_geocode(name: str, city: str) -> tuple[float, float] | tuple[None, None]:
    """用高德POI搜索拿经纬度"""
    try:
        params = {"key": AMAP_WEB_KEY, "keywords": name, "offset": 1, "output": "json"}
        if city:
            params["city"] = city
            params["citylimit"] = "true"
        r = requests.get("https://restapi.amap.com/v3/place/text", params=params, timeout=5).json()
        pois = [p for p in r.get("pois", []) if str(p.get("typecode","")).startswith("100")]
        if not pois:
            pois = r.get("pois", [])  # fallback：没有住宿类就用第一个结果
        if pois:
            lng, lat = pois[0]["location"].split(",")
            return float(lat), float(lng)
    except Exception as e:
        print("geocode error:", e)
    return None, None

# ── 意图识别 ──────────────────────────────────────────────────────────────────

DONE_KEYWORDS  = ["好了", "看结果", "没了", "完了", "结果", "看看", "对比", "比较"]
CLEAR_KEYWORDS = ["清空", "重置", "清除", "重来", "重新开始"]
DONE_KEYWORDS_SET = set(DONE_KEYWORDS)
HOTEL_DOMAINS = ["ctrip", "qunar", "meituan", "fliggy", "alitrip", "ly.com",
                 "dianping", "tongcheng", "hotel",
                 "dpurl.cn",    # 大众点评/美团短链
                 "mt.cn",       # 美团短链
                 "u.meituan",   # 美团短链
                 "dwz.cn",      # 通用短链（同程等）
                 "suo.im",
                 ]

INTENT_SYSTEM = """你是意图分类器。判断用户消息属于哪种意图，只返回JSON，不要其他文字。

意图说明：
- import   : 用户在分享/添加酒店（含链接、分享文本、转发卡片）
- done     : 用户想看地图/结果/候选名单
- clear    : 用户想清空/删除全部候选酒店
- delete   : 用户想去掉/删除/取消某个具体酒店或某个城市的酒店
- chitchat : 旅行咨询或其他闲聊

返回格式（仅JSON）：
{"intent": "chitchat"}
{"intent": "delete", "target": "上海"}   ← delete时填城市名或酒店关键词
{"intent": "import"}"""

def classify_intent(text: str, msgtype: str) -> tuple[str, str]:
    """返回 (intent, target)。target 仅 delete 时有值。"""
    # 1. 非文字消息直接判 import
    if msgtype in ("image", "miniprogram", "miniprogram_text"):
        return ("import", "")
    # 2. 含酒店链接 → import
    if re.search(r'https?://', text) and any(k in text for k in HOTEL_DOMAINS):
        return ("import", "")
    # 3. DeepSeek 意图分类（有 key 时）
    if DEEPSEEK_KEY:
        try:
            r = requests.post(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {DEEPSEEK_KEY}", "Content-Type": "application/json"},
                json={"model": "deepseek-chat", "max_tokens": 60,
                      "messages": [{"role": "system", "content": INTENT_SYSTEM},
                                    {"role": "user",   "content": text}]},
                timeout=8
            ).json()
            raw = r["choices"][0]["message"]["content"].strip()
            obj = json.loads(raw)
            intent = obj.get("intent", "chitchat")
            target = obj.get("target", "")
            # 额外验证：import 意图还要看文本里是否真的有酒店数据
            if intent == "import" and not parse_hotel_text(text):
                intent = "import_hint"
            return (intent, target)
        except Exception as e:
            print("intent classify error:", e)
    # 4. 无 key 时纯关键词兜底
    if parse_hotel_text(text):
        return ("import", "")
    if any(k in text for k in CLEAR_KEYWORDS):
        return ("clear", "")
    if any(k in text for k in DONE_KEYWORDS_SET):
        return ("done", "")
    return ("chitchat", "")

# ── DeepSeek 闲聊 ─────────────────────────────────────────────────────────────

PERSONA_PROMPT = """你是「旅途向导」，一个专注旅行规划的AI助手，风格：亲切、风趣、简洁。
你帮用户规划国内旅行行程——找酒店、看景点、统筹通勤距离。
如果用户问旅行相关问题就认真回答；如果用户闲聊就顺着说几句然后引导回旅行话题。
重要约束：
- 你没有执行操作的能力，不能查询订单、修改预订等，不要谎称已执行。
- 如果用户要清空全部，告诉他发「清空列表」由系统处理；如果要删某城市/某酒店，直接说出来系统会识别。
- 只回答你确实知道的事，不要编造酒店信息或景点数据。
回复控制在100字以内，不用加emoji堆砌。"""

def deepseek_chat(user_msg: str) -> str:
    if not DEEPSEEK_KEY:
        return "有什么旅行相关的问题都可以问我～发酒店链接或截图，我帮你整理候选名单！"
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
        return "旅行的话题我都能聊～有酒店想加进候选名单吗？把链接发给我就行！"

# ── Bot 状态机 ────────────────────────────────────────────────────────────────

def handle_user_message(open_kfid: str, user_id: str, text: str, msgtype: str,
                        miniprogram: dict = None, image_bytes: bytes = None):
    user = get_or_create_user(user_id)
    bot_state = user["bot_state"]
    hotel_count = get_hotel_count(user["id"])
    intent, delete_target = classify_intent(text, msgtype)

    # ── 状态1：Onboarding（首次进入）────────────────────────────────────────
    if bot_state == 1:
        set_bot_state(user_id, 2)
        send_text(open_kfid, user_id,
            "你好！我是旅途向导 🗺️\n\n"
            "使用方式很简单：\n"
            "① 把携程/去哪儿/美团等平台的酒店分享文本或链接发给我\n"
            "② 我帮你存好候选名单\n"
            "③ 发「看结果」打开地图，按景点距离排酒店\n\n"
            f"现在可以直接发酒店链接开始～或者打开规划页面：{H5_URL}")
        return

    # ── 状态2：闲聊（中枢）──────────────────────────────────────────────────
    # 分支A：导入意图
    if intent == "import":
        if miniprogram:
            ctrip = parse_miniprogram(miniprogram)
        elif image_bytes:
            ctrip = parse_hotel_image(image_bytes)
            if not ctrip:
                send_text(open_kfid, user_id,
                    "收到截图，不过我没能从中识别出酒店信息 🤔\n\n"
                    "截图里需要有酒店名称和城市才能识别，也可以直接复制酒店链接发过来～")
                return
        elif msgtype == "miniprogram_text":
            # KF把小程序卡片压成文本，标题里通常有酒店名但没有城市
            # 先尝试用 DeepSeek 从标题提取，失败则引导用户换方式
            title = re.sub(r'^\[小程序\]\s*', '', text).strip()
            ctrip = parse_hotel_text(title) if title else None
            if not ctrip:
                send_text(open_kfid, user_id,
                    "收到小程序卡片，但微信把它转成了纯文字，酒店详情丢失了 😅\n\n"
                    "换个方式试试：\n"
                    "① 在 App 里点「分享」→「复制文字」，把文字粘过来\n"
                    "② 或者复制酒店页面链接发过来")
                return
        else:
            ctrip = parse_hotel_text(text)
        if ctrip:
            # 同一hotelId不重复入库
            if ctrip["hotel_id"]:
                with get_db() as conn:
                    exists = conn.execute(
                        "SELECT id FROM hotels WHERE user_id=? AND hotel_id=?",
                        (user["id"], ctrip["hotel_id"])
                    ).fetchone()
                if exists:
                    send_text(open_kfid, user_id,
                        f"「{ctrip['name']}」已经在候选名单里了～\n当前候选酒店：{hotel_count} 家")
                    return
            # 剥掉括号/特殊符号后取前段，提高高德命中率
            clean = re.split(r'[｜|（(]', ctrip["name"])[0].strip()
            clean = re.sub(r'[·•\s]+', ' ', clean).strip()
            search_name = clean[:15] if len(clean) > 15 else clean
            city_for_geocode = ctrip["city"] or ""
            lat, lng = amap_geocode(search_name, city_for_geocode)
            save_hotel(
                user_id=user["id"],
                name=ctrip["name"],
                city=ctrip["city"],
                source_url=ctrip["url"],
                hotel_id=ctrip["hotel_id"],
                lat=lat, lng=lng,
                rating=ctrip["rating"],
                raw_text=text[:500],
                platform=ctrip.get("platform", "")
            )
            set_user_city(user_id, ctrip["city"])
            hotel_count += 1
            loc_str = f"📍 已定位到地图" if lat else "（坐标定位失败，后续补）"
            platform_str = f" [{ctrip.get('platform', '')}]" if ctrip.get('platform') else ""
            send_text(open_kfid, user_id,
                f"✅ 已记录：{ctrip['name']}"
                + (f"（{ctrip['rating']}分）" if ctrip["rating"] else "")
                + platform_str
                + f"\n{loc_str}\n\n"
                f"当前候选酒店：{hotel_count} 家\n"
                f"继续发酒店，或发「看结果」打开对比页面")
        else:
            send_text(open_kfid, user_id,
                "收到！不过我没能识别出酒店信息 🤔\n\n"
                "试试这几种方式：\n"
                "① 在携程/美团/去哪儿 App 里点「分享」→「复制文字」，把文字发过来\n"
                "② 直接粘贴酒店页面链接\n\n"
                "如果刚才发的是短链接，重新发一遍通常可以解决～")
        return

    # 分支A-hint：提到导入但没有可解析的数据
    if intent == "import_hint":
        send_text(open_kfid, user_id,
            "想加酒店到候选名单吗？\n\n"
            "把携程/去哪儿/美团里的酒店分享文本或链接发给我，我来记录 👇")
        return

    # 分支：删除特定酒店
    if intent == "delete":
        if not delete_target:
            send_text(open_kfid, user_id,
                "想去掉哪家酒店？告诉我城市名或酒店名，我来删 ✂️")
            return
        with get_db() as conn:
            rows = conn.execute(
                "SELECT id, name, platform FROM hotels WHERE user_id=? AND (city LIKE ? OR name LIKE ? OR platform LIKE ?)",
                (user["id"], f"%{delete_target}%", f"%{delete_target}%", f"%{delete_target}%")
            ).fetchall()
            if not rows:
                send_text(open_kfid, user_id,
                    f"候选名单里没找到和「{delete_target}」相关的酒店～\n\n发「看结果」可以查看当前全部候选。")
                return
            ids = [r["id"] for r in rows]
            conn.execute(
                f"DELETE FROM hotels WHERE id IN ({','.join('?'*len(ids))})", ids
            )
            conn.commit()
        names = "、".join(r["name"] for r in rows[:3])
        if len(rows) > 3:
            names += f" 等{len(rows)}家"
        remaining = get_hotel_count(user["id"])
        send_text(open_kfid, user_id,
            f"✅ 已删除：{names}\n\n剩余候选酒店：{remaining} 家")
        return

    # 分支B：完成导入，看结果
    if intent == "done":
        if hotel_count == 0:
            send_text(open_kfid, user_id,
                "还没有候选酒店呢～\n\n"
                "先把想考虑的酒店链接或分享文本发给我，我帮你存好，再来看对比结果！")
        else:
            h5_with_uid = f"{H5_URL}?uid={user_id}"
            send_text(open_kfid, user_id,
                f"已收录 {hotel_count} 家候选酒店 🏨\n\n"
                f"点击下方链接，在地图上看各酒店到景点的通勤距离 👇\n{h5_with_uid}\n\n"
                "选好景点后会自动按距离排名～")
        return

    # 分支：清空列表
    if intent == "clear":
        with get_db() as conn:
            conn.execute("DELETE FROM hotels WHERE user_id=?", (user["id"],))
            conn.commit()
        send_text(open_kfid, user_id,
            "✅ 已清空候选酒店列表\n\n重新发酒店链接或分享文本，开始新一轮规划～")
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
_process_lock = threading.Lock()

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
    with _process_lock:
        cursor = kv_get("sync_cursor")
        payload = {"token": sync_token, "open_kfid": open_kf_id, "limit": 1000}
        if cursor:
            payload["cursor"] = cursor
        token = get_access_token()
        resp = requests.post("https://qyapi.weixin.qq.com/cgi-bin/kf/sync_msg",
                             params={"access_token": token}, json=payload).json()
        next_cursor = resp.get("next_cursor")
        if next_cursor:
            kv_set("sync_cursor", next_cursor)
        for m in resp.get("msg_list", []):
            msg_id = m.get("msgid", "")
            if msg_id and is_processed(msg_id):
                continue
            msgtype = m.get("msgtype", "")
            user_id = m.get("external_userid", "")
            if not user_id:
                continue
            print("=== msg:", msgtype, json.dumps(m, ensure_ascii=False))
            if msgtype == "text":
                text = m.get("text", {}).get("content", "")
                # 微信KF有时把小程序卡片压成文本，单独处理
                if text.startswith("[小程序]"):
                    handle_user_message(open_kf_id, user_id, text, "miniprogram_text")
                else:
                    handle_user_message(open_kf_id, user_id, text, msgtype)
            elif msgtype == "miniprogram":
                mp = m.get("miniprogram", {})
                handle_user_message(open_kf_id, user_id, "", msgtype, miniprogram=mp)
            elif msgtype == "image":
                media_id = m.get("image", {}).get("media_id", "")
                img_bytes = download_media(media_id, token) if media_id else None
                handle_user_message(open_kf_id, user_id, "", msgtype, image_bytes=img_bytes)
            else:
                continue

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
        "citylimit": "true", "offset": 10, "output": "json",
    }).json()
    pois = []
    for p in r.get("pois", []):
        if not str(p.get("typecode", "")).startswith("100"):
            continue
        lng, lat = p["location"].split(",")
        pois.append({"id": p["id"], "name": p["name"],
                     "address": p.get("address", ""), "lng": float(lng), "lat": float(lat)})
    return {"pois": pois}

@app.get("/api/user/hotels")
async def user_hotels(wecom_id: str):
    user = get_or_create_user(wecom_id)
    return {"hotels": get_hotels(user["id"]), "city": user["city"]}

@app.get("/api/city/info")
async def city_info(city: str):
    # 城市中心坐标
    center = {"lat": 34.3416, "lng": 108.9398}  # fallback（西安）
    try:
        r = requests.get("https://restapi.amap.com/v3/geocode/geo", params={
            "key": AMAP_WEB_KEY, "address": city, "output": "json"
        }, timeout=5).json()
        geocodes = r.get("geocodes", [])
        if geocodes:
            lng, lat = geocodes[0]["location"].split(",")
            center = {"lat": float(lat), "lng": float(lng)}
    except Exception as e:
        print("city center error:", e)

    # 主要景点（旅游景点类型：110200|110100|110000）
    attractions = []
    try:
        r = requests.get("https://restapi.amap.com/v3/place/text", params={
            "key": AMAP_WEB_KEY, "keywords": city + "景点",
            "city": city, "citylimit": "true",
            "types": "110000", "offset": 10, "output": "json",
        }, timeout=5).json()
        for i, p in enumerate(r.get("pois", [])):
            if not p.get("location"):
                continue
            lng, lat = p["location"].split(",")
            attractions.append({
                "id": str(i + 1),
                "name": p["name"],
                "lng": float(lng),
                "lat": float(lat),
            })
    except Exception as e:
        print("attractions error:", e)

    return {"city": city, "center": center, "attractions": attractions}
