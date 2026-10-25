"""
ربات اصلی بیمه - پیاده‌سازی فاز اول کامل + اسکلت فاز ۲ و ۳
"""
import os
import re
import json
import logging
import asyncio
import threading
import uuid
from datetime import datetime
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.constants import ParseMode, ChatAction

from ..config import BOT_TOKEN, ADMIN_IDS, PORT, DEBUG, DEFAULT_MESSAGES, STATUS_LABELS_FA, STATUS_FLOW, RATE_LIMIT_MESSAGES_PER_MINUTE
from ..database import (
    upsert_user, get_user, set_user_phone, get_state, set_state, clear_state,
    list_insurance_types, get_insurance_type, list_fields,
    create_order, get_order, list_orders, count_orders, update_order_status, set_order_field,
    list_faq, get_stats, get_setting, set_setting, init_db, check_rate_limit
)
from ..services.sheets_service import append_order, update_order_status_sheet, append_customer, test_connection
from ..services.ai_service import get_ai
from ..knowledge.retriever import get_kb
from .keyboards import (
    main_reply_keyboard, insurance_types_inline, confirm_inline, edit_fields_inline,
    admin_menu_inline, admin_orders_filter_inline, status_change_inline, contact_request_keyboard, cancel_inline, back_to_main_inline
)
from .states import *

log = logging.getLogger(__name__)
logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO if not DEBUG else logging.DEBUG)

# ---------- Helpers ----------

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def esc(s: str) -> str:
    if not s:
        return ""
    return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def t_now():
    return datetime.now().isoformat()

def validate_field(field: dict, value: str):
    """اعتبارسنجی بر اساس نوع فیلد"""
    ftype = field.get("field_type")
    label = field.get("label")
    if field.get("is_required") and not value.strip():
        return False, f"فیلد «{label}» الزامی است."
    if ftype == "number":
        try:
            v = float(value.replace(",","").replace("،",""))
            if field.get("validation_min") is not None and v < field["validation_min"]:
                return False, f"{label} نباید کمتر از {field['validation_min']} باشد."
            if field.get("validation_max") is not None and v > field["validation_max"]:
                return False, f"{label} نباید بیشتر از {field['validation_max']} باشد."
        except:
            return False, f"لطفاً {label} را به صورت عدد وارد کنید."
    elif ftype == "phone":
        # ایران: 09xxxxxxxxx یا +98...
        cleaned = re.sub(r"\D","", value)
        if not re.match(r"^(09\d{9}|989\d{9}|9\d{9})$", cleaned):
            # اجازه بده شماره با 0 شروع شود ولی هشدار
            if len(cleaned) < 10 or len(cleaned) > 13:
                return False, "شماره تماس معتبر وارد کنید (مثلاً 09123456789)."
    elif ftype == "national_id":
        if not re.match(r"^\d{10}$", value.strip()):
            return False, "کد ملی باید ۱۰ رقم باشد."
    elif ftype == "date":
        # ساده: YYYY/MM/DD یا YYYY-MM-DD یا 1403/05/12
        if not re.match(r"^\d{4}[/-]\d{1,2}[/-]\d{1,2}$", value.strip()):
            return False, "تاریخ را به صورت 1403/05/12 یا 2025-01-01 وارد کنید."
        # می‌توان تاریخ شمسی را هم بررسی کرد ولی ساده می‌گیریم
    elif ftype == "select":
        opts = field.get("options")
        if isinstance(opts, str):
            try:
                opts = json.loads(opts)
            except:
                opts = []
        if opts and value not in opts:
            return False, f"لطفاً یکی از گزینه‌ها را انتخاب کنید: {', '.join(opts)}"
    if field.get("validation_regex"):
        try:
            if not re.match(field["validation_regex"], value):
                return False, f"مقدار {label} معتبر نیست."
        except:
            pass
    return True, ""

def format_order_summary(order_data: dict, fields: list, insurance_name: str) -> str:
    lines = [f"📋 *پیش‌نمایش درخواست* — {esc(insurance_name)}", ""]
    for f in fields:
        key = f["field_key"]
        label = f["label"]
        val = order_data.get(key, "—")
        lines.append(f"• *{esc(label)}*: {esc(str(val))}")
    # اگر فیلدی خارج از فرم بود
    for k, v in order_data.items():
        if k not in [f["field_key"] for f in fields]:
            lines.append(f"• *{esc(k)}*: {esc(str(v))}")
    lines.append("")
    lines.append("لطفاً اطلاعات را بررسی و تایید کنید.")
    return "\n".join(lines)

# ---------- Bot Handlers ----------

async def start_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    # ثبت/به‌روزرسانی کاربر
    upsert_user(u.id, u.username or "", u.first_name or "", u.last_name or "")
    clear_state(u.id)
    # رفرال ساده (اگر با ?start=ref_xxx آمده)
    if ctx.args and ctx.args[0].startswith("ref_"):
        code = ctx.args[0][4:]
        # در فاز ۳ کامل می‌شود
        await update.message.reply_text(f"🎁 با کد معرف {esc(code)} وارد شدید — تخفیف شما لحاظ خواهد شد.")

    welcome = get_setting("welcome_message", DEFAULT_MESSAGES["welcome"])
    if "{name}" in welcome:
        welcome = welcome.format(name=esc(u.first_name or "عزیز"))
    await update.message.reply_text(
        welcome,
        reply_markup=main_reply_keyboard(is_admin(u.id)),
        parse_mode=ParseMode.MARKDOWN
    )
    # اگر شیت فعال باشد، مشتری را ثبت کن (غیرهمزمان)
    try:
        user = get_user(u.id)
        threading.Thread(target=lambda: append_customer(user), daemon=True).start()
    except:
        pass

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        DEFAULT_MESSAGES["help"],
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_reply_keyboard(is_admin(update.effective_user.id))
    )

async def cancel_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    clear_state(update.effective_user.id)
    await update.message.reply_text("✅ عملیات لغو شد.", reply_markup=main_reply_keyboard(is_admin(update.effective_user.id)))

# --- منوی اصلی (پیام متنی) ---

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    uid = update.effective_user.id
    upsert_user(uid, update.effective_user.username or "", update.effective_user.first_name or "")

    # ضد اسپم
    if not check_rate_limit(uid, RATE_LIMIT_MESSAGES_PER_MINUTE):
        await update.message.reply_text("⏳ لطفاً کمی صبر کنید و دوباره تلاش کنید.")
        return

    # اگر در حالت handoff هستیم، فقط ادمین می‌تواند پاسخ دهد - کاربر عادی پیامش برای کارشناس ذخیره شود
    st = get_state(uid)
    if st.get("state") == STATE_HANDOFF:
        await update.message.reply_text(
            "💬 پیام شما برای کارشناس ثبت شد. به زودی با شما تماس می‌گیریم.\n"
            "برای بازگشت به حالت خودکار، /cancel را بزنید.",
            reply_markup=main_reply_keyboard(is_admin(uid))
        )
        # اطلاع به ادمین‌ها
        for aid in ADMIN_IDS:
            try:
                await ctx.bot.send_message(aid, f"💬 پیام از کاربر {esc(update.effective_user.first_name)} (ID:{uid}) در حالت کارشناسی:\n\n{esc(text)}")
            except:
                pass
        return

    # اگر در حال پر کردن فرم هستیم
    if st.get("state") == STATE_ORDER_FILLING:
        await handle_order_field_input(update, ctx)
        return
    if st.get("state") == STATE_ORDER_EDIT_FIELD:
        await handle_edit_field_input(update, ctx)
        return
    if st.get("state") == STATE_AWAITING_CONSULT:
        await handle_consultation(update, ctx)
        return
    if st.get("state") == STATE_ADMIN_SEARCH:
        await handle_admin_search_input(update, ctx)
        return
    if st.get("state") == STATE_ADMIN_BROADCAST:
        await handle_admin_broadcast_input(update, ctx)
        return
    if st.get("state") == STATE_ADMIN_ADD_TYPE:
        await handle_admin_add_type_input(update, ctx)
        return

    # منوی اصلی
    if text == "💬 مشاوره هوشمند":
        await start_consultation(update, ctx)
    elif text == "📋 استعلام / ثبت درخواست":
        await start_order_flow(update, ctx)
    elif text == "📂 پیگیری درخواست‌ها":
        await list_my_orders(update, ctx)
    elif text == "👤 ارتباط با کارشناس":
        await request_handoff(update, ctx)
    elif text == "ℹ️ راهنما":
        await help_cmd(update, ctx)
    elif text == "💰 باشگاه مشتریان":
        await club_handler(update, ctx)
    elif text == "⚙️ پنل مدیریت":
        if is_admin(uid):
            await admin_menu(update, ctx)
        else:
            await update.message.reply_text("⛔️ دسترسی محدود است.")
    elif text in ("/start", "شروع", "سلام", "درود"):
        await start_cmd(update, ctx)
    else:
        # اگر متن آزاد است، به عنوان مشاوره در نظر بگیر
        # تشخیص نیت با AI
        ai = get_ai()
        intent = ai.detect_intent(text) if ai.is_available() else "other"
        if intent in ("consultation", "order", "handoff"):
            if intent == "handoff":
                await request_handoff(update, ctx)
            elif intent == "order":
                await start_order_flow(update, ctx)
                # همچنین به عنوان سوال ذخیره کن
                await handle_consultation(update, ctx, pre_text=text)
            else:
                await handle_consultation(update, ctx, pre_text=text)
        else:
            # پاسخ RAG مستقیم
            await handle_consultation(update, ctx, pre_text=text)

# ---------- مشاوره هوشمند ----------

async def start_consultation(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    set_state(uid, STATE_AWAITING_CONSULT, {"history": []})
    await update.message.reply_text(
        "💬 *مشاوره هوشمند بیمه*\n\n"
        "سوالت رو بپرس — مثلاً:\n"
        "• «بیمه شخص ثالث چه پوشش‌هایی داره؟»\n"
        "• «مدارک لازم برای بدنه چیه؟»\n"
        "• «تفاوت بیمه عمر و تامین آتیه؟»\n\n"
        "هر وقت خواستی «لغو» بزن تا خارج شی.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=cancel_inline()
    )

async def handle_consultation(update: Update, ctx: ContextTypes.DEFAULT_TYPE, pre_text: str = None):
    text = pre_text or (update.message.text.strip() if update.message else "")
    if not text:
        return
    uid = update.effective_user.id
    if text in ("لغو", "/cancel", "❌ لغو"):
        clear_state(uid)
        await update.message.reply_text("✅ از حالت مشاوره خارج شدید.", reply_markup=main_reply_keyboard(is_admin(uid)))
        return

    # تایپینگ
    await ctx.bot.send_chat_action(uid, ChatAction.TYPING)

    # بازیابی منابع
    kb = get_kb()
    results = kb.search(text, top_k=3)
    contexts = [r["content"] for r in results]
    sources = [r["source"] for r in results]

    # اگر FAQ تاییدشده داریم، اول آن را چک کن (اولویت)
    faqs = list_faq(approved_only=True)
    faq_match = None
    for f in faqs:
        # ساده: اگر 2 کلمه مشترک
        q_words = set(text.split())
        f_words = set(f["question"].split())
        if len(q_words & f_words) >= 2:
            faq_match = f
            break
    if faq_match:
        await update.message.reply_text(
            f"✅ *پاسخ تاییدشده:*\n\n{esc(faq_match['answer'])}\n\n—\nمنبع: FAQ تاییدشده",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 ثبت درخواست مرتبط", callback_data="quick_order")],
                [InlineKeyboardButton("👤 صحبت با کارشناس", callback_data="handoff")],
            ])
        )
        return

    ai = get_ai()
    st = get_state(uid)
    history = (st.get("data") or {}).get("history", [])

    if not ai.is_available():
        # بدون AI: فقط منابع را نشان بده
        if contexts:
            resp = f"📚 بر اساس منابع تاییدشده:\n\n{esc(contexts[0][:800])}\n\n—\nمنبع: {esc(sources[0])}\n\n"
            resp += "برای اطلاعات دقیق‌تر، «ارتباط با کارشناس» را بزنید یا درخواست ثبت کنید."
            await update.message.reply_text(resp, reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 ثبت درخواست", callback_data="quick_order")],
                [InlineKeyboardButton("👤 کارشناس", callback_data="handoff")]
            ]))
        else:
            await update.message.reply_text(
                DEFAULT_MESSAGES["ai_unavailable"] + "\n\nمی‌تونم درخواست‌تون رو ثبت کنم یا به کارشناس وصل‌تون کنم.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 ثبت درخواست", callback_data="quick_order")],
                    [InlineKeyboardButton("👤 کارشناس", callback_data="handoff")]
                ])
            )
        return

    # با AI
    result = ai.answer_with_context(text, contexts, chat_history=history)
    answer = result.get("answer")
    if not answer:
        await update.message.reply_text(
            DEFAULT_MESSAGES["ai_unavailable"],
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👤 کارشناس", callback_data="handoff")]])
        )
        return

    # ذخیره تاریخچه
    history.append({"role": "user", "content": text})
    history.append({"role": "assistant", "content": answer})
    # محدود به 8 پیام
    if len(history) > 8:
        history = history[-8:]
    set_state(uid, STATE_AWAITING_CONSULT, {"history": history})

    # افزودن منبع اگر دارد
    footer = ""
    if result.get("has_source") and sources:
        footer = f"\n\n—\n📎 منبع: {esc(sources[0])}"
    elif result.get("should_handoff"):
        footer = "\n\nبرای بررسی دقیق‌تر می‌تونم شما رو به کارشناس وصل کنم."

    kb_buttons = []
    if result.get("should_handoff"):
        kb_buttons.append([InlineKeyboardButton("👤 ارتباط با کارشناس", callback_data="handoff")])
    kb_buttons.append([InlineKeyboardButton("📋 ثبت درخواست", callback_data="quick_order")])
    kb_buttons.append([InlineKeyboardButton("❌ پایان مشاوره", callback_data="end_consult")])

    await update.message.reply_text(
        esc(answer) + footer,
        reply_markup=InlineKeyboardMarkup(kb_buttons)
    )

# ---------- ثبت سفارش (فرم پویا) ----------

async def start_order_flow(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    types = list_insurance_types(active_only=True)
    if not types:
        await update.message.reply_text("⚠️ در حال حاضر نوع بیمه‌ای تعریف نشده. لطفاً با کارشناس تماس بگیرید.")
        return
    uid = update.effective_user.id
    set_state(uid, STATE_ORDER_SELECT_TYPE, {})
    # اگر از طریق دکمه آمده، پیام را ویرایش کن در غیر اینصورت ارسال جدید
    text = "📌 لطفاً نوع بیمه موردنظر رو انتخاب کنید:"
    kb = insurance_types_inline(types)
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=kb)
            return
        except:
            pass
    await update.message.reply_text(text, reply_markup=kb)

async def handle_insurance_type_selected(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    type_id = q.data.replace("ins_", "")
    itype = get_insurance_type(type_id)
    if not itype:
        await q.edit_message_text("⚠️ نوع بیمه یافت نشد.")
        return
    uid = q.from_user.id
    fields = list_fields(type_id)
    if not fields:
        # اگر فیلدی تعریف نشده، یک فرم ساده بساز
        await q.edit_message_text(f"📌 {esc(itype['name'])}\n\n{esc(itype.get('intro_message') or 'لطفاً توضیح درخواست خود را بنویسید:')}")
        set_state(uid, STATE_ORDER_FILLING, {"insurance_type_id": type_id, "fields": [], "data": {}, "idx": 0, "insurance_name": itype["name"]})
        # مستقیم به تایید برو با یک فیلد توضیحات
        set_state(uid, STATE_ORDER_FILLING, {"insurance_type_id": type_id, "fields": [{"field_key":"description","label":"توضیح درخواست","field_type":"text","is_required":True}], "data": {}, "idx":0, "insurance_name": itype["name"]})
        await ctx.bot.send_message(uid, f"✏️ *{esc('توضیح درخواست')}*:\n{esc('لطفاً درخواست خود را به صورت متن آزاد بنویسید:')}", parse_mode=ParseMode.MARKDOWN, reply_markup=cancel_inline())
        return

    intro = itype.get("intro_message") or f"برای {itype['name']} اطلاعات زیر را وارد کنید."
    await q.edit_message_text(f"📌 *{esc(itype['name'])}*\n\n{esc(intro)}", parse_mode=ParseMode.MARKDOWN)
    # شروع فرم
    set_state(uid, STATE_ORDER_FILLING, {
        "insurance_type_id": type_id,
        "insurance_name": itype["name"],
        "fields": fields,
        "data": {},
        "idx": 0
    })
    await ask_next_field(uid, ctx)

async def ask_next_field(uid: int, ctx: ContextTypes.DEFAULT_TYPE):
    st = get_state(uid)
    data = st.get("data") or {}
    fields = data.get("fields", [])
    idx = data.get("idx", 0)
    if idx >= len(fields):
        # همه فیلدها پر شد -> نمایش خلاصه
        await show_order_preview(uid, ctx)
        return
    field = fields[idx]
    label = field["label"]
    help_text = field.get("help_text") or field.get("placeholder") or ""
    ftype = field.get("field_type")
    is_req = " (الزامی)" if field.get("is_required") else " (اختیاری - «رد کردن» بزنید)"
    text = f"✏️ *{esc(label)}*{is_req}\n"
    if help_text:
        text += f"_{esc(help_text)}_\n"
    if ftype == "select" and field.get("options"):
        opts = field["options"]
        if isinstance(opts, str):
            try:
                opts = json.loads(opts)
            except:
                opts = []
        # دکمه برای انتخاب
        rows = [[InlineKeyboardButton(str(o), callback_data=f"fieldopt_{o}")] for o in opts]
        rows.append([InlineKeyboardButton("⏭ رد کردن", callback_data="field_skip")] if not field.get("is_required") else [])
        rows.append([InlineKeyboardButton("❌ لغو", callback_data="order_cancel")])
        # حذف خالی
        rows = [r for r in rows if r]
        await ctx.bot.send_message(uid, text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(rows))
    elif ftype == "phone":
        await ctx.bot.send_message(uid, text + "\n📱 می‌تونید دکمه زیر رو بزنید:", parse_mode=ParseMode.MARKDOWN, reply_markup=contact_request_keyboard())
    else:
        suffix = "\n\nبرای رد کردن (در صورت اختیاری) کلمه «رد» را بفرستید."
        await ctx.bot.send_message(uid, text + suffix, parse_mode=ParseMode.MARKDOWN, reply_markup=cancel_inline())

async def handle_order_field_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip() if update.message.text else ""
    # اگر شماره تماس share شد
    if update.message.contact:
        text = update.message.contact.phone_number

    if text in ("لغو", "/cancel", "❌ لغو", "لغو درخواست"):
        clear_state(uid)
        await update.message.reply_text("❌ درخواست لغو شد.", reply_markup=main_reply_keyboard(is_admin(uid)))
        return
    if text in ("رد", "رد کردن", "⏭ رد کردن", "-"):
        text = ""

    st = get_state(uid)
    data = st.get("data") or {}
    fields = data.get("fields", [])
    idx = data.get("idx", 0)
    if idx >= len(fields):
        return
    field = fields[idx]

    # اگر خالی و اختیاری
    if not text and not field.get("is_required"):
        data["idx"] = idx + 1
        set_state(uid, STATE_ORDER_FILLING, data)
        await ask_next_field(uid, ctx)
        return
    if not text and field.get("is_required"):
        await update.message.reply_text(f"⚠️ فیلد «{esc(field['label'])}» الزامی است. لطفاً مقدار را وارد کنید.")
        return

    # اعتبارسنجی
    ok, err = validate_field(field, text)
    if not ok:
        await update.message.reply_text(f"⚠️ {esc(err)}\nلطفاً دوباره وارد کنید:")
        return

    # ذخیره
    data["data"][field["field_key"]] = text
    # اگر فیلد phone بود، در پروفایل هم ذخیره کن
    if field["field_type"] == "phone":
        set_user_phone(uid, text)
    data["idx"] = idx + 1
    set_state(uid, STATE_ORDER_FILLING, data)
    await ask_next_field(uid, ctx)

async def handle_field_option(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data_val = q.data
    uid = q.from_user.id
    if data_val == "field_skip":
        st = get_state(uid)
        d = st.get("data") or {}
        idx = d.get("idx", 0)
        d["idx"] = idx + 1
        set_state(uid, STATE_ORDER_FILLING, d)
        try:
            await q.edit_message_text("⏭ رد شد.")
        except:
            pass
        await ask_next_field(uid, ctx)
        return
    if data_val.startswith("fieldopt_"):
        val = data_val.replace("fieldopt_", "")
        st = get_state(uid)
        d = st.get("data") or {}
        fields = d.get("fields", [])
        idx = d.get("idx", 0)
        if idx < len(fields):
            field = fields[idx]
            d["data"][field["field_key"]] = val
            d["idx"] = idx + 1
            set_state(uid, STATE_ORDER_FILLING, d)
            try:
                await q.edit_message_text(f"✅ انتخاب شد: {esc(val)}")
            except:
                pass
            await ask_next_field(uid, ctx)
        return

async def show_order_preview(uid: int, ctx: ContextTypes.DEFAULT_TYPE):
    st = get_state(uid)
    data = st.get("data") or {}
    fields = data.get("fields", [])
    order_data = data.get("data", {})
    insurance_name = data.get("insurance_name", "")
    text = format_order_summary(order_data, fields, insurance_name)
    set_state(uid, STATE_ORDER_CONFIRM, data)
    await ctx.bot.send_message(uid, text, parse_mode=ParseMode.MARKDOWN, reply_markup=confirm_inline())

async def handle_order_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    st = get_state(uid)
    if st.get("state") != STATE_ORDER_CONFIRM:
        await q.edit_message_text("⚠️ درخواستی برای تایید یافت نشد.")
        return
    data = st.get("data") or {}
    insurance_type_id = data.get("insurance_type_id")
    insurance_name = data.get("insurance_name")
    order_data = data.get("data", {})

    # جلوگیری از ثبت تکراری (idempotency): اگر همین کاربر در 30 ثانیه اخیر همین داده را ثبت کرده
    # ساده: چک بر اساس هش داده + زمان
    # اینجا با یک کلید ساده در state
    import hashlib
    h = hashlib.md5(json.dumps(order_data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    last_hash = data.get("last_submit_hash")
    last_time = data.get("last_submit_time")
    now_ts = datetime.now().timestamp()
    if last_hash == h and last_time and (now_ts - last_time) < 30:
        await q.edit_message_text("⚠️ این درخواست قبلاً ثبت شده است. لطفاً صبر کنید.")
        return

    # ایجاد سفارش
    user = get_user(uid)
    contact_name = user.get("first_name") or order_data.get("name") or ""
    contact_phone = user.get("phone") or order_data.get("phone") or ""
    try:
        order_id = create_order(
            telegram_id=uid,
            insurance_type_id=insurance_type_id,
            insurance_type_name=insurance_name,
            data=order_data,
            contact_name=contact_name,
            contact_phone=contact_phone,
            source="telegram_bot"
        )
    except Exception as e:
        log.error("create order failed: %s", e)
        await q.edit_message_text("❌ خطا در ثبت درخواست. لطفاً دوباره تلاش کنید.")
        return

    # ثبت در Google Sheets (غیرهمزمان با retry)
    order = get_order(order_id)
    def _sheets_job():
        append_order(order)
        append_customer(user)
    threading.Thread(target=_sheets_job, daemon=True).start()

    # اعلان به ادمین‌ها
    for aid in ADMIN_IDS:
        try:
            txt = (
                f"📬 *سفارش جدید* `{esc(order_id)}`\n"
                f"👤 {esc(user.get('first_name') or '')} (@{esc(user.get('username') or '-')}) — ID:{uid}\n"
                f"📌 {esc(insurance_name)}\n"
                f"📞 {esc(contact_phone or '—')}\n"
                f"📝 {esc(json.dumps(order_data, ensure_ascii=False)[:500])}"
            )
            await ctx.bot.send_message(
                aid, txt, parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👁 مشاهده", callback_data=f"adm_view_{order_id}")]])
            )
        except Exception as e:
            log.warning("notify admin %s failed: %s", aid, e)

    # پیام موفقیت به کاربر
    itype = get_insurance_type(insurance_type_id)
    success_msg = (itype.get("success_message") if itype else None) or DEFAULT_MESSAGES["order_created"].format(order_id=order_id)
    clear_state(uid)
    await q.edit_message_text(f"✅ {esc(success_msg)}\n\n🔖 شناسه: `{esc(order_id)}`", parse_mode=ParseMode.MARKDOWN)
    await ctx.bot.send_message(
        uid,
        "📂 می‌تونید وضعیت درخواست رو از «پیگیری درخواست‌ها» ببینید.\n"
        "کارشناس به زودی با شما تماس می‌گیرد.",
        reply_markup=main_reply_keyboard(is_admin(uid))
    )

async def handle_order_edit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    st = get_state(uid)
    data = st.get("data") or {}
    fields = data.get("fields", [])
    if not fields:
        await q.edit_message_text("⚠️ قابل ویرایش نیست.")
        return
    await q.edit_message_text("✏️ کدام فیلد را می‌خواهید ویرایش کنید؟", reply_markup=edit_fields_inline(fields))

async def handle_edit_field_select(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    field_key = q.data.replace("editfield_", "")
    uid = q.from_user.id
    st = get_state(uid)
    data = st.get("data") or {}
    fields = data.get("fields", [])
    field = next((f for f in fields if f["field_key"] == field_key), None)
    if not field:
        await q.edit_message_text("⚠️ فیلد یافت نشد.")
        return
    # تنظیم حالت ویرایش
    data["editing_key"] = field_key
    set_state(uid, STATE_ORDER_EDIT_FIELD, data)
    await q.edit_message_text(f"✏️ مقدار جدید «{esc(field['label'])}» را وارد کنید:\n_{esc(field.get('help_text') or '')}_", parse_mode=ParseMode.MARKDOWN, reply_markup=cancel_inline())
    await ctx.bot.send_message(uid, f"مقدار فعلی: {esc(str(data.get('data', {}).get(field_key, '—')))}")

async def handle_edit_field_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip() if update.message.text else ""
    if text in ("لغو", "/cancel"):
        # برگرد به پیش‌نمایش
        st = get_state(uid)
        data = st.get("data") or {}
        data.pop("editing_key", None)
        set_state(uid, STATE_ORDER_CONFIRM, data)
        await show_order_preview(uid, ctx)
        return
    st = get_state(uid)
    data = st.get("data") or {}
    key = data.get("editing_key")
    if not key:
        return
    fields = data.get("fields", [])
    field = next((f for f in fields if f["field_key"] == key), None)
    if not field:
        return
    ok, err = validate_field(field, text)
    if not ok:
        await update.message.reply_text(f"⚠️ {esc(err)}")
        return
    data["data"][key] = text
    data.pop("editing_key", None)
    set_state(uid, STATE_ORDER_CONFIRM, data)
    await update.message.reply_text("✅ ویرایش شد.")
    await show_order_preview(uid, ctx)

async def handle_order_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query if update.callback_query else None
    uid = update.effective_user.id if update.effective_user else q.from_user.id
    clear_state(uid)
    if q:
        await q.answer()
        try:
            await q.edit_message_text("❌ درخواست لغو شد.")
        except:
            await ctx.bot.send_message(uid, "❌ درخواست لغو شد.")
    else:
        await update.message.reply_text("❌ درخواست لغو شد.", reply_markup=main_reply_keyboard(is_admin(uid)))

# ---------- پیگیری سفارش ----------

async def list_my_orders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    orders = list_orders(limit=10, telegram_id=uid)
    if not orders:
        await update.message.reply_text("📂 هنوز درخواستی ثبت نکرده‌اید.", reply_markup=main_reply_keyboard(is_admin(uid)))
        return
    lines = ["📂 *درخواست‌های شما:*", ""]
    rows = []
    for o in orders:
        status_fa = STATUS_LABELS_FA.get(o["status"], o["status"])
        lines.append(f"• `{esc(o['id'])}` — {esc(o['insurance_type_name'])} — {esc(status_fa)} — {o['created_at'][:10]}")
        rows.append([InlineKeyboardButton(f"👁 {o['id']} — {o['insurance_type_name']}", callback_data=f"view_{o['id']}")])
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(rows))

async def view_order_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    oid = q.data.replace("view_", "")
    order = get_order(oid)
    if not order or order["telegram_id"] != q.from_user.id:
        await q.edit_message_text("⚠️ درخواست یافت نشد.")
        return
    await send_order_detail(q, order, is_admin_view=False, ctx=ctx)

async def send_order_detail(query_or_update, order: dict, is_admin_view=False, ctx=None):
    # order: dict از DB
    status_fa = STATUS_LABELS_FA.get(order["status"], order["status"])
    data = order.get("data") or {}
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except:
            data = {}
    lines = [
        f"📦 *سفارش* `{esc(order['id'])}`",
        f"📌 نوع: {esc(order['insurance_type_name'])}",
        f"📊 وضعیت: {esc(status_fa)}",
        f"📅 ثبت: {esc(order['created_at'])}",
        f"👤 نام: {esc(order.get('contact_name') or '—')}",
        f"📞 تماس: {esc(order.get('contact_phone') or '—')}",
        "",
        "*جزئیات:*",
    ]
    for k, v in data.items():
        lines.append(f"• {esc(k)}: {esc(str(v))}")
    if order.get("quoted_price"):
        lines.append(f"\n💰 قیمت اعلام‌شده: {esc(str(order['quoted_price']))} تومان")
    if order.get("discount_code"):
        lines.append(f"🎁 کد تخفیف: {esc(order['discount_code'])}")

    # دکمه‌ها
    if is_admin_view:
        rows = [
            [InlineKeyboardButton("🔄 تغییر وضعیت", callback_data=f"adm_change_{order['id']}")],
            [InlineKeyboardButton("💰 ثبت قیمت", callback_data=f"adm_price_{order['id']}")],
            [InlineKeyboardButton("👤 ارجاع به کارشناس", callback_data=f"adm_assign_{order['id']}")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="adm_orders")],
        ]
    else:
        rows = [
            [InlineKeyboardButton("👤 ارتباط با کارشناس", callback_data="handoff")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="back_orders")],
        ]
    text = "\n".join(lines)
    # اگر query است، edit
    try:
        if hasattr(query_or_update, "edit_message_text"):
            await query_or_update.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(rows))
        else:
            await query_or_update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(rows))
    except Exception as e:
        log.warning("send_order_detail failed: %s", e)

# ---------- ارتباط با کارشناس ----------

async def request_handoff(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    set_state(uid, STATE_HANDOFF, {"requested_at": t_now()})
    # اطلاع به ادمین
    user = get_user(uid)
    for aid in ADMIN_IDS:
        try:
            await ctx.bot.send_message(
                aid,
                f"🚨 *درخواست کارشناس*\n👤 {esc(user.get('first_name') or '')} (@{esc(user.get('username') or '-')}) — ID:{uid}\n"
                f"📞 {esc(user.get('phone') or '—')}\n\n"
                f"برای پاسخ، به همین کاربر پیام دهید یا وضعیت را تغییر دهید.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("👁 مشاهده درخواست‌ها", callback_data=f"adm_orders")],
                    [InlineKeyboardButton("✅ بازگردانی به ربات", callback_data=f"adm_resume_{uid}")]
                ])
            )
        except:
            pass
    # پیام به کاربر
    target = update.message if update.message else (update.callback_query.message if update.callback_query else None)
    text = DEFAULT_MESSAGES["handoff_requested"]
    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text(text)
        except:
            await ctx.bot.send_message(uid, text)
    else:
        await update.message.reply_text(text, reply_markup=main_reply_keyboard(is_admin(uid)))

async def resume_auto_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id):
        await q.answer("⛔️ فقط مدیر", show_alert=True)
        return
    await q.answer()
    uid = int(q.data.replace("adm_resume_", ""))
    clear_state(uid)
    try:
        await ctx.bot.send_message(uid, "✅ کارشناس مکالمه را به حالت خودکار بازگرداند. می‌تونید ادامه بدید.", reply_markup=main_reply_keyboard(is_admin(uid)))
        await q.edit_message_text(f"✅ کاربر {uid} به حالت خودکار برگشت.")
    except Exception as e:
        await q.edit_message_text(f"⚠️ خطا: {e}")

# ---------- باشگاه مشتریان ----------

async def club_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = get_user(uid)
    if not user:
        await update.message.reply_text("⚠️ خطا")
        return
    score = user.get("score", 0)
    level = user.get("level", "bronze")
    code = user.get("referral_code") or f"REF{uid%10000}"
    bot_username = os.getenv("BOT_USERNAME", "")
    link = f"https://t.me/{bot_username}?start=ref_{code}" if bot_username else code
    text = (
        f"💰 *باشگاه مشتریان*\n\n"
        f"👤 امتیاز شما: *{score}*\n"
        f"🏅 سطح: {esc(level)}\n\n"
        f"🔗 کد معرف شما: `{esc(code)}`\n"
        f"📨 لینک دعوت: {esc(link)}\n\n"
        f"با دعوت دوستان و خرید بیمه، امتیاز بگیرید و تخفیف دریافت کنید!\n"
        f"امتیاز فقط پس از تایید خرید توسط کارشناس ثبت می‌شود."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_reply_keyboard(is_admin(uid)))

# ---------- ادمین ----------

async def admin_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id if update.effective_user else update.callback_query.from_user.id
    if not is_admin(uid):
        if update.message:
            await update.message.reply_text("⛔️ دسترسی محدود")
        else:
            await update.callback_query.answer("⛔️ فقط مدیر", show_alert=True)
        return
    text = "⚙️ *پنل مدیریت*\n\nیکی از گزینه‌ها را انتخاب کنید:"
    kb = admin_menu_inline()
    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        except:
            await ctx.bot.send_message(uid, text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

async def admin_dashboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        await q.answer("⛔️ فقط مدیر", show_alert=True)
        return
    stats = get_stats()
    lines = [
        "📊 *داشبورد*",
        "",
        f"📦 کل سفارش‌ها: {stats.get('total_orders',0)}",
        f"👥 کل کاربران: {stats.get('total_users',0)}",
        f"⏳ بدون پیگیری (جدید/در انتظار): {stats.get('pending',0)}",
        "",
        "*بر اساس وضعیت:*",
    ]
    for k, v in stats.items():
        if k.startswith("status_"):
            fa = STATUS_LABELS_FA.get(k.replace("status_",""), k)
            lines.append(f"• {fa}: {v}")
    if stats.get("by_type"):
        lines.append("\n*بر اساس نوع بیمه:*")
        for name, cnt in stats["by_type"].items():
            lines.append(f"• {esc(name)}: {cnt}")
    # نرخ تبدیل
    purchased = stats.get("status_purchased", 0)
    total = stats.get("total_orders", 1)
    rate = round(purchased/total*100,1) if total else 0
    lines.append(f"\n✅ نرخ تبدیل به خرید: {rate}%")
    await q.edit_message_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 منو", callback_data="adm_menu")]]))

async def admin_orders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return
    # پیش‌فرض همه
    await show_admin_orders(q, filter_status="all", page=0, ctx=ctx)

async def show_admin_orders(query, filter_status="all", page=0, ctx=None):
    limit = 8
    offset = page * limit
    orders = list_orders(limit=limit+1, offset=offset, status=None if filter_status=="all" else filter_status)
    has_next = len(orders) > limit
    orders = orders[:limit]
    if not orders:
        text = f"📦 سفارشی با وضعیت «{esc(filter_status)}» یافت نشد."
        rows = [[InlineKeyboardButton("🔙 منو", callback_data="adm_menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))
        return
    lines = [f"📦 *سفارش‌ها* — {esc(filter_status)} (صفحه {page+1})", ""]
    rows = []
    for o in orders:
        fa = STATUS_LABELS_FA.get(o["status"], o["status"])
        lines.append(f"• `{esc(o['id'])}` — {esc(o['insurance_type_name'])} — {esc(fa)} — {o['created_at'][:16]}")
        rows.append([InlineKeyboardButton(f"{o['id']} — {o['insurance_type_name']}", callback_data=f"adm_view_{o['id']}")])
    # فیلتر
    rows.append([InlineKeyboardButton("🔍 فیلتر", callback_data="adm_filter_menu")])
    # صفحه‌بندی
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ قبلی", callback_data=f"adm_orders_page_{filter_status}_{page-1}"))
    if has_next:
        nav.append(InlineKeyboardButton("بعدی ▶️", callback_data=f"adm_orders_page_{filter_status}_{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔙 منو", callback_data="adm_menu")])
    await query.edit_message_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(rows))

async def admin_filter_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("🔍 فیلتر بر اساس وضعیت:", reply_markup=admin_orders_filter_inline())

async def admin_orders_page(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    # data: adm_orders_page_{status}_{page}
    parts = q.data.replace("adm_orders_page_", "").rsplit("_",1)
    status = parts[0]
    page = int(parts[1])
    await show_admin_orders(q, filter_status=status, page=page, ctx=ctx)

async def admin_view_order(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return
    oid = q.data.replace("adm_view_", "")
    order = get_order(oid)
    if not order:
        await q.edit_message_text("⚠️ سفارش یافت نشد.")
        return
    await send_order_detail(q, order, is_admin_view=True, ctx=ctx)

async def admin_change_status_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    oid = q.data.replace("adm_change_", "")
    order = get_order(oid)
    if not order:
        await q.edit_message_text("⚠️ یافت نشد")
        return
    await q.edit_message_text(f"🔄 تغییر وضعیت سفارش `{esc(oid)}`\nوضعیت فعلی: {esc(STATUS_LABELS_FA.get(order['status'], order['status']))}\n\nوضعیت جدید را انتخاب کنید:", parse_mode=ParseMode.MARKDOWN, reply_markup=status_change_inline(oid))

async def admin_set_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return
    # data: adm_set_{order_id}_{status}
    rest = q.data.replace("adm_set_", "")
    # order_id شامل ORD-... پس split از آخر
    parts = rest.rsplit("_", 1)
    if len(parts) != 2:
        await q.answer("خطا", show_alert=True)
        return
    oid, new_status = parts
    ok = update_order_status(oid, new_status, actor_id=q.from_user.id, note="تغییر از پنل")
    if ok:
        # همگام با شیت
        threading.Thread(target=lambda: update_order_status_sheet(oid, new_status), daemon=True).start()
        # اطلاع به کاربر
        order = get_order(oid)
        if order:
            try:
                await ctx.bot.send_message(order["telegram_id"], f"📢 وضعیت درخواست `{esc(oid)}` به «{esc(STATUS_LABELS_FA.get(new_status, new_status))}» تغییر کرد.", parse_mode=ParseMode.MARKDOWN)
            except:
                pass
        await q.edit_message_text(f"✅ وضعیت به «{esc(STATUS_LABELS_FA.get(new_status, new_status))}» تغییر کرد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👁 مشاهده", callback_data=f"adm_view_{oid}")],[InlineKeyboardButton("🔙 لیست", callback_data="adm_orders")]]))
    else:
        await q.edit_message_text("⚠️ خطا در تغییر وضعیت")

async def admin_sheets_test(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ok, msg = test_connection()
    icon = "✅" if ok else "❌"
    await q.edit_message_text(f"{icon} تست اتصال Google Sheets:\n\n{esc(msg)}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 منو", callback_data="adm_menu")]]))

async def admin_settings_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    welcome = get_setting("welcome_message", DEFAULT_MESSAGES["welcome"])
    bot_active = get_setting("bot_active", True)
    text = (
        f"⚙️ *تنظیمات*\n\n"
        f"🤖 ربات فعال: {'✅' if bot_active else '❌'}\n"
        f"👋 پیام خوشامد:\n{esc(welcome[:500])}\n\n"
        f"برای ویرایش، دستورات زیر را بفرستید (در پنل وب آینده):\n"
        f"• /set_welcome <متن>\n"
        f"• /toggle_bot"
    )
    await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 منو", callback_data="adm_menu")]]))

async def admin_search_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    set_state(uid, STATE_ADMIN_SEARCH, {})
    await q.edit_message_text("🔍 *جستجو*\n\nشناسه سفارش، نام، شماره تماس یا نوع بیمه را بفرستید:\n(یا /cancel برای لغو)", parse_mode=ParseMode.MARKDOWN)

async def handle_admin_search_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    if text == "/cancel":
        clear_state(uid)
        await update.message.reply_text("لغو شد.", reply_markup=main_reply_keyboard(True))
        return
    # جستجو ساده در DB
    # اگر با ORD شروع شود -> مستقیم
    if text.upper().startswith("ORD"):
        order = get_order(text.upper())
        if order:
            clear_state(uid)
            await send_order_detail(update, order, is_admin_view=True, ctx=ctx)
            return
    # جستجو در 20 سفارش آخر
    orders = list_orders(limit=50)
    matched = []
    for o in orders:
        data_str = json.dumps(o.get("data") or {}, ensure_ascii=False)
        if text in o["id"] or text in (o.get("contact_phone") or "") or text in (o.get("contact_name") or "") or text in (o.get("insurance_type_name") or "") or text in data_str:
            matched.append(o)
    clear_state(uid)
    if not matched:
        await update.message.reply_text("🔍 نتیجه‌ای یافت نشد.", reply_markup=main_reply_keyboard(True))
        return
    lines = [f"🔍 نتایج برای «{esc(text)}» ({len(matched)} مورد):", ""]
    rows = []
    for o in matched[:8]:
        lines.append(f"• `{esc(o['id'])}` — {esc(o['insurance_type_name'])} — {esc(STATUS_LABELS_FA.get(o['status'], o['status']))}")
        rows.append([InlineKeyboardButton(f"{o['id']}", callback_data=f"adm_view_{o['id']}")])
    rows.append([InlineKeyboardButton("🔙 منو", callback_data="adm_menu")])
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(rows))

# --- ادمین: افزودن نوع بیمه (ساده) ---
async def admin_types_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    types = list_insurance_types(active_only=False)
    lines = ["🗂 *انواع بیمه* (برای افزودن نوع جدید دستور /add_type را بزنید)", ""]
    rows = []
    for t in types:
        icon = "✅" if t["is_active"] else "⛔️"
        lines.append(f"{icon} {esc(t['name'])} — `{esc(t['slug'])}`")
        rows.append([InlineKeyboardButton(f"{t['name']}", callback_data=f"adm_type_{t['id']}")])
    rows.append([InlineKeyboardButton("➕ افزودن نوع جدید", callback_data="adm_add_type")])
    rows.append([InlineKeyboardButton("🔙 منو", callback_data="adm_menu")])
    await q.edit_message_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(rows))

async def admin_add_type_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    set_state(uid, STATE_ADMIN_ADD_TYPE, {"step": "name"})
    await q.edit_message_text("➕ *افزودن نوع بیمه جدید*\n\nنام بیمه را بفرستید (مثلاً: بیمه باربری):\n(یا /cancel)", parse_mode=ParseMode.MARKDOWN)

async def handle_admin_add_type_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    if text == "/cancel":
        clear_state(uid)
        await update.message.reply_text("لغو شد.", reply_markup=main_reply_keyboard(True))
        return
    st = get_state(uid)
    data = st.get("data") or {}
    step = data.get("step")
    if step == "name":
        data["name"] = text
        data["step"] = "slug"
        set_state(uid, STATE_ADMIN_ADD_TYPE, data)
        await update.message.reply_text(f"نام: {esc(text)}\n\nحالا شناسه انگلیسی (slug) را بفرستید (مثلاً bimeh-barbar):")
    elif step == "slug":
        slug = re.sub(r"[^a-z0-9-_]", "-", text.lower())
        data["slug"] = slug
        data["step"] = "desc"
        set_state(uid, STATE_ADMIN_ADD_TYPE, data)
        await update.message.reply_text(f"slug: {esc(slug)}\n\nتوضیح کوتاه را بفرستید (یا «رد»):")
    elif step == "desc":
        if text in ("رد", "-", "skip"):
            text = ""
        from ..database import create_insurance_type
        try:
            create_insurance_type(data["name"], data["slug"], description=text)
            clear_state(uid)
            await update.message.reply_text(f"✅ نوع بیمه «{esc(data['name'])}» اضافه شد.", reply_markup=main_reply_keyboard(True))
        except Exception as e:
            await update.message.reply_text(f"⚠️ خطا: {esc(str(e))}")
            clear_state(uid)

# --- کمپین / گزارش (استاب) ---
async def admin_campaign_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("📣 *کمپین‌ها*\n\nاین بخش در فاز ۳ تکمیل می‌شود.\nدر حال حاضر می‌توانید از «پیام همگانی» ساده استفاده کنید:\n\n/campaign <متن>  — ارسال به همه کاربران (با رعایت نرخ)", parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 منو", callback_data="adm_menu")]]))

async def admin_report_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    stats = get_stats()
    text = (
        "📈 *گزارش*\n\n"
        f"📦 کل: {stats.get('total_orders',0)}\n"
        f"👥 کاربران: {stats.get('total_users',0)}\n"
        f"⏳ در انتظار: {stats.get('pending',0)}\n\n"
        "برای گزارش تفصیلی به Google Sheets مراجعه کنید (برگه Orders)."
    )
    await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 منو", callback_data="adm_menu")]]))

async def admin_users_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    from ..database import get_conn
    conn = get_conn()
    cur = conn.execute("SELECT * FROM users ORDER BY joined_at DESC LIMIT 10")
    rows_db = cur.fetchall()
    conn.close()
    lines = ["👥 *کاربران اخیر* (۱۰ نفر آخر):", ""]
    for r in rows_db:
        lines.append(f"• {esc(r['first_name'] or '-')} @{esc(r['username'] or '-')} — ID:{r['telegram_id']} — {r['joined_at'][:10]}")
    await q.edit_message_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 منو", callback_data="adm_menu")]]))

async def admin_faq_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    faqs = list_faq()
    lines = [f"❓ *FAQ* ({len(faqs)} مورد)", ""]
    for f in faqs[:5]:
        icon = "✅" if f["is_approved"] else "⏳"
        lines.append(f"{icon} {esc(f['question'][:60])}")
    lines.append("\nبرای افزودن FAQ: /add_faq سوال | جواب")
    await q.edit_message_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 منو", callback_data="adm_menu")]]))

async def admin_knowledge_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    kb = get_kb()
    lines = [
        "📚 *منابع دانش*",
        "",
        f"📄 تعداد قطعات: {len(kb.chunks)}",
        "",
        "برای افزودن سند، فایل PDF/DOCX/TXT را به ربات بفرستید (در حالت ادمین).",
        "سپس با /reload_kb ایندکس را بازسازی کنید.",
        "",
        "فایل‌ها را در پوشه knowledge_docs قرار دهید."
    ]
    await q.edit_message_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 بازسازی ایندکس", callback_data="adm_reload_kb")],[InlineKeyboardButton("🔙 منو", callback_data="adm_menu")]]))

async def admin_reload_kb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("در حال بازسازی...")
    kb = get_kb()
    kb.reload()
    await q.edit_message_text(f"✅ ایندکس بازسازی شد — {len(kb.chunks)} قطعه", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 منو", callback_data="adm_menu")]]))

# --- دستورات متنی ادمین ---
async def handle_admin_commands(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id
    if not is_admin(uid):
        return False
    if text.startswith("/set_welcome"):
        new = text.replace("/set_welcome","").strip()
        if new:
            set_setting("welcome_message", new)
            await update.message.reply_text("✅ پیام خوشامد به‌روزرسانی شد.")
        else:
            await update.message.reply_text("مثال: /set_welcome سلام {name} عزیز!")
        return True
    if text.startswith("/toggle_bot"):
        cur = get_setting("bot_active", True)
        set_setting("bot_active", not cur)
        await update.message.reply_text(f"🤖 ربات {'فعال' if not cur else 'غیرفعال'} شد.")
        return True
    if text.startswith("/campaign"):
        msg = text.replace("/campaign","").strip()
        if not msg:
            await update.message.reply_text("مثال: /campaign متن پیام")
            return True
        # ارسال با رعایت نرخ (هر 0.05 ثانیه یک پیام)
        from ..database import get_conn
        conn = get_conn()
        cur = conn.execute("SELECT telegram_id FROM users WHERE is_blocked=0")
        users = [r["telegram_id"] for r in cur.fetchall()]
        conn.close()
        await update.message.reply_text(f"📣 ارسال به {len(users)} کاربر آغاز شد (با تاخیر)...")
        async def _send():
            sent = 0
            for tid in users:
                try:
                    await ctx.bot.send_message(tid, f"📢 اطلاعیه:\n\n{msg}")
                    sent += 1
                    await asyncio.sleep(0.05)  # ~20 پیام در ثانیه (حد تلگرام 30)
                except Exception as e:
                    log.warning("campaign to %s failed: %s", tid, e)
                    await asyncio.sleep(0.1)
            await ctx.bot.send_message(uid, f"✅ کمپین تمام شد — {sent}/{len(users)} ارسال موفق")
        asyncio.create_task(_send())
        return True
    if text.startswith("/add_faq"):
        rest = text.replace("/add_faq","").strip()
        if "|" not in rest:
            await update.message.reply_text("مثال: /add_faq بیمه ثالث چیست؟ | بیمه اجباری خودرو است")
            return True
        q, a = rest.split("|",1)
        from ..database import create_faq
        create_faq(q.strip(), a.strip(), is_approved=True)
        await update.message.reply_text("✅ FAQ اضافه شد.")
        return True
    if text.startswith("/reload_kb"):
        kb = get_kb()
        kb.reload()
        await update.message.reply_text(f"✅ {len(kb.chunks)} قطعه بارگذاری شد.")
        return True
    if text.startswith("/add_type"):
        await admin_add_type_start(update, ctx)
        return True
    if text.startswith("/stats"):
        stats = get_stats()
        await update.message.reply_text(f"📊 {json.dumps(stats, ensure_ascii=False, indent=2)}")
        return True
    return False

# ---------- فایل‌ها (دانش) ----------

async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        # فایل کاربر عادی - در فاز ۱ فقط پیام
        await update.message.reply_text("📎 فایل شما دریافت شد. برای ثبت درخواست، لطفاً از «استعلام / ثبت درخواست» استفاده کنید.")
        return
    doc = update.message.document
    if not doc:
        return
    # ذخیره
    fname = doc.file_name or f"doc_{doc.file_unique_id}"
    dest = Path("knowledge_docs") / fname
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        f = await ctx.bot.get_file(doc.file_id)
        await f.download_to_drive(str(dest))
        await update.message.reply_text(f"✅ فایل «{esc(fname)}» ذخیره شد. در حال ایندکس...")
        from ..knowledge.retriever import get_kb
        kb = get_kb()
        # تلاش برای افزودن به DB
        try:
            from pathlib import Path as P
            added = kb.add_document(P(dest), title=fname, is_official=True)
            await update.message.reply_text(f"✅ {added} قطعه به دانش اضافه شد. ({len(kb.chunks)} کل)")
        except Exception as e:
            kb.reload()
            await update.message.reply_text(f"✅ فایل ذخیره شد و ایندکس بازسازی شد ({len(kb.chunks)} قطعه). خطای جزئی: {e}")
    except Exception as e:
        await update.message.reply_text(f"❌ خطا در ذخیره: {e}")

# ---------- Callback Router ----------

async def callback_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or not q.data:
        return
    data = q.data
    # عمومی
    if data == "back_main":
        clear_state(q.from_user.id)
        try:
            await q.edit_message_text("🏠 به منوی اصلی بازگشتید.\nاز دکمه‌های زیر استفاده کنید.")
        except:
            pass
        await ctx.bot.send_message(q.from_user.id, "🏠 منوی اصلی", reply_markup=main_reply_keyboard(is_admin(q.from_user.id)))
        await q.answer()
        return
    if data == "quick_order":
        await start_order_flow(update, ctx)
        return
    if data == "handoff":
        await request_handoff(update, ctx)
        return
    if data == "end_consult":
        clear_state(q.from_user.id)
        await q.answer()
        try:
            await q.edit_message_text("✅ مشاوره پایان یافت.")
        except:
            pass
        await ctx.bot.send_message(q.from_user.id, "🏠 منوی اصلی", reply_markup=main_reply_keyboard(is_admin(q.from_user.id)))
        return
    if data in ("order_confirm", "order_cancel", "order_edit"):
        if data == "order_confirm":
            await handle_order_confirm(update, ctx)
        elif data == "order_cancel":
            await handle_order_cancel(update, ctx)
        elif data == "order_edit":
            await handle_order_edit(update, ctx)
        return
    if data.startswith("editfield_"):
        await handle_edit_field_select(update, ctx)
        return
    if data.startswith("fieldopt_") or data == "field_skip":
        await handle_field_option(update, ctx)
        return
    if data.startswith("ins_"):
        await handle_insurance_type_selected(update, ctx)
        return
    if data.startswith("view_"):
        await view_order_callback(update, ctx)
        return
    if data == "back_orders":
        await q.answer()
        # شبیه‌سازی list_my_orders ولی via callback
        orders = list_orders(limit=10, telegram_id=q.from_user.id)
        if not orders:
            await q.edit_message_text("📂 درخواستی ندارید.")
            return
        lines = ["📂 درخواست‌های شما:", ""]
        rows = []
        for o in orders:
            fa = STATUS_LABELS_FA.get(o["status"], o["status"])
            lines.append(f"• `{esc(o['id'])}` — {esc(o['insurance_type_name'])} — {esc(fa)}")
            rows.append([InlineKeyboardButton(f"{o['id']}", callback_data=f"view_{o['id']}")])
        await q.edit_message_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(rows))
        return
    # ادمین
    if data.startswith("adm_"):
        if not is_admin(q.from_user.id):
            await q.answer("⛔️ فقط مدیر", show_alert=True)
            return
        if data == "adm_menu":
            await admin_menu(update, ctx)
        elif data == "adm_dashboard":
            await admin_dashboard(update, ctx)
        elif data == "adm_orders":
            await admin_orders(update, ctx)
        elif data == "adm_filter_menu":
            await admin_filter_menu(update, ctx)
        elif data.startswith("adm_filter_"):
            status = data.replace("adm_filter_","")
            await show_admin_orders(q, filter_status=status, page=0, ctx=ctx)
        elif data.startswith("adm_orders_page_"):
            await admin_orders_page(update, ctx)
        elif data.startswith("adm_view_"):
            await admin_view_order(update, ctx)
        elif data.startswith("adm_change_"):
            await admin_change_status_menu(update, ctx)
        elif data.startswith("adm_set_"):
            await admin_set_status(update, ctx)
        elif data == "adm_sheets_test":
            await admin_sheets_test(update, ctx)
        elif data == "adm_settings":
            await admin_settings_menu(update, ctx)
        elif data == "adm_search":
            await admin_search_start(update, ctx)
        elif data == "adm_types":
            await admin_types_menu(update, ctx)
        elif data == "adm_add_type":
            await admin_add_type_start(update, ctx)
        elif data == "adm_campaign":
            await admin_campaign_menu(update, ctx)
        elif data == "adm_report":
            await admin_report_menu(update, ctx)
        elif data == "adm_users":
            await admin_users_menu(update, ctx)
        elif data == "adm_faq":
            await admin_faq_menu(update, ctx)
        elif data == "adm_knowledge":
            await admin_knowledge_menu(update, ctx)
        elif data == "adm_reload_kb":
            await admin_reload_kb(update, ctx)
        elif data.startswith("adm_resume_"):
            await resume_auto_callback(update, ctx)
        elif data == "adm_close":
            try:
                await q.message.delete()
            except:
                await q.edit_message_text("بسته شد.")
        else:
            await q.answer()
        return
    await q.answer()

# ---------- Main polling ----------

async def error_handler(update, ctx):
    log.exception("Update %s caused error %s", update, ctx.error)
    try:
        if update and update.effective_user:
            await ctx.bot.send_message(update.effective_user.id, "⚠️ خطای موقت رخ داد. لطفاً دوباره تلاش کنید.")
    except:
        pass

def build_app():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN تنظیم نشده - فایل .env را بررسی کنید")
    init_db()
    # پیش‌بارگذاری دانش
    try:
        get_kb()
    except Exception as e:
        log.warning("KB preload failed: %s", e)

    app = Application.builder().token(BOT_TOKEN).build()
    # دستورات
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    # کال‌بک‌ها اول
    app.add_handler(CallbackQueryHandler(callback_router))
    # فایل‌ها (قبل از text handler)
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_document))
    app.add_handler(MessageHandler(filters.CONTACT, handle_text))
    # متن: اول دستورات ادمین را چک کن
    async def text_wrapper(update, ctx):
        # اگر دستور ادمین بود
        if update.message and update.message.text:
            if await handle_admin_commands(update, ctx):
                return
        await handle_text(update, ctx)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_wrapper))
    # همچنین دستورات متنی که با / شروع می‌شوند اما هندل نشده‌اند
    app.add_handler(MessageHandler(filters.COMMAND, text_wrapper))
    app.add_error_handler(error_handler)
    return app

# برای main.py
async def run_polling():
    app = build_app()
    log.info("🤖 ربات بیمه در حال اجرا (polling)... Admins: %s", ADMIN_IDS)
    await app.initialize()
    await app.start()
    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    log.info("✓ Polling started")
    # تا ابد
    while True:
        await asyncio.sleep(3600)

