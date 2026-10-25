"""
کیبوردهای فارسی و راست‌چین
"""
from telegram import ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton

def main_reply_keyboard(is_admin: bool = False):
    rows = [
        [KeyboardButton("💬 مشاوره هوشمند"), KeyboardButton("📋 استعلام / ثبت درخواست")],
        [KeyboardButton("📂 پیگیری درخواست‌ها"), KeyboardButton("👤 ارتباط با کارشناس")],
        [KeyboardButton("ℹ️ راهنما"), KeyboardButton("💰 باشگاه مشتریان")],
    ]
    if is_admin:
        rows.append([KeyboardButton("⚙️ پنل مدیریت")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, input_field_placeholder="پیامت رو بنویس...")

def insurance_types_inline(types: list):
    rows = []
    for t in types:
        # t: dict با id, name
        rows.append([InlineKeyboardButton(f"📌 {t['name']}", callback_data=f"ins_{t['id']}")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")])
    return InlineKeyboardMarkup(rows)

def confirm_inline():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تایید و ثبت نهایی", callback_data="order_confirm")],
        [InlineKeyboardButton("✏️ ویرایش", callback_data="order_edit"), InlineKeyboardButton("❌ لغو", callback_data="order_cancel")],
    ])

def edit_fields_inline(fields: list):
    rows = []
    for f in fields:
        rows.append([InlineKeyboardButton(f"✏️ {f['label']}", callback_data=f"editfield_{f['field_key']}")])
    rows.append([InlineKeyboardButton("✅ تایید و ثبت", callback_data="order_confirm")])
    rows.append([InlineKeyboardButton("❌ لغو درخواست", callback_data="order_cancel")])
    return InlineKeyboardMarkup(rows)

def order_actions_inline(order_id: str, is_admin=False):
    if is_admin:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("👁 جزئیات", callback_data=f"adm_view_{order_id}")],
        ])
    else:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("👁 جزئیات", callback_data=f"view_{order_id}")],
        ])

def admin_menu_inline():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 داشبورد", callback_data="adm_dashboard"), InlineKeyboardButton("📦 سفارش‌ها", callback_data="adm_orders")],
        [InlineKeyboardButton("🔍 جستجو", callback_data="adm_search"), InlineKeyboardButton("👥 کاربران", callback_data="adm_users")],
        [InlineKeyboardButton("🗂 انواع بیمه", callback_data="adm_types"), InlineKeyboardButton("❓ FAQ", callback_data="adm_faq")],
        [InlineKeyboardButton("📚 منابع دانش", callback_data="adm_knowledge"), InlineKeyboardButton("🎁 تخفیف‌ها", callback_data="adm_discounts")],
        [InlineKeyboardButton("📣 کمپین", callback_data="adm_campaign"), InlineKeyboardButton("📈 گزارش", callback_data="adm_report")],
        [InlineKeyboardButton("⚙️ تنظیمات", callback_data="adm_settings"), InlineKeyboardButton("🔌 اتصال شیت", callback_data="adm_sheets_test")],
        [InlineKeyboardButton("✖️ بستن", callback_data="adm_close")],
    ])

def admin_orders_filter_inline():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("همه", callback_data="adm_filter_all"), InlineKeyboardButton("جدید", callback_data="adm_filter_new")],
        [InlineKeyboardButton("در انتظار", callback_data="adm_filter_waiting_info"), InlineKeyboardButton("آماده", callback_data="adm_filter_ready_for_review")],
        [InlineKeyboardButton("ارجاع شده", callback_data="adm_filter_assigned"), InlineKeyboardButton("قیمت داده", callback_data="adm_filter_quoted")],
        [InlineKeyboardButton("خریداری", callback_data="adm_filter_purchased"), InlineKeyboardButton("لغو", callback_data="adm_filter_cancelled")],
        [InlineKeyboardButton("🔙 منو", callback_data="adm_menu")],
    ])

def status_change_inline(order_id: str):
    from ..config import STATUS_FLOW, STATUS_LABELS_FA
    # برای سادگی همه وضعیت‌ها را نشان بده
    rows = []
    for status, label in STATUS_LABELS_FA.items():
        rows.append([InlineKeyboardButton(label, callback_data=f"adm_set_{order_id}_{status}")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"adm_view_{order_id}")])
    return InlineKeyboardMarkup(rows)

def pagination_inline(prefix: str, page: int, has_next: bool):
    rows = []
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ قبلی", callback_data=f"{prefix}_page_{page-1}"))
    if has_next:
        nav.append(InlineKeyboardButton("بعدی ▶️", callback_data=f"{prefix}_page_{page+1}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(rows)

def contact_request_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📱 ارسال شماره تماس", request_contact=True)], [KeyboardButton("⏭ رد کردن")]],
        resize_keyboard=True, one_time_keyboard=True
    )

def cancel_inline():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="order_cancel")]])

def back_to_main_inline():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 بازگشت به منو", callback_data="back_main")]])
