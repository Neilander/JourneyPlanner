import os, json, re, sqlite3, threading, base64
import xml.etree.ElementTree as ET
import requests
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from wechatpy.enterprise.crypto import WeChatCrypto
from apscheduler.schedulers.background import BackgroundScheduler

load_dotenv()
CORP_ID      = os.environ["WECOM_CORP_ID"]
KF_SECRET    = os.environ["WECOM_KF_SECRET"]
TOKEN        = os.environ["WECOM_TOKEN"]
AES_KEY      = os.environ["WECOM_AES_KEY"]
AMAP_WEB_KEY = os.environ["AMAP_WEB_KEY"]
DEEPSEEK_KEY     = os.environ.get("DEEPSEEK_API_KEY", "")
BAIDU_OCR_API_KEY    = os.environ.get("BAIDU_OCR_API_KEY", "")
BAIDU_OCR_SECRET_KEY = os.environ.get("BAIDU_OCR_SECRET_KEY", "")
QWEATHER_KEY     = os.environ.get("QWEATHER_KEY", "")
SERPAPI_KEY      = os.environ.get("SERPAPI_KEY", "")

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
            city                 TEXT DEFAULT '',
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
        CREATE TABLE IF NOT EXISTS trips (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            wecom_id    TEXT NOT NULL,
            city        TEXT DEFAULT '',
            days        INTEGER DEFAULT 2,
            preference  TEXT DEFAULT '',
            bundle_text TEXT NOT NULL,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_trips_wecom ON trips(wecom_id);
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
        rc_cols = [r[1] for r in conn.execute("PRAGMA table_info(route_cache)").fetchall()]
        if "walk" not in rc_cols:
            conn.execute("ALTER TABLE route_cache ADD COLUMN walk REAL DEFAULT 0")
            print("DB migration: added walk column to route_cache")
        if "open_kfid" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN open_kfid TEXT DEFAULT ''")
            print("DB migration: added open_kfid column to users")
        if "push_enabled" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN push_enabled INTEGER DEFAULT 1")
            print("DB migration: added push_enabled column to users")
        # 修正：city 默认值曾为'西安'，把没有西安酒店的用户的 city 重置为空
        conn.execute("""
            UPDATE users SET city=''
            WHERE city='西安'
            AND id NOT IN (
                SELECT DISTINCT user_id FROM hotels WHERE city='西安'
            )
        """)
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

# ── 对话历史（最近N轮，用于闲聊上下文）──────────────────────────────────────────
CHAT_HISTORY_TURNS = 3   # 保留最近3轮问答

def get_chat_history(wecom_id: str) -> list[dict]:
    raw = kv_get(f"chat_history:{wecom_id}")
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []

TRASH_EXPIRE_TURNS = 5  # 超过5轮对话后清空回收站

def append_chat_history(wecom_id: str, user_msg: str, assistant_msg: str):
    history = get_chat_history(wecom_id)
    history.append({"role": "user", "content": user_msg})
    history.append({"role": "assistant", "content": assistant_msg})
    history = history[-(CHAT_HISTORY_TURNS * 2):]
    kv_set(f"chat_history:{wecom_id}", json.dumps(history, ensure_ascii=False))
    # 回收站倒计时：每轮对话递减，归零时清空
    trash_ttl_key = f"trash_ttl:{wecom_id}"
    ttl_raw = kv_get(trash_ttl_key)
    if ttl_raw is not None:
        ttl = int(ttl_raw) - 1
        if ttl <= 0:
            kv_set(f"trash:{wecom_id}", "[]")
            kv_set(trash_ttl_key, "0")
        else:
            kv_set(trash_ttl_key, str(ttl))

def trash_save(wecom_id: str, rows: list[dict]):
    """把被删的酒店行存入回收站，重置倒计时。"""
    data = [dict(r) for r in rows]
    kv_set(f"trash:{wecom_id}", json.dumps(data, ensure_ascii=False))
    kv_set(f"trash_ttl:{wecom_id}", str(TRASH_EXPIRE_TURNS))

def trash_restore(wecom_id: str, user_db_id: int) -> list[dict]:
    """从回收站取出酒店并写回 hotels 表，返回恢复的行列表。"""
    raw = kv_get(f"trash:{wecom_id}")
    if not raw:
        return []
    try:
        rows = json.loads(raw)
    except Exception:
        return []
    if not rows:
        return []
    with get_db() as conn:
        for r in rows:
            conn.execute(
                """INSERT OR IGNORE INTO hotels
                   (user_id, name, city, source_url, hotel_id, lat, lng, rating, raw_text, platform)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_db_id, r.get("name",""), r.get("city",""), r.get("source_url",""),
                 r.get("hotel_id",""), r.get("lat"), r.get("lng"),
                 r.get("rating",""), r.get("raw_text",""), r.get("platform",""))
            )
        conn.commit()
    # 恢复后清空回收站
    kv_set(f"trash:{wecom_id}", "[]")
    kv_set(f"trash_ttl:{wecom_id}", "0")
    return rows

def trash_clear(wecom_id: str):
    """新酒店导入时清空回收站。"""
    kv_set(f"trash:{wecom_id}", "[]")
    kv_set(f"trash_ttl:{wecom_id}", "0")

def pending_import_save(wecom_id: str, ctrip: dict):
    """暂存待确认的酒店导入信息（城市不匹配时使用）。"""
    kv_set(f"pending_import:{wecom_id}", json.dumps(ctrip, ensure_ascii=False))

def pending_import_pop(wecom_id: str) -> dict | None:
    """取出并清除待确认的导入信息。"""
    raw = kv_get(f"pending_import:{wecom_id}")
    if not raw:
        return None
    kv_set(f"pending_import:{wecom_id}", "")
    try:
        return json.loads(raw)
    except Exception:
        return None

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


def download_media(media_id: str, token: str, accept: str = "image") -> bytes | None:
    """从微信KF接口下载媒体文件，accept 传 'image' 或 'audio' 过滤 Content-Type"""
    try:
        r = requests.get(
            "https://qyapi.weixin.qq.com/cgi-bin/media/get",
            params={"access_token": token, "media_id": media_id},
            timeout=10
        )
        ct = r.headers.get("Content-Type", "")
        print(f"[download_media] status={r.status_code} Content-Type={ct!r}")
        if r.status_code == 200 and ct.startswith(accept):
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

def baidu_asr(audio_bytes: bytes) -> str | None:
    """百度短语音识别（AMR 格式），返回识别文字或 None"""
    if not BAIDU_OCR_API_KEY or not BAIDU_OCR_SECRET_KEY:
        return None
    try:
        token = get_baidu_ocr_token()
        if not token:
            return None
        r = requests.post(
            "https://vop.baidu.com/server_api",
            json={
                "format": "amr",
                "rate": 8000,
                "channel": 1,
                "cuid": "journeyplanner_kf",
                "token": token,
                "speech": base64.b64encode(audio_bytes).decode(),
                "len": len(audio_bytes),
            },
            timeout=12
        ).json()
        if r.get("err_no") == 0 and r.get("result"):
            text = r["result"][0].strip()
            print(f"[baidu_asr] 识别结果: {text!r}")
            return text
        print(f"[baidu_asr] err_no={r.get('err_no')} err_msg={r.get('err_msg')}")
        return None
    except Exception as e:
        print(f"[baidu_asr] error: {e}")
        return None

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
- import              : 用户在分享/添加酒店（含链接、分享文本、转发卡片）
- done                : 用户想看地图/结果/候选名单
- clear               : 用户想清空/删除全部候选酒店
- delete              : 用户想去掉/删除/取消某个具体酒店或某个城市的酒店
- restore             : 用户想撤销/恢复刚才删除的酒店（如"删错了""帮我恢复""撤销删除"等）
- set_city            : 用户只是切换目的地城市，没有明确要求规划行程或看景点推荐（如"改成成都""换到北京""目的地改为上海""下周飞成都"）
- confirm             : 用户表示同意/确认（如"好""可以""确认""嗯""行"等）
- cancel              : 用户表示拒绝/取消（如"不""算了""取消""不换""不用了"等）
- attraction_query    : 用户明确询问某景点的实时/近期状态，须含疑问词或状态词（人多吗/开放吗/开门吗/排队/现在/今天/最近/这周/怎么样/适不适合）。例："大雁塔现在人多吗""故宫今天开门吗""武侯祠最近人流怎样"。注意："我要去X""想去X""打算去X"不算 attraction_query，应归 chitchat。
- attraction_recommend: 用户想知道某城市/目的地有哪些值得去的景点或餐厅，但不要求完整行程规划。例："成都有什么好玩的""推荐几个西安必去的地方""北京适合亲子的景点""成都好吃的在哪"
- plan_trip           : 用户明确要求规划/安排一次具体行程，通常包含天数或行程安排意图。例："帮我规划成都三天""西安两天怎么安排""给我做个行程表"。必填字段：target(城市)、days(天数，默认2)、preference(偏好关键词，可为空)
- chitchat            : 旅行咨询或其他闲聊

优先级说明（有歧义时）：
- 含「加入规划」「加到行程」「加进规划」「帮我规划」等明确把景点/目的地纳入行程的词 → plan_trip（preference填景点名）
- 含天数/行程/规划 → plan_trip
- 只问有什么/推荐什么景点餐厅 → attraction_recommend
- 只说去哪个城市没有其他意图 → set_city

返回格式（仅JSON）：
{"intent": "chitchat"}
{"intent": "delete", "target": "上海"}
{"intent": "restore"}
{"intent": "set_city", "target": "成都"}
{"intent": "confirm"}
{"intent": "cancel"}
{"intent": "attraction_query"}
{"intent": "attraction_recommend", "target": "成都", "preference": "亲子"}
{"intent": "plan_trip", "target": "成都", "days": 3, "preference": "历史美食"}
{"intent": "import"}"""

def _do_save_hotel(open_kfid: str, user_id: str, user: dict, ctrip: dict, raw_text: str, hotel_count: int):
    """实际执行酒店入库、城市更新、分析触发、回复用户。"""
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
        raw_text=raw_text[:500],
        platform=ctrip.get("platform", "")
    )
    trash_clear(user_id)
    if ctrip["city"]:
        set_user_city(user_id, ctrip["city"])
        user["city"] = ctrip["city"]
    threading.Thread(
        target=run_hotel_analysis,
        args=(new_hotel_id, ctrip["name"], ctrip.get("hotel_id", ""), ""),
        daemon=True
    ).start()
    hotel_count += 1
    loc_str = "📍 已定位到地图" if lat else "（坐标定位失败，后续补）"
    platform_str = f" [{ctrip.get('platform', '')}]" if ctrip.get('platform') else ""
    send_text(open_kfid, user_id,
        f"✅ 已记录：{ctrip['name']}"
        + (f"（{ctrip['rating']}分）" if ctrip["rating"] else "")
        + platform_str
        + f"\n{loc_str}"
        + f"\n\n当前候选酒店：{hotel_count} 家\n"
        f"继续发酒店，或发「看结果」打开对比页面")

def classify_intent(text: str, msgtype: str) -> tuple[str, str, dict]:
    """返回 (intent, target, extras)。
    target: delete/set_city/plan_trip/attraction_recommend 时有值（城市或关键词）
    extras: plan_trip 时含 {"days": int, "preference": str}；其他意图为 {}
    """
    # 1. 非文字消息直接判 import
    if msgtype in ("image", "miniprogram", "miniprogram_text"):
        return ("import", "", {})
    # 2. 含酒店链接 → import
    if re.search(r'https?://', text) and any(k in text for k in HOTEL_DOMAINS):
        return ("import", "", {})
    # 3. DeepSeek 意图分类（有 key 时）
    if DEEPSEEK_KEY:
        try:
            r = requests.post(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {DEEPSEEK_KEY}", "Content-Type": "application/json"},
                json={"model": "deepseek-chat", "max_tokens": 80,
                      "messages": [{"role": "system", "content": INTENT_SYSTEM},
                                    {"role": "user",   "content": text}]},
                timeout=8
            ).json()
            raw = r["choices"][0]["message"]["content"].strip()
            obj = json.loads(raw)
            intent = obj.get("intent", "chitchat")
            target = obj.get("target", "")
            extras = {k: v for k, v in obj.items() if k not in ("intent", "target")}
            if intent == "import" and not parse_hotel_text(text):
                intent = "import_hint"
            return (intent, target, extras)
        except Exception as e:
            print("intent classify error:", e)
    # 4. 无 key 时纯关键词兜底
    if parse_hotel_text(text):
        return ("import", "", {})
    if any(k in text for k in CLEAR_KEYWORDS):
        return ("clear", "", {})
    if any(k in text for k in DONE_KEYWORDS_SET):
        return ("done", "", {})
    if is_attraction_query_fallback(text):
        return ("attraction_query", "", {})
    return ("chitchat", "", {})

# ── 景点实时情况查询 ──────────────────────────────────────────────────────────

ATTRACTION_INTRO_PROMPT = """你是旅行助手，用自身知识介绍用户询问的景点或餐厅。

要求：
- 150字以内，简洁温暖
- 涵盖：是什么、有什么特色、适合哪类人、大概游览时间
- 结尾可加一句贴心提示（如最佳季节、注意事项）
- 不要编造具体票价或开放时间数字"""

def query_attraction_intro(name: str, city: str = "") -> str:
    """用 DeepSeek 自身知识介绍景点/餐厅，不走 SerpAPI"""
    if not DEEPSEEK_KEY:
        return f"暂时无法获取「{name}」的介绍，建议直接搜索了解～"
    try:
        r = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_KEY}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "max_tokens": 200,
                  "messages": [
                      {"role": "system", "content": ATTRACTION_INTRO_PROMPT},
                      {"role": "user", "content": f"{'【' + city + '】' if city else ''}「{name}」是什么地方？"},
                  ]},
            timeout=12
        ).json()
        return r["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[attraction_intro] error: {e}")
        return f"暂时无法介绍「{name}」，建议直接搜索了解～"

ATTRACTION_QUERY_PROMPT = """你是旅行助手，根据以下搜索结果回答用户关于景点的最新情况（人流、开放状态、特殊通知等）。

要求：
- 100字以内，简洁直接
- 只回答用户问的那个景点，不要用其他景点的信息混充
- 只说搜索结果里有的信息，没有就说「暂时没找到最新消息」
- 如果有关闭/限流/特殊情况，用⚠️标出
- 注明信息来源时间（如「据X月X日消息」）
- 不要编造信息"""

_ATTRACTION_FALLBACK_KEYWORDS = [
    "人多吗", "人多不多", "拥挤", "排队", "等待", "限流",
    "开放吗", "开门吗", "关闭", "停止开放", "暂停",
    "最新情况", "现在怎么样", "今天怎么样", "值得去吗",
    "能看到吗", "看不到", "特殊", "通知", "公告",
]

def is_attraction_query_fallback(text: str) -> bool:
    """无 DeepSeek key 时的关键词兜底。"""
    return any(k in text for k in _ATTRACTION_FALLBACK_KEYWORDS)

def search_attraction_info(attraction: str, city: str = "") -> str | None:
    """用 SerpAPI 搜索景点最新情况，返回摘要文本"""
    if not SERPAPI_KEY:
        return None
    query = f"{city}{attraction} 最新情况 {__import__('datetime').date.today().strftime('%Y年')}"
    try:
        r = requests.get("https://serpapi.com/search", params={
            "api_key": SERPAPI_KEY,
            "engine": "baidu",
            "q": query,
            "num": 5,
        }, timeout=10).json()

        from datetime import datetime, timedelta
        import re as _re_date
        cutoff = datetime.now() - timedelta(days=90)

        def _parse_date(date_str: str):
            """把 SerpAPI 返回的日期字符串解析为 datetime，失败返回 None"""
            if not date_str:
                return None
            for fmt in ("%Y年%m月%d日", "%Y-%m-%d", "%b %d, %Y", "%Y/%m/%d"):
                try:
                    return datetime.strptime(date_str.strip(), fmt)
                except ValueError:
                    pass
            # 尝试提取 "X天前/X周前/X个月前"
            m = _re_date.search(r'(\d+)\s*(天|周|个月|月)前', date_str)
            if m:
                n, unit = int(m.group(1)), m.group(2)
                delta = {"天": timedelta(days=n), "周": timedelta(weeks=n),
                         "个月": timedelta(days=n*30), "月": timedelta(days=n*30)}.get(unit)
                return (datetime.now() - delta) if delta else None
            return None

        snippets = []
        # 摘要框（无日期，直接加）
        if r.get("answer_box", {}).get("snippet"):
            snippets.append(r["answer_box"]["snippet"])
        # 普通搜索结果，过滤3个月以上的旧新闻
        for item in r.get("organic_results", [])[:5]:
            snippet = item.get("snippet", "")
            date_str = item.get("date", "")
            if not snippet:
                continue
            dt = _parse_date(date_str)
            if dt and dt < cutoff:
                continue  # 超过3个月，跳过
            snippets.append(f"[{date_str}] {snippet}" if date_str else snippet)

        if not snippets:
            return None
        return "\n".join(snippets[:5])
    except Exception as e:
        print(f"[serpapi] error: {e}")
    return None

def query_attraction_status(text: str, city: str = "") -> str:
    """主入口：搜索 + DeepSeek 提炼，返回回复文字"""
    if not DEEPSEEK_KEY:
        return "暂时无法查询景点信息，请直接搜索了解最新情况～"

    raw = search_attraction_info(text, city)
    if not raw:
        return f"没找到关于「{text}」的最新消息，建议直接搜索或查看景区官方公众号～"

    try:
        r = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_KEY}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "max_tokens": 150,
                  "messages": [
                      {"role": "system", "content": ATTRACTION_QUERY_PROMPT},
                      {"role": "user", "content": f"用户问：{text}\n\n搜索结果：\n{raw}"},
                  ]},
            timeout=15
        ).json()
        return r["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[attraction_query] deepseek error: {e}")
        return f"查到了一些信息但整理失败，建议直接搜索「{text} 最新」了解～"

# ── DeepSeek 闲聊 ─────────────────────────────────────────────────────────────

PERSONA_PROMPT = """你是「旅途向导」，一个旅行规划小助手。风格：像一张手写便签——简短、温暖、有点手绘感，偶尔用一个贴切的 emoji，但不堆砌。

【你能做的事】
- 收集酒店：用户发链接 / 截图，自动识别录入候选名单
- 删除酒店：理解「去掉上海的」「把如家删了」「清空列表」等自然语言指令
- 通勤排名：网页端选景点后，按公共交通/驾车/步行排名酒店
- 避雷分析：自动分析大众点评评论，提炼⚠️警告点和评分
- 多城市：发「换城市 成都」可切换
- 旅行咨询：回答城市介绍、景点推荐、行程建议、交通攻略、美食推荐、最佳旅行时间等
- 酒店推荐：根据用户需求推荐适合的酒店类型或区域（不能代预订）
- 当前城市/目的地查询：告知用户当前设置的目的地城市

【你不能做的事，必须如实说明，不能假装能做】
- 不能查订单、不能代预订、不能看实时价格
- 不能退改签，让用户联系平台客服
- 分析数据非实时，仅供参考

【回复规则】
- 100字以内，像写便签一样干净
- 旅行、酒店、景点、交通、美食、城市相关问题都可以回答
- 与旅行完全无关的话题（如编程、政治、娱乐八卦）一句话拒绝：「我只会旅行相关的事～」
- 只说确定的事，不编造数据
- 用户问「怎么用」「有什么功能」，用上面的内容简洁回答
- 用户问「现在的城市」「目的地是哪」时，告知当前设置的城市"""

def deepseek_chat(user_msg: str, city: str = "", history: list[dict] = None) -> str:
    if not DEEPSEEK_KEY:
        return "有什么旅行相关的问题都可以问我～发酒店链接或截图，我帮你整理候选名单！"
    system = PERSONA_PROMPT
    if city:
        system += f"\n\n【当前用户目的地城市】{city}"
    messages = [{"role": "system", "content": system}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_msg})
    try:
        r = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_KEY}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "max_tokens": 150,
                  "messages": messages},
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
    # 记录本次会话的 open_kfid，供主动推送使用
    if open_kfid and user.get("open_kfid") != open_kfid:
        with get_db() as conn:
            conn.execute("UPDATE users SET open_kfid=? WHERE wecom_id=?", (open_kfid, user_id))
            conn.commit()
        user["open_kfid"] = open_kfid
    bot_state = user["bot_state"]
    hotel_count = get_hotel_count(user["id"])
    intent, delete_target, intent_extras = classify_intent(text, msgtype)

    # 天气推送开关
    if msgtype == "text" and text.strip() in ("开启天气提醒", "打开天气提醒", "天气提醒"):
        with get_db() as conn:
            conn.execute("UPDATE users SET push_enabled=1 WHERE wecom_id=?", (user_id,))
            conn.commit()
        send_text(open_kfid, user_id, "✅ 已开启天气提醒！每天早上 8 点会给你推送目的地天气和预警 🌤️\n\n发「关闭天气提醒」可随时关闭。")
        return
    if msgtype == "text" and text.strip() in ("关闭天气提醒", "取消天气提醒", "停止天气提醒"):
        with get_db() as conn:
            conn.execute("UPDATE users SET push_enabled=0 WHERE wecom_id=?", (user_id,))
            conn.commit()
        send_text(open_kfid, user_id, "已关闭天气提醒。发「开启天气提醒」可重新开启。")
        return

    # 非闲聊意图时重置连续闲聊计数
    if intent != "chitchat":
        kv_set(f"offtopic:{user_id}", "0")

    # ── 行程规划中间状态（优先于意图分类，避免数字被误判）──────────────────
    if msgtype == "text":
        plan_state = plan_get(user_id, "state")
        if plan_state in ("selecting_transport", "selecting_attractions", "selecting_restaurants"):
            handle_plan_selection(open_kfid, user_id, text)
            return
        if plan_state == "generating":
            send_text(open_kfid, user_id, "行程正在生成中，稍等片刻～ ⏳")
            return

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
            # 城市不匹配：暂存，先询问用户
            old_city = user.get("city", "")
            if ctrip["city"] and old_city and ctrip["city"] != old_city:
                pending_import_save(user_id, ctrip)
                send_text(open_kfid, user_id,
                    f"这家酒店在「{ctrip['city']}」，但你当前的目的地是「{old_city}」。\n\n"
                    f"要把目的地切换到「{ctrip['city']}」并收录这家酒店吗？\n"
                    f"回复「好」确认，「不换」则取消本次导入。")
                return
            # 正常入库
            _do_save_hotel(open_kfid, user_id, user, ctrip, text, hotel_count)
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
                """SELECT id, name, city, source_url, hotel_id, lat, lng, rating, raw_text, platform
                   FROM hotels WHERE user_id=? AND (city LIKE ? OR name LIKE ? OR platform LIKE ?)""",
                (user["id"], f"%{delete_target}%", f"%{delete_target}%", f"%{delete_target}%")
            ).fetchall()
            if not rows:
                send_text(open_kfid, user_id,
                    f"候选名单里没找到和「{delete_target}」相关的酒店～\n\n发「看结果」可以查看当前全部候选。")
                return
            trash_save(user_id, rows)  # 先备份到回收站
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
            f"✅ 已删除：{names}\n\n"
            f"剩余候选酒店：{remaining} 家\n"
            f"如果删错了，发「恢复」可以找回～")
        return

    # 分支：恢复误删酒店
    if intent == "restore":
        restored = trash_restore(user_id, user["id"])
        if not restored:
            send_text(open_kfid, user_id,
                "没有可以恢复的记录～\n\n回收站在新酒店导入或5轮对话后会自动清空。")
        else:
            names = "、".join(r.get("name","") for r in restored[:3])
            if len(restored) > 3:
                names += f" 等{len(restored)}家"
            remaining = get_hotel_count(user["id"])
            send_text(open_kfid, user_id,
                f"✅ 已恢复：{names}\n\n当前候选酒店：{remaining} 家")
        return

    # 分支：确认/取消待处理的城市切换导入
    if intent in ("confirm", "cancel"):
        pending = pending_import_pop(user_id)
        if pending:
            if intent == "confirm":
                _do_save_hotel(open_kfid, user_id, user, pending, "", get_hotel_count(user["id"]))
            else:
                send_text(open_kfid, user_id,
                    f"好的，已取消导入「{pending.get('name','')}」，目的地保持「{user.get('city','')}」不变。")
            return
        # 没有待处理的 pending，当普通闲聊
        # fall through to chitchat

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

    # 分支：切换目的地城市
    if intent == "set_city":
        new_city = delete_target.strip()  # target 字段复用 delete_target 变量
        if new_city:
            set_user_city(user_id, new_city)
            user["city"] = new_city
            send_text(open_kfid, user_id, f"✅ 目的地已切换为「{new_city}」，帮你查一下当地信息～ 🗺️")
            def _send_brief():
                brief = fetch_city_brief(new_city)
                send_text(open_kfid, user_id, brief)
            threading.Thread(target=_send_brief, daemon=True).start()
        else:
            send_text(open_kfid, user_id, "请告诉我要换到哪个城市～比如「改成成都」")
        return

    # 分支：清空列表
    if intent == "clear":
        with get_db() as conn:
            conn.execute("DELETE FROM hotels WHERE user_id=?", (user["id"],))
            conn.commit()
        send_text(open_kfid, user_id,
            "✅ 已清空候选酒店列表\n\n重新发酒店链接或分享文本，开始新一轮规划～")
        return

    # 分支：行程规划 F4
    if intent == "attraction_recommend" and msgtype == "text":
        city       = delete_target.strip() or user.get("city", "")
        preference = intent_extras.get("preference", "")
        if not city:
            send_text(open_kfid, user_id, "你想了解哪个城市的景点或餐厅呢？")
            return
        def _recommend():
            kw_attraction = f"{preference}景点" if preference else "热门景点"
            kw_restaurant = f"{preference}美食" if preference else "特色餐厅"
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
                fa = ex.submit(search_pois, city, kw_attraction, "110000", 6)
                fr = ex.submit(search_pois, city, kw_restaurant, "050000", 5)
                attractions = fa.result()
                restaurants = fr.result()
            lines = [f"📍 {city}{'·' + preference if preference else ''} 推荐\n"]
            if attractions:
                lines.append("【景点】")
                for p in attractions:
                    lines.append(f"• {p['name']}" + (f"（{p['address']}）" if p.get("address") else ""))
            if restaurants:
                lines.append("\n【餐厅】")
                for p in restaurants:
                    rating = f" ⭐{p['rating']}" if p.get("rating") else ""
                    lines.append(f"• {p['name']}{rating}" + (f"（{p['address']}）" if p.get("address") else ""))
            lines.append("\n想规划具体行程？告诉我天数，我帮你安排路线～")
            send_text(open_kfid, user_id, "\n".join(lines))
        send_text(open_kfid, user_id, f"帮你查{city}的景点和餐厅，稍等～ 🗺️")
        threading.Thread(target=_recommend, daemon=True).start()
        return

    if intent == "plan_trip" and msgtype == "text":
        city       = delete_target.strip() or user.get("city", "")
        days       = int(intent_extras.get("days", 2))
        preference = intent_extras.get("preference", "")
        if not city:
            send_text(open_kfid, user_id, "你想规划哪个城市的行程呢？告诉我城市名就行～")
            return
        if city != user.get("city", ""):
            set_user_city(user_id, city)
            user["city"] = city
        threading.Thread(
            target=handle_plan_trip,
            args=(open_kfid, user_id, city, days, preference),
            daemon=True
        ).start()
        return

    # 分支C-1：景点实时情况查询
    if intent == "attraction_query" and msgtype == "text":
        deny = check_rate_limit(user_id, "deepseek")
        if deny:
            send_text(open_kfid, user_id, deny)
            return
        log_usage(user_id, "deepseek")
        reply = query_attraction_status(text, user.get("city", ""))
        send_text(open_kfid, user_id, reply)
        append_chat_history(user_id, text, reply)
        return

    # 分支C：普通闲聊
    deny = check_rate_limit(user_id, "deepseek")
    if deny:
        send_text(open_kfid, user_id, deny)
        return

    log_usage(user_id, "deepseek")
    history = get_chat_history(user_id)
    reply = deepseek_chat(text, city=user.get("city", ""), history=history)
    send_text(open_kfid, user_id, reply)
    append_chat_history(user_id, text, reply)

# ── 和风天气 ─────────────────────────────────────────────────────────────────

# 高德天气接口（免费，复用已有 AMAP_WEB_KEY）
AMAP_WEATHER_URL = "https://restapi.amap.com/v3/weather/weatherInfo"

# 高德天气描述 → emoji
_AMAP_WEATHER_EMOJI = {
    "晴": "☀️", "少云": "🌤️", "晴间多云": "⛅", "多云": "🌥️",
    "阴": "☁️", "有风": "🌬️", "平静": "😌", "微风": "🍃",
    "和风": "🍃", "清风": "🍃", "强风": "💨", "疾风": "💨",
    "大风": "🌬️", "烈风": "🌬️", "风暴": "🌀", "狂爆风": "🌀",
    "飓风": "🌀", "热带风暴": "🌀", "龙卷风": "🌪️",
    "阵雨": "🌦️", "雷阵雨": "⛈️", "雷阵雨并伴有冰雹": "⛈️",
    "小雨": "🌧️", "中雨": "🌧️", "大雨": "🌧️", "暴雨": "🌧️",
    "大暴雨": "🌧️", "特大暴雨": "🌧️", "强阵雨": "🌧️",
    "强雷阵雨": "⛈️", "极端降雨": "🌧️", "毛毛雨": "🌦️",
    "雨": "🌧️", "小雨-中雨": "🌧️", "中雨-大雨": "🌧️",
    "大雨-暴雨": "🌧️", "暴雨-大暴雨": "🌧️", "大暴雨-特大暴雨": "🌧️",
    "雨雪天气": "🌨️", "雨夹雪": "🌨️", "阵雨夹雪": "🌨️",
    "冻雨": "🌨️", "雪": "❄️", "阵雪": "🌨️", "小雪": "🌨️",
    "中雪": "❄️", "大雪": "❄️", "暴雪": "❄️", "小雪-中雪": "❄️",
    "中雪-大雪": "❄️", "大雪-暴雪": "❄️", "浮尘": "🌫️",
    "扬沙": "🌫️", "沙尘暴": "🌫️", "强沙尘暴": "🌫️",
    "霾": "😷", "中度霾": "😷", "重度霾": "😷", "严重霾": "😷",
    "雾": "🌫️", "浓雾": "🌫️", "强浓雾": "🌫️", "轻雾": "🌫️",
    "大雾": "🌫️", "特强浓雾": "🌫️", "热": "🌡️", "冷": "🥶",
    "未知": "🌡️",
}

def _amap_weather_emoji(desc: str) -> str:
    return _AMAP_WEATHER_EMOJI.get(desc, "🌡️")

def amap_weather_now(city: str) -> dict | None:
    """高德实时天气，返回标准化字典"""
    try:
        r = requests.get(AMAP_WEATHER_URL, params={
            "key": AMAP_WEB_KEY, "city": city,
            "extensions": "base", "output": "JSON",
        }, timeout=6).json()
        lives = r.get("lives", [])
        if lives:
            w = lives[0]
            return {
                "text":      w.get("weather", ""),
                "temp":      w.get("temperature", ""),
                "windDir":   w.get("winddirection", ""),
                "windScale": w.get("windpower", ""),
                "humidity":  w.get("humidity", ""),
            }
    except Exception as e:
        print(f"[amap_weather] {city} error: {e}")
    return None

def amap_weather_warnings(city: str) -> list[dict]:
    """高德暂无预警接口，返回空列表占位"""
    return []

# 兼容旧函数名
def qweather_now(city: str) -> dict | None:
    return amap_weather_now(city)

def qweather_warnings(city: str) -> list[dict]:
    return amap_weather_warnings(city)

def format_weather_push(city: str) -> str | None:
    """格式化今日天气推送文字，无 key 或请求失败返回 None"""
    now = qweather_now(city)
    if not now:
        return None
    emoji = _amap_weather_emoji(now.get("text", ""))
    text = now.get("text", "")
    temp = now.get("temp", "")
    wind_dir = now.get("windDir", "")
    wind_sc  = now.get("windScale", "")
    humidity = now.get("humidity", "")

    lines = [
        f"🗺️ {city} · 今日天气",
        f"{emoji} {text}　{temp}°C",
        f"💨 {wind_dir} {wind_sc}级　💧 湿度 {humidity}%",
    ]

    # 预警
    warnings = qweather_warnings(city)
    if warnings:
        lines.append("")
        lines.append("⚠️ 气象预警：")
        for w in warnings[:3]:
            sev  = w.get("severityColor", "")
            title = w.get("title", w.get("typeName", "预警"))
            lines.append(f"  🔴 {title}" if sev in ("Red", "Orange") else f"  🟡 {title}")
        lines.append("出行注意安全～")
    else:
        lines.append("\n今天天气不错，出发顺利！☀️")

    return "\n".join(lines)

# ── F4 行程规划 Bundle ────────────────────────────────────────────────────────

import math

AMAP_POI_URL   = "https://restapi.amap.com/v3/place/text"
AMAP_TRANSIT_URL = "https://restapi.amap.com/v3/direction/transit/integrated"
AMAP_DRIVING_URL = "https://restapi.amap.com/v3/direction/driving"

# plan_state 值: idle / selecting_attractions / selecting_restaurants / generating
def plan_get(user_id: str, field: str):
    raw = kv_get(f"plan_{field}:{user_id}")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return raw  # 字符串直接返回

def plan_set(user_id: str, field: str, value):
    kv_set(f"plan_{field}:{user_id}", json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value)

def plan_clear(user_id: str):
    for field in ("state", "meta", "attractions", "restaurants", "selection", "bundles"):
        kv_set(f"plan_{field}:{user_id}", "")
    kv_set(f"plan_attrpage:{user_id}", "0")
    kv_set(f"plan_shown_attractions:{user_id}", "[]")

def search_pois(city: str, keywords: str, poi_type: str = "", limit: int = 8) -> list[dict]:
    """高德 POI 搜索，返回标准化列表。poi_type 为空则不限类型（适合按名称精确查找）"""
    params = {
        "key": AMAP_WEB_KEY, "city": city,
        "keywords": keywords,
        "offset": limit, "page": 1, "extensions": "base",
    }
    if poi_type:
        params["types"] = poi_type
    try:
        r = requests.get(AMAP_POI_URL, params=params, timeout=8).json()
        pois = []
        for p in r.get("pois", []):
            loc = p.get("location", "")
            if not loc:
                continue
            lng, lat = loc.split(",")
            pois.append({
                "name":    p.get("name", ""),
                "address": p.get("address", ""),
                "rating":  p.get("biz_ext", {}).get("rating", "") if isinstance(p.get("biz_ext"), dict) else "",
                "lat":     float(lat),
                "lng":     float(lng),
                "id":      p.get("id", ""),
            })
        return pois[:limit]
    except Exception as e:
        print(f"[search_pois] error: {e}")
        return []

def haversine_km(lat1, lng1, lat2, lng2) -> float:
    """两点直线距离（公里）"""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2
    return R * 2 * math.asin(math.sqrt(a))

def amap_transit_minutes(origin_lat, origin_lng, dest_lat, dest_lng, city: str) -> int | None:
    """高德公共交通导航，返回分钟数；失败返回 None"""
    try:
        r = requests.get(AMAP_TRANSIT_URL, params={
            "key": AMAP_WEB_KEY,
            "origin":      f"{origin_lng},{origin_lat}",
            "destination": f"{dest_lng},{dest_lat}",
            "city": city, "cityd": city,
        }, timeout=8).json()
        routes = r.get("route", {}).get("transits", [])
        if routes:
            return int(routes[0].get("duration", 0)) // 60
    except Exception as e:
        print(f"[amap_transit] error: {e}")
    return None

def amap_driving_minutes(origin_lat, origin_lng, dest_lat, dest_lng) -> int | None:
    """高德驾车导航，返回分钟数；失败返回 None"""
    try:
        r = requests.get(AMAP_DRIVING_URL, params={
            "key": AMAP_WEB_KEY,
            "origin":      f"{origin_lng},{origin_lat}",
            "destination": f"{dest_lng},{dest_lat}",
            "extensions": "base",
        }, timeout=8).json()
        paths = r.get("route", {}).get("paths", [])
        if paths:
            return int(paths[0].get("duration", 0)) // 60
    except Exception as e:
        print(f"[amap_driving] error: {e}")
    return None

def build_distance_info(selected: list[dict], city: str,
                        transport_mode: str = "both") -> list[list]:
    """
    混合算法：
    1. 直线距离初筛，超过 30km 标注「较远」，跳过路线请求
    2. 合理范围内并行请求公交 + 驾车时间（按 transport_mode 决定请求哪些）
    返回 matrix[i][j] = {"km": float, "transit_min": int|None,
                          "driving_min": int|None, "far": bool}
    """
    import concurrent.futures
    n = len(selected)
    matrix = [[None]*n for _ in range(n)]

    # 收集需要请求路线的 (i, j) 对
    pairs_to_query = []
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            km = haversine_km(selected[i]["lat"], selected[i]["lng"],
                              selected[j]["lat"], selected[j]["lng"])
            far = km > 30
            matrix[i][j] = {"km": round(km, 1), "transit_min": None, "driving_min": None, "far": far}
            if not far:
                pairs_to_query.append((i, j))

    # 并行请求路线
    def _query(i, j):
        a, b = selected[i], selected[j]
        transit = driving = None
        if transport_mode in ("transit", "both"):
            transit = amap_transit_minutes(a["lat"], a["lng"], b["lat"], b["lng"], city)
        if transport_mode in ("driving", "both"):
            driving = amap_driving_minutes(a["lat"], a["lng"], b["lat"], b["lng"])
        return i, j, transit, driving

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        futures = [ex.submit(_query, i, j) for i, j in pairs_to_query]
        for f in concurrent.futures.as_completed(futures):
            i, j, transit, driving = f.result()
            matrix[i][j]["transit_min"] = transit
            matrix[i][j]["driving_min"] = driving

    return matrix

BUNDLE_PROMPT = """你是专业旅行规划师。根据用户选择的景点和餐厅、距离/交通时间信息，生成 2-3 套行程方案。

规则：
- 每套方案有独特风格（如：轻松版 / 深度版 / 特种兵版）
- 每天安排不超过 3 个景点 + 1-2 家餐厅，考虑交通时间
- 用交通信息里提供的时间标注每段路程（如「驾车约 15 分钟」或「公交约 25 分钟」）
- 如同时有公交和驾车时间，在行程里分别注明（如「公交 25 分钟 / 驾车 12 分钟」）
- 如果某两个景点标注为「较远」，避免安排在同一天
- 输出纯文本，格式清晰，用【方案名】和 Day1/Day2 分隔
- 结尾问用户选哪套，或是否需要调整"""

_MODE_LABEL = {"transit": "公共交通", "driving": "驾车", "both": "公共交通+驾车"}

def generate_bundles(selected_attractions: list[dict], selected_restaurants: list[dict],
                     city: str, days: int, preference: str,
                     transport_mode: str = "both") -> str:
    all_selected = selected_attractions + selected_restaurants
    matrix = build_distance_info(all_selected, city, transport_mode)

    dist_lines = []
    names = [p["name"] for p in all_selected]
    for i in range(len(all_selected)):
        for j in range(i+1, len(all_selected)):
            m = matrix[i][j]
            if m["far"]:
                dist_lines.append(f"{names[i]} ↔ {names[j]}：较远（直线{m['km']}km），不建议同天")
                continue
            parts = []
            if m.get("transit_min"):
                parts.append(f"公交约{m['transit_min']}分钟")
            if m.get("driving_min"):
                parts.append(f"驾车约{m['driving_min']}分钟")
            time_str = " / ".join(parts) if parts else f"直线{m['km']}km"
            dist_lines.append(f"{names[i]} ↔ {names[j]}：{time_str}（{m['km']}km）")

    attraction_list = "\n".join(f"- {p['name']}（{p['address'] or '景点'}）" for p in selected_attractions)
    restaurant_list = "\n".join(f"- {p['name']}（{p['address'] or '餐厅'}）" for p in selected_restaurants)
    mode_note = f"交通方式偏好：{_MODE_LABEL.get(transport_mode, '公共交通+驾车')}"

    user_content = (f"目的地：{city}，行程天数：{days}天，偏好：{preference}\n{mode_note}\n\n"
                    f"已选景点：\n{attraction_list}\n\n"
                    f"已选餐厅：\n{restaurant_list}\n\n"
                    f"交通信息：\n" + "\n".join(dist_lines))
    try:
        r = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_KEY}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "max_tokens": 800,
                  "messages": [{"role": "system", "content": BUNDLE_PROMPT},
                                {"role": "user",   "content": user_content}]},
            timeout=20
        ).json()
        return r["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[generate_bundles] error: {e}")
        return "行程生成失败，请稍后再试～"

def parse_selection(text: str, max_n: int) -> list[int]:
    """从用户输入里提取编号，返回 0-based 索引列表。支持"1 3 5"/"1、3、5"/"第一和第三"等"""
    nums = [int(x) for x in re.findall(r'\d+', text) if 1 <= int(x) <= max_n]
    return [n - 1 for n in dict.fromkeys(nums)]  # 去重，保序，转 0-based

def format_poi_list(pois: list[dict], start_idx: int = 1) -> str:
    lines = []
    for i, p in enumerate(pois):
        rating_str = f" ⭐{p['rating']}" if p.get("rating") else ""
        lines.append(f"{start_idx + i}. {p['name']}{rating_str}")
    return "\n".join(lines)

def handle_plan_trip(open_kfid: str, user_id: str, city: str, days: int, preference: str):
    """启动行程规划流程：先询问交通方式，再展示景点"""
    plan_clear(user_id)
    plan_set(user_id, "meta", {"city": city, "days": days, "preference": preference, "transport_mode": "both"})
    plan_set(user_id, "state", "selecting_transport")
    send_text(open_kfid, user_id,
        f"好的！{city} {days} 天行程，先告诉我你的出行方式：\n\n"
        f"1️⃣ 公共交通（地铁/公交）\n"
        f"2️⃣ 自驾\n"
        f"3️⃣ 两种都参考\n\n"
        f"回复 1、2 或 3")

def _plan_sub_intent(text: str, state: str, pois: list[dict]) -> dict:
    """在规划流程某状态内识别子意图，处理名称/编号混用和语音矛盾。
    返回 dict，如 {"intent":"select","indices":[0,2]} (0-based)
    """
    n = len(pois)
    poi_list_str = "\n".join(f"{i+1}. {p['name']}" for i, p in enumerate(pois))

    prompts = {
        "selecting_transport": (
            "用户正在选择出行交通方式，选项：1公共交通 2自驾 3两种都参考。\n"
            "只返回JSON：\n"
            '{"intent":"select","mode":"transit"}  ← 公共交通\n'
            '{"intent":"select","mode":"driving"}   ← 自驾\n'
            '{"intent":"select","mode":"both"}      ← 两种都参考\n'
            '{"intent":"cancel"}                    ← 取消规划'
        ),
        "selecting_attractions": (
            f"用户正在从以下景点列表做选择：\n{poi_list_str}\n\n"
            "用户可能用编号、名称或自然语言表达选择；语音输入可能有矛盾（如'要1不对要3'），"
            "请自动去除矛盾，只保留最终意图。\n"
            "重要规则：\n"
            "1. 只有当用户说的名称/编号与列表中某项明确匹配时，才返回 select。\n"
            "2. 如果用户提到的景点名称不在列表中（如'武侯祠''宽窄巷子'等），返回 search_add，不要猜测或选错误的列表项。\n"
            '3. cancel 仅在用户明确说【不玩了】【取消】【算了不规划了】等时触发。\n'
            "query_intro：想了解景点的介绍/特色/是什么（如'这是什么地方''有什么好玩的'）\n"
            "query_realtime：想查景点实时状态（如'人多吗''开放吗''现在怎么样'）\n"
            "只返回JSON（indices为1-based编号列表）：\n"
            '{"intent":"select","indices":[1,3]}              ← 选列表中的景点（名称或编号能确认匹配）\n'
            '{"intent":"search_add","target":"武侯祠"}        ← 想添加列表中没有的景点\n'
            '{"intent":"done"}                                ← 景点选好了，去选餐厅（如"好了""去选餐厅""下一步"）\n'
            '{"intent":"query_intro","target":"景点名"}       ← 想了解景点介绍\n'
            '{"intent":"query_realtime","target":"景点名"}    ← 想查实时情况\n'
            '{"intent":"refresh","preference":""}             ← 换一批景点\n'
            '{"intent":"back"}                                ← 返回交通方式选择\n'
            '{"intent":"cancel"}                              ← 明确取消规划\n'
            '{"intent":"other","reply":"..."}                 ← 抱怨/疑问/无法识别，reply填回复内容'
        ),
        "selecting_restaurants": (
            f"用户正在从以下餐厅列表做选择：\n{poi_list_str}\n\n"
            "用户可能用编号、名称或自然语言；语音矛盾自动去除，只保留最终意图。\n"
            "重要规则：\n"
            "1. 只有当用户说的名称/编号与列表中某项明确匹配时，才返回 select。\n"
            "2. 如果用户提到的餐厅名称不在列表中，返回 search_add，不要猜测选错误项。\n"
            '3. cancel 仅在用户明确说【不吃了】【取消】【算了】等时触发。\n'
            "query_intro：想了解餐厅的介绍/特色/口味（如'这家什么风格''好不好吃'）\n"
            "query_realtime：想查实时情况（如'现在排队吗''今天几点关'）\n"
            "只返回JSON（indices为1-based编号列表）：\n"
            '{"intent":"select","indices":[2,4]}              ← 选列表中的餐厅（名称或编号能确认匹配）\n'
            '{"intent":"search_add","target":"餐厅名"}        ← 想添加列表中没有的餐厅\n'
            '{"intent":"done"}                                ← 选好了，生成行程（如"好了""生成""可以了""好了生成行程"）\n'
            '{"intent":"skip"}                                ← 跳过餐厅直接生成行程（不选餐厅）\n'
            '{"intent":"query_intro","target":"餐厅名"}       ← 想了解餐厅介绍\n'
            '{"intent":"query_realtime","target":"餐厅名"}    ← 想查实时情况\n'
            '{"intent":"refresh","preference":""}             ← 换一批餐厅\n'
            '{"intent":"back"}                                ← 返回景点选择\n'
            '{"intent":"cancel"}                              ← 明确取消规划\n'
            '{"intent":"other","reply":"..."}                 ← 抱怨/疑问/无法识别，reply填回复内容'
        ),
    }

    system = prompts.get(state, "")
    if DEEPSEEK_KEY and system:
        try:
            r = requests.post(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {DEEPSEEK_KEY}", "Content-Type": "application/json"},
                json={"model": "deepseek-chat", "max_tokens": 60,
                      "messages": [{"role": "system", "content": system},
                                    {"role": "user",   "content": text}]},
                timeout=8
            ).json()
            obj = json.loads(r["choices"][0]["message"]["content"].strip())
            # 把 1-based indices 转成 0-based，过滤越界
            if "indices" in obj:
                obj["indices"] = [i-1 for i in obj["indices"] if 1 <= i <= n]
            return obj
        except Exception as e:
            print(f"[plan_sub_intent] error: {e}")

    # 兜底：纯数字解析
    nums = [int(x)-1 for x in re.findall(r'\d+', text) if 1 <= int(x) <= n]
    if nums:
        return {"intent": "select", "indices": list(dict.fromkeys(nums))}
    if any(k in text for k in ("跳过", "不选", "直接")):
        return {"intent": "skip"}
    if any(k in text for k in ("返回", "上一步", "重选")):
        return {"intent": "back"}
    if any(k in text for k in ("取消", "算了", "不规划")):
        return {"intent": "cancel"}
    return {"intent": "unknown"}


# 景点多类别搜索配置：(关键词, 高德POI类型码)
# 全部指定类型码，避免 Amap 返回学校/医院等无关地点
_ATTRACTION_BUCKETS = [
    ("", "110201"),   # 游览景区
    ("", "110202"),   # 古迹遗址
    ("", "141200"),   # 博物馆
    ("", "110101"),   # 公园
    ("", "110103"),   # 广场/步行街（网红打卡类）
    ("", "110200"),   # 风景名胜区
]

# 名称含这些词的 POI 直接排除（学校、医院、政府机关等）
_NON_ATTRACTION_KEYWORDS = {
    "学校", "中学", "小学", "大学", "学院", "幼儿园", "培训", "职业技术",
    "医院", "诊所", "卫生院", "药店",
    "银行", "信用社", "保险",
    "政府", "机关", "派出所", "公安", "法院", "检察",
    "加油站", "停车场", "服务区",
}

def _is_tourist_attraction(name: str) -> bool:
    return not any(kw in name for kw in _NON_ATTRACTION_KEYWORDS)

def _fetch_attractions_multi(city: str, preference: str, exclude_names: set = None) -> list[dict]:
    """并发搜多个类别，去重合并，排除已展示过的，返回最多 8 条"""
    import concurrent.futures
    exclude = exclude_names or set()
    if preference:
        # 有偏好时：先按偏好关键词+各大景点类型搜
        buckets = [
            (preference, "110201"), (preference, "141200"),
            (preference, "110101"), (preference, "110202"),
        ]
    else:
        buckets = _ATTRACTION_BUCKETS

    # 已展示过太多时尝试第2页
    amap_page = 2 if len(exclude) >= 8 else 1

    results: list[dict] = []
    seen: set[str] = set(exclude)

    def _fetch(kw, ptype):
        params = {"key": AMAP_WEB_KEY, "city": city, "keywords": kw,
                  "offset": 10, "page": amap_page, "extensions": "base"}
        if ptype:
            params["types"] = ptype
        try:
            r = requests.get(AMAP_POI_URL, params=params, timeout=8).json()
            pois = []
            for p in r.get("pois", []):
                loc = p.get("location", "")
                if not loc:
                    continue
                name = p.get("name", "")
                if not _is_tourist_attraction(name):
                    continue
                lng, lat = loc.split(",")
                pois.append({
                    "name": name,
                    "address": p.get("address", ""),
                    "rating": p.get("biz_ext", {}).get("rating", "") if isinstance(p.get("biz_ext"), dict) else "",
                    "lat": float(lat), "lng": float(lng), "id": p.get("id", ""),
                })
            return pois
        except Exception as e:
            print(f"[_fetch_attractions] {kw} error: {e}")
            return []

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(buckets)) as ex:
        futures = [ex.submit(_fetch, kw, pt) for kw, pt in buckets]
        for f in concurrent.futures.as_completed(futures):
            for p in f.result():
                if p["name"] not in seen:
                    seen.add(p["name"])
                    results.append(p)

    return results[:8]


def _show_attractions(open_kfid, user_id, city, preference, page=0):
    """搜景点并展示，换一批时排除已展示过的"""
    # 读取已展示名称
    shown_raw = kv_get(f"plan_shown_attractions:{user_id}") or "[]"
    try:
        shown_names: set = set(json.loads(shown_raw))
    except Exception:
        shown_names = set()

    attractions = _fetch_attractions_multi(city, preference, shown_names)
    if not attractions:
        send_text(open_kfid, user_id, f"没找到更多{city}景点了，换个关键词试试？")
        return
    # 记录已展示名称，供下次换一批排除
    shown_names.update(p["name"] for p in attractions)
    kv_set(f"plan_shown_attractions:{user_id}", json.dumps(list(shown_names), ensure_ascii=False))

    plan_set(user_id, "attractions", attractions)
    plan_set(user_id, "state", "selecting_attractions")
    reply = (f"📍 {city} 景点推荐（回复编号或名称，可多选）：\n\n"
             f"{format_poi_list(attractions)}\n\n"
             f"可以发数字、景点名，或说「换一批」「想了解XX」「返回」")
    send_text(open_kfid, user_id, reply)


def _show_restaurants(open_kfid, user_id, city, preference):
    """搜餐厅并展示"""
    rest_keywords = f"{preference}餐厅" if preference else "餐厅"
    restaurants = search_pois(city, rest_keywords, "050000", limit=6)
    if not restaurants:
        return []
    plan_set(user_id, "restaurants", restaurants)
    plan_set(user_id, "state", "selecting_restaurants")
    reply = (f"🍜 {city} 餐厅推荐（回复编号或名称，1-3 家）：\n\n"
             f"{format_poi_list(restaurants)}\n\n"
             f"可发数字、餐厅名，「跳过」直接生成行程，或「换一批」「返回」")
    send_text(open_kfid, user_id, reply)
    return restaurants


def handle_plan_selection(open_kfid: str, user_id: str, text: str):
    """处理用户在行程规划各步骤的输入，每步先做子意图分类再分流"""
    state = plan_get(user_id, "state")
    meta  = plan_get(user_id, "meta") or {}
    city           = meta.get("city", "")
    days           = meta.get("days", 2)
    preference     = meta.get("preference", "")
    transport_mode = meta.get("transport_mode", "both")

    # ── 交通方式选择 ──────────────────────────────────────────────────────────
    if state == "selecting_transport":
        sub = _plan_sub_intent(text, state, [])
        if sub.get("intent") == "cancel":
            plan_clear(user_id)
            send_text(open_kfid, user_id, "已取消行程规划，随时可以重新开始～")
            return
        mode = sub.get("mode")
        if not mode:
            send_text(open_kfid, user_id, "回复 1（公共交通）、2（自驾）或 3（两种都参考）就行～")
            return
        meta["transport_mode"] = mode
        plan_set(user_id, "meta", meta)
        send_text(open_kfid, user_id, f"按{_MODE_LABEL[mode]}规划！帮你查{city}景点，稍等～ 🗺️")
        threading.Thread(target=_show_attractions, args=(open_kfid, user_id, city, preference), daemon=True).start()
        return

    # ── 景点选择 ──────────────────────────────────────────────────────────────
    if state == "selecting_attractions":
        attractions = plan_get(user_id, "attractions") or []

        # 预检：含"加入/添加…规划/行程"明确动作 → 直接 search_add，不走 DeepSeek
        import re as _re2
        _add_pattern = _re2.compile(
            r'(加入|加进|添加|把.{1,10}加|我要.{1,10}规划|我想加|帮我加|也加一下|加上).{0,6}(规划|行程|里|进来|上去)?'
        )
        _precheck_add = _add_pattern.search(text)
        if _precheck_add:
            # 提取目标：去掉动词前缀和后缀
            _cleaned = _re2.sub(r'(帮我|把|将|加入规划|加进规划|加入行程|加进行程|加入|添加|加进来|加上|我要|我想|也|规划|行程)', '', text).strip()
            if _cleaned and not any(p["name"] in _cleaned or _cleaned in p["name"] for p in attractions):
                sub = {"intent": "search_add", "target": _cleaned}
            else:
                sub = _plan_sub_intent(text, state, attractions)
        else:
            sub = _plan_sub_intent(text, state, attractions)
        intent = sub.get("intent")

        if intent == "cancel":
            plan_clear(user_id)
            send_text(open_kfid, user_id, "已取消行程规划，随时可以重新开始～")
            return

        if intent == "other":
            reply = sub.get("reply") or "没太明白～可以直接发编号选景点，或说「换一批」「返回」"
            send_text(open_kfid, user_id, reply)
            return

        if intent == "back":
            plan_set(user_id, "state", "selecting_transport")
            send_text(open_kfid, user_id,
                f"好，重新选择出行方式：\n\n"
                f"1️⃣ 公共交通　2️⃣ 自驾　3️⃣ 两种都参考\n\n回复 1、2 或 3")
            return

        if intent == "search_add":
            target = sub.get("target", "")
            if not target:
                send_text(open_kfid, user_id, "没听清你想加哪个景点，再说一次？")
                return
            send_text(open_kfid, user_id, f"帮你搜一下「{target}」，稍等～")
            def _search_and_add(tgt=target):
                results = search_pois(city, tgt, "", limit=1)  # 不限类型，按名称精确找
                if not results:
                    send_text(open_kfid, user_id, f"没找到「{tgt}」，可以换个名字再试，或从列表里选～")
                    return
                poi = results[0]
                # 加入已选列表并继续
                sel = plan_get(user_id, "selection") or {}
                already = sel.get("attractions", [])
                if not any(p["name"] == poi["name"] for p in already):
                    already.append(poi)
                plan_set(user_id, "selection", {"attractions": already, "restaurants": []})
                names = "、".join(p["name"] for p in already)
                send_text(open_kfid, user_id, f"已加入：{poi['name']} ✅\n当前已选：{names}\n\n继续选景点，或发「好了去选餐厅」「换一批」")
            threading.Thread(target=_search_and_add, daemon=True).start()
            return

        if intent == "refresh":
            pref = sub.get("preference", preference)
            send_text(open_kfid, user_id, "换一批景点，稍等～")
            threading.Thread(target=_show_attractions, args=(open_kfid, user_id, city, pref), daemon=True).start()
            return

        if intent in ("query_intro", "query_realtime"):
            target = sub.get("target", "")
            if intent == "query_intro":
                send_text(open_kfid, user_id, f"帮你介绍一下「{target}」～")
                def _q():
                    reply = query_attraction_intro(target, city)
                    send_text(open_kfid, user_id, reply + "\n\n继续从上面的列表选景点，或发「换一批」")
            else:
                send_text(open_kfid, user_id, f"帮你查一下「{target}」的最新情况～")
                def _q():
                    reply = query_attraction_status(target, city)
                    send_text(open_kfid, user_id, reply + "\n\n继续从上面的列表选景点，或发「换一批」")
            threading.Thread(target=_q, daemon=True).start()
            return

        if intent == "done":
            # 用 search_add 加入的景点已在 selection 里，直接进入餐厅
            sel = plan_get(user_id, "selection") or {}
            selected_attractions = sel.get("attractions", [])
            if not selected_attractions:
                send_text(open_kfid, user_id, "还没选景点～先发编号或说景点名加进来～")
                return
            names = "、".join(p["name"] for p in selected_attractions)
            send_text(open_kfid, user_id, f"选好了：{names} ✅\n\n帮你找几家餐厅，稍等～ 🍜")
            threading.Thread(target=_show_restaurants, args=(open_kfid, user_id, city, preference), daemon=True).start()
            return

        if intent == "select":
            indices = sub.get("indices", [])
            if not indices:
                send_text(open_kfid, user_id, "没看清你选了哪些～发编号（如「1 3」）或景点名都行")
                return
            selected_pois = [attractions[i] for i in indices if i < len(attractions)]
            # Python 层保护：如果选中的 POI 名称都没出现在用户文本里，且用户文本包含非数字内容，
            # 说明 DeepSeek 猜错了，转为 search_add
            import re as _re
            text_no_num = _re.sub(r'[\d\s，、和跟还有]', '', text)
            selected_names_in_text = any(p["name"] in text or any(c in text for c in p["name"] if len(c) > 1)
                                         for p in selected_pois)
            if text_no_num and not selected_names_in_text and len(text_no_num) >= 2:
                # 提取用户可能想要的景点名（去掉动词前缀）
                cleaned = _re.sub(r'^(帮我|把|将|加|添加|加入|规划|我要|我想|我|去|也)', '', text_no_num)
                cleaned = _re.sub(r'(加入规划|加到规划|规划进去|加进来|也加上)$', '', cleaned).strip()
                if cleaned:
                    send_text(open_kfid, user_id, f"帮你搜一下「{cleaned}」，稍等～")
                    def _search_and_add_guard(tgt=cleaned):
                        results = search_pois(city, tgt, "", limit=1)
                        if not results:
                            send_text(open_kfid, user_id, f"没找到「{tgt}」，可以换个名字，或从列表里选编号～")
                            return
                        poi = results[0]
                        sel = plan_get(user_id, "selection") or {}
                        already = sel.get("attractions", [])
                        if not any(p["name"] == poi["name"] for p in already):
                            already.append(poi)
                        plan_set(user_id, "selection", {"attractions": already, "restaurants": []})
                        names = "、".join(p["name"] for p in already)
                        send_text(open_kfid, user_id, f"已加入：{poi['name']} ✅\n当前已选：{names}\n\n继续选景点，或发「好了去选餐厅」")
                    threading.Thread(target=_search_and_add_guard, daemon=True).start()
                    return

            selected_attractions = selected_pois
            plan_set(user_id, "selection", {"attractions": selected_attractions, "restaurants": []})
            names = "、".join(p["name"] for p in selected_attractions)
            send_text(open_kfid, user_id, f"选好了：{names} ✅\n\n帮你找几家餐厅，稍等～ 🍜")
            def _next(sa=selected_attractions):
                rests = _show_restaurants(open_kfid, user_id, city, preference)
                if not rests:
                    _trigger_bundle_generation(open_kfid, user_id, sa, [], city, days, preference, transport_mode)
            threading.Thread(target=_next, daemon=True).start()
            return

        send_text(open_kfid, user_id, "没明白你的意思～发编号选景点，或说「换一批」「返回」")
        return

    # ── 餐厅选择 ──────────────────────────────────────────────────────────────
    if state == "selecting_restaurants":
        restaurants = plan_get(user_id, "restaurants") or []
        selection   = plan_get(user_id, "selection") or {}
        selected_attractions = selection.get("attractions", [])

        # 预检：明确"生成行程/好了/可以了" → done（绕过 DeepSeek，避免 skip 清空已选餐厅）
        import re as _re3
        _done_pat = _re3.compile(r'(生成行程|生成规划|好了生成|可以(了|生成)|好了.{0,4}(吧|～|！|!|$)|行程生成|开始生成)')
        if _done_pat.search(text):
            sub = {"intent": "done"}
        else:
            # 预检：明确"加入/添加"动作 → search_add
            _add_pat2 = _re3.compile(
                r'(加入|加进|添加|把.{1,10}加|我要.{1,10}(规划|行程)|我想加|帮我加|也加一下|加上).{0,6}(规划|行程|里|进来|上去)?'
            )
            if _add_pat2.search(text):
                _cleaned2 = _re3.sub(r'(帮我|把|将|加入规划|加进规划|加入行程|加进行程|加入|添加|加进来|加上|我要|我想|也|规划|行程)', '', text).strip()
                if _cleaned2 and not any(p["name"] in _cleaned2 or _cleaned2 in p["name"] for p in restaurants):
                    sub = {"intent": "search_add", "target": _cleaned2}
                else:
                    sub = _plan_sub_intent(text, state, restaurants)
            else:
                sub = _plan_sub_intent(text, state, restaurants)
        intent = sub.get("intent")

        if intent == "cancel":
            plan_clear(user_id)
            send_text(open_kfid, user_id, "已取消行程规划，随时可以重新开始～")
            return

        if intent == "other":
            reply = sub.get("reply") or "没太明白～可以发编号选餐厅，或说「换一批」「跳过」「返回」"
            send_text(open_kfid, user_id, reply)
            return

        if intent == "back":
            plan_set(user_id, "state", "selecting_attractions")
            attractions = plan_get(user_id, "attractions") or []
            if attractions:
                reply = (f"返回景点选择 👇\n\n{format_poi_list(attractions)}\n\n"
                         f"重新发编号或名称选景点，或说「换一批」")
                send_text(open_kfid, user_id, reply)
            else:
                threading.Thread(target=_show_attractions, args=(open_kfid, user_id, city, preference), daemon=True).start()
            return

        if intent == "search_add":
            target = sub.get("target", "")
            if not target:
                send_text(open_kfid, user_id, "没听清你想加哪家餐厅，再说一次？")
                return
            send_text(open_kfid, user_id, f"帮你搜一下「{target}」，稍等～")
            def _search_rest(tgt=target):
                results = search_pois(city, tgt, "", limit=1)  # 不限类型，按名称精确找
                if not results:
                    send_text(open_kfid, user_id, f"没找到「{tgt}」，可以换个名字再试，或从列表里选～")
                    return
                poi = results[0]
                # 获取当前已选餐厅并追加
                sel = plan_get(user_id, "selection") or {}
                curr_rests = sel.get("restaurants", [])
                if not any(p["name"] == poi["name"] for p in curr_rests):
                    curr_rests.append(poi)
                sel["restaurants"] = curr_rests
                plan_set(user_id, "selection", sel)
                names = "、".join(p["name"] for p in curr_rests)
                send_text(open_kfid, user_id, f"已加入：{poi['name']} ✅\n当前已选餐厅：{names}\n\n继续选餐厅，或发「好了生成行程」「跳过」")
            threading.Thread(target=_search_rest, daemon=True).start()
            return

        if intent == "refresh":
            pref = sub.get("preference", preference)
            send_text(open_kfid, user_id, "换一批餐厅，稍等～")
            threading.Thread(target=_show_restaurants, args=(open_kfid, user_id, city, pref), daemon=True).start()
            return

        if intent in ("query_intro", "query_realtime"):
            target = sub.get("target", "")
            if intent == "query_intro":
                send_text(open_kfid, user_id, f"帮你介绍一下「{target}」～")
                def _q():
                    reply = query_attraction_intro(target, city)
                    send_text(open_kfid, user_id, reply + "\n\n继续从上面的列表选餐厅，或发「跳过」直接生成行程")
            else:
                send_text(open_kfid, user_id, f"帮你查一下「{target}」的最新情况～")
                def _q():
                    reply = query_attraction_status(target, city)
                    send_text(open_kfid, user_id, reply + "\n\n继续从上面的列表选餐厅，或发「跳过」直接生成行程")
            threading.Thread(target=_q, daemon=True).start()
            return

        if intent == "done":
            # 用 selection 里已存的餐厅（含 search_add 加入的）
            sel = plan_get(user_id, "selection") or {}
            selected_restaurants = sel.get("restaurants", [])
            plan_set(user_id, "state", "generating")
            _trigger_bundle_generation(open_kfid, user_id, selected_attractions, selected_restaurants,
                                       city, days, preference, transport_mode)
            return

        if intent in ("skip", "select"):
            if intent == "skip":
                selected_restaurants = []
            else:
                indices = sub.get("indices", [])
                selected_restaurants = [restaurants[i] for i in indices if i < len(restaurants)]
                # 合并 search_add 加入的餐厅
                sel = plan_get(user_id, "selection") or {}
                added = sel.get("restaurants", [])
                for p in added:
                    if not any(r["name"] == p["name"] for r in selected_restaurants):
                        selected_restaurants.append(p)
            plan_set(user_id, "state", "generating")
            _trigger_bundle_generation(open_kfid, user_id, selected_attractions, selected_restaurants,
                                       city, days, preference, transport_mode)
            return

        send_text(open_kfid, user_id, "没明白～发编号选餐厅，「跳过」直接生成，或说「换一批」「返回」")

def _trigger_bundle_generation(open_kfid, user_id, selected_attractions, selected_restaurants,
                                city, days, preference, transport_mode="both"):
    send_text(open_kfid, user_id, "正在生成行程方案，稍等 30 秒左右～ ⏳")
    def _gen():
        bundles_text = generate_bundles(selected_attractions, selected_restaurants,
                                        city, days, preference, transport_mode)
        plan_set(user_id, "bundles", bundles_text)
        plan_set(user_id, "state", "idle")
        # 持久化到 trips 历史表，获取 trip_id 用于链接
        trip_id = None
        try:
            with get_db() as conn:
                cur = conn.execute(
                    "INSERT INTO trips (wecom_id, city, days, preference, bundle_text) VALUES (?,?,?,?,?)",
                    (user_id, city, days, preference, bundles_text)
                )
                trip_id = cur.lastrowid
        except Exception as e:
            print(f"[trips] save error: {e}")
        # 发链接，点击在网页端查看漂亮排版
        if trip_id:
            trip_url = f"{H5_URL}?uid={user_id}&trip_id={trip_id}"
            send_text(open_kfid, user_id,
                f"🗺️ {city} {days}日行程方案生成好啦！\n\n"
                f"点击查看完整行程 👇\n{trip_url}\n\n"
                f"行程包含 2 套方案，可在页面里切换～")
        else:
            send_text(open_kfid, user_id, bundles_text)
    threading.Thread(target=_gen, daemon=True).start()

CITY_BRIEF_PROMPT = """你是旅行助手，根据下面的天气数据和搜索摘要，为用户生成一段目的地城市简报。

要求：
- 150字以内，像写便签一样简洁温暖
- 结构：当前天气 → 1-2句城市/旅行亮点或近期注意事项 → 一句鼓励
- 只用搜索结果里有的信息，不要编造
- 适当用 emoji 点缀，但不堆砌"""

def fetch_city_brief(city: str) -> str:
    """并行拉高德天气 + SerpAPI 城市攻略，用 DeepSeek 整合成简报。失败时返回纯天气文字。"""
    import concurrent.futures

    def _get_weather():
        return amap_weather_now(city)

    def _get_search():
        if not SERPAPI_KEY:
            return None
        try:
            query = f"{city} 旅游攻略 景点推荐 {__import__('datetime').date.today().strftime('%Y年')}"
            r = requests.get("https://serpapi.com/search", params={
                "api_key": SERPAPI_KEY, "engine": "baidu", "q": query, "num": 5,
            }, timeout=10).json()
            snippets = []
            if r.get("answer_box", {}).get("snippet"):
                snippets.append(r["answer_box"]["snippet"])
            for item in r.get("organic_results", [])[:4]:
                s = item.get("snippet", "")
                if s:
                    snippets.append(s)
            return "\n".join(snippets[:5]) if snippets else None
        except Exception as e:
            print(f"[city_brief] serpapi error: {e}")
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f_weather = ex.submit(_get_weather)
        f_search  = ex.submit(_get_search)
        weather = f_weather.result()
        search  = f_search.result()

    # 组装 prompt 上下文
    weather_str = ""
    if weather:
        emoji = _amap_weather_emoji(weather.get("text", ""))
        weather_str = (f"天气：{emoji}{weather.get('text','')} {weather.get('temp','')}°C，"
                       f"{weather.get('windDir','')}风{weather.get('windScale','')}级，"
                       f"湿度{weather.get('humidity','')}%")

    if not DEEPSEEK_KEY:
        # 无 AI，返回纯天气
        return format_weather_push(city) or f"已切换到「{city}」，祝旅途顺利！"

    context_parts = []
    if weather_str:
        context_parts.append(f"【实时天气】{weather_str}")
    if search:
        context_parts.append(f"【搜索摘要】\n{search}")

    if not context_parts:
        return f"已切换到「{city}」，祝旅途顺利！"

    try:
        r = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_KEY}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "max_tokens": 200,
                  "messages": [
                      {"role": "system", "content": CITY_BRIEF_PROMPT},
                      {"role": "user", "content": f"目的地：{city}\n\n" + "\n\n".join(context_parts)},
                  ]},
            timeout=15
        ).json()
        return r["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[city_brief] deepseek error: {e}")
        return weather_str or f"已切换到「{city}」，祝旅途顺利！"

# ── 主动推送 ──────────────────────────────────────────────────────────────────

def push_weather_to_user(wecom_id: str, open_kfid: str, city: str):
    """给单个用户推送天气"""
    if not open_kfid or not city:
        return
    msg = format_weather_push(city)
    if not msg:
        print(f"[push] skip {wecom_id}: no weather data")
        return
    try:
        send_text(open_kfid, wecom_id, msg)
        print(f"[push] sent weather to {wecom_id} city={city}")
    except Exception as e:
        print(f"[push] error for {wecom_id}: {e}")

def run_daily_weather_push():
    """每天 8:00 推送天气给所有开启提醒的用户"""
    print("[push] running daily weather push...")
    with get_db() as conn:
        users = conn.execute(
            "SELECT wecom_id, open_kfid, city FROM users WHERE push_enabled=1 AND open_kfid!='' AND city!=''"
        ).fetchall()
    print(f"[push] {len(users)} users to push")
    for u in users:
        threading.Thread(
            target=push_weather_to_user,
            args=(u["wecom_id"], u["open_kfid"], u["city"]),
            daemon=True
        ).start()

# ── 调度器 ────────────────────────────────────────────────────────────────────

_scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
_scheduler.add_job(run_daily_weather_push, "cron", hour=8, minute=0, id="daily_weather")

@asynccontextmanager
async def lifespan(app: FastAPI):
    _scheduler.start()
    print("[scheduler] started, next weather push at 08:00 Asia/Shanghai")
    yield
    _scheduler.shutdown(wait=False)
    print("[scheduler] stopped")

# ── WeChat KF 基础设施 ────────────────────────────────────────────────────────

app = FastAPI(lifespan=lifespan)
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
    # 0=未处理 1=智能助手(API) 2=待接入池 3=人工接待 4=已结束
    # 配了「接待人员」后新会话会进 2（待接入池），bot 也主动接管，保证自动回复
    if service_state in (0, 2, 4):
        r2 = requests.post("https://qyapi.weixin.qq.com/cgi-bin/kf/service_state/trans",
                           params={"access_token": token},
                           json={"open_kfid": open_kfid, "external_userid": user_id,
                                 "service_state": 1})
        ok = r2.json().get("errcode", -1) == 0
        if not ok:
            print(f"trans to 1 failed from state {service_state}: {r2.json()}")
        return ok
    elif service_state == 1:
        return True
    else:
        # 3=人工接待中：不抢人工的会话
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
        print(f"[sync_msg] errcode={resp.get('errcode')} errmsg={resp.get('errmsg')} "
              f"msgs={len(resp.get('msg_list', []))} cursor={cursor!r}->{resp.get('next_cursor')!r} "
              f"has_more={resp.get('has_more')}", flush=True)
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
            elif msgtype == "voice":
                media_id = m.get("voice", {}).get("media_id", "")
                if media_id:
                    audio_bytes = download_media(media_id, token, accept="audio")
                    if audio_bytes:
                        recognized = baidu_asr(audio_bytes)
                        if recognized:
                            handle_user_message(open_kf_id, user_id, recognized, "text")
                        else:
                            send_text(open_kf_id, user_id, "语音没听清，麻烦发文字给我 😊")
                    else:
                        send_text(open_kf_id, user_id, "语音下载失败，请重新发送或改用文字 😊")
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
    event = root.findtext("Event")
    print(f"[callback] event={event!r} token={'Y' if sync_token else 'N'} kfid={open_kf_id!r} xml={xml[:200]!r}", flush=True)
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

# 大众点评城市 ID 映射（主要城市）
DIANPING_CITY_IDS = {
    "北京": 2, "上海": 1, "广州": 4, "深圳": 7, "成都": 8, "杭州": 10,
    "武汉": 11, "南京": 9, "西安": 23, "重庆": 6, "苏州": 15, "天津": 3,
    "长沙": 12, "郑州": 17, "青岛": 20, "厦门": 5, "昆明": 24, "大连": 22,
    "宁波": 16, "沈阳": 21, "哈尔滨": 25, "福州": 18, "济南": 19, "合肥": 26,
}

def search_dianping_shop_id(hotel_name: str, city: str = "西安") -> str:
    """在大众点评搜索酒店，返回 shop_id"""
    city_id = DIANPING_CITY_IDS.get(city, 23)  # 默认西安
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://m.dianping.com/",
    }
    try:
        resp = requests.get(
            f"https://m.dianping.com/search/searchlist?cityId={city_id}&keyword={requests.utils.quote(hotel_name)}&type=0",
            headers=headers, timeout=10
        )
        # 从 HTML 中提取第一个酒店 shop id
        shop_ids = re.findall(r'/shop/(\d{8,12})', resp.text)
        if shop_ids:
            sid = shop_ids[0]
            print(f"[dianping_search] found shop for '{hotel_name}': {sid}")
            return sid
        # 备用：从 JSON 数据岛提取
        ids = re.findall(r'"shopId"\s*:\s*"?(\d{8,12})"?', resp.text)
        if ids:
            print(f"[dianping_search] found shop (json) for '{hotel_name}': {ids[0]}")
            return ids[0]
    except Exception as e:
        print(f"dianping_search error: {e}")
    return ""

def fetch_dianping_reviews(hotel_name: str, city: str = "西安", max_reviews: int = 30) -> list[str]:
    """抓取大众点评评论"""
    shop_id = search_dianping_shop_id(hotel_name, city)
    if not shop_id:
        print(f"dianping: no shop_id found for '{hotel_name}'")
        return []

    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": f"https://m.dianping.com/shop/{shop_id}",
    }
    reviews = []
    try:
        # 差评优先（sortType=2 最新，type=6 差评/中评）
        for sort in [2, 1]:  # 2=最新，1=热门
            resp = requests.get(
                f"https://m.dianping.com/shop/{shop_id}/review_all/p1?sortType={sort}",
                headers=headers, timeout=10
            )
            raw = resp.text

            # 提取评论文本（大众点评 H5 的结构）
            # 评论在 <p class="...desc..."> 或 data-content 里
            texts = re.findall(r'data-content="([^"]{15,500})"', raw)
            if not texts:
                texts = re.findall(r'<p[^>]*class="[^"]*review[^"]*"[^>]*>([^<]{15,500})</p>', raw, re.DOTALL)
            if not texts:
                # 尝试 JSON 数据岛
                texts = re.findall(r'"content"\s*:\s*"([^"]{15,500})"', raw)
            if not texts:
                # 尝试 reviewText 字段
                texts = re.findall(r'"reviewText"\s*:\s*"([^"]{15,500})"', raw)

            texts = [t.replace('\\n', ' ').replace('\\"', '"').strip() for t in texts]
            texts = [t for t in texts if len(t) > 15]
            reviews.extend(texts)
            if reviews:
                break  # 拿到评论就不用试下一个排序了

    except Exception as e:
        print(f"dianping_reviews error: {e}")

    # 去重
    seen = set()
    deduped = []
    for r in reviews:
        key = r[:40]
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    print(f"dianping reviews fetched: {len(deduped)} for shop_id={shop_id}")
    return deduped[:max_reviews]

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
    # 多源评论合并：大众点评（主力）+ 携程（补充）
    dp_reviews   = fetch_dianping_reviews(hotel_name, city)
    ctrip_reviews = fetch_ctrip_reviews(hotel_id)
    seen = set()
    reviews = []
    for r in dp_reviews + ctrip_reviews:
        key = r[:50]
        if key not in seen:
            seen.add(key)
            reviews.append(r)
    print(f"[analysis] total reviews: {len(reviews)} (dianping={len(dp_reviews)}, ctrip={len(ctrip_reviews)})")

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

def _get_cached(key: str):
    """返回 (minutes, walk) 或 None"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT minutes, walk FROM route_cache WHERE cache_key=? AND created_at > datetime('now', ?)",
            (key, f"-{CACHE_TTL_HOURS} hours")
        ).fetchone()
    return (row[0], row[1] or 0) if row else None

def _set_cached(key: str, minutes: float, walk: float = 0):
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO route_cache (cache_key, minutes, walk) VALUES (?, ?, ?)",
            (key, minutes, walk)
        )
        conn.commit()

def amap_transit_detail(olat, olng, dlat, dlng, city: str):
    """高德公共交通路线，返回 (总分钟, 步行分钟)。步行从各分段的 walking.duration 累加。"""
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
            plan = plans[0]
            total = int(plan.get("duration", 0) or 0)
            walk = 0
            for seg in plan.get("segments", []):
                w = seg.get("walking") or {}
                walk += int(w.get("duration", 0) or 0)
            return round(total / 60, 1), round(walk / 60, 1)
    except Exception as e:
        print("amap_transit error:", e)
    return None, 0.0

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

def get_route_detail(olat, olng, dlat, dlng, mode: str, city: str = "") -> dict:
    """获取两点间通勤时间拆分（分钟），优先读缓存。
    返回 {"min": 总耗时, "walk": 步行耗时, "ride": 乘车/驾车耗时}。
    - transit: walk=路线里步行段时间, ride=其余（地铁/公交/等车）
    - driving: walk=0, ride=总时间
    - walking: walk=总时间, ride=0
    """
    key = _route_cache_key(olat, olng, dlat, dlng, mode)
    cached = _get_cached(key)
    if cached is not None:
        minutes, walk = cached
        return {"min": minutes, "walk": walk, "ride": round(max(0.0, minutes - walk), 1)}

    minutes = None
    walk = 0.0
    if mode == "transit":
        minutes, walk = amap_transit_detail(olat, olng, dlat, dlng, city)
    elif mode == "driving":
        minutes = amap_driving_minutes(olat, olng, dlat, dlng)
        walk = 0.0
    elif mode == "walking":
        minutes = amap_walking_minutes(olat, olng, dlat, dlng)
        walk = minutes if minutes is not None else None

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
        if mode == "walking":
            walk = minutes
        elif mode == "transit":
            walk = round(minutes * 0.25, 1)   # 估算：约 1/4 时间在步行
        else:
            walk = 0.0

    _set_cached(key, minutes, walk)
    return {"min": minutes, "walk": walk, "ride": round(max(0.0, minutes - walk), 1)}

# 兼容旧调用：只要总分钟数
def get_route_minutes(olat, olng, dlat, dlng, mode: str, city: str = "") -> float:
    return get_route_detail(olat, olng, dlat, dlng, mode, city)["min"]

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
    返回: {matrix: {hotel_id: {attraction_id: {min, walk, ride}}}}
      min  = 总耗时（分钟）
      walk = 步行段耗时
      ride = 乘车/驾车段耗时（min - walk）
    """
    hotels = body.get("hotels", [])
    attractions = body.get("attractions", [])
    mode = body.get("mode", "transit")
    city = body.get("city", "")
    matrix: dict = {}
    for h in hotels:
        matrix[h["id"]] = {}
        for a in attractions:
            matrix[h["id"]][a["id"]] = get_route_detail(
                h["lat"], h["lng"], a["lat"], a["lng"], mode, city
            )
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

@app.get("/api/user/trips")
async def user_trips(wecom_id: str):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, city, days, preference, bundle_text, created_at FROM trips WHERE wecom_id=? ORDER BY created_at DESC LIMIT 20",
            (wecom_id,)
        ).fetchall()
    return {"trips": [dict(r) for r in rows]}

@app.get("/api/trips/{trip_id}")
async def get_trip(trip_id: int):
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, city, days, preference, bundle_text, created_at FROM trips WHERE id=?",
            (trip_id,)
        ).fetchone()
    if not row:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Trip not found")
    return dict(row)

@app.post("/api/admin/push_weather")
async def admin_push_weather(body: dict):
    """手动触发天气推送（测试用），支持指定单个 wecom_id 或推送全部"""
    wecom_id = body.get("wecom_id", "")
    if wecom_id:
        with get_db() as conn:
            row = conn.execute(
                "SELECT wecom_id, open_kfid, city FROM users WHERE wecom_id=?", (wecom_id,)
            ).fetchone()
        if not row:
            return {"ok": False, "msg": "user not found"}
        push_weather_to_user(row["wecom_id"], row["open_kfid"], row["city"])
        return {"ok": True, "pushed": 1}
    else:
        threading.Thread(target=run_daily_weather_push, daemon=True).start()
        return {"ok": True, "msg": "push triggered in background"}

@app.get("/api/weather/now")
async def weather_now_api(city: str):
    """实时天气查询（前端可调用）"""
    now = qweather_now(city)
    warnings = qweather_warnings(city)
    if not now:
        return {"ok": False}
    return {
        "ok": True,
        "city": city,
        "now": now,
        "warnings": warnings,
        "formatted": format_weather_push(city),
    }

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
            "extensions": "all",
        }, timeout=5).json()
        for i, p in enumerate(r.get("pois", [])):
            if not p.get("location"):
                continue
            lng, lat = p["location"].split(",")
            # 取第一张照片 URL（extensions=all 时 photos 字段存在）
            photos = p.get("photos") or []
            photo_url = photos[0].get("url") if photos else None
            if photo_url:
                photo_url = photo_url.replace("http://", "https://", 1)
            attractions.append({
                "id": str(i + 1),
                "name": p["name"],
                "lng": float(lng),
                "lat": float(lat),
                "photo_url": photo_url,
            })
    except Exception as e:
        print("attractions error:", e)

    return {"city": city, "center": center, "attractions": attractions}
