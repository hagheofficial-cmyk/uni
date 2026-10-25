"""
تنظیمات مرکزی پروژه بیمه
همه اسرار از متغیر محیطی خوانده می‌شوند - هیچ توکنی در کد نیست
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent.parent.parent
DATA_DIR = Path(os.getenv("DATABASE_PATH", str(BASE_DIR / "data" / "app.db"))).parent
UPLOAD_DIR = Path(os.getenv("UPLOAD_FOLDER", str(BASE_DIR / "uploads")))
KNOWLEDGE_DIR = Path(os.getenv("KNOWLEDGE_FOLDER", str(BASE_DIR / "knowledge_docs")))

# اطمینان از وجود پوشه‌ها
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)

# --- تلگرام ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_USER_IDS", "").split(",") if x.strip().isdigit()]
BOT_USERNAME = os.getenv("BOT_USERNAME", "").strip().lstrip("@")
PORT = int(os.getenv("PORT") or os.getenv("WEBAPP_PORT") or "10000")
DATABASE_PATH = os.getenv("DATABASE_PATH", str(BASE_DIR / "data" / "app.db"))

# --- هوش مصنوعی ---
AI_PROVIDER = os.getenv("AI_PROVIDER", "openai").strip().lower()  # openai|groq|gemini|openrouter|disabled
AI_API_KEY = os.getenv("AI_API_KEY", "").strip()
AI_MODEL = os.getenv("AI_MODEL", "gpt-4o-mini").strip()
AI_BASE_URL = os.getenv("AI_BASE_URL", "").strip() or None
# اگر کلید خالی باشد، AI غیرفعال می‌شود ولی ربات کار می‌کند
AI_ENABLED = bool(AI_API_KEY and AI_PROVIDER != "disabled")

# --- Google Sheets ---
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "").strip()
GOOGLE_SA_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
GOOGLE_SA_CONTENT = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT", "").strip()
SHEETS_ENABLED = bool(GOOGLE_SHEET_ID and (GOOGLE_SA_JSON or GOOGLE_SA_CONTENT))

# --- عمومی ---
DEBUG = os.getenv("DEBUG", "false").lower() in ("1", "true", "yes")
LOG_CHANNEL_ID = os.getenv("LOG_CHANNEL_ID", "").strip()

# نگاشت وضعیت‌ها (انگلیسی -> فارسی)
STATUS_LABELS_FA = {
    "new": "🆕 جدید",
    "waiting_info": "⏳ در انتظار اطلاعات",
    "ready_for_review": "👀 آماده بررسی",
    "assigned": "👤 ارجاع به کارشناس",
    "contacted": "📞 تماس گرفته شد",
    "quoted": "💰 قیمت اعلام شد",
    "purchased": "✅ خریداری شد",
    "rejected": "❌ رد شد",
    "cancelled": "🚫 لغو شد",
}

STATUS_FLOW = {
    "new": ["waiting_info", "assigned", "cancelled"],
    "waiting_info": ["ready_for_review", "cancelled"],
    "ready_for_review": ["assigned", "cancelled"],
    "assigned": ["contacted", "cancelled"],
    "contacted": ["quoted", "rejected", "cancelled"],
    "quoted": ["purchased", "rejected", "cancelled"],
    "purchased": [],
    "rejected": ["new"],
    "cancelled": ["new"],
}

# پیام‌های پیش‌فرض فارسی
DEFAULT_MESSAGES = {
    "welcome": (
        "👋 سلام {name} عزیز!\n\n"
        "به *ربات هوشمند مشاوره بیمه* خوش آمدید.\n"
        "من می‌تونم در انتخاب بیمه مناسب راهنماییتون کنم، به سوالات‌تون پاسخ بدم و درخواست‌تون رو ثبت کنم.\n\n"
        "لطفاً یکی از گزینه‌های زیر رو انتخاب کنید:"
    ),
    "help": (
        "ℹ️ *راهنمای ربات*\n\n"
        "• 💬 *مشاوره هوشمند* — سوالت رو بپرس، بر اساس منابع تاییدشده جواب می‌گیرید\n"
        "• 📋 *استعلام / ثبت درخواست* — نوع بیمه رو انتخاب و اطلاعات رو مرحله‌ای وارد کنید\n"
        "• 📂 *پیگیری درخواست* — وضعیت درخواست‌هات رو ببین\n"
        "• 👤 *ارتباط با کارشناس* — مکالمه رو به اپراتور انسانی وصل می‌کنم\n"
        "• در هر مرحله «لغو» بزنید تا خارج شوید\n"
    ),
    "unknown": "متوجه منظورتون نشدم. لطفاً از دکمه‌های زیر استفاده کنید یا سوالتون رو واضح‌تر بپرسید.",
    "ai_unavailable": "در حال حاضر پاسخ هوشمند موقتاً در دسترس نیست، اما می‌تونم درخواست‌تون رو ثبت کنم یا شما رو به کارشناس وصل کنم.",
    "handoff_requested": "✅ درخواست شما برای صحبت با کارشناس ثبت شد. همکاران ما به زودی با شما تماس می‌گیرند.\nتا آن زمان پاسخ خودکار متوقف می‌شود.",
    "order_created": "✅ درخواست شما با شناسه *{order_id}* ثبت شد!\nکارشناسان ما به زودی بررسی و با شما تماس می‌گیرند.",
}

# محدودیت‌ها
RATE_LIMIT_MESSAGES_PER_MINUTE = 20
MAX_FILE_SIZE_MB = 20
ALLOWED_FILE_TYPES = {".pdf", ".jpg", ".jpeg", ".png", ".doc", ".docx", ".txt"}
