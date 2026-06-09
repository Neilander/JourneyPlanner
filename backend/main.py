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
            city                 TEXT DEFAULT '西安',
            selected_attractions TEXT DEFAULT '[]',
            created_at           DATETIME DEFAULT CURRENT_TIMESTAMP
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
        CREATE TABLE IF NOT EXISTS hotel_analysis (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            hotel_db_id  INTEGER UNIQUE NOT NULL,
            amap_rating  REAL,
            amap_reviews INTEGER DEFAULT 0,
            ctrip_raw    TEXT DEFAULT '',   -- 原始评论文本（JSON数组）
            summary      TEXT DEFAULT '',   -- DeepSeek分析结果（JSON）
            analyzed_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS route_cache (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            cache_key   TEXT UNIQUE NOT NULL,
            minutes     REAL NOT NULL,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_route_cache ON route_cache(cache_key);
        CREATE TABLE IF NOT EXISTS usage_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            wecom_id  TEXT NOT NULL,
            action    TEXT NOT NULL,   -- 'msg' | 'ocr' | 'deepseek'
            ts        DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_usage_log ON usage_log(wecom_id, action, ts);
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
        user_cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
        if "selected_attractions" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN selected_attractions TEXT DEFAULT '[]'")
            print("DB migration: added selected_attractions column to users")
        conn.commit()

migrate_db()

# ── 频率限制 ──────────────────────────────────────────────────────────────────
# 单用户限制
USER_LIMITS = {
    "msg":      (50,  "hour"),   # 每小时最多50条消息
    "ocr":      (20,  "day"),    # 每天最多20张图片
    "deepseek": (100, "day"),    # 每天最多100次DeepSeek
}
# 全局每日上限（所有用户合计）
GLOBAL_DAILY_LIMITS = {
    "ocr":      200,
    "deepseek": 1000,
}

def _window_start(window: str) -> str:
    """返回当前时间窗口的起始时间字符串（ISO格式）"""
    from datetime import datetime, timedelta
    now = datetime.utcnow()
    if window == "hour":
        start = now.replace(minute=0, second=0, microsecond=0)
    else:  # day
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.isoformat()

def log_usage(wecom_id: str, action: str):
    with get_db() as conn:
        conn.execute("INSERT INTO usage_log (wecom_id, action) VALUES (?, ?)", (wecom_id, action))
        conn.commit()

def check_rate_limit(wecom_id: str, action: str) -> str | None:
    """返回 None 表示允许，返回字符串表示拒绝原因（直接发给用户）"""
    limit, window = USER_LIMITS.get(action, (9999, "day"))
    win_start = _window_start(window)
    with get_db() as conn:
        # 用户级别检查
        count = conn.execute(
            "SELECT COUNT(*) FROM usage_log WHERE wecom_id=? AND action=? AND ts>=?",
            (wecom_id, action, win_start)
        ).fetchone()[0]
        if count >= limit:
            unit = "小时" if window == "hour" else "天"
            return f"你今{'天' if window=='day' else '小时'}的{'图片' if action=='ocr' else '消息'}发送太频繁啦，每{unit}最多 {limit} 次，稍后再试～"
        # 全局检查
        global_limit = GLOBAL_DAILY_LIMITS.get(action)
        if global_limit:
            day_start = _window_start("day")
            global_count = conn.execute(
                "SELECT COUNT(*) FROM usage_log WHERE action=? AND ts>=?",
                (action, day_start)
            ).fetchone()[0]
            if global_count >= global_limit:
                return "服务今日请求量已达上限，明天再来吧～"
    return None

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
               city: str = "", platform: str = "") -> int:
    with get_db() as conn:
        cur = conn.execute("""
            INSERT INTO hotels (user_id, name, city, source_url, hotel_id, lat, lng, rating, raw_text, platform)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, name, city, source_url, hotel_id, lat, lng, rating, raw_text, platform))
        conn.commit()
        return cur.lastrowid

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
    # ── 频率检查 ─────────────────────────────────────────────────────────────
    deny = check_rate_limit(user_id, "msg")
    if deny:
        send_text(open_kfid, user_id, deny)
        return
    log_usage(user_id, "msg")

    if image_bytes:
        deny = check_rate_limit(user_id, "ocr")
        if deny:
            send_text(open_kfid, user_id, deny)
            return
        log_usage(user_id, "ocr")

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
            new_hotel_id = save_hotel(
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
            # 后台异步触发评价分析
            threading.Thread(
                target=run_hotel_analysis,
                args=(new_hotel_id, ctrip["name"], ctrip.get("hotel_id", ""), ""),
                daemon=True
            ).start()
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
    deny = check_rate_limit(user_id, "deepseek")
    if deny:
        send_text(open_kfid, user_id, deny)
        return
    log_usage(user_id, "deepseek")
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

# ── 酒店评价分析 ──────────────────────────────────────────────────────────────

REVIEW_ANALYSIS_PROMPT = """你是专业的酒店点评分析师。分析以下携程酒店评论，提取所有可能影响住客体验的问题点。

核心原则：
1. 即使只有1-2条差评提到某个问题，也必须列出（标注"个别提到"）——用户需要完整信息做决策
2. 不要因为整体评分高就淡化或忽略具体问题
3. 着重关注：隔音/噪音、卫生/清洁度、热水稳定性、停车便利、电梯等候、WiFi质量、
   房间气味、设施老旧程度、服务态度、早餐质量、位置/交通、空调效果、床品舒适度、
   装修风格与图片是否相符、周边环境

返回JSON格式（只返回JSON，不要其他内容）：
{
  "highlights": ["优点1", "优点2"],
  "warnings": [
    {
      "issue": "问题简述（如：隔音差）",
      "severity": "高|中|低",
      "frequency": "多人提到|个别提到",
      "detail": "具体描述，引用原评论关键词"
    }
  ],
  "verdict": "一句话总结，含最需注意的避雷点"
}"""

AI_INFERENCE_PROMPT = """你是专业的酒店顾问。根据酒店名称和高德地图评分，推断该酒店可能存在的优缺点，给出参考建议。

说明：当前无法获取真实用户评论，请基于酒店品牌、类型、评分等信息进行合理推断。
- 如果是知名连锁品牌（如汉庭、如家、锦江等），结合该品牌一般口碑
- 如果是独立酒店或精品酒店，根据名称和评分推断
- 高分(≥4.5)但仍需提示潜在注意事项；低分(<4.0)着重列出常见问题

返回JSON格式（只返回JSON，不要其他内容）：
{
  "highlights": ["优点1（如适用）"],
  "warnings": [
    {
      "issue": "可能存在的问题",
      "severity": "高|中|低",
      "frequency": "品牌共性|待核实",
      "detail": "具体说明，标注「基于品牌经验推断」"
    }
  ],
  "verdict": "一句话总结，注明「数据来源：AI推断，建议入住前查阅最新评论」"
}"""

def analyze_hotel_by_name(hotel_name: str, amap_rating: float | None, amap_count: int) -> dict | None:
    """无评论时，用 DeepSeek 根据酒店名+评分推断分析"""
    if not DEEPSEEK_KEY:
        return None
    rating_str = f"高德评分：{amap_rating}（{amap_count}条评价）" if amap_rating else "暂无评分数据"
    prompt = f"酒店名称：{hotel_name}\n{rating_str}"
    try:
        r = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_KEY}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "max_tokens": 600,
                  "messages": [{"role": "system", "content": AI_INFERENCE_PROMPT},
                                {"role": "user",   "content": prompt}]},
            timeout=20
        ).json()
        content = r["choices"][0]["message"]["content"].strip()
        content = re.sub(r'^```[a-z]*\n?|\n?```$', '', content).strip()
        return json.loads(content)
    except Exception as e:
        print("analyze_hotel_by_name error:", e)
    return None

def search_amap_poi_id(hotel_name: str, city: str = "") -> str:
    """用酒店名在高德搜索，返回第一个住宿类 POI ID"""
    try:
        params = {
            "key": AMAP_WEB_KEY,
            "keywords": hotel_name,
            "types": "100000",  # 住宿类
            "output": "json",
            "offset": 5,
        }
        if city:
            params["city"] = city
        r = requests.get("https://restapi.amap.com/v3/place/text", params=params, timeout=6).json()
        for p in r.get("pois", []):
            if str(p.get("typecode", "")).startswith("100"):
                print(f"[amap_search] found POI for '{hotel_name}': {p['id']} {p['name']}")
                return p["id"]
    except Exception as e:
        print("amap_search error:", e)
    return ""

def search_ctrip_hotel_id(hotel_name: str, city: str = "") -> str:
    """在携程搜索酒店名，返回第一个匹配的 hotel_id"""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
            "Accept": "application/json",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        r = requests.get(
            "https://m.ctrip.com/restapi/soa2/13444/json/getHotelList",
            params={"keyword": hotel_name, "city": city or "", "pageIndex": 1, "pageSize": 5},
            headers=headers, timeout=8
        ).json()
        hotels = r.get("Response", {}).get("HotelList", [])
        if hotels:
            hid = str(hotels[0].get("HotelID", ""))
            if hid:
                print(f"[ctrip_search] found hotel for '{hotel_name}': {hid}")
                return hid
    except Exception as e:
        print("ctrip_search error:", e)
    # fallback: 从携程搜索页HTML提取
    try:
        resp = requests.get(
            f"https://m.ctrip.com/webapp/hotel/search?keyword={requests.utils.quote(hotel_name)}",
            headers=headers, timeout=8
        )
        ids = re.findall(r'/webapp/hotel/(\d{5,8})', resp.text)
        if ids:
            print(f"[ctrip_search_html] found hotel for '{hotel_name}': {ids[0]}")
            return ids[0]
    except Exception as e:
        print("ctrip_search_html error:", e)
    return ""

def fetch_amap_hotel_rating(amap_poi_id: str) -> tuple[float | None, int]:
    """从高德获取酒店评分和评论数"""
    if not amap_poi_id:
        return None, 0
    try:
        r = requests.get("https://restapi.amap.com/v3/place/detail", params={
            "key": AMAP_WEB_KEY, "id": amap_poi_id, "output": "json",
        }, timeout=6).json()
        pois = r.get("pois", [])
        if pois:
            p = pois[0]
            rating = float(p.get("biz_ext", {}).get("rating") or 0) or None
            comment_num = int(p.get("biz_ext", {}).get("comment_num") or 0)
            return rating, comment_num
    except Exception as e:
        print("amap_rating error:", e)
    return None, 0

def fetch_amap_reviews(amap_poi_id: str, max_reviews: int = 30) -> list[str]:
    """用高德 API 拉取 POI 评论，返回评论文本列表"""
    if not amap_poi_id:
        return []
    reviews = []
    try:
        # 高德评论接口（需 Web 服务 key）
        for page in range(1, 4):
            r = requests.get(
                "https://restapi.amap.com/v3/place/detail",
                params={
                    "key": AMAP_WEB_KEY,
                    "id": amap_poi_id,
                    "output": "json",
                    "extensions": "all",
                },
                timeout=8
            ).json()
            pois = r.get("pois", [])
            if not pois:
                break
            # 评论在 biz_ext.navi 或 event 字段中，也可能在 photos 里的描述
            p = pois[0]
            # 尝试从 event 和 深度字段取评论
            for ev in p.get("event", []):
                desc = ev.get("description", "")
                if desc and len(desc) > 10:
                    reviews.append(desc)
            break  # 高德 detail 不分页，一次够了
    except Exception as e:
        print("amap_reviews error:", e)

    # 高德 Web API 评论字段有限，用搜索补充
    if len(reviews) < 5:
        try:
            r2 = requests.get(
                "https://restapi.amap.com/v5/place/detail",
                params={
                    "key": AMAP_WEB_KEY,
                    "id": amap_poi_id,
                    "show_fields": "business,rating,comment",
                    "output": "json",
                },
                timeout=8
            ).json()
            for item in (r2.get("pois") or []):
                biz = item.get("business") or {}
                for c in biz.get("comment", {}).get("list", []):
                    txt = c.get("content", "")
                    if txt and len(txt) > 10:
                        reviews.append(txt)
        except Exception as e:
            print("amap_reviews_v5 error:", e)

    print(f"amap reviews fetched: {len(reviews)} for poi={amap_poi_id}")
    return reviews[:max_reviews]

def fetch_ctrip_reviews(hotel_id: str, max_reviews: int = 30) -> list[str]:
    """抓取携程手机端评论，返回评论文本列表"""
    if not hotel_id:
        return []
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Content-Type": "application/json",
        "Referer": f"https://m.ctrip.com/webapp/hotel/{hotel_id}",
        "Origin": "https://m.ctrip.com",
    }
    reviews = []
    # 策略1：JSON API
    for page in range(1, 3):
        try:
            r = requests.post(
                "https://m.ctrip.com/restapi/soa2/13444/json/getHotelCommentList",
                headers=headers,
                json={
                    "hotelId": int(hotel_id),
                    "pageIndex": page,
                    "pageSize": max_reviews // 2,
                    "sortType": 1,
                    "filterType": 0,
                    "head": {"cid": "09031014111144141", "ctok": "", "cver": "1.0",
                             "lang": "01", "sid": "8888", "syscode": "09"},
                },
                timeout=10
            ).json()
            items = (r.get("result") or {}).get("hotelCommentList") or []
            for item in items:
                content = item.get("content") or item.get("commentContent") or ""
                if content and len(content) > 10:
                    reviews.append(content)
            if items:
                break
        except Exception as e:
            print(f"ctrip_api page{page} error:", e)

    # 策略2：移动端网页解析（fallback）
    if not reviews:
        try:
            resp = requests.get(
                f"https://m.ctrip.com/webapp/hotel/{hotel_id}",
                headers=headers, timeout=10
            )
            texts = re.findall(r'"content"\s*:\s*"([^"]{20,500})"', resp.text)
            reviews = list(dict.fromkeys(texts))[:max_reviews]
        except Exception as e:
            print("ctrip_html error:", e)

    print(f"ctrip reviews fetched: {len(reviews)} for hotel_id={hotel_id}")
    return reviews[:max_reviews]

def analyze_hotel_reviews(reviews: list[str], hotel_name: str) -> dict | None:
    """用 DeepSeek 分析评论，提炼避雷要点"""
    if not DEEPSEEK_KEY or not reviews:
        return None
    # 截取前4000字符避免超token
    combined = f"酒店：{hotel_name}\n\n评论：\n" + "\n---\n".join(reviews)
    combined = combined[:4000]
    try:
        r = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_KEY}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "max_tokens": 800,
                  "messages": [{"role": "system", "content": REVIEW_ANALYSIS_PROMPT},
                                {"role": "user",   "content": combined}]},
            timeout=20
        ).json()
        content = r["choices"][0]["message"]["content"].strip()
        content = re.sub(r'^```[a-z]*\n?|\n?```$', '', content).strip()
        return json.loads(content)
    except Exception as e:
        print("analyze_hotel_reviews error:", e)
    return None

def run_hotel_analysis(hotel_db_id: int, hotel_name: str, hotel_id: str, amap_poi_id: str):
    """后台异步：拉评分+评论+分析，结果写入 hotel_analysis 表"""
    print(f"[analysis] start: {hotel_name} ctrip={hotel_id} amap={amap_poi_id}")
    # 从 DB 获取城市信息（用于搜索时缩小范围）
    city = ""
    with get_db() as conn:
        row = conn.execute(
            "SELECT u.city FROM hotels h JOIN users u ON h.user_id=u.id WHERE h.id=?",
            (hotel_db_id,)
        ).fetchone()
        if row:
            city = row[0] or ""

    # 没有 amap_poi_id → 用酒店名搜索
    if not amap_poi_id:
        amap_poi_id = search_amap_poi_id(hotel_name, city)

    # 没有携程 hotel_id → 用酒店名搜索
    if not hotel_id:
        hotel_id = search_ctrip_hotel_id(hotel_name, city)

    amap_rating, amap_count = fetch_amap_hotel_rating(amap_poi_id)
    # 高德评论 + 携程评论合并（高德更稳定，携程内容更丰富）
    amap_reviews_list = fetch_amap_reviews(amap_poi_id)
    ctrip_reviews_list = fetch_ctrip_reviews(hotel_id)
    # 去重合并，高德优先放前面
    seen = set()
    reviews = []
    for r in amap_reviews_list + ctrip_reviews_list:
        key = r[:50]
        if key not in seen:
            seen.add(key)
            reviews.append(r)
    print(f"[analysis] total reviews: {len(reviews)} (amap={len(amap_reviews_list)}, ctrip={len(ctrip_reviews_list)})")

    if reviews:
        # 有真实评论 → 用评论分析
        summary = analyze_hotel_reviews(reviews, hotel_name)
    else:
        # 无评论 → AI 根据酒店名+评分推断
        print(f"[analysis] no reviews, falling back to AI inference for: {hotel_name}")
        summary = analyze_hotel_by_name(hotel_name, amap_rating, amap_count)
    with get_db() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO hotel_analysis
              (hotel_db_id, amap_rating, amap_reviews, ctrip_raw, summary, analyzed_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (
            hotel_db_id,
            amap_rating,
            amap_count,
            json.dumps(reviews[:10], ensure_ascii=False),  # 只存前10条作为参考
            json.dumps(summary, ensure_ascii=False) if summary else "",
        ))
        conn.commit()
    print(f"[analysis] done: {hotel_name}, rating={amap_rating}, reviews={len(reviews)}")

# ── 真实路线时间（高德 + SQLite 缓存）────────────────────────────────────────

CACHE_TTL_HOURS = 24

def _route_cache_key(olat, olng, dlat, dlng, mode: str) -> str:
    # 坐标精度保留4位，避免微小差异导致缓存miss
    return f"{round(olat,4)},{round(olng,4)}-{round(dlat,4)},{round(dlng,4)}-{mode}"

def _get_cached(key: str) -> float | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT minutes FROM route_cache WHERE cache_key=? AND created_at > datetime('now', ?)",
            (key, f"-{CACHE_TTL_HOURS} hours")
        ).fetchone()
    return row[0] if row else None

def _set_cached(key: str, minutes: float):
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO route_cache (cache_key, minutes) VALUES (?, ?)",
            (key, minutes)
        )
        conn.commit()

def amap_transit_minutes(olat, olng, dlat, dlng, city: str) -> float | None:
    """高德公共交通路线，返回分钟数"""
    try:
        r = requests.get("https://restapi.amap.com/v3/direction/transit/integrated", params={
            "key": AMAP_WEB_KEY,
            "origin": f"{olng},{olat}",
            "destination": f"{dlng},{dlat}",
            "city": city, "cityd": city,
            "strategy": 0,  # 最快
            "output": "json",
        }, timeout=8).json()
        plans = r.get("route", {}).get("transits", [])
        if plans:
            return round(int(plans[0].get("duration", 0)) / 60, 1)
    except Exception as e:
        print("amap_transit error:", e)
    return None

def amap_driving_minutes(olat, olng, dlat, dlng) -> float | None:
    """高德驾车路线，返回分钟数"""
    try:
        r = requests.get("https://restapi.amap.com/v3/direction/driving", params={
            "key": AMAP_WEB_KEY,
            "origin": f"{olng},{olat}",
            "destination": f"{dlng},{dlat}",
            "strategy": 10,  # 不走高速
            "output": "json",
        }, timeout=8).json()
        paths = r.get("route", {}).get("paths", [])
        if paths:
            return round(int(paths[0].get("duration", 0)) / 60, 1)
    except Exception as e:
        print("amap_driving error:", e)
    return None

def amap_walking_minutes(olat, olng, dlat, dlng) -> float | None:
    """高德步行，返回分钟数"""
    try:
        r = requests.get("https://restapi.amap.com/v3/direction/walking", params={
            "key": AMAP_WEB_KEY,
            "origin": f"{olng},{olat}",
            "destination": f"{dlng},{dlat}",
            "output": "json",
        }, timeout=8).json()
        paths = r.get("route", {}).get("paths", [])
        if paths:
            return round(int(paths[0].get("duration", 0)) / 60, 1)
    except Exception as e:
        print("amap_walking error:", e)
    return None

def get_route_minutes(olat, olng, dlat, dlng, mode: str, city: str = "") -> float:
    """获取两点间通勤时间（分钟），优先读缓存"""
    key = _route_cache_key(olat, olng, dlat, dlng, mode)
    cached = _get_cached(key)
    if cached is not None:
        return cached

    minutes = None
    if mode == "transit":
        minutes = amap_transit_minutes(olat, olng, dlat, dlng, city)
    elif mode == "driving":
        minutes = amap_driving_minutes(olat, olng, dlat, dlng)
    elif mode == "walking":
        minutes = amap_walking_minutes(olat, olng, dlat, dlng)

    if minutes is None:
        # fallback: 直线距离估算
        from math import radians, sin, cos, atan2, sqrt
        R = 6371
        dlat_r = radians(dlat - olat)
        dlng_r = radians(dlng - olng)
        a = sin(dlat_r/2)**2 + cos(radians(olat))*cos(radians(dlat))*sin(dlng_r/2)**2
        km = R * 2 * atan2(sqrt(a), sqrt(1-a))
        speed = {"transit": 20, "driving": 30, "walking": 5}.get(mode, 20)
        minutes = round(km / speed * 60, 1)

    _set_cached(key, minutes)
    return minutes

@app.post("/api/commute/matrix")
async def commute_matrix(body: dict):
    """
    计算酒店×景点通勤矩阵
    body: {
      hotels: [{id, name, lat, lng}],
      attractions: [{id, name, lat, lng}],
      mode: "transit" | "driving" | "walking",
      city: "北京"
    }
    返回: {matrix: {hotel_id: {attraction_id: minutes}}}
    """
    hotels = body.get("hotels", [])
    attractions = body.get("attractions", [])
    mode = body.get("mode", "transit")
    city = body.get("city", "")
    matrix: dict = {}
    for h in hotels:
        matrix[h["id"]] = {}
        for a in attractions:
            m = get_route_minutes(h["lat"], h["lng"], a["lat"], a["lng"], mode, city)
            matrix[h["id"]][a["id"]] = m
    return {"matrix": matrix}

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
async def user_hotels(wecom_id: str, background_tasks: BackgroundTasks):
    user = get_or_create_user(wecom_id)
    hotels = get_hotels(user["id"])
    # 内嵌 analysis，没有的酒店后台触发补跑
    with get_db() as conn:
        for h in hotels:
            row = conn.execute(
                "SELECT * FROM hotel_analysis WHERE hotel_db_id=?", (h["id"],)
            ).fetchone()
            need_analysis = False
            if row:
                h["analysis"] = {
                    "amap_rating": row["amap_rating"],
                    "amap_reviews": row["amap_reviews"],
                    "summary": json.loads(row["summary"]) if row["summary"] else None,
                }
                # 如果之前分析结果都是空（评分和评论都没拿到），重新触发一次
                if row["amap_rating"] is None and not row["summary"]:
                    need_analysis = True
            else:
                h["analysis"] = None
                need_analysis = True
            if need_analysis:
                background_tasks.add_task(
                    run_hotel_analysis,
                    h["id"], h["name"], h.get("hotel_id", ""), ""
                )
    return {
        "hotels": hotels,
        "city": user["city"],
        "selected_attractions": json.loads(user.get("selected_attractions") or "[]"),
    }

@app.post("/api/user/selections")
async def save_selections(body: dict):
    wecom_id = body.get("wecom_id", "")
    attractions = body.get("attractions", [])
    if not wecom_id:
        return {"ok": False}
    with get_db() as conn:
        conn.execute("UPDATE users SET selected_attractions=? WHERE wecom_id=?",
                     (json.dumps(attractions, ensure_ascii=False), wecom_id))
        conn.commit()
    return {"ok": True}

@app.post("/api/user/hotel")
async def add_user_hotel(body: dict):
    wecom_id = body.get("wecom_id", "")
    h = body.get("hotel", {})
    if not wecom_id or not h.get("name"):
        return {"ok": False, "id": None}
    user = get_or_create_user(wecom_id)
    # 避免重复（同名同坐标）
    with get_db() as conn:
        exists = conn.execute(
            "SELECT id FROM hotels WHERE user_id=? AND name=?",
            (user["id"], h["name"])
        ).fetchone()
        if exists:
            return {"ok": True, "id": exists["id"]}
        cur = conn.execute(
            "INSERT INTO hotels (user_id, name, city, lat, lng, source_url, hotel_id, rating, raw_text, platform) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (user["id"], h["name"], h.get("city", ""), h.get("lat"), h.get("lng"),
             "", h.get("amap_id", ""), "", "", "前端搜索")
        )
        conn.commit()
        new_id = cur.lastrowid
        # 后台分析（高德 amap_id 作为 poi_id）
        threading.Thread(
            target=run_hotel_analysis,
            args=(new_id, h["name"], "", h.get("amap_id", "")),
            daemon=True
        ).start()
        return {"ok": True, "id": new_id}

@app.get("/api/hotel/analysis")
async def hotel_analysis(hotel_id: int):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM hotel_analysis WHERE hotel_db_id=?", (hotel_id,)
        ).fetchone()
    if not row:
        return {"status": "pending"}
    summary = json.loads(row["summary"]) if row["summary"] else None
    return {
        "status": "done",
        "amap_rating": row["amap_rating"],
        "amap_reviews": row["amap_reviews"],
        "summary": summary,
    }

@app.post("/api/hotel/reanalyze")
async def reanalyze_hotel(body: dict, background_tasks: BackgroundTasks):
    """强制重新分析某用户所有酒店（清掉旧记录，重新跑）"""
    wecom_id = body.get("wecom_id", "")
    if not wecom_id:
        return {"ok": False}
    user = get_or_create_user(wecom_id)
    hotels = get_hotels(user["id"])
    with get_db() as conn:
        for h in hotels:
            conn.execute("DELETE FROM hotel_analysis WHERE hotel_db_id=?", (h["id"],))
        conn.commit()
    for h in hotels:
        background_tasks.add_task(run_hotel_analysis, h["id"], h["name"], h.get("hotel_id", ""), "")
    return {"ok": True, "count": len(hotels)}

@app.delete("/api/user/hotel/{hotel_id}")
async def delete_user_hotel(hotel_id: int, wecom_id: str):
    user = get_or_create_user(wecom_id)
    with get_db() as conn:
        conn.execute("DELETE FROM hotels WHERE id=? AND user_id=?", (hotel_id, user["id"]))
        conn.commit()
    return {"ok": True}

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
