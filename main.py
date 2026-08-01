#!/usr/bin/env python3
"""🎓 ربات سفارش پروژه دانشگاهی — PTB 21.3 + Flask
پنل مدیریت (فقط ادمین)، باشگاه مشتریان با لینک معرف، پرداخت دستی (شماره کارت + رسید)،
پیام همگانی، تنظیمات قابل ویرایش (قیمت‌ها، پیش‌پرداخت روشن/خاموش، تعداد سفارش‌های نمایشی)
"""

import os, json, hmac, hashlib, logging, threading, uuid, time, asyncio
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs

import requests
from flask import Flask, request, jsonify, render_template, send_from_directory
from dotenv import load_dotenv
from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup,
                      MenuButtonWebApp, WebAppInfo, ReplyKeyboardMarkup, KeyboardButton)
from telegram.ext import (Application, CommandHandler, MessageHandler,
                          CallbackQueryHandler, filters, ContextTypes)
from telegram.constants import ParseMode

load_dotenv()
BASE_DIR = Path(__file__).parent
UPLOAD_FOLDER = BASE_DIR / "uploads"; UPLOAD_FOLDER.mkdir(exist_ok=True)
DB_FILE = BASE_DIR / "database.json"

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_USER_IDS", "").split(",") if x.strip()]
PORT = int(os.getenv("PORT") or os.getenv("WEBAPP_PORT") or "5000")

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# ═══════════════ DATABASE ═══════════════
def _default_db():
    return {
        "categories": [], "orders": [], "files": [], "payments": [],
        "wallets": [], "referrals": [], "users": [], "broadcasts": [],
        "settings": {
            "advance_percent": 50, "min_advance": 100000, "currency": "تومان",
            "advance_enabled": True, "customer_orders_display_limit": 8,
            "support_phone": "", "support_telegram": "",
            "referral_discount_percent": 10, "referral_reward": 50000,
            "bank_card_number": "", "bank_card_holder": "", "bank_name": "", "bank_note": "",
        },
        "notifications": {
            "pending": {"to_user": "✅ سفارش شما با موفقیت ثبت شد.", "to_admin": "📬 سفارش جدید ثبت شد!"},
            "paid_advance": {"to_user": "💰 پیش‌پرداخت تأیید شد. پروژه شروع شد!", "to_admin": ""},
            "in_progress": {"to_user": "🔄 پروژه در حال انجام است.", "to_admin": ""},
            "completed": {"to_user": "🎉 پروژه تکمیل شد! لطفاً پرداخت نهایی را انجام دهید.", "to_admin": ""},
            "paid_final": {"to_user": "💵 تسویه انجام شد. متشکریم!", "to_admin": ""},
            "cancelled": {"to_user": "❌ سفارش لغو شد.", "to_admin": ""},
        }
    }

def _db():
    if DB_FILE.exists():
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f: d = json.load(f)
            base = _default_db()
            for k, v in base.items():
                if k not in d: d[k] = v
            if isinstance(d.get("settings"), dict):
                for k, v in base["settings"].items():
                    d["settings"].setdefault(k, v)
            return d
        except Exception:
            pass
    return _default_db()

def _save(d):
    with open(DB_FILE, "w", encoding="utf-8") as f: json.dump(d, f, ensure_ascii=False, indent=2)

_uid = lambda: str(uuid.uuid4())[:10]
_now = lambda: datetime.now().isoformat()

# ═══════════════ CRUD: دسته‌بندی ═══════════════
def cat_add(n, d, p, a=None):
    db = _db(); s = db["settings"]
    c = {"id": _uid(), "name": n, "description": d, "price": int(p),
         "advance_percent": a or s["advance_percent"], "created_at": _now(), "active": True}
    db["categories"].append(c); _save(db); return c

def cat_get(cid):
    for c in _db()["categories"]:
        if c["id"] == cid: return c
    return None

def cat_all(active_only=True):
    cats = _db()["categories"]
    return [c for c in cats if c.get("active", True)] if active_only else cats

def cat_update(cid, **kw):
    db = _db()
    for c in db["categories"]:
        if c["id"] == cid: c.update(kw); _save(db); return c
    return None

cat_delete = lambda cid: cat_update(cid, active=False)

# ═══════════════ CRUD: کاربران ═══════════════
def user_register(uid_, uname="", fname=""):
    db = _db()
    for u in db["users"]:
        if u["id"] == uid_:
            u["username"] = uname or u.get("username", "")
            u["first_name"] = fname or u.get("first_name", "")
            u["last_seen"] = _now()
            _save(db); return u
    nu = {"id": uid_, "username": uname or "", "first_name": fname or "",
          "joined_at": _now(), "last_seen": _now(),
          "referred_by": None, "referral_used": False}
    db["users"].append(nu); _save(db); return nu

def user_get(uid_):
    for u in _db()["users"]:
        if u["id"] == uid_: return u
    return None

def user_update(uid_, **kw):
    db = _db()
    for u in db["users"]:
        if u["id"] == uid_: u.update(kw); _save(db); return u
    return None

def user_all():
    return _db()["users"]

# ═══════════════ CRUD: سفارش ═══════════════
def order_create(uid_, uname, fname, cid, desc):
    cat = cat_get(cid)
    if not cat: return None
    s = settings_get()
    advance_on = bool(s.get("advance_enabled", True))
    adv = int(cat["price"] * cat["advance_percent"] / 100) if advance_on else 0
    final = cat["price"] - adv

    # تخفیف معرف برای اولین سفارش (فقط وقتی از لینک معرف آمده باشد)
    discount = 0; ref_code = None
    u = user_get(uid_)
    if u and u.get("referred_by") and not u.get("referral_used"):
        discount = int(cat["price"] * s.get("referral_discount_percent", 10) / 100)
        ref_code = u["referred_by"]
        user_update(uid_, referral_used=True)
    if discount:
        if adv > 0: adv = max(0, adv - discount)
        else: final = max(0, final - discount)

    db = _db()
    o = {"id": _uid(), "user_id": uid_, "username": uname or "", "first_name": fname or "",
         "category_id": cid, "category_name": cat["name"], "description": desc,
         "price": cat["price"], "advance_amount": adv, "final_amount": final,
         "discount": discount, "referral_code": ref_code, "referral_discount_applied": bool(discount),
         "status": "pending", "created_at": _now(), "updated_at": _now()}
    db["orders"].append(o); _save(db); return o

def order_get(oid):
    for o in _db()["orders"]:
        if o["id"] == oid: return o
    return None

def order_all(status=None, uid_=None):
    orders = _db()["orders"]
    if status: orders = [o for o in orders if o["status"] == status]
    if uid_: orders = [o for o in orders if o["user_id"] == uid_]
    return sorted(orders, key=lambda x: x["created_at"], reverse=True)

def order_update(oid, **kw):
    db = _db()
    for o in db["orders"]:
        if o["id"] == oid: o.update(kw); o["updated_at"] = _now(); _save(db); return o
    return None

# ═══════════════ CRUD: فایل ═══════════════
def file_add(oid, fn, fp, tgid):
    db = _db()
    f = {"id": _uid(), "order_id": oid, "filename": fn, "file_path": fp, "telegram_file_id": tgid, "uploaded_at": _now()}
    db["files"].append(f); _save(db); return f

file_get = lambda oid: [f for f in _db()["files"] if f["order_id"] == oid]

# ═══════════════ CRUD: پرداخت ═══════════════
def pay_create(oid, amt, pt):
    db = _db()
    p = {"id": _uid(), "order_id": oid, "amount": amt, "payment_type": pt,
         "authority": "", "ref_id": "", "status": "pending", "created_at": _now(),
         "admin_approved": None, "receipt_file_id": None, "receipt_path": None,
         "receipt_sent_at": None, "approved_at": None, "approved_by": None}
    db["payments"].append(p); _save(db); return p

def pay_get(pid):
    for p in _db()["payments"]:
        if p["id"] == pid: return p
    return None

def pay_update(pid, **kw):
    db = _db()
    for p in db["payments"]:
        if p["id"] == pid: p.update(kw); _save(db); return p
    return None

pay_by_order = lambda oid: [p for p in _db()["payments"] if p["order_id"] == oid]

def payment_stage(o):
    """نوع پرداخت موردنیاز سفارش: advance یا final"""
    if o["status"] == "completed": return "final"
    if o["status"] == "pending" and settings_get().get("advance_enabled", True): return "advance"
    return "final"

def payment_amount(o, stage):
    return o["advance_amount"] if stage == "advance" else o["final_amount"]

def get_or_create_payment(o):
    """پرداخت در انتظار برای مرحله فعلی سفارش را برمی‌گرداند (اگر نبود می‌سازد)."""
    stage = payment_stage(o)
    for p in pay_by_order(o["id"]):
        if p["status"] == "pending" and p["payment_type"] == stage:
            return p
    return pay_create(o["id"], payment_amount(o, stage), stage)

# ═══════════════ CRUD: کیف پول ═══════════════
def wallet_get(uid_):
    for w in _db()["wallets"]:
        if w["user_id"] == uid_: return w
    return None

def wallet_create(uid_):
    db = _db()
    w = {"id": _uid(), "user_id": uid_, "balance": 0, "transactions": [], "created_at": _now()}
    db["wallets"].append(w); _save(db); return w

def wallet_ensure(uid_):
    w = wallet_get(uid_)
    return w if w else wallet_create(uid_)

def wallet_credit(uid_, amt, desc=""):
    db = _db()
    for w in db["wallets"]:
        if w["user_id"] == uid_:
            w["balance"] += amt
            w["transactions"].append({"type": "credit", "amount": amt, "description": desc, "date": _now()})
            _save(db); return w
    return None

# ═══════════════ CRUD: معرف ═══════════════
def ref_create(uid_, code=None):
    db = _db()
    code = code or f"REF{uid_}{_uid()[:4]}"
    r = {"id": _uid(), "user_id": uid_, "code": code.upper(), "invited_count": 0,
         "total_earned": 0, "invited_user_ids": [], "created_at": _now()}
    db["referrals"].append(r); _save(db); return r

def ref_get_by_user(uid_):
    for r in _db()["referrals"]:
        if r["user_id"] == uid_: return r
    return None

def ref_get_by_code(code):
    for r in _db()["referrals"]:
        if r["code"].upper() == code.upper(): return r
    return None

def ref_record_invite(ref_uid, inv_uid):
    """ثبت دعوت جدید + واریز پاداش به کیف پول معرف (بدون ثبت تکراری)"""
    db = _db()
    reward = db["settings"].get("referral_reward", 50000)
    for r in db["referrals"]:
        if r["user_id"] == ref_uid:
            invited = r.setdefault("invited_user_ids", [])
            if inv_uid in invited: return None  # جلوگیری از پاداش تکراری
            invited.append(inv_uid)
            r["invited_count"] = len(invited)
            r["total_earned"] = r.get("total_earned", 0) + reward
            # واریز به کیف پول در همان instance دیتابیس (رفع باگ قدیمی)
            w = None
            for ww in db["wallets"]:
                if ww["user_id"] == ref_uid: w = ww; break
            if w is None:
                w = {"id": _uid(), "user_id": ref_uid, "balance": 0, "transactions": [], "created_at": _now()}
                db["wallets"].append(w)
            w["balance"] += reward
            w["transactions"].append({"type": "credit", "amount": reward,
                                      "description": f"پاداش دعوت کاربر {inv_uid}", "date": _now()})
            _save(db); return r
    return None

# ═══════════════ تنظیمات و اعلان‌ها ═══════════════
settings_get = lambda: _db()["settings"]
def settings_update(**kw):
    db = _db(); db["settings"].update(kw); _save(db); return db["settings"]

notif_get = lambda: _db()["notifications"]
def notif_update(data):
    db = _db(); db["notifications"] = data; _save(db); return db["notifications"]

def bank_info_text():
    s = settings_get()
    lines = []
    if s.get("bank_card_number"):
        lines.append(f"💳 شماره کارت: {s['bank_card_number']}")
    if s.get("bank_card_holder"):
        lines.append(f"👤 به نام: {s['bank_card_holder']}")
    if s.get("bank_name"):
        lines.append(f"🏦 بانک: {s['bank_name']}")
    if s.get("bank_note"):
        lines.append(f"📝 {s['bank_note']}")
    return "\n".join(lines) or "💳 شماره کارت هنوز در تنظیمات پنل مدیریت ثبت نشده است."

def dashboard():
    db = _db(); oo = db["orders"]; pp = db["payments"]
    return {
        "total_orders": len(oo),
        "pending": len([o for o in oo if o["status"] == "pending"]),
        "in_progress": len([o for o in oo if o["status"] in ("paid_advance", "in_progress")]),
        "completed": len([o for o in oo if o["status"] == "completed"]),
        "cancelled": len([o for o in oo if o["status"] == "cancelled"]),
        "total_earned": sum(p["amount"] for p in pp if p["status"] == "paid"),
        "pending_payments": sum(p["amount"] for p in pp if p["status"] == "pending"),
        "categories": len(db["categories"]),
        "total_users": len(db["users"]) or len(set(o["user_id"] for o in oo)),
        "pending_approval_payments": len([p for p in pp if p["status"] == "pending" and p.get("admin_approved") is None]),
        "pending_receipts": len([p for p in pp if p["status"] == "pending" and p.get("receipt_file_id")]),
    }

def customer_dashboard(uid_):
    oo = order_all(uid_=uid_)
    w = wallet_ensure(uid_); ref = ref_get_by_user(uid_) or ref_create(uid_)
    return {
        "orders_count": len(oo),
        "active_orders": len([o for o in oo if o["status"] in ("paid_advance", "in_progress")]),
        "completed_orders": len([o for o in oo if o["status"] in ("completed", "paid_final")]),
        "wallet_balance": w["balance"],
        "referral_code": ref["code"] if ref else None,
        "referral_count": ref["invited_count"] if ref else 0,
        "referral_earned": ref["total_earned"] if ref else 0,
    }

def esc(s):
    if not s: return ""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

# ═══════════════ PUBLIC URL ═══════════════
def detect_url():
    for k in ["WEBAPP_URL", "RENDER_EXTERNAL_URL"]:
        v = os.getenv(k, "")
        if v: return v
    v = os.getenv("RENDER_EXTERNAL_HOSTNAME", "")
    return f"https://{v}" if v else ""

def bot_username():
    return os.environ.get("BOT_USERNAME", "")

def referral_link(code):
    u = bot_username()
    return f"https://t.me/{u}?start=ref_{code}" if u else ""

# ═══════════════ FLASK ═══════════════
flask_app = Flask(__name__)
PUBLIC_URL = ""

def _auth(admin_only=False):
    init = request.headers.get("X-Telegram-Init-Data") or request.args.get("initData", "")
    if init:
        try:
            p = parse_qs(init); d = {k: v[0] for k, v in p.items()}; h = d.pop("hash", "")
            check = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
            sk = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
            if hmac.new(sk, check.encode(), hashlib.sha256).hexdigest() == h:
                u = json.loads(d.get("user", "{}"))
                if admin_only and u.get("id") not in ADMIN_IDS: return None
                return u
        except: pass
    ak = request.args.get("admin_key", "")
    if ak and ak == BOT_TOKEN[:16]:
        return {"id": ADMIN_IDS[0] if ADMIN_IDS else 0, "is_admin": True}
    return None

def _denied_page(title="⛔️ دسترسی محدود", msg="این صفحه فقط برای مدیران است."):
    return f"""<!DOCTYPE html><html dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>
*{{margin:0;padding:0}}body{{font-family:-apple-system,Tahoma;background:#0a0a0a;color:#f5f5f5;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px}}
.card{{background:#141414;border-radius:20px;padding:40px 28px;max-width:360px;width:100%;text-align:center;border:1px solid #222}}
.ic{{font-size:44px;margin-bottom:14px}}h2{{font-size:17px;margin-bottom:10px}}p{{font-size:13px;color:#888;line-height:1.8}}
a.btn{{display:inline-block;margin-top:18px;padding:12px 26px;background:#fff;color:#000;text-decoration:none;border-radius:12px;font-weight:600;font-size:14px}}
</style></head><body><div class="card"><div class="ic">🔒</div><h2>{title}</h2><p>{msg}</p>
<a class="btn" href="https://t.me/{bot_username()}">بازگشت به ربات</a></div></body></html>"""

# ── صفحات ──
@flask_app.route("/")
def health(): return "OK"

@flask_app.route("/admin")
def admin_page():
    if not _auth(admin_only=True):
        return _denied_page("⛔️ دسترسی محدود", "این صفحه فقط برای مدیران است.<br>از منوی ربات (دکمه «⚙️ پنل مدیریت») وارد شوید."), 403
    return render_template("admin.html")

@flask_app.route("/panel")
def cust_page():
    if not _auth(admin_only=False):
        return _denied_page("⚠️ ورود نامعتبر", "لطفاً از طریق ربات و دکمه «پنل کاربری» وارد شوید."), 403
    return render_template("customer.html")

# ── API مدیریت ──
def _admin_only(f):
    def w(*a, **kw):
        if not _auth(admin_only=True): return jsonify({"error": "unauthorized"}), 403
        return f(*a, **kw)
    w.__name__ = f.__name__; return w

@flask_app.route("/api/admin/dashboard")
@_admin_only
def api_dashboard(): return jsonify(dashboard())

@flask_app.route("/api/admin/categories")
@_admin_only
def api_cats(): return jsonify(_db()["categories"])

@flask_app.route("/api/admin/categories", methods=["POST"])
@_admin_only
def api_cat_create():
    d = request.json or {}
    if not d.get("name") or not d.get("price"): return jsonify({"error": "name & price required"}), 400
    return jsonify(cat_add(d["name"].strip(), d.get("description", "").strip(), int(d["price"]), d.get("advance_percent")))

@flask_app.route("/api/admin/categories/<cid>", methods=["PUT"])
@_admin_only
def api_cat_update(cid):
    d = request.json or {}
    c = cat_update(cid, **{k: v for k, v in d.items() if k in ["name", "description", "price", "advance_percent", "active"]})
    return jsonify(c) if c else (jsonify({"error": "not found"}), 404)

@flask_app.route("/api/admin/categories/<cid>", methods=["DELETE"])
@_admin_only
def api_cat_delete(cid): cat_delete(cid); return jsonify({"ok": True})

@flask_app.route("/api/admin/orders")
@_admin_only
def api_orders():
    oo = order_all(status=request.args.get("status"))
    for o in oo: o["files"] = file_get(o["id"]); o["payments"] = pay_by_order(o["id"])
    return jsonify(oo)

@flask_app.route("/api/admin/orders/<oid>")
@_admin_only
def api_order_detail(oid):
    o = order_get(oid)
    if not o: return jsonify({"error": "not found"}), 404
    o["files"] = file_get(oid); o["payments"] = pay_by_order(oid)
    return jsonify(o)

@flask_app.route("/api/admin/orders/<oid>/status", methods=["PUT"])
@_admin_only
def api_order_status(oid):
    ns = (request.json or {}).get("status")
    if ns not in ["pending", "paid_advance", "in_progress", "completed", "paid_final", "cancelled"]:
        return jsonify({"error": "invalid status"}), 400
    o = order_update(oid, status=ns)
    if not o: return jsonify({"error": "not found"}), 404
    txt = notif_get().get(ns, {}).get("to_user", "")
    if txt: _notify_user(o["user_id"], txt)
    # وقتی پروژه تکمیل می‌شود، اطلاعات پرداخت نهایی + دکمه ارسال رسید برای مشتری ارسال می‌شود
    if ns == "completed":
        p = get_or_create_payment(o)
        msg = (f"🎉 پروژه #{o['id']} تکمیل شد!\n\n"
               f"💰 مبلغ نهایی قابل پرداخت: {p['amount']:,} تومان\n\n"
               f"🏦 اطلاعات پرداخت:\n{bank_info_text()}\n\n"
               f"پس از واریز روی دکمه «ارسال رسید پرداخت» بزنید و عکس رسید را بفرستید.")
        _notify_user_kb(o["user_id"], msg, [[InlineKeyboardButton("🧾 ارسال رسید پرداخت", callback_data=f"send_receipt_{o['id']}")]])
    return jsonify(o)

@flask_app.route("/api/admin/payments")
@_admin_only
def api_payments(): return jsonify(_db()["payments"])

@flask_app.route("/api/admin/payments/<pid>/approve", methods=["PUT"])
@_admin_only
def api_pay_approve(pid):
    p = pay_update(pid, admin_approved=True, status="paid", approved_at=_now())
    if not p: return jsonify({"error": "not found"}), 404
    o = order_get(p["order_id"])
    if o:
        if p["payment_type"] == "advance":
            order_update(o["id"], status="paid_advance")
        elif o["status"] == "completed":
            order_update(o["id"], status="paid_final")
        else:
            order_update(o["id"], status="paid_advance")
        _notify_user(o["user_id"], f"✅ رسید پرداخت {p['amount']:,} تومان برای سفارش #{o['id']} تأیید شد. متشکریم!")
    return jsonify(p)

@flask_app.route("/api/admin/payments/<pid>/reject", methods=["PUT"])
@_admin_only
def api_pay_reject(pid):
    p = pay_update(pid, admin_approved=False, status="failed")
    if not p: return jsonify({"error": "not found"}), 404
    o = order_get(p["order_id"])
    if o:
        _notify_user(o["user_id"], f"❌ رسید پرداخت سفارش #{o['id']} رد شد. لطفاً با پشتیبانی تماس بگیرید.")
    return jsonify(p)

@flask_app.route("/api/admin/settings")
@_admin_only
def api_settings(): return jsonify(settings_get())

@flask_app.route("/api/admin/settings", methods=["PUT"])
@_admin_only
def api_settings_upd():
    d = request.json or {}
    ok = ["advance_percent", "min_advance", "currency", "support_phone", "support_telegram",
          "referral_discount_percent", "referral_reward", "advance_enabled",
          "customer_orders_display_limit", "bank_card_number", "bank_card_holder",
          "bank_name", "bank_note"]
    return jsonify(settings_update(**{k: v for k, v in d.items() if k in ok}))

@flask_app.route("/api/admin/notifications")
@_admin_only
def api_notifs(): return jsonify(notif_get())

@flask_app.route("/api/admin/notifications", methods=["PUT"])
@_admin_only
def api_notifs_upd(): return jsonify(notif_update(request.json or {}))

@flask_app.route("/api/admin/users")
@_admin_only
def api_users(): return jsonify(user_all())

@flask_app.route("/api/admin/broadcast", methods=["POST"])
@_admin_only
def api_broadcast():
    msg = (request.json or {}).get("message", "").strip()
    if not msg: return jsonify({"error": "message required"}), 400
    db = _db()
    users = db["users"]
    db["broadcasts"].append({"message": msg, "sent_at": _now(), "target_count": len(users)})
    _save(db)
    _broadcast(msg)
    return jsonify({"ok": True, "count": len(users)})

@flask_app.route("/api/admin/files/<fid>")
@_admin_only
def api_file_dl(fid):
    for f in _db()["files"]:
        if f["id"] == fid:
            p = Path(f["file_path"])
            if p.exists(): return send_from_directory(str(p.parent), p.name, download_name=f["filename"])
    return jsonify({"error": "not found"}), 404

@flask_app.route("/api/admin/receipts/<pid>")
@_admin_only
def api_receipt_dl(pid):
    p = pay_get(pid)
    if not p or not p.get("receipt_path"): return jsonify({"error": "not found"}), 404
    pp = Path(p["receipt_path"])
    if pp.exists(): return send_from_directory(str(pp.parent), pp.name, download_name=f"receipt_{pid}.jpg")
    return jsonify({"error": "not found"}), 404

# ── API مشتری ──
def _user_only(f):
    def w(*a, **kw):
        u = _auth(admin_only=False)
        if not u: return jsonify({"error": "unauthorized"}), 403
        user_register(u.get("id"), u.get("username", ""), u.get("first_name", ""))
        return f(u, *a, **kw)
    w.__name__ = f.__name__; return w

@flask_app.route("/api/customer/dashboard")
@_user_only
def api_cust_dash(u): return jsonify(customer_dashboard(u["id"]))

@flask_app.route("/api/customer/categories")
@_user_only
def api_cust_cats(u): return jsonify(cat_all(True))

@flask_app.route("/api/customer/orders")
@_user_only
def api_cust_orders(u):
    oo = order_all(uid_=u["id"])
    for o in oo: o["files"] = file_get(o["id"]); o["payments"] = pay_by_order(o["id"])
    return jsonify(oo)

@flask_app.route("/api/customer/orders", methods=["POST"])
@_user_only
def api_cust_order_create(u):
    d = request.json or {}
    cid, desc = d.get("category_id"), d.get("description", "")
    if not cid: return jsonify({"error": "category_id required"}), 400
    o = order_create(u["id"], u.get("username", ""), u.get("first_name", ""), cid, desc)
    if not o: return jsonify({"error": "category not found"}), 404
    p = get_or_create_payment(o)
    for aid in ADMIN_IDS:
        _notify_user(aid, f"📬 سفارش جدید #{o['id']}\nاز: {esc(u.get('first_name', 'کاربر'))}\nدسته: {o['category_name']}\nمبلغ: {o['price']:,} تومان")
    msg = (f"✅ سفارش #{o['id']} ثبت شد!\n\n"
           f"💰 مبلغ قابل پرداخت: {p['amount']:,} تومان\n\n"
           f"🏦 اطلاعات پرداخت:\n{bank_info_text()}\n\n"
           f"پس از واریز روی دکمه «ارسال رسید پرداخت» بزنید و عکس رسید را بفرستید.")
    _notify_user_kb(u["id"], msg, [[InlineKeyboardButton("🧾 ارسال رسید پرداخت", callback_data=f"send_receipt_{o['id']}")]])
    return jsonify(o)

@flask_app.route("/api/customer/orders/<oid>")
@_user_only
def api_cust_order_detail(u, oid):
    o = order_get(oid)
    if not o or o["user_id"] != u["id"]: return jsonify({"error": "not found"}), 404
    o["files"] = file_get(oid); o["payments"] = pay_by_order(oid)
    o["bank_info"] = bank_info_text()
    o["bot_username"] = bot_username()
    return jsonify(o)

@flask_app.route("/api/customer/orders/<oid>/ref_code", methods=["POST"])
@_user_only
def api_cust_order_ref(u, oid):
    o = order_get(oid)
    if not o or o["user_id"] != u["id"]: return jsonify({"error": "not found"}), 404
    code = (request.json or {}).get("code", "").strip().upper()
    ref = ref_get_by_code(code)
    if not ref or ref["user_id"] == u["id"]: return jsonify({"error": "کد معرف نامعتبر"}), 400
    pct = settings_get().get("referral_discount_percent", 10)
    discount = int(o["price"] * pct / 100)
    order_update(oid, discount=discount, referral_code=code, referral_discount_applied=True)
    ref_record_invite(ref["user_id"], u["id"])  # پاداش معرف (با جلوگیری از ثبت تکراری)
    return jsonify({"discount": discount})

@flask_app.route("/api/customer/wallet")
@_user_only
def api_cust_wallet(u):
    w = wallet_ensure(u["id"]); ref = ref_get_by_user(u["id"]) or ref_create(u["id"])
    s = settings_get()
    return jsonify({
        "wallet": w, "referral": ref,
        "bot_username": bot_username(),
        "referral_link": referral_link(ref["code"]) if ref else "",
        "settings": {
            "referral_reward": s.get("referral_reward", 50000),
            "referral_discount_percent": s.get("referral_discount_percent", 10),
        }
    })

@flask_app.route("/api/customer/payments", methods=["POST"])
@_user_only
def api_cust_pay_create(u):
    """ایجاد درخواست پرداخت دستی (کارت به کارت) — لینک پرداخت آنلاین وجود ندارد"""
    d = request.json or {}
    oid = d.get("order_id")
    o = order_get(oid)
    if not o or o["user_id"] != u["id"]: return jsonify({"error": "not found"}), 404
    p = get_or_create_payment(o)
    return jsonify({
        "payment_id": p["id"], "order_id": oid, "amount": p["amount"],
        "payment_type": p["payment_type"], "status": p["status"],
        "bank_info": bank_info_text(),
        "bot_username": bot_username(),
    })

@flask_app.route("/api/customer/referral/apply", methods=["POST"])
@_user_only
def api_cust_ref_apply(u):
    code = (request.json or {}).get("code", "").strip().upper()
    ref = ref_get_by_code(code)
    if not ref: return jsonify({"error": "کد معرف نامعتبر"}), 400
    if ref["user_id"] == u["id"]: return jsonify({"error": "کد خودتان!"}), 400
    for o in order_all(uid_=u["id"]):
        if o.get("referral_code"): return jsonify({"error": "قبلاً استفاده کرده‌اید"}), 400
    ref_record_invite(ref["user_id"], u["id"])
    user_update(u["id"], referred_by=ref["code"])
    return jsonify({"discount_percent": settings_get().get("referral_discount_percent", 10), "message": "کد معرف تأیید شد!"})

# ═══════════════ TELEGRAM BOT ═══════════════
user_sessions = {}
MAIN_KB = ReplyKeyboardMarkup([
    [KeyboardButton("📋 ثبت سفارش جدید")],
    [KeyboardButton("📂 سفارش‌های من"), KeyboardButton("👥 باشگاه مشتریان")],
    [KeyboardButton("💰 کیف پول"), KeyboardButton("📞 پشتیبانی")],
    [KeyboardButton("🧾 ارسال رسید پرداخت")],
], resize_keyboard=True)

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    user_sessions.pop(u.id, None)
    user_register(u.id, u.username or "", u.first_name or "")
    wallet_ensure(u.id)
    if not ref_get_by_user(u.id): ref_create(u.id)

    args = ctx.args or []
    if args:
        arg = args[0].lower()
        if arg.startswith("ref_"):
            ref = ref_get_by_code(arg[4:])
            if ref and ref["user_id"] != u.id:
                r = ref_record_invite(ref["user_id"], u.id)
                user_update(u.id, referred_by=ref["code"])
                if r:
                    reward = settings_get().get("referral_reward", 50000)
                    _notify_user(ref["user_id"],
                        f"🎉 دعوت جدید! کاربر {esc(u.first_name or u.id)} با لینک شما عضو شد.\n"
                        f"💰 پاداش {reward:,} تومان به کیف پول شما اضافه شد.")
                    await update.message.reply_text(
                        f"🎉 خوش آمدی! با کد معرف {esc(ref['code'])} عضو شدی و تخفیف اولین سفارشت فعال شد. 🎁")
            elif ref:
                await update.message.reply_text("این کد معرف خودتان است!")
            else:
                await update.message.reply_text("کد معرف نامعتبر است.")
        elif arg.startswith("receipt_"):
            pid = arg.split("_", 1)[1]
            p = pay_get(pid)
            o = order_get(p["order_id"]) if p else None
            if p and o and o["user_id"] == u.id and p["status"] == "pending":
                user_sessions[u.id] = {"state": "awaiting_receipt", "payment_id": pid}
                await update.message.reply_text(
                    f"🧾 سفارش #{o['id']} — مبلغ {p['amount']:,} تومان\n\n"
                    f"🏦 اطلاعات پرداخت:\n{bank_info_text()}\n\n"
                    f"📸 حالا عکس رسید پرداخت را بفرستید:")
                return
            else:
                await update.message.reply_text("درخواست پرداخت معتبری یافت نشد.")

    if u.id in ADMIN_IDS and PUBLIC_URL:
        try:
            await ctx.bot.set_chat_menu_button(chat_id=u.id,
                menu_button=MenuButtonWebApp(text="⚙️ پنل مدیریت", web_app=WebAppInfo(url=f"{PUBLIC_URL}/admin")))
        except: pass
    await update.message.reply_text(
        f"👋 سلام {esc(u.first_name)}!\n\nبه ربات سفارش پروژه دانشگاهی خوش آمدی.\nبرای شروع روی «ثبت سفارش جدید» کلیک کن.",
        reply_markup=MAIN_KB)

async def handle_all(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg: return
    u = update.effective_user
    if msg.document or msg.photo or msg.video or msg.voice:
        return await handle_file(update, ctx)
    if not msg.text: return
    t = msg.text
    handlers = {
        "📋 ثبت سفارش جدید": order_start, "📂 سفارش‌های من": my_orders,
        "👥 باشگاه مشتریان": club, "💰 کیف پول": wallet_cmd, "📞 پشتیبانی": support_cmd,
        "🧾 ارسال رسید پرداخت": receipt_cmd,
    }
    if t in handlers: return await handlers[t](update, ctx)
    s = user_sessions.get(u.id, {})
    if s.get("state") == "entering_desc": return await order_desc(update, ctx)
    await msg.reply_text("از دکمه‌های زیر استفاده کن 👇", reply_markup=MAIN_KB)

async def order_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cats = cat_all(True)
    if not cats: await update.message.reply_text("⚠️ دسته‌بندی فعالی وجود ندارد.", reply_markup=MAIN_KB); return
    user_sessions[update.effective_user.id] = {"state": "selecting_category"}
    kb = [[InlineKeyboardButton(f"{c['name']} — {c['price']:,} تومان", callback_data=f"cat_{c['id']}")] for c in cats]
    kb.append([InlineKeyboardButton("انصراف", callback_data="cancel")])
    await update.message.reply_text("🎯 نوع پروژه را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(kb))

async def cat_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); u = q.from_user
    if q.data == "cancel": user_sessions.pop(u.id, None); await q.edit_message_text("لغو شد."); return
    cid = q.data.replace("cat_", ""); cat = cat_get(cid)
    if not cat: await q.edit_message_text("⚠️ دسته‌بندی نامعتبر."); return
    user_sessions[u.id] = {"state": "entering_desc", "category_id": cid}
    adv = int(cat["price"] * cat["advance_percent"] / 100)
    adv_line = f"💳 پیش‌پرداخت: {adv:,} تومان" if settings_get().get("advance_enabled", True) else "💳 پرداخت کامل (پیش‌پرداخت غیرفعال)"
    await q.edit_message_text(f"📌 {cat['name']}\n💰 قیمت: {cat['price']:,} تومان\n{adv_line}\n\n📝 توضیحات پروژه را بنویسید:")

async def order_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user; s = user_sessions.pop(u.id, {})
    if s.get("state") != "entering_desc": return
    o = order_create(u.id, u.username, u.first_name, s["category_id"], update.message.text)
    if not o: await update.message.reply_text("⚠️ خطا."); return
    await update.message.reply_text(f"✅ سفارش #{o['id']} ثبت شد!\n\nحالا فایل‌های مدارک را بفرست (عکس، PDF، Word و...)\nبعدش روی «اتمام» کلیک کن.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ اتمام آپلود", callback_data=f"finish_{o['id']}")],
            [InlineKeyboardButton("⏭ رد کردن", callback_data=f"skip_{o['id']}")]]))
    for aid in ADMIN_IDS:
        try: await ctx.bot.send_message(aid, f"📬 سفارش جدید #{o['id']}\nاز: {esc(u.first_name)} (@{u.username or '---'})\nدسته: {o['category_name']}\nمبلغ: {o['price']:,} تومان")
        except: pass

async def handle_file(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user; msg = update.message
    file = msg.document or (msg.photo[-1] if msg.photo else None) or msg.video or msg.voice
    if not file: return
    fname = getattr(file, 'file_name', None) or f"file_{file.file_id[:8]}"
    tf = await ctx.bot.get_file(file.file_id)
    ext = Path(tf.file_path or "").suffix or (".jpg" if msg.photo else "")
    if not Path(fname).suffix: fname = f"{fname}{ext}"

    # ── حالت ارسال رسید پرداخت ──
    s = user_sessions.get(u.id, {})
    if s.get("state") == "awaiting_receipt":
        pid = s.get("payment_id")
        p = pay_get(pid) if pid else None
        o = order_get(p["order_id"]) if p else None
        if not p or not o or o["user_id"] != u.id:
            user_sessions.pop(u.id, None)
            await msg.reply_text("⚠️ درخواست پرداخت معتبری یافت نشد. دوباره تلاش کنید.")
            return
        try:
            sp = UPLOAD_FOLDER / f"rcpt_{_uid()}_{fname}"
            await tf.download_to_drive(sp)
        except Exception:
            sp = None
        pay_update(pid, receipt_file_id=file.file_id, receipt_path=str(sp) if sp else None, receipt_sent_at=_now())
        user_sessions.pop(u.id, None)
        await msg.reply_text("✅ رسید شما دریافت شد و برای مدیر ارسال شد.\nپس از تأیید، به شما اطلاع داده می‌شود.")
        for aid in ADMIN_IDS:
            try:
                kb = [[InlineKeyboardButton("✅ تأیید", callback_data=f"payappr_{pid}"),
                       InlineKeyboardButton("❌ رد", callback_data=f"payrej_{pid}")]]
                cap = (f"🧾 رسید پرداخت\nسفارش #{o['id']} — {o['category_name']}\n"
                       f"نوع: {'پیش‌پرداخت' if p['payment_type'] == 'advance' else 'پرداخت نهایی'}\n"
                       f"مبلغ: {p['amount']:,} تومان\nاز: {esc(u.first_name or u.id)}")
                if msg.photo:
                    await ctx.bot.send_photo(aid, file.file_id, caption=cap, reply_markup=InlineKeyboardMarkup(kb))
                else:
                    await ctx.bot.send_document(aid, file.file_id, caption=cap, reply_markup=InlineKeyboardMarkup(kb))
            except: pass
        return

    # ── آپلود فایل مدارک سفارش ──
    try:
        sp = UPLOAD_FOLDER / f"{_uid()}_{fname}"
        await tf.download_to_drive(sp)
        oo = order_all(uid_=u.id)
        if not oo: await msg.reply_text("⚠️ اول سفارش ثبت کن."); return
        file_add(oo[0]["id"], fname, str(sp), file.file_id)
        await msg.reply_text(f"✅ «{esc(fname)}» آپلود شد.\nفایل بعدی یا «اتمام».")
    except Exception as e:
        await msg.reply_text("⚠️ خطا در آپلود.")

async def finish_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); u = q.from_user
    data = q.data; oid = data.replace("finish_", "").replace("skip_", "")
    o = order_get(oid)
    if not o or o["user_id"] != u.id: await q.edit_message_text("⚠️ سفارش یافت نشد."); return
    await q.edit_message_text("✅ فایل‌ها دریافت شد!" if data.startswith("finish_") else "✅ اطلاعات ثبت شد.")
    p = get_or_create_payment(o)
    pay_type = "پیش‌پرداخت" if p["payment_type"] == "advance" else "پرداخت (مبلغ کامل)" if not settings_get().get("advance_enabled", True) else "پرداخت نهایی"
    txt = (f"📋 سفارش #{oid}\n💰 مبلغ: {o['price']:,} تومان\n"
           f"💳 {pay_type}: {p['amount']:,} تومان\n\n"
           f"🏦 اطلاعات پرداخت:\n{bank_info_text()}\n\n"
           f"🧾 پس از واریز، روی دکمه «ارسال رسید پرداخت» بزنید و عکس رسید را بفرستید.")
    kb = [[InlineKeyboardButton("🧾 ارسال رسید پرداخت", callback_data=f"send_receipt_{oid}")],
          [InlineKeyboardButton("📱 پنل کاربری", web_app=WebAppInfo(url=f"{PUBLIC_URL}/panel?order={oid}"))]]
    await ctx.bot.send_message(u.id, txt, reply_markup=InlineKeyboardMarkup(kb))

async def my_orders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user; oo = order_all(uid_=u.id)
    if not oo: await update.message.reply_text("📂 سفارشی نداری.", reply_markup=MAIN_KB); return
    sm = {"pending": "⏳ در انتظار", "paid_advance": "💰 در حال انجام", "in_progress": "🔄 در حال انجام",
          "completed": "✅ تکمیل", "paid_final": "💵 تسویه", "cancelled": "❌ لغو"}
    limit = int(settings_get().get("customer_orders_display_limit", 8) or 8)
    shown = oo[:limit]
    txt = f"📂 سفارش‌های شما (نمایش {len(shown)} از {len(oo)}):\n\n"
    for o in shown:
        txt += f"▸ #{o['id']} — {o['category_name']}\n   {sm.get(o['status'], o['status'])} | {o['price']:,} تومان\n\n"
    if len(oo) > limit:
        txt += f"و {len(oo) - limit} سفارش دیگر... (تعداد نمایش از پنل مدیریت قابل تغییر است)"
    await update.message.reply_text(txt, reply_markup=MAIN_KB)

async def club(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user; ref = ref_get_by_user(u.id) or ref_create(u.id); s = settings_get()
    link = referral_link(ref["code"])
    txt = (f"👥 باشگاه مشتریان\n\n"
           f"🔗 لینک معرف شما:\n<code>{link or ref['code']}</code>\n"
           f"👤 تعداد دعوت: {ref['invited_count']} نفر\n"
           f"💰 پاداش دریافتی: {ref['total_earned']:,} تومان\n\n"
           f"🎁 پاداش هر دعوت: {s.get('referral_reward', 50000):,} تومان (به کیف پول اضافه می‌شود)\n"
           f"🎫 تخفیف برای دعوت‌شونده: {s.get('referral_discount_percent', 10)}%\n\n"
           f"📋 لینک را برای دوستانتان بفرستید تا عضو شوند. به‌محض عضویت، پاداش به کیف پول شما واریز می‌شود.")
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML, reply_markup=MAIN_KB)

async def wallet_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user; w = wallet_ensure(u.id)
    txt = f"💰 کیف پول\n\nموجودی: {w['balance']:,} تومان\n\n📋 تراکنش‌های اخیر:\n"
    for t in w.get("transactions", [])[-5:][::-1]:
        sign, color = ("+", "🟢") if t["type"] == "credit" else ("-", "🔴")
        txt += f"{color} {sign}{t['amount']:,} — {t.get('description', '')}\n"
    if not w.get("transactions"): txt += "تراکنشی ثبت نشده است."
    await update.message.reply_text(txt, reply_markup=MAIN_KB)

async def support_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    s = settings_get(); txt = "📞 پشتیبانی:\n"
    if s.get("support_phone"): txt += f"📱 {s['support_phone']}\n"
    if s.get("support_telegram"): txt += f"💬 @{s['support_telegram']}\n"
    if not s.get("support_phone") and not s.get("support_telegram"): txt += "از همین ربات پیام دهید."
    await update.message.reply_text(txt, reply_markup=MAIN_KB)

async def receipt_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """دکمه «ارسال رسید پرداخت» در کیبورد"""
    u = update.effective_user
    oo = [o for o in order_all(uid_=u.id) if o["status"] in ("pending", "completed")]
    if not oo:
        await update.message.reply_text("⚠️ سفارشی برای پرداخت ندارید.", reply_markup=MAIN_KB); return
    if len(oo) == 1:
        await ask_receipt(update, ctx, oo[0]); return
    kb = [[InlineKeyboardButton(f"#{o['id']} — {o['category_name']} ({payment_amount(o, payment_stage(o)):,} تومان)",
                                callback_data=f"send_receipt_{o['id']}")] for o in oo[:8]]
    await update.message.reply_text("🧾 برای کدام سفارش می‌خواهید رسید بفرستید؟", reply_markup=InlineKeyboardMarkup(kb))

async def ask_receipt(update: Update, ctx: ContextTypes.DEFAULT_TYPE, o):
    u = update.effective_user
    p = get_or_create_payment(o)
    user_sessions[u.id] = {"state": "awaiting_receipt", "payment_id": p["id"]}
    await update.message.reply_text(
        f"🧾 سفارش #{o['id']} — مبلغ {p['amount']:,} تومان\n\n"
        f"🏦 اطلاعات پرداخت:\n{bank_info_text()}\n\n"
        f"📸 عکس رسید پرداخت را بفرستید:", reply_markup=MAIN_KB)

async def send_receipt_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); u = q.from_user
    oid = q.data.replace("send_receipt_", "")
    o = order_get(oid)
    if not o or o["user_id"] != u.id:
        await q.answer("سفارش یافت نشد", show_alert=True); return
    p = get_or_create_payment(o)
    user_sessions[u.id] = {"state": "awaiting_receipt", "payment_id": p["id"]}
    try:
        await q.edit_message_text(
            f"🧾 سفارش #{oid} — مبلغ {p['amount']:,} تومان\n\n"
            f"🏦 اطلاعات پرداخت:\n{bank_info_text()}\n\n"
            f"📸 عکس رسید پرداخت را بفرستید:")
    except Exception:
        await ctx.bot.send_message(u.id, "📸 عکس رسید پرداخت را بفرستید:")

async def receipt_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """تأیید/رد رسید توسط ادمین از داخل ربات"""
    q = update.callback_query; await q.answer()
    if q.from_user.id not in ADMIN_IDS:
        await q.answer("⛔️ فقط مدیر می‌تواند تأیید کند", show_alert=True); return
    pid = q.data.split("_", 1)[1]
    p = pay_get(pid)
    if not p: await q.edit_message_text("پرداخت یافت نشد."); return
    o = order_get(p["order_id"])
    if q.data.startswith("payappr_"):
        pay_update(pid, admin_approved=True, status="paid", approved_at=_now(), approved_by=q.from_user.id)
        if o:
            if p["payment_type"] == "advance":
                order_update(o["id"], status="paid_advance")
            elif o["status"] == "completed":
                order_update(o["id"], status="paid_final")
            else:
                order_update(o["id"], status="paid_advance")
            _notify_user(o["user_id"],
                f"✅ رسید پرداخت {p['amount']:,} تومان برای سفارش #{o['id']} تأیید شد. متشکریم!")
        try: await q.edit_message_caption("✅ پرداخت تأیید شد.")
        except Exception: await q.edit_message_text("✅ پرداخت تأیید شد.")
    else:
        pay_update(pid, admin_approved=False, status="failed")
        if o:
            _notify_user(o["user_id"], f"❌ رسید پرداخت سفارش #{o['id']} رد شد. لطفاً با پشتیبانی تماس بگیرید.")
        try: await q.edit_message_caption("❌ پرداخت رد شد.")
        except Exception: await q.edit_message_text("❌ پرداخت رد شد.")

# ═══════════════ NOTIFICATION ═══════════════
def _notify_user(uid_, msg):
    """ارسال پیام ساده به کاربر تلگرام (غیرمسدودکننده)"""
    def _run():
        async def _send():
            app = Application.builder().token(BOT_TOKEN).build()
            try:
                await app.initialize()
                await app.bot.send_message(chat_id=uid_, text=msg)
            except: pass
            finally:
                try: await app.shutdown()
                except: pass
        try:
            loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
            loop.run_until_complete(_send()); loop.close()
        except: pass
    threading.Thread(target=_run, daemon=True).start()

def _notify_user_kb(uid_, msg, buttons):
    """ارسال پیام با دکمه شیشه‌ای (غیرمسدودکننده)"""
    def _run():
        async def _send():
            app = Application.builder().token(BOT_TOKEN).build()
            try:
                await app.initialize()
                await app.bot.send_message(chat_id=uid_, text=msg, reply_markup=InlineKeyboardMarkup(buttons))
            except: pass
            finally:
                try: await app.shutdown()
                except: pass
        try:
            loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
            loop.run_until_complete(_send()); loop.close()
        except: pass
    threading.Thread(target=_run, daemon=True).start()

def _broadcast(text):
    """پیام همگانی به تمام کاربران ثبت‌شده"""
    def _run():
        async def _send():
            app = Application.builder().token(BOT_TOKEN).build()
            ok = 0
            try:
                await app.initialize()
                for u in user_all():
                    try:
                        await app.bot.send_message(chat_id=u["id"], text=text)
                        ok += 1
                    except: pass
                log.info(f"📣 broadcast sent to {ok}/{len(user_all())} users")
            finally:
                try: await app.shutdown()
                except: pass
        try:
            loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
            loop.run_until_complete(_send()); loop.close()
        except: pass
    threading.Thread(target=_run, daemon=True).start()

# ═══════════════ SEED ═══════════════
def seed():
    d = _db()
    if not d["categories"]:
        for n, de, p in [("مقاله و تحقیق", "نگارش مقاله، تحقیق کلاسی، پروپوزال", 500000),
                          ("برنامه‌نویسی", "پایتون، جاوا، C++، وب، اندروید", 1500000),
                          ("پاورپوینت", "اسلایدهای حرفه‌ای و قالب اختصاصی", 300000),
                          ("حل تمرین", "حل تمرین‌های درسی و مسائل", 200000),
                          ("طراحی گرافیک", "پوستر، لوگو، اینفوگرافیک", 400000)]:
            cat_add(n, de, p)
        log.info("demo categories seeded.")

# ═══════════════ MAIN ═══════════════
def run_flask():
    """Run Flask in daemon thread."""
    flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

async def run_bot():
    """Async: build, start, and poll forever (with retry so a failure never kills Flask)."""
    seed()
    global PUBLIC_URL
    PUBLIC_URL = detect_url()
    if not PUBLIC_URL:
        PUBLIC_URL = f"http://localhost:{PORT}"
    log.info(f"PUBLIC_URL = {PUBLIC_URL}")

    try:
        r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe", timeout=10)
        if r.json().get("ok"):
            os.environ["BOT_USERNAME"] = r.json()["result"]["username"]
            log.info(f"bot: @{os.environ['BOT_USERNAME']}")
        else:
            log.error(f"getMe failed: {r.json()}")
    except Exception as e:
        log.error(f"getMe error (will retry): {e}")

    # polling با تلاش مجدد: اگر اینترنت/توکن مشکل داشت، سرور و پنل مدیریت زنده می‌مانند
    while True:
        try:
            app = Application.builder().token(BOT_TOKEN).build()
            app.add_handler(CommandHandler("start", start))
            app.add_handler(CallbackQueryHandler(cat_callback, pattern="^cat_"))
            app.add_handler(CallbackQueryHandler(cat_callback, pattern="^cancel$"))
            app.add_handler(CallbackQueryHandler(finish_callback, pattern="^finish_"))
            app.add_handler(CallbackQueryHandler(finish_callback, pattern="^skip_"))
            app.add_handler(CallbackQueryHandler(send_receipt_callback, pattern="^send_receipt_"))
            app.add_handler(CallbackQueryHandler(receipt_callback, pattern="^payappr_"))
            app.add_handler(CallbackQueryHandler(receipt_callback, pattern="^payrej_"))
            app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_all))
            await app.initialize()
            await app.start()
            await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
            log.info(f"🤖 Bot polling started | 🌐 {PUBLIC_URL}/admin | 👤 {PUBLIC_URL}/panel")
            break
        except Exception as e:
            log.error(f"⚠️ bot polling error (retrying in 10s): {e}")
            try:
                await app.shutdown()
            except: pass
            await asyncio.sleep(10)

    # Keep alive forever
    while True:
        await asyncio.sleep(3600)

def main():
    threading.Thread(target=run_flask, daemon=True).start()
    time.sleep(1.5)
    log.info(f"🌐 Flask on port {PORT}")
    import asyncio
    asyncio.run(run_bot())

if __name__ == "__main__":
    main()
