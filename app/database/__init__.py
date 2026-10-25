"""
پایگاه داده سبک SQLite
- بدون نیاز به سرور جدا
- مناسب برای وضعیت مکالمه، انواع بیمه، سفارش‌ها، کش شیت و ...
- برای داده‌های تراکنشی (سفارش) از SQLite به عنوان کش + Google Sheets به عنوان منبع حقیقت
"""
import sqlite3
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any
from ..config import DATABASE_PATH

log = logging.getLogger(__name__)

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    phone TEXT,
    joined_at TEXT,
    last_seen TEXT,
    is_blocked BOOLEAN DEFAULT 0,
    is_operator BOOLEAN DEFAULT 0,
    score INTEGER DEFAULT 0,
    level TEXT DEFAULT 'bronze',
    referral_code TEXT,
    referred_by INTEGER,
    extra JSON DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS conversation_state (
    telegram_id INTEGER PRIMARY KEY,
    state TEXT,
    data JSON,
    updated_at TEXT,
    FOREIGN KEY(telegram_id) REFERENCES users(telegram_id)
);

CREATE TABLE IF NOT EXISTS insurance_types (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    description TEXT,
    intro_message TEXT,
    success_message TEXT,
    is_active BOOLEAN DEFAULT 1,
    sort_order INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS insurance_fields (
    id TEXT PRIMARY KEY,
    insurance_type_id TEXT NOT NULL,
    field_key TEXT NOT NULL,
    label TEXT NOT NULL,
    field_type TEXT NOT NULL, -- text|number|date|select|boolean|phone|national_id
    placeholder TEXT,
    help_text TEXT,
    is_required BOOLEAN DEFAULT 1,
    validation_regex TEXT,
    validation_min REAL,
    validation_max REAL,
    options JSON, -- برای select
    sort_order INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT 1,
    FOREIGN KEY(insurance_type_id) REFERENCES insurance_types(id) ON DELETE CASCADE,
    UNIQUE(insurance_type_id, field_key)
);

CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    telegram_id INTEGER NOT NULL,
    insurance_type_id TEXT,
    insurance_type_name TEXT,
    status TEXT DEFAULT 'new',
    data JSON, -- تمام فیلدهای فرم
    contact_name TEXT,
    contact_phone TEXT,
    discount_code TEXT,
    discount_amount REAL DEFAULT 0,
    quoted_price REAL,
    assigned_to INTEGER,
    source TEXT,
    created_at TEXT,
    updated_at TEXT,
    sheets_synced BOOLEAN DEFAULT 0,
    FOREIGN KEY(telegram_id) REFERENCES users(telegram_id),
    FOREIGN KEY(insurance_type_id) REFERENCES insurance_types(id)
);

CREATE TABLE IF NOT EXISTS order_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT,
    actor_id INTEGER,
    from_status TEXT,
    to_status TEXT,
    note TEXT,
    created_at TEXT,
    FOREIGN KEY(order_id) REFERENCES orders(id)
);

CREATE TABLE IF NOT EXISTS knowledge_sources (
    id TEXT PRIMARY KEY,
    title TEXT,
    file_path TEXT,
    file_type TEXT,
    is_official BOOLEAN DEFAULT 1,
    is_active BOOLEAN DEFAULT 1,
    chunk_count INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id TEXT PRIMARY KEY,
    source_id TEXT,
    chunk_index INTEGER,
    content TEXT,
    token_count INTEGER,
    metadata JSON,
    FOREIGN KEY(source_id) REFERENCES knowledge_sources(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS faq (
    id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    source_id TEXT,
    insurance_type_id TEXT,
    is_approved BOOLEAN DEFAULT 0,
    is_active BOOLEAN DEFAULT 1,
    view_count INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT,
    FOREIGN KEY(source_id) REFERENCES knowledge_sources(id),
    FOREIGN KEY(insurance_type_id) REFERENCES insurance_types(id)
);

CREATE TABLE IF NOT EXISTS discounts (
    id TEXT PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    title TEXT,
    discount_type TEXT, -- percent|fixed
    value REAL,
    start_at TEXT,
    end_at TEXT,
    max_uses INTEGER,
    used_count INTEGER DEFAULT 0,
    max_per_user INTEGER DEFAULT 1,
    insurance_type_ids JSON,
    is_active BOOLEAN DEFAULT 1,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS discount_uses (
    id TEXT PRIMARY KEY,
    discount_id TEXT,
    telegram_id INTEGER,
    order_id TEXT,
    used_at TEXT,
    FOREIGN KEY(discount_id) REFERENCES discounts(id),
    FOREIGN KEY(telegram_id) REFERENCES users(telegram_id)
);

CREATE TABLE IF NOT EXISTS loyalty_rules (
    id TEXT PRIMARY KEY,
    title TEXT,
    event TEXT, -- purchase|referral|review
    points INTEGER,
    is_active BOOLEAN DEFAULT 1
);

CREATE TABLE IF NOT EXISTS loyalty_history (
    id TEXT PRIMARY KEY,
    telegram_id INTEGER,
    points INTEGER,
    reason TEXT,
    order_id TEXT,
    created_at TEXT,
    FOREIGN KEY(telegram_id) REFERENCES users(telegram_id)
);

CREATE TABLE IF NOT EXISTS campaigns (
    id TEXT PRIMARY KEY,
    title TEXT,
    message_text TEXT,
    filter_json JSON,
    status TEXT DEFAULT 'draft', -- draft|sending|sent|stopped
    total_count INTEGER DEFAULT 0,
    sent_count INTEGER DEFAULT 0,
    failed_count INTEGER DEFAULT 0,
    created_by INTEGER,
    created_at TEXT,
    sent_at TEXT
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value JSON,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS rate_limit (
    telegram_id INTEGER,
    window_start TEXT,
    count INTEGER,
    PRIMARY KEY(telegram_id, window_start)
);
"""

def get_conn():
    p = Path(DATABASE_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def init_db():
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
        _seed_defaults(conn)
        log.info("✓ Database initialized at %s", DATABASE_PATH)
    finally:
        conn.close()

def _seed_defaults(conn):
    # تنظیمات پیش‌فرض
    defaults = {
        "welcome_message": "👋 سلام! به ربات مشاوره بیمه خوش آمدید.",
        "bot_active": True,
        "ai_enabled": True,
        "handoff_enabled": True,
        "maintenance_mode": False,
    }
    for k, v in defaults.items():
        conn.execute("INSERT OR IGNORE INTO app_settings(key, value, updated_at) VALUES(?,?,?)",
                     (k, json.dumps(v, ensure_ascii=False), datetime.now().isoformat()))
    # انواع بیمه پیش‌فرض اگر خالی باشد
    cur = conn.execute("SELECT COUNT(*) as c FROM insurance_types")
    if cur.fetchone()["c"] == 0:
        now = datetime.now().isoformat()
        import uuid
        types = [
            ("bimeh-sales", "بیمه شخص ثالث", "بیمه اجباری برای جبران خسارت به اشخاص ثالث در حوادث رانندگی", "برای استعلام شخص ثالث به اطلاعات خودرو نیاز داریم.", "درخواست شخص ثالث شما ثبت شد. کارشناس به زودی قیمت دقیق را اعلام می‌کند."),
            ("bimeh-badane", "بیمه بدنه", "پوشش خسارت‌های وارد به بدنه خودرو", "برای بیمه بدنه مشخصات خودرو و ارزش آن را وارد کنید.", "درخواست بدنه ثبت شد."),
            ("bimeh-atash", "بیمه آتش‌سوزی", "پوشش خسارت آتش‌سوزی اماکن مسکونی و تجاری", "اطلاعات ملک و ارزش دارایی را وارد کنید.", "درخواست آتش‌سوزی ثبت شد."),
            ("bimeh-darman", "بیمه درمان تکمیلی", "پوشش هزینه‌های درمانی مازاد بر بیمه پایه", "تعداد نفرات و سن را مشخص کنید.", "درخواست درمان ثبت شد."),
            ("bimeh-omar", "بیمه عمر", "بیمه عمر و سرمایه‌گذاری", "اطلاعات فردی و مدت بیمه را وارد کنید.", "درخواست بیمه عمر ثبت شد."),
            ("bimeh-masouliat", "بیمه مسئولیت", "پوشش مسئولیت کارفرما و حرفه‌ای", "نوع فعالیت و تعداد پرسنل را وارد کنید.", "درخواست مسئولیت ثبت شد."),
        ]
        for slug, name, desc, intro, success in types:
            tid = str(uuid.uuid4())[:8]
            conn.execute("INSERT INTO insurance_types(id, name, slug, description, intro_message, success_message, sort_order, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                         (tid, name, slug, desc, intro, success, 0, now, now))
            # فیلدهای نمونه
            field_defs = {
                "bimeh-sales": [
                    ("car_type", "نوع خودرو", "select", True, '["سواری","وانت","موتورسیکلت","کامیون"]', 1),
                    ("car_model", "مدل خودرو", "text", True, None, 2),
                    ("car_year", "سال ساخت", "number", True, None, 3),
                    ("expire_date", "تاریخ انقضای بیمه‌نامه فعلی", "date", False, None, 4),
                    ("phone", "شماره تماس", "phone", True, None, 5),
                ],
                "bimeh-badane": [
                    ("car_model", "مدل خودرو", "text", True, None, 1),
                    ("car_value", "ارزش خودرو (تومان)", "number", True, None, 2),
                    ("car_year", "سال ساخت", "number", True, None, 3),
                    ("phone", "شماره تماس", "phone", True, None, 4),
                ],
                "bimeh-atash": [
                    ("property_type", "نوع ملک", "select", True, '["مسکونی","تجاری","اداری","صنعتی"]', 1),
                    ("area", "متراژ (متر مربع)", "number", True, None, 2),
                    ("value", "ارزش دارایی (تومان)", "number", True, None, 3),
                    ("phone", "شماره تماس", "phone", True, None, 4),
                ],
                "bimeh-darman": [
                    ("persons", "تعداد نفرات", "number", True, None, 1),
                    ("avg_age", "میانگین سنی", "number", False, None, 2),
                    ("phone", "شماره تماس", "phone", True, None, 3),
                ],
                "bimeh-omar": [
                    ("birth_year", "سال تولد", "number", True, None, 1),
                    ("duration", "مدت بیمه (سال)", "select", True, '["5","10","15","20","30"]', 2),
                    ("phone", "شماره تماس", "phone", True, None, 3),
                ],
                "bimeh-masouliat": [
                    ("activity", "نوع فعالیت", "text", True, None, 1),
                    ("personnel", "تعداد پرسنل", "number", True, None, 2),
                    ("phone", "شماره تماس", "phone", True, None, 3),
                ],
            }
            fields = field_defs.get(slug, [])
            for fk, label, ftype, req, opts, order in fields:
                fid = str(uuid.uuid4())[:8]
                conn.execute("INSERT INTO insurance_fields(id, insurance_type_id, field_key, label, field_type, is_required, options, sort_order) VALUES(?,?,?,?,?,?,?,?)",
                             (fid, tid, fk, label, ftype, 1 if req else 0, opts, order))
        conn.commit()
    # امتیاز وفاداری پیش‌فرض
    cur = conn.execute("SELECT COUNT(*) as c FROM loyalty_rules")
    if cur.fetchone()["c"] == 0:
        import uuid
        now = datetime.now().isoformat()
        conn.execute("INSERT INTO loyalty_rules(id, title, event, points) VALUES(?,?,?,?)", (str(uuid.uuid4())[:8], "خرید موفق", "purchase", 100))
        conn.execute("INSERT INTO loyalty_rules(id, title, event, points) VALUES(?,?,?,?)", (str(uuid.uuid4())[:8], "دعوت دوست", "referral", 50))
        conn.commit()

# --- Helpers ---
def _row_to_dict(row):
    if row is None:
        return None
    d = dict(row)
    # parse JSON fields
    for k in ("extra", "data", "options", "value", "filter_json", "insurance_type_ids", "metadata"):
        if k in d and isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except:
                pass
    return d

# --- Users ---
def upsert_user(telegram_id: int, username: str = "", first_name: str = "", last_name: str = ""):
    conn = get_conn()
    try:
        now = datetime.now().isoformat()
        cur = conn.execute("SELECT telegram_id FROM users WHERE telegram_id=?", (telegram_id,))
        if cur.fetchone():
            conn.execute("UPDATE users SET username=?, first_name=?, last_name=?, last_seen=? WHERE telegram_id=?",
                         (username, first_name, last_name, now, telegram_id))
        else:
            import uuid, random, string
            code = f"REF{telegram_id%10000}{''.join(random.choices(string.ascii_uppercase, k=3))}"
            conn.execute("INSERT INTO users(telegram_id, username, first_name, last_name, joined_at, last_seen, referral_code) VALUES(?,?,?,?,?,?,?)",
                         (telegram_id, username, first_name, last_name, now, now, code))
        conn.commit()
        cur = conn.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,))
        return _row_to_dict(cur.fetchone())
    finally:
        conn.close()

def get_user(telegram_id: int):
    conn = get_conn()
    try:
        cur = conn.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,))
        return _row_to_dict(cur.fetchone())
    finally:
        conn.close()

def set_user_phone(telegram_id: int, phone: str):
    conn = get_conn()
    try:
        conn.execute("UPDATE users SET phone=? WHERE telegram_id=?", (phone, telegram_id))
        conn.commit()
    finally:
        conn.close()

# --- Conversation ---
def get_state(telegram_id: int):
    conn = get_conn()
    try:
        cur = conn.execute("SELECT * FROM conversation_state WHERE telegram_id=?", (telegram_id,))
        r = _row_to_dict(cur.fetchone())
        if r:
            return r
        return {"telegram_id": telegram_id, "state": None, "data": {}, "updated_at": None}
    finally:
        conn.close()

def set_state(telegram_id: int, state: Optional[str], data: dict = None):
    conn = get_conn()
    try:
        now = datetime.now().isoformat()
        j = json.dumps(data or {}, ensure_ascii=False)
        conn.execute("INSERT OR REPLACE INTO conversation_state(telegram_id, state, data, updated_at) VALUES(?,?,?,?)",
                     (telegram_id, state, j, now))
        conn.commit()
    finally:
        conn.close()

def clear_state(telegram_id: int):
    set_state(telegram_id, None, {})

# --- Insurance Types ---
def list_insurance_types(active_only=True):
    conn = get_conn()
    try:
        q = "SELECT * FROM insurance_types WHERE is_active=1 ORDER BY sort_order, name" if active_only else "SELECT * FROM insurance_types ORDER BY sort_order, name"
        cur = conn.execute(q)
        return [_row_to_dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

def get_insurance_type(type_id: str):
    conn = get_conn()
    try:
        cur = conn.execute("SELECT * FROM insurance_types WHERE id=?", (type_id,))
        return _row_to_dict(cur.fetchone())
    finally:
        conn.close()

def get_insurance_type_by_slug(slug: str):
    conn = get_conn()
    try:
        cur = conn.execute("SELECT * FROM insurance_types WHERE slug=?", (slug,))
        return _row_to_dict(cur.fetchone())
    finally:
        conn.close()

def create_insurance_type(name, slug, description="", intro="", success="", sort_order=0):
    import uuid
    conn = get_conn()
    try:
        tid = str(uuid.uuid4())[:8]
        now = datetime.now().isoformat()
        conn.execute("INSERT INTO insurance_types(id, name, slug, description, intro_message, success_message, sort_order, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                     (tid, name, slug, description, intro, success, sort_order, now, now))
        conn.commit()
        return get_insurance_type(tid)
    finally:
        conn.close()

def update_insurance_type(type_id, **kw):
    conn = get_conn()
    try:
        sets = ", ".join([f"{k}=?" for k in kw.keys()])
        vals = list(kw.values()) + [datetime.now().isoformat(), type_id]
        conn.execute(f"UPDATE insurance_types SET {sets}, updated_at=? WHERE id=?", vals)
        conn.commit()
        return get_insurance_type(type_id)
    finally:
        conn.close()

def list_fields(insurance_type_id: str):
    conn = get_conn()
    try:
        cur = conn.execute("SELECT * FROM insurance_fields WHERE insurance_type_id=? AND is_active=1 ORDER BY sort_order", (insurance_type_id,))
        return [_row_to_dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

def create_field(insurance_type_id, field_key, label, field_type, is_required=True, placeholder="", help_text="", options=None, sort_order=0):
    import uuid, json as js
    conn = get_conn()
    try:
        fid = str(uuid.uuid4())[:8]
        opts = js.dumps(options, ensure_ascii=False) if options else None
        conn.execute("INSERT INTO insurance_fields(id, insurance_type_id, field_key, label, field_type, placeholder, help_text, is_required, options, sort_order) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (fid, insurance_type_id, field_key, label, field_type, placeholder, help_text, 1 if is_required else 0, opts, sort_order))
        conn.commit()
        return fid
    finally:
        conn.close()

# --- Orders ---
def create_order(telegram_id, insurance_type_id, insurance_type_name, data: dict, contact_name="", contact_phone="", source="", discount_code=""):
    import uuid
    conn = get_conn()
    try:
        oid = f"ORD-{uuid.uuid4().hex[:8].upper()}"
        now = datetime.now().isoformat()
        j = json.dumps(data, ensure_ascii=False)
        conn.execute("INSERT INTO orders(id, telegram_id, insurance_type_id, insurance_type_name, status, data, contact_name, contact_phone, discount_code, source, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                     (oid, telegram_id, insurance_type_id, insurance_type_name, "new", j, contact_name, contact_phone, discount_code, source, now, now))
        conn.execute("INSERT INTO order_events(order_id, actor_id, from_status, to_status, note, created_at) VALUES(?,?,?,?,?,?)",
                     (oid, telegram_id, None, "new", "ثبت اولیه", now))
        conn.commit()
        return oid
    finally:
        conn.close()

def get_order(order_id: str):
    conn = get_conn()
    try:
        cur = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,))
        return _row_to_dict(cur.fetchone())
    finally:
        conn.close()

def list_orders(limit=20, offset=0, status=None, telegram_id=None):
    conn = get_conn()
    try:
        q = "SELECT * FROM orders WHERE 1=1"
        params = []
        if status and status != "all":
            q += " AND status=?"
            params.append(status)
        if telegram_id:
            q += " AND telegram_id=?"
            params.append(telegram_id)
        q += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        cur = conn.execute(q, params)
        return [_row_to_dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

def count_orders(status=None, telegram_id=None):
    conn = get_conn()
    try:
        q = "SELECT COUNT(*) as c FROM orders WHERE 1=1"
        params = []
        if status and status != "all":
            q += " AND status=?"
            params.append(status)
        if telegram_id:
            q += " AND telegram_id=?"
            params.append(telegram_id)
        cur = conn.execute(q, params)
        return cur.fetchone()["c"]
    finally:
        conn.close()

def update_order_status(order_id, new_status, actor_id=None, note=""):
    conn = get_conn()
    try:
        cur = conn.execute("SELECT status FROM orders WHERE id=?", (order_id,))
        row = cur.fetchone()
        if not row:
            return False
        old = row["status"]
        now = datetime.now().isoformat()
        conn.execute("UPDATE orders SET status=?, updated_at=?, assigned_to=COALESCE(?, assigned_to) WHERE id=?", (new_status, now, actor_id, order_id))
        conn.execute("INSERT INTO order_events(order_id, actor_id, from_status, to_status, note, created_at) VALUES(?,?,?,?,?,?)",
                     (order_id, actor_id, old, new_status, note, now))
        conn.commit()
        return True
    finally:
        conn.close()

def set_order_field(order_id, field, value):
    conn = get_conn()
    try:
        now = datetime.now().isoformat()
        conn.execute(f"UPDATE orders SET {field}=?, updated_at=? WHERE id=?", (value, now, order_id))
        conn.commit()
    finally:
        conn.close()

# --- FAQ ---
def list_faq(approved_only=False, active_only=True):
    conn = get_conn()
    try:
        q = "SELECT * FROM faq WHERE 1=1"
        params = []
        if approved_only:
            q += " AND is_approved=1"
        if active_only:
            q += " AND is_active=1"
        q += " ORDER BY created_at DESC"
        cur = conn.execute(q, params)
        return [_row_to_dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

def create_faq(question, answer, source_id=None, insurance_type_id=None, is_approved=False):
    import uuid
    conn = get_conn()
    try:
        fid = str(uuid.uuid4())[:8]
        now = datetime.now().isoformat()
        conn.execute("INSERT INTO faq(id, question, answer, source_id, insurance_type_id, is_approved, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
                     (fid, question, answer, source_id, insurance_type_id, 1 if is_approved else 0, now, now))
        conn.commit()
        return fid
    finally:
        conn.close()

def approve_faq(faq_id):
    conn = get_conn()
    try:
        conn.execute("UPDATE faq SET is_approved=1, updated_at=? WHERE id=?", (datetime.now().isoformat(), faq_id))
        conn.commit()
    finally:
        conn.close()

# --- Settings ---
def get_setting(key, default=None):
    conn = get_conn()
    try:
        cur = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,))
        row = cur.fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except:
            return row["value"]
    finally:
        conn.close()

def set_setting(key, value):
    conn = get_conn()
    try:
        now = datetime.now().isoformat()
        j = json.dumps(value, ensure_ascii=False)
        conn.execute("INSERT OR REPLACE INTO app_settings(key, value, updated_at) VALUES(?,?,?)", (key, j, now))
        conn.commit()
    finally:
        conn.close()

# --- Stats ---
def get_stats():
    conn = get_conn()
    try:
        stats = {}
        cur = conn.execute("SELECT COUNT(*) as c FROM orders")
        stats["total_orders"] = cur.fetchone()["c"]
        cur = conn.execute("SELECT status, COUNT(*) as c FROM orders GROUP BY status")
        for r in cur.fetchall():
            stats[f"status_{r['status']}"] = r["c"]
        cur = conn.execute("SELECT COUNT(*) as c FROM users")
        stats["total_users"] = cur.fetchone()["c"]
        cur = conn.execute("SELECT COUNT(*) as c FROM orders WHERE status IN ('new','waiting_info','ready_for_review')")
        stats["pending"] = cur.fetchone()["c"]
        # per insurance type
        cur = conn.execute("SELECT insurance_type_name, COUNT(*) as c FROM orders GROUP BY insurance_type_name")
        stats["by_type"] = {r["insurance_type_name"] or "نامشخص": r["c"] for r in cur.fetchall()}
        return stats
    finally:
        conn.close()

# --- Rate limit ---
def check_rate_limit(telegram_id: int, max_per_minute=20) -> bool:
    """برگرداندن True اگر مجاز است، False اگر محدود شد"""
    import time
    conn = get_conn()
    try:
        now = datetime.now()
        window = now.strftime("%Y-%m-%d %H:%M")
        cur = conn.execute("SELECT count FROM rate_limit WHERE telegram_id=? AND window_start=?", (telegram_id, window))
        row = cur.fetchone()
        if row:
            if row["count"] >= max_per_minute:
                return False
            conn.execute("UPDATE rate_limit SET count=count+1 WHERE telegram_id=? AND window_start=?", (telegram_id, window))
        else:
            # پاکسازی قدیمی
            conn.execute("DELETE FROM rate_limit WHERE window_start < ?", ((now.timestamp() - 3600),))
            conn.execute("INSERT INTO rate_limit(telegram_id, window_start, count) VALUES(?,?,1)", (telegram_id, window))
        conn.commit()
        return True
    finally:
        conn.close()
