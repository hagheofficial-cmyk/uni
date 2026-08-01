#!/usr/bin/env python3
"""🎓 ربات سفارش پروژه دانشگاهی — نسخهٔ تمام‌رباتی (بدون مینی‌اپ/وب‌اپ)

همه چیز داخل تلگرام انجام می‌شود:
- مشتری: ثبت سفارش، آپلود فایل، پرداخت دستی (شماره کارت + رسید)، باشگاه مشتریان، کیف پول
- ادمین: آمار، سفارش‌ها، پرداخت‌ها، دسته‌بندی‌ها، تنظیمات، پیام همگانی، کاربران، اعلان‌ها
"""

import os, json, logging, threading, uuid, time, asyncio
from datetime import datetime
from pathlib import Path

import requests
from flask import Flask
from dotenv import load_dotenv
from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup,
                      ReplyKeyboardMarkup, KeyboardButton)
from telegram.ext import (Application, CommandHandler, MessageHandler,
                          CallbackQueryHandler, filters, ContextTypes)
from telegram.constants import ParseMode

load_dotenv()
BASE_DIR = Path(__file__).parent
UPLOAD_FOLDER = Path(os.getenv("UPLOAD_FOLDER", str(BASE_DIR / "uploads"))); UPLOAD_FOLDER.mkdir(exist_ok=True)
DB_FILE = Path(os.getenv("DATABASE_PATH", str(BASE_DIR / "database.json")))

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
    if o["status"] == "completed": return "final"
    if o["status"] == "pending" and settings_get().get("advance_enabled", True): return "advance"
    return "final"

def payment_amount(o, stage):
    return o["advance_amount"] if stage == "advance" else o["final_amount"]

def get_or_create_payment(o):
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
    db = _db()
    reward = db["settings"].get("referral_reward", 50000)
    for r in db["referrals"]:
        if r["user_id"] == ref_uid:
            invited = r.setdefault("invited_user_ids", [])
            if inv_uid in invited: return None
            invited.append(inv_uid)
            r["invited_count"] = len(invited)
            r["total_earned"] = r.get("total_earned", 0) + reward
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
    if s.get("bank_card_number"): lines.append(f"💳 شماره کارت: {s['bank_card_number']}")
    if s.get("bank_card_holder"): lines.append(f"👤 به نام: {s['bank_card_holder']}")
    if s.get("bank_name"): lines.append(f"🏦 بانک: {s['bank_name']}")
    if s.get("bank_note"): lines.append(f"📝 {s['bank_note']}")
    return "\n".join(lines) or "💳 شماره کارت هنوز در تنظیمات ثبت نشده است."

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
        "pending_receipts": len([p for p in pp if p["status"] == "pending" and p.get("receipt_file_id")]),
    }

def esc(s):
    if not s: return ""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

def bot_username():
    return os.environ.get("BOT_USERNAME", "")

def referral_link(code):
    u = bot_username()
    return f"https://t.me/{u}?start=ref_{code}" if u else ""

def to_int(t):
    try: return int(str(t).replace(",", "").strip())
    except: return None

# ═══════════════ لیبل‌ها ═══════════════
STATUS_LABELS = {"pending": "⏳ در انتظار", "paid_advance": "💰 پیش‌پرداخت", "in_progress": "🔄 در حال انجام",
                 "completed": "✅ تکمیل", "paid_final": "💵 تسویه", "cancelled": "❌ لغو"}
STATUS_FLOW = {"pending": ["paid_advance", "cancelled"], "paid_advance": ["in_progress", "cancelled"],
               "in_progress": ["completed", "cancelled"], "completed": ["paid_final", "cancelled"],
               "cancelled": ["pending"]}
FLOW_BTN = {"paid_advance": "تأیید پیش‌پرداخت", "in_progress": "شروع انجام", "completed": "تکمیل پروژه",
            "paid_final": "تسویه نهایی", "cancelled": "لغو سفارش", "pending": "بازگشت به انتظار"}
PAY_LABEL = {"pending": "در انتظار تأیید", "paid": "پرداخت شده", "failed": "ناموفق"}

# ═══════════════ FLASK (فقط برای زنده ماندن سرویس Render) ═══════════════
flask_app = Flask(__name__)

@flask_app.route("/")
def health(): return "OK"

@flask_app.route("/admin")
def old_admin():
    return "<div dir='rtl' style='font-family:Tahoma;text-align:center;padding:40px;color:#333'>⛔️ پنل مدیریت حذف شد — همه‌چیز داخل خود ربات تلگرام است.<br>ربات را باز کنید و دکمه «⚙️ پنل مدیریت» را بزنید.</div>"

@flask_app.route("/panel")
def old_panel():
    return "<div dir='rtl' style='font-family:Tahoma;text-align:center;padding:40px;color:#333'>⚠️ پنل کاربری حذف شد — همه‌چیز داخل خود ربات تلگرام است.</div>"

# ═══════════════ TELEGRAM BOT ═══════════════
user_sessions = {}   # state machine مشتری
admin_sessions = {}  # state machine ادمین (منوها)

def btn(text, cb): return InlineKeyboardButton(text, callback_data=cb)

def main_kb(uid):
    rows = [
        [KeyboardButton("📋 ثبت سفارش جدید")],
        [KeyboardButton("📂 سفارش‌های من"), KeyboardButton("👥 باشگاه مشتریان")],
        [KeyboardButton("💰 کیف پول"), KeyboardButton("📞 پشتیبانی")],
        [KeyboardButton("🧾 ارسال رسید پرداخت")],
    ]
    if uid in ADMIN_IDS:
        rows.append([KeyboardButton("⚙️ پنل مدیریت")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def admin_menu_kb():
    return InlineKeyboardMarkup([
        [btn("📊 آمار", "adm_stats"), btn("📦 سفارش‌ها", "adm_orders"), btn("💳 پرداخت‌ها", "adm_pays")],
        [btn("🗂 دسته‌بندی‌ها", "adm_cats"), btn("⚙️ تنظیمات", "adm_set"), btn("📣 پیام همگانی", "adm_bc")],
        [btn("👥 کاربران", "adm_users"), btn("🔔 اعلان‌ها", "adm_notifs")],
        [btn("✖️ بستن", "adm_close")],
    ])

# ── /start ──
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
                    f"📸 حالا عکس رسید پرداخت را بفرستید:", reply_markup=main_kb(u.id))
                return
            else:
                await update.message.reply_text("درخواست پرداخت معتبری یافت نشد.")

    await update.message.reply_text(
        f"👋 سلام {esc(u.first_name)}!\n\nبه ربات سفارش پروژه دانشگاهی خوش آمدی.\nبرای شروع روی «ثبت سفارش جدید» کلیک کن.",
        reply_markup=main_kb(u.id))

async def cancel_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    user_sessions.pop(u.id, None); admin_sessions.pop(u.id, None)
    await update.message.reply_text("لغو شد.", reply_markup=main_kb(u.id))

async def admin_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if u.id not in ADMIN_IDS:
        await update.message.reply_text("⛔️ دسترسی محدود.")
        return
    await update.message.reply_text("⚙️ پنل مدیریت", reply_markup=admin_menu_kb())

# ── مسیریابی پیام‌های متنی ──
async def handle_all(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg: return
    u = update.effective_user
    if msg.document or msg.photo or msg.video or msg.voice:
        return await handle_file(update, ctx)
    if not msg.text: return
    t = msg.text

    # منوی مدیریت (ادمین)
    if t == "⚙️ پنل مدیریت":
        if u.id in ADMIN_IDS:
            await msg.reply_text("⚙️ پنل مدیریت", reply_markup=admin_menu_kb())
        else:
            await msg.reply_text("⛔️ دسترسی محدود.")
        return

    # حالت‌های در حال انجام ادمین (دریافت مقدار از ادمین)
    if u.id in ADMIN_IDS and u.id in admin_sessions:
        if await admin_text(update, ctx): return

    # حالت‌های در حال انجام مشتری
    s = user_sessions.get(u.id, {})
    if s.get("state") == "entering_desc":
        return await order_desc(update, ctx)

    handlers = {
        "📋 ثبت سفارش جدید": order_start, "📂 سفارش‌های من": my_orders,
        "👥 باشگاه مشتریان": club, "💰 کیف پول": wallet_cmd, "📞 پشتیبانی": support_cmd,
        "🧾 ارسال رسید پرداخت": receipt_cmd,
    }
    if t in handlers: return await handlers[t](update, ctx)
    await msg.reply_text("از دکمه‌های زیر استفاده کن 👇", reply_markup=main_kb(u.id))

# ── ثبت سفارش ──
async def order_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cats = cat_all(True)
    if not cats:
        await update.message.reply_text("⚠️ دسته‌بندی فعالی وجود ندارد.", reply_markup=main_kb(update.effective_user.id)); return
    kb = [[InlineKeyboardButton(f"{c['name']} — {c['price']:,} تومان", callback_data=f"cat_{c['id']}")] for c in cats]
    kb.append([InlineKeyboardButton("انصراف", callback_data="cancel")])
    await update.message.reply_text("🎯 نوع پروژه را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(kb))

async def cat_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); u = q.from_user
    if q.data == "cancel":
        user_sessions.pop(u.id, None); await q.edit_message_text("لغو شد."); return
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
        try:
            await ctx.bot.send_message(aid,
                f"📬 سفارش جدید #{o['id']}\nاز: {esc(u.first_name)} (@{u.username or '---'})\nدسته: {o['category_name']}\nمبلغ: {o['price']:,} تومان",
                reply_markup=InlineKeyboardMarkup([[btn("📦 مشاهده سفارش", f"o_{o['id']}")]]))
        except: pass

async def handle_file(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user; msg = update.message
    file = msg.document or (msg.photo[-1] if msg.photo else None) or msg.video or msg.voice
    if not file: return
    fname = getattr(file, 'file_name', None) or f"file_{file.file_id[:8]}"
    tf = await ctx.bot.get_file(file.file_id)
    ext = Path(tf.file_path or "").suffix or (".jpg" if msg.photo else "")
    if not Path(fname).suffix: fname = f"{fname}{ext}"

    # ── ارسال رسید پرداخت ──
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
                kb = InlineKeyboardMarkup([[btn("✅ تأیید", f"payappr_{pid}"), btn("❌ رد", f"payrej_{pid}")]])
                cap = (f"🧾 رسید پرداخت\nسفارش #{o['id']} — {o['category_name']}\n"
                       f"نوع: {'پیش‌پرداخت' if p['payment_type'] == 'advance' else 'پرداخت نهایی'}\n"
                       f"مبلغ: {p['amount']:,} تومان\nاز: {esc(u.first_name or u.id)}")
                if msg.photo:
                    await ctx.bot.send_photo(aid, file.file_id, caption=cap, reply_markup=kb)
                else:
                    await ctx.bot.send_document(aid, file.file_id, caption=cap, reply_markup=kb)
            except: pass
        return

    # ── فایل مدارک سفارش ──
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
           f"🧾 پس از واریز، روی «ارسال رسید پرداخت» بزنید و عکس رسید را بفرستید.")
    kb = InlineKeyboardMarkup([
        [btn("🧾 ارسال رسید پرداخت", f"send_receipt_{oid}")],
        [btn("📋 جزئیات سفارش", f"co_{oid}")]])
    await ctx.bot.send_message(u.id, txt, reply_markup=kb)

# ── سفارش‌های من ──
def orders_list_markup(uid_):
    oo = order_all(uid_=uid_)
    limit = int(settings_get().get("customer_orders_display_limit", 8) or 8)
    shown = oo[:limit]
    if not oo: return "📂 سفارشی نداری.", None
    lines = [f"📂 سفارش‌های شما ({len(shown)} از {len(oo)}):", ""]
    for o in shown:
        lines.append(f"▸ #{o['id']} — {o['category_name']} | {STATUS_LABELS.get(o['status'], o['status'])} | {o['price']:,} تومان")
    if len(oo) > limit: lines.append(f"\nو {len(oo) - limit} سفارش دیگر…")
    rows = [[btn(f"#{o['id']} — {o['category_name']}", f"co_{o['id']}")] for o in shown]
    return "\n".join(lines), InlineKeyboardMarkup(rows)

async def my_orders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    txt, kb = orders_list_markup(u.id)
    await update.message.reply_text(txt, reply_markup=kb)

def cust_order_text(o):
    lines = [f"📦 سفارش #{o['id']} — {STATUS_LABELS.get(o['status'], o['status'])}", "",
             f"🗂 {o['category_name']}",
             f"💰 قیمت: {o['price']:,} تومان",
             f"💳 پیش‌پرداخت: {o['advance_amount']:,} تومان",
             f"💵 پرداخت نهایی: {o['final_amount']:,} تومان"]
    if o.get("discount"): lines.append(f"🎁 تخفیف: {o['discount']:,} تومان")
    if o.get("description"): lines += ["", f"📝 {o['description']}"]
    fs = file_get(o["id"]); ps = pay_by_order(o["id"])
    lines += ["", f"📎 فایل‌ها: {len(fs)}"]
    if ps:
        lines.append("💳 پرداخت‌ها:")
        for p in ps:
            lines.append(f"   • {'پیش‌پرداخت' if p['payment_type'] == 'advance' else 'نهایی'}: {p['amount']:,} تومان — {PAY_LABEL.get(p['status'], p['status'])}")
    return "\n".join(lines)

async def cust_order_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); u = q.from_user
    d = q.data
    if d == "co_list":
        txt, kb = orders_list_markup(u.id)
        await q.edit_message_text(txt, reply_markup=kb)
        return
    oid = d.replace("co_", "")
    o = order_get(oid)
    if not o or o["user_id"] != u.id:
        await q.answer("سفارش یافت نشد", show_alert=True); return
    rows = []
    if o["status"] in ("pending", "completed"):
        rows.append([btn("💳 اطلاعات پرداخت", f"payinfo_{oid}")])
    rows.append([btn("↩️ بازگشت به لیست", "co_list")])
    await q.edit_message_text(cust_order_text(o), reply_markup=InlineKeyboardMarkup(rows))

async def payinfo_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); u = q.from_user
    oid = q.data.replace("payinfo_", "")
    o = order_get(oid)
    if not o or o["user_id"] != u.id:
        await q.answer("سفارش یافت نشد", show_alert=True); return
    p = get_or_create_payment(o)
    txt = (f"💳 سفارش #{oid}\n"
           f"💰 مبلغ قابل پرداخت: {p['amount']:,} تومان\n\n"
           f"🏦 اطلاعات پرداخت:\n{bank_info_text()}\n\n"
           f"🧾 پس از واریز، «ارسال رسید» را بزنید و عکس رسید را بفرستید.")
    kb = InlineKeyboardMarkup([
        [btn("🧾 ارسال رسید پرداخت", f"send_receipt_{oid}")],
        [btn("↩️ بازگشت", f"co_{oid}")]])
    await q.edit_message_text(txt, reply_markup=kb)

# ── باشگاه، کیف پول، پشتیبانی ──
async def club(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user; ref = ref_get_by_user(u.id) or ref_create(u.id); s = settings_get()
    link = referral_link(ref["code"])
    txt = (f"👥 باشگاه مشتریان\n\n"
           f"🔗 لینک معرف شما:\n<code>{link or ref['code']}</code>\n"
           f"👤 تعداد دعوت: {ref['invited_count']} نفر\n"
           f"💰 پاداش دریافتی: {ref['total_earned']:,} تومان\n\n"
           f"🎁 پاداش هر دعوت: {s.get('referral_reward', 50000):,} تومان (به کیف پول اضافه می‌شود)\n"
           f"🎫 تخفیف برای دعوت‌شونده: {s.get('referral_discount_percent', 10)}%\n\n"
           f"📋 لینک را برای دوستانتان بفرستید تا عضو شوند.")
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML, reply_markup=main_kb(u.id))

async def wallet_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user; w = wallet_ensure(u.id)
    txt = f"💰 کیف پول\n\nموجودی: {w['balance']:,} تومان\n\n📋 تراکنش‌های اخیر:\n"
    for t in w.get("transactions", [])[-5:][::-1]:
        sign, color = ("+", "🟢") if t["type"] == "credit" else ("-", "🔴")
        txt += f"{color} {sign}{t['amount']:,} — {t.get('description', '')}\n"
    if not w.get("transactions"): txt += "تراکنشی ثبت نشده است."
    await update.message.reply_text(txt, reply_markup=main_kb(u.id))

async def support_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    s = settings_get(); txt = "📞 پشتیبانی:\n"
    if s.get("support_phone"): txt += f"📱 {s['support_phone']}\n"
    if s.get("support_telegram"): txt += f"💬 @{s['support_telegram']}\n"
    if not s.get("support_phone") and not s.get("support_telegram"): txt += "از همین ربات پیام دهید."
    await update.message.reply_text(txt, reply_markup=main_kb(u.id))

# ── ارسال رسید (دکمه کیبورد) ──
async def receipt_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    oo = [o for o in order_all(uid_=u.id) if o["status"] in ("pending", "completed")]
    if not oo:
        await update.message.reply_text("⚠️ سفارشی برای پرداخت ندارید.", reply_markup=main_kb(u.id)); return
    if len(oo) == 1:
        await ask_receipt(update, ctx, oo[0]); return
    kb = [[btn(f"#{o['id']} — {o['category_name']} ({payment_amount(o, payment_stage(o)):,} تومان)", f"send_receipt_{o['id']}")] for o in oo[:8]]
    await update.message.reply_text("🧾 برای کدام سفارش می‌خواهید رسید بفرستید؟", reply_markup=InlineKeyboardMarkup(kb))

async def ask_receipt(update: Update, ctx: ContextTypes.DEFAULT_TYPE, o):
    u = update.effective_user
    p = get_or_create_payment(o)
    user_sessions[u.id] = {"state": "awaiting_receipt", "payment_id": p["id"]}
    await update.message.reply_text(
        f"🧾 سفارش #{o['id']} — مبلغ {p['amount']:,} تومان\n\n"
        f"🏦 اطلاعات پرداخت:\n{bank_info_text()}\n\n"
        f"📸 عکس رسید پرداخت را بفرستید:", reply_markup=main_kb(u.id))

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

# ═══════════════ ADMIN: تأیید/رد رسید ═══════════════
async def receipt_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
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
            _notify_user(o["user_id"], f"✅ رسید پرداخت {p['amount']:,} تومان برای سفارش #{o['id']} تأیید شد. متشکریم!")
        try: await q.edit_message_caption("✅ پرداخت تأیید شد.")
        except Exception: await q.edit_message_text("✅ پرداخت تأیید شد.")
    else:
        pay_update(pid, admin_approved=False, status="failed")
        if o:
            _notify_user(o["user_id"], f"❌ رسید پرداخت سفارش #{o['id']} رد شد. لطفاً با پشتیبانی تماس بگیرید.")
        try: await q.edit_message_caption("❌ پرداخت رد شد.")
        except Exception: await q.edit_message_text("❌ پرداخت رد شد.")

# ═══════════════ ADMIN: منوها ═══════════════
def admin_stats_text():
    d = dashboard()
    return (f"📊 آمار کلی\n\n"
            f"👥 کاربران: {d['total_users']}\n"
            f"📦 کل سفارش‌ها: {d['total_orders']}\n"
            f"   ⏳ در انتظار: {d['pending']}\n"
            f"   🔄 در حال انجام: {d['in_progress']}\n"
            f"   ✅ تکمیل: {d['completed']}\n"
            f"   ❌ لغو: {d['cancelled']}\n"
            f"💰 درآمد تأییدشده: {d['total_earned']:,} تومان\n"
            f"🧾 رسید در انتظار تأیید: {d['pending_receipts']}\n"
            f"🗂 دسته‌بندی فعال: {d['categories']}")

def admin_orders_view(flt="all"):
    oo = order_all(status=None if flt == "all" else flt)
    shown = oo[:8]
    if not shown:
        txt = f"📦 سفارش‌ها ({len(oo)} مورد)\n\nچیزی نیست."
    else:
        lines = [f"📦 سفارش‌ها ({len(oo)} مورد)", ""]
        for o in shown:
            lines.append(f"▸ #{o['id']} — {o['category_name']} | {STATUS_LABELS.get(o['status'], o['status'])} | {o['price']:,} تومان")
        txt = "\n".join(lines)
    rows = [[btn("همه", "of_all"), btn("در انتظار", "of_pending"), btn("پیش‌پرداخت", "of_paid_advance"),
             btn("در حال انجام", "of_in_progress"), btn("تکمیل", "of_completed"), btn("لغو", "of_cancelled")]]
    rows += [[btn(f"#{o['id']} — {o['category_name']}", f"o_{o['id']}")] for o in shown]
    rows.append([btn("↩️ منوی مدیریت", "adm_menu")])
    return txt, InlineKeyboardMarkup(rows)

def admin_order_view(o):
    fs = file_get(o["id"]); ps = pay_by_order(o["id"])
    lines = [f"📦 سفارش #{o['id']} — {STATUS_LABELS.get(o['status'], o['status'])}",
             f"👤 {o['first_name'] or '-'} @{o['username'] or '-'} (ID: {o['user_id']})",
             f"🗂 {o['category_name']}",
             f"💰 قیمت: {o['price']:,} | پیش: {o['advance_amount']:,} | نهایی: {o['final_amount']:,}"]
    if o.get("discount"): lines.append(f"🎁 تخفیف: {o['discount']:,}")
    if o.get("description"): lines.append(f"\n📝 {o['description']}")
    if fs:
        lines.append(f"\n📎 فایل‌ها ({len(fs)}):")
        for f in fs: lines.append(f"  • {f['filename']}")
    if ps:
        lines.append("\n💳 پرداخت‌ها:")
        for p in ps:
            lines.append(f"  • {'پیش‌پرداخت' if p['payment_type'] == 'advance' else 'نهایی'}: {p['amount']:,} — {PAY_LABEL.get(p['status'], p['status'])}")
    rows = []
    nxt = STATUS_FLOW.get(o["status"], [])
    if nxt:
        rows.append([btn(FLOW_BTN.get(s, s), f"s_{o['id']}_{s}") for s in nxt])
    if fs:
        rows.append([btn(f"📎 {f['filename'][:20]}", f"f_{f['id']}") for f in fs[:4]])
    for p in ps:
        if p["status"] == "pending":
            row = [btn("✓ تأیید", f"payappr_{p['id']}"), btn("✗ رد", f"payrej_{p['id']}")]
            if p.get("receipt_file_id"): row.append(btn("🧾 رسید", f"r_{p['id']}"))
            rows.append(row)
    rows.append([btn("↩️ لیست سفارش‌ها", "of_all"), btn("🏠 منو", "adm_menu")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)

def admin_pays_view():
    pp = sorted(_db()["payments"], key=lambda x: x["created_at"], reverse=True)
    pending = [p for p in pp if p["status"] == "pending"]
    rest = [p for p in pp if p["status"] != "pending"]
    shown = (pending + rest)[:8]
    lines = [f"💳 پرداخت‌ها ({len(pp)})", ""]
    for p in shown:
        lines.append(f"▸ #{p['order_id']} | {'پیش‌پرداخت' if p['payment_type'] == 'advance' else 'نهایی'} | {p['amount']:,} تومان | {PAY_LABEL.get(p['status'], p['status'])}")
    if not shown: lines.append("پرداختی نیست.")
    rows = []
    for p in pending[:5]:
        row = [btn("✓ تأیید", f"payappr_{p['id']}"), btn("✗ رد", f"payrej_{p['id']}")]
        if p.get("receipt_file_id"): row.append(btn("🧾 رسید", f"r_{p['id']}"))
        rows.append(row)
    rows.append([btn("↩️ منوی مدیریت", "adm_menu")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)

def admin_cats_view():
    cats = cat_all(False)
    lines = ["🗂 دسته‌بندی‌ها", ""]
    for i, cc in enumerate(cats, 1):
        st = "✅" if cc.get("active", True) else "⛔️"
        lines.append(f"{i}. {st} {cc['name']} — {cc['price']:,} تومان — پیش {cc.get('advance_percent', 50)}٪")
    if not cats: lines.append("دسته‌ای تعریف نشده.")
    rows = [[btn("💰 قیمت", f"cp_{cc['id']}"), btn("٪ پیش‌پرداخت", f"ca_{cc['id']}"), btn("فعال/غیرفعال", f"cd_{cc['id']}")] for cc in cats]
    rows.append([btn("＋ افزودن دسته", "adm_catadd"), btn("↩️ منوی مدیریت", "adm_menu")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)

def admin_settings_view():
    s = settings_get()
    adv = "✅ روشن" if s.get("advance_enabled", True) else "❌ خاموش"
    lines = [
        "⚙️ تنظیمات", "",
        f"💳 پیش‌پرداخت: {adv} — {s.get('advance_percent', 50)}٪",
        f"📄 تعداد سفارش نمایش به مشتری: {s.get('customer_orders_display_limit', 8)}",
        f"💳 شماره کارت: {s.get('bank_card_number') or '—'}",
        f"👤 نام صاحب کارت: {s.get('bank_card_holder') or '—'}",
        f"🏦 بانک: {s.get('bank_name') or '—'}",
        f"📝 توضیح پرداخت: {s.get('bank_note') or '—'}",
        f"🎁 پاداش هر دعوت: {s.get('referral_reward', 50000):,} تومان",
        f"🎫 تخفیف دعوت‌شونده: {s.get('referral_discount_percent', 10)}٪",
        f"📱 پشتیبانی: {s.get('support_phone') or '—'} / @{s.get('support_telegram') or '—'}",
    ]
    rows = [
        [btn("💳 روشن/خاموش پیش‌پرداخت", "sa_adv"), btn("درصد پیش", "se_advpct"), btn("تعداد نمایش", "se_limit")],
        [btn("شماره کارت", "se_card"), btn("نام صاحب", "se_holder"), btn("بانک", "se_bank")],
        [btn("توضیح پرداخت", "se_note"), btn("پاداش دعوت", "se_reward"), btn("تخفیف", "se_refdisc")],
        [btn("تلفن", "se_phone"), btn("تلگرام", "se_tg")],
        [btn("↩️ منوی مدیریت", "adm_menu")],
    ]
    return "\n".join(lines), InlineKeyboardMarkup(rows)

def admin_users_view():
    us = sorted(user_all(), key=lambda x: x.get("joined_at", ""), reverse=True)
    lines = [f"👥 کاربران: {len(us)}", ""]
    for uu in us[:15]:
        lines.append(f"• {uu.get('first_name') or '—'} @{uu.get('username') or '—'} (ID: {uu['id']}) — {uu.get('joined_at', '')[:10]}")
    if not us: lines.append("کاربری ثبت نشده.")
    rows = [[btn("↩️ منوی مدیریت", "adm_menu")]]
    return "\n".join(lines), InlineKeyboardMarkup(rows)

def admin_notifs_view():
    rows = [[btn(f"{STATUS_LABELS[s]}", f"nt_{s}")] for s in STATUS_LABELS]
    rows.append([btn("↩️ منوی مدیریت", "adm_menu")])
    return "🔔 اعلان‌های وضعیت سفارش\nبرای هر وضعیت، پیام مشتری و ادمین را ویرایش کنید:", InlineKeyboardMarkup(rows)

# ═══════════════ ADMIN: دریافت متن (state machine) ═══════════════
SETTING_STATE_KEY = {
    "set_advpct": "advance_percent", "set_limit": "customer_orders_display_limit",
    "set_card": "bank_card_number", "set_holder": "bank_card_holder", "set_bank": "bank_name",
    "set_note": "bank_note", "set_reward": "referral_reward", "set_refdisc": "referral_discount_percent",
    "set_phone": "support_phone", "set_tg": "support_telegram",
}
SETTING_NUMERIC = {"set_advpct", "set_limit", "set_reward", "set_refdisc"}

async def admin_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    st = admin_sessions.get(u.id)
    if not st: return False
    t = update.message.text.strip()
    state = st["state"]

    if state == "bc_text":
        admin_sessions.pop(u.id, None)
        n = len(user_all())
        _broadcast(t)
        await update.message.reply_text(f"📣 پیام همگانی به {n} کاربر در حال ارسال است.")
        return True

    if state == "cat_add_name":
        st["data"]["name"] = t; st["state"] = "cat_add_desc"
        await update.message.reply_text("📝 توضیحات دسته را بفرستید (یا «-» برای بدون توضیح):")
        return True
    if state == "cat_add_desc":
        st["data"]["desc"] = "" if t == "-" else t; st["state"] = "cat_add_price"
        await update.message.reply_text("💰 قیمت را به تومان بفرستید (مثلاً 500000):")
        return True
    if state == "cat_add_price":
        v = to_int(t)
        if v is None:
            await update.message.reply_text("⚠️ لطفاً عدد بفرستید."); return True
        st["data"]["price"] = v; st["state"] = "cat_add_pct"
        await update.message.reply_text(f"٪ درصد پیش‌پرداخت را بفرستید (یا «-» برای پیش‌فرض {settings_get().get('advance_percent', 50)}):")
        return True
    if state == "cat_add_pct":
        v = to_int(t) if t != "-" else None
        d = st["data"]
        cat_add(d["name"], d.get("desc", ""), d["price"], v)
        admin_sessions.pop(u.id, None)
        txt, kb = admin_cats_view()
        await update.message.reply_text(f"✅ دسته «{d['name']}» اضافه شد.")
        await update.message.reply_text(txt, reply_markup=kb)
        return True

    if state == "cat_price":
        v = to_int(t)
        if v is None:
            await update.message.reply_text("⚠️ لطفاً عدد بفرستید."); return True
        cc = cat_update(st["cid"], price=v)
        admin_sessions.pop(u.id, None)
        await update.message.reply_text(f"✅ قیمت «{cc['name']}» شد {v:,} تومان.")
        txt, kb = admin_cats_view()
        await update.message.reply_text(txt, reply_markup=kb)
        return True
    if state == "cat_pct":
        v = to_int(t)
        if v is None or v < 0 or v > 100:
            await update.message.reply_text("⚠️ عدد بین 0 تا 100 بفرستید."); return True
        cc = cat_update(st["cid"], advance_percent=v)
        admin_sessions.pop(u.id, None)
        await update.message.reply_text(f"✅ درصد پیش‌پرداخت «{cc['name']}» شد {v}٪.")
        txt, kb = admin_cats_view()
        await update.message.reply_text(txt, reply_markup=kb)
        return True

    if state in SETTING_STATE_KEY:
        key = SETTING_STATE_KEY[state]
        if state in SETTING_NUMERIC:
            v = to_int(t)
            if v is None:
                await update.message.reply_text("⚠️ لطفاً عدد بفرستید."); return True
        else:
            v = t.lstrip("@") if key == "support_telegram" else t
        settings_update(**{key: v})
        admin_sessions.pop(u.id, None)
        await update.message.reply_text(f"✅ ذخیره شد.")
        txt, kb = admin_settings_view()
        await update.message.reply_text(txt, reply_markup=kb)
        return True

    if state in ("notif_user", "notif_admin"):
        status = st["status"]
        db = _db()
        n = db["notifications"].get(status, {"to_user": "", "to_admin": ""})
        n["to_user" if state == "notif_user" else "to_admin"] = t
        db["notifications"][status] = n
        _save(db)
        admin_sessions.pop(u.id, None)
        await update.message.reply_text(f"✅ پیام «{STATUS_LABELS[status]}» ذخیره شد.")
        return True

    return False

# ═══════════════ ADMIN: کال‌بک‌ها ═══════════════
async def admin_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; d = q.data
    u = q.from_user
    if u.id not in ADMIN_IDS:
        await q.answer("⛔️ فقط مدیر", show_alert=True); return

    if d == "adm_menu":
        await q.answer(); await q.edit_message_text("⚙️ پنل مدیریت", reply_markup=admin_menu_kb()); return
    if d == "adm_close":
        try: await q.message.delete()
        except Exception:
            await q.answer(); await q.edit_message_text("بسته شد.")
        return
    if d == "adm_stats":
        await q.answer()
        await q.edit_message_text(admin_stats_text(), reply_markup=InlineKeyboardMarkup([[btn("↩️ منوی مدیریت", "adm_menu")]])); return
    if d == "adm_orders":
        await q.answer(); txt, kb = admin_orders_view("all"); await q.edit_message_text(txt, reply_markup=kb); return
    if d.startswith("of_"):
        await q.answer(); txt, kb = admin_orders_view(d[3:]); await q.edit_message_text(txt, reply_markup=kb); return
    if d == "adm_pays":
        await q.answer(); txt, kb = admin_pays_view(); await q.edit_message_text(txt, reply_markup=kb); return
    if d == "adm_cats":
        await q.answer(); txt, kb = admin_cats_view(); await q.edit_message_text(txt, reply_markup=kb); return
    if d == "adm_set":
        await q.answer(); txt, kb = admin_settings_view(); await q.edit_message_text(txt, reply_markup=kb); return
    if d == "adm_users":
        await q.answer(); txt, kb = admin_users_view(); await q.edit_message_text(txt, reply_markup=kb); return
    if d == "adm_notifs":
        await q.answer(); txt, kb = admin_notifs_view(); await q.edit_message_text(txt, reply_markup=kb); return
    if d == "adm_bc":
        await q.answer()
        admin_sessions[u.id] = {"state": "bc_text"}
        await q.edit_message_text("📣 متن پیام همگانی را بفرستید (یا /cancel):"); return
    if d == "adm_catadd":
        await q.answer()
        admin_sessions[u.id] = {"state": "cat_add_name", "data": {}}
        await q.edit_message_text("➕ نام دسته جدید را بفرستید (یا /cancel):"); return

    if d.startswith("cp_") or d.startswith("ca_"):
        await q.answer()
        cid = d[3:]; cc = cat_get(cid)
        if not cc: return
        if d.startswith("cp_"):
            admin_sessions[u.id] = {"state": "cat_price", "cid": cid}
            await q.edit_message_text(f"💰 قیمت جدید «{cc['name']}» را به تومان بفرستید (یا /cancel):")
        else:
            admin_sessions[u.id] = {"state": "cat_pct", "cid": cid}
            await q.edit_message_text(f"٪ درصد پیش‌پرداخت جدید «{cc['name']}» را بفرستید (یا /cancel):")
        return
    if d.startswith("cd_"):
        cid = d[3:]; cc = cat_get(cid)
        if cc:
            cat_update(cid, active=not cc.get("active", True))
        await q.answer()
        txt, kb = admin_cats_view(); await q.edit_message_text(txt, reply_markup=kb); return

    if d == "sa_adv":
        s = settings_get()
        settings_update(advance_enabled=not s.get("advance_enabled", True))
        await q.answer("✅")
        txt, kb = admin_settings_view(); await q.edit_message_text(txt, reply_markup=kb); return
    if d.startswith("se_"):
        await q.answer()
        prompts = {
            "se_advpct": ("set_advpct", "٪ درصد پیش‌پرداخت (0 تا 100) را بفرستید:"),
            "se_limit": ("set_limit", "تعداد سفارش‌های نمایش به مشتری را بفرستید:"),
            "se_card": ("set_card", "شماره کارت را بفرستید:"),
            "se_holder": ("set_holder", "نام صاحب کارت را بفرستید:"),
            "se_bank": ("set_bank", "نام بانک را بفرستید:"),
            "se_note": ("set_note", "توضیح پرداخت را بفرستید (یا «-»):"),
            "se_reward": ("set_reward", "مبلغ پاداش هر دعوت (تومان) را بفرستید:"),
            "se_refdisc": ("set_refdisc", "٪ تخفیف دعوت‌شونده (0 تا 100) را بفرستید:"),
            "se_phone": ("set_phone", "تلفن پشتیبانی را بفرستید (یا «-»):"),
            "se_tg": ("set_tg", "تلگرام پشتیبانی بدون @ را بفرستید (یا «-»):"),
        }
        state, prompt = prompts.get(d, (None, None))
        if state:
            admin_sessions[u.id] = {"state": state}
            await q.edit_message_text(prompt + "\n(یا /cancel)")
        return

    if d.startswith("nt_"):
        await q.answer()
        status = d[3:]
        n = notif_get().get(status, {"to_user": "", "to_admin": ""})
        txt = (f"🔔 اعلان «{STATUS_LABELS.get(status, status)}»\n\n"
               f"👤 به مشتری:\n{n.get('to_user') or '—'}\n\n"
               f"👨‍💼 به ادمین:\n{n.get('to_admin') or '—'}")
        kb = InlineKeyboardMarkup([
            [btn("✏️ ویرایش پیام مشتری", f"nu_{status}"), btn("✏️ ویرایش پیام ادمین", f"na_{status}")],
            [btn("↩️ لیست اعلان‌ها", "adm_notifs")]])
        await q.edit_message_text(txt, reply_markup=kb); return
    if d.startswith("nu_") or d.startswith("na_"):
        await q.answer()
        status = d[3:]
        admin_sessions[u.id] = {"state": "notif_user" if d.startswith("nu_") else "notif_admin", "status": status}
        await q.edit_message_text(f"✏️ پیام جدید برای «{STATUS_LABELS.get(status, status)}» را بفرستید (یا /cancel):"); return

async def o_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.from_user.id not in ADMIN_IDS:
        await q.answer("⛔️ فقط مدیر", show_alert=True); return
    oid = q.data[2:]
    o = order_get(oid)
    if not o: await q.edit_message_text("سفارش یافت نشد."); return
    txt, kb = admin_order_view(o)
    await q.edit_message_text(txt, reply_markup=kb)

async def s_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.from_user.id not in ADMIN_IDS:
        await q.answer("⛔️ فقط مدیر", show_alert=True); return
    parts = q.data.split("_", 2)
    oid, ns = parts[1], parts[2]
    if ns not in STATUS_LABELS: return
    o = order_update(oid, status=ns)
    if o:
        txt = notif_get().get(ns, {}).get("to_user", "")
        if txt: _notify_user(o["user_id"], txt)
        if ns == "completed":
            p = get_or_create_payment(o)
            msg = (f"🎉 پروژه #{o['id']} تکمیل شد!\n"
                   f"💰 مبلغ نهایی قابل پرداخت: {p['amount']:,} تومان\n\n"
                   f"🏦 اطلاعات پرداخت:\n{bank_info_text()}\n\n"
                   f"🧾 پس از واریز روی «ارسال رسید پرداخت» بزنید و عکس رسید را بفرستید.")
            _notify_user_kb(o["user_id"], msg, [[btn("🧾 ارسال رسید پرداخت", f"send_receipt_{o['id']}")]])
    o2 = order_get(oid)
    if o2:
        txt, kb = admin_order_view(o2)
        await q.edit_message_text(txt, reply_markup=kb)

async def f_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id not in ADMIN_IDS:
        await q.answer("⛔️ فقط مدیر", show_alert=True); return
    fid = q.data[2:]
    fl = next((f for f in _db()["files"] if f["id"] == fid), None)
    if not fl:
        await q.answer("فایل یافت نشد"); return
    await q.answer("📎 در حال ارسال…")
    try:
        if fl.get("telegram_file_id"):
            await ctx.bot.send_document(q.from_user.id, fl["telegram_file_id"], filename=fl["filename"])
        else:
            p = Path(fl["file_path"])
            if p.exists():
                with open(p, "rb") as fh:
                    await ctx.bot.send_document(q.from_user.id, fh, filename=fl["filename"])
    except Exception:
        try: await ctx.bot.send_message(q.from_user.id, "⚠️ خطا در ارسال فایل.")
        except: pass

async def r_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id not in ADMIN_IDS:
        await q.answer("⛔️ فقط مدیر", show_alert=True); return
    pid = q.data[2:]
    p = pay_get(pid)
    if not p or not p.get("receipt_file_id"):
        await q.answer("رسیدی موجود نیست"); return
    await q.answer("🧾 در حال ارسال…")
    fid = p["receipt_file_id"]
    try:
        await ctx.bot.send_photo(q.from_user.id, fid, caption=f"🧾 رسید پرداخت {p['amount']:,} تومان — سفارش #{p['order_id']}")
    except Exception:
        try: await ctx.bot.send_document(q.from_user.id, fid, caption=f"🧾 رسید پرداخت")
        except Exception: pass

# ═══════════════ مسیریاب کال‌بک‌ها ═══════════════
async def callback_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q: return
    d = q.data
    if d.startswith("cat_") or d == "cancel": return await cat_callback(update, ctx)
    if d.startswith("finish_") or d.startswith("skip_"): return await finish_callback(update, ctx)
    if d.startswith("send_receipt_"): return await send_receipt_callback(update, ctx)
    if d.startswith("payappr_") or d.startswith("payrej_"): return await receipt_callback(update, ctx)
    if d.startswith("co_"): return await cust_order_cb(update, ctx)
    if d.startswith("payinfo_"): return await payinfo_cb(update, ctx)
    if d.startswith("o_"): return await o_cb(update, ctx)
    if d.startswith("s_"): return await s_cb(update, ctx)
    if d.startswith("f_"): return await f_cb(update, ctx)
    if d.startswith("r_"): return await r_cb(update, ctx)
    return await admin_cb(update, ctx)

# ═══════════════ NOTIFICATION ═══════════════
def _notify_user(uid_, msg):
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
    flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

async def run_bot():
    seed()
    try:
        r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe", timeout=10)
        if r.json().get("ok"):
            os.environ["BOT_USERNAME"] = r.json()["result"]["username"]
            log.info(f"bot: @{os.environ['BOT_USERNAME']}")
        else:
            log.error(f"getMe failed: {r.json()}")
    except Exception as e:
        log.error(f"getMe error (will retry): {e}")

    while True:
        try:
            app = Application.builder().token(BOT_TOKEN).build()
            app.add_handler(CommandHandler("start", start))
            app.add_handler(CommandHandler("cancel", cancel_cmd))
            app.add_handler(CommandHandler("admin", admin_cmd))
            app.add_handler(CallbackQueryHandler(callback_router))
            app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_all))
            await app.initialize()
            await app.start()
            await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
            log.info("🤖 Bot polling started | همه چیز داخل ربات")
            break
        except Exception as e:
            log.error(f"⚠️ bot polling error (retrying in 10s): {e}")
            try:
                await app.shutdown()
            except: pass
            await asyncio.sleep(10)

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
