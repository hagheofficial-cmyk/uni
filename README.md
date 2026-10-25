# 🤖 ربات هوشمند مشاوره و فروش بیمه (تلگرام)

ربات تلگرام **فارسی و راست‌چین** برای مشاوره بیمه، پاسخ‌گویی هوشمند (RAG)، استعلام و ثبت سفارش، ذخیره در Google Sheets، ارجاع به کارشناس انسانی، پنل مدیریت داخل تلگرام، باشگاه مشتریان، تخفیف و کمپین.

> **وضعیت:** فاز ۱ (هسته عملیاتی) — **تکمیل و تست‌شده** · فاز ۲ و ۳ اسکلت آماده، قابل گسترش بدون بازنویسی

---

## ✨ ویژگی‌ها (فاز ۱)

### مشتری
- 👋 خوشامدگویی فارسی + منوی دکمه‌ای
- 💬 **مشاوره هوشمند** — بازیابی از اسناد تاییدشده (TF-IDF + RAG) + لایه انتزاعی AI (قابل تعویض OpenAI/Groq/Gemini/OpenRouter)
- 📋 **استعلام/ثبت درخواست** با فرم‌های **پویا و قابل تنظیم** برای هر نوع بیمه (فیلدهای متنی، عددی، تاریخ شمسی، انتخابی، تلفن، تایید)
- ✅ اعتبارسنجی، ویرایش قبل از ثبت، لغو، جلوگیری از ثبت تکراری (idempotency + هش)
- 🔖 شناسه یکتا `ORD-XXXXXXXX` برای هر سفارش
- 📂 **پیگیری درخواست‌ها** — مشاهده وضعیت فارسی (جدید، در انتظار، ارجاع‌شده، قیمت‌داده، خریداری...)
- 👤 **ارجاع به کارشناس** — توقف پاسخ خودکار، اطلاع به ادمین، بازگردانی به حالت خودکار
- 📎 آپلود اسناد دانش توسط ادمین (PDF/DOCX/TXT/MD/CSV) → ایندکس خودکار
- 🛡️ ضد اسپم (Rate Limit ۲۰ پیام/دقیقه)، لاگ بدون افشای توکن

### ادمین (فقط IDهای مجاز)
- ⚙️ پنل مدیریت با دکمه‌های شیشه‌ای داخل تلگرام
- 📊 داشبورد (کل سفارش‌ها، کاربران، وضعیت‌ها، نرخ تبدیل)
- 📦 لیست سفارش‌ها با فیلتر وضعیت + صفحه‌بندی + جستجو (ID/نام/تلفن/نوع بیمه)
- 👁 مشاهده جزئیات، تغییر وضعیت (با لاگ و اطلاع به مشتری)، ثبت قیمت، ارجاع
- 🗂 مدیریت انواع بیمه (افزودن/غیرفعال‌سازی) و فیلدها
- ❓ مدیریت FAQ (تاییدشده/در انتظار)
- 📚 مدیریت منابع دانش + بازسازی ایندکس
- 🎁 تخفیف‌ها (ساختار DB آماده)
- 📣 کمپین/پیام همگانی با رعایت نرخ تلگرام (۲۰ msg/sec + تاخیر)
- ⚙️ تنظیمات (پیام خوشامد، فعال/غیرفعال ربات)
- 🔌 تست اتصال Google Sheets
- 👥 لیست کاربران

### فنی
- **Python 3.11** + **python-telegram-bot 21.9** (پایدار، async)
- **SQLite (WAL)** برای وضعیت مکالمه، انواع بیمه، سفارش‌ها (کش) → بازیابی پس از ریستارت
- **Google Sheets API (gspread)** به‌عنوان منبع حقیقت سفارش‌ها — با retry و مدیریت 429
- **RAG سبک:** chunking هوشمند + TF-IDF (scikit-learn) + fallback جستجوی ساده — قابل ارتقا به embeddings بدون تغییر اینترفیس
- **لایه AI انتزاعی:** `AI_PROVIDER` → تعویض مدل با تغییر `.env` (بدون بازنویسی)
- **Flask health server** برای Render/Railway (`/` → 200)
- **امنیت:** اسرار فقط در `.env`، عدم لاگ توکن، validation ورودی، کنترل دسترسی ادمین

---

## 🏗 معماری

```
Telegram ──► python-telegram-bot (polling) ──► handlers (consultation / orders / admin / handoff)
                        │
                        ├──► SQLite (app.db) — state, insurance_types, orders, users, knowledge_chunks
                        ├──► Google Sheets (Orders, Customers, ...) — append/update با retry
                        ├──► KnowledgeBase (TF-IDF) — retriever.get_kb().search(query)
                        └──► AIProvider (OpenAI-compatible) — ai.answer_with_context()
```

**چرا polling نه webhook؟** روی هاست‌های رایگان (Render Free) که filesystem موقتی و sleep دارند، polling ساده‌تر و بدون نیاز به دامنه/HTTPS است. برای production پایدار، Worker دائمی (Render Paid $7/mo یا Fly.io/Railway) توصیه می‌شود — کد هر دو را پشتیبانی می‌کند.

**چرا بدون Docker/n8n/DB سنگین؟** طبق الزام «سادگی و کم‌هزینه»، SQLite + Sheets برای <۱۰۰ پیام/روز کافی و قابل نگهداری است؛ در صورت رشد، می‌توان Postgres را جایگزین کرد بدون تغییر لاجیک.

---

## 💸 سرویس‌های خارجی — هزینه و سهمیه

| سرویس | پلن رایگان | محدودیت | هزینه بعد از سهمیه | جایگزین |
|-------|------------|----------|---------------------|----------|
| **Render Free** | Web Service رایگان | sleep بعد از ۱۵ دقیقه عدم ترافیک، FS موقتی، 750h/mo | $7/mo (Starter, بدون sleep + disk) | Railway, Fly.io, Hetzner ($4) |
| **Telegram Bot API** | نامحدود | 30 msg/sec به یک چت، 20 msg/min در گروه | رایگان | — |
| **Google Sheets API** | 300 req/min per project، 60 req/min per user | 429 در صورت انفجار | رایگان (سهمیه بالا) | — |
| **Groq** | بسیار سخاوتمندانه (14.4k req/day برای llama3.3-70b) | نرخ محدود ولی برای <100 msg کافی | $0.59 / 1M tokens | OpenRouter free models |
| **OpenAI** | $5 اعتبار اولیه | — | gpt-4o-mini $0.15/1M in, $0.60/1M out | Gemini Flash رایگان |
| **Gemini (Google)** | 15 RPM, 1500 req/day (Flash) | محدود ولی رایگان پایدار | — | Groq |
| **OpenRouter** | مدل‌های `:free` (llama, gemma...) | متغیر | پرداخت به ازای استفاده | — |

> **توصیه فاز ۱:** `AI_PROVIDER=groq` + `MODEL=llama-3.3-70b-versatile` → فارسی عالی، رایگان، سریع. یا `gemini-1.5-flash` برای سهمیه پایدار.

---

## 🚀 نصب و اجرای محلی

```bash
git clone https://github.com/hagheofficial-cmyk/uni.git
cd uni
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# داخل .env: BOT_TOKEN و ADMIN_USER_IDS را پر کنید
python main.py
# یا برای تست:
python -m pytest tests -v
```

متغیرهای ضروری:

| متغیر | توضیح |
|------|-------|
| `BOT_TOKEN` | از @BotFather (`/newbot`) |
| `ADMIN_USER_IDS` | آیدی عددی ادمین‌ها با کاما (از @userinfobot بگیرید) |
| `AI_PROVIDER` / `AI_API_KEY` / `AI_MODEL` | اگر خالی بماند، ربات بدون AI هم کار می‌کند |
| `GOOGLE_SHEET_ID` + `GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT` | برای Sheets (اختیاری در توسعه) |

---

## 🔑 ساخت ربات در BotFather

1. در تلگرام به `@BotFather` بروید → `/newbot` → نام و username بدهید
2. توکن را کپی کنید → در `.env` بگذارید
3. `/setdescription` و `/setabouttext` — توضیح فارسی
4. `/setcommands`:
```
start - شروع و منوی اصلی
help - راهنما
cancel - لغو عملیات
```

---

## 📊 اتصال Google Sheets

1. به [Google Cloud Console](https://console.cloud.google.com) بروید → پروژه بسازید
2. APIهای **Google Sheets API** و **Google Drive API** را فعال کنید
3. **Service Account** بسازید → کلید JSON دانلود کنید
4. یک Google Sheet بسازید → ID آن را از URL کپی کنید (`https://docs.google.com/spreadsheets/d/<ID>/edit`)
5. Sheet را با ایمیل `service account` (مثل `xxx@project.iam.gserviceaccount.com`) **Share → Editor** کنید
6. در `.env`:
```ini
GOOGLE_SHEET_ID=<ID>
GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT={"type":"service_account", ... کل JSON در یک خط ...}
```
یا فایل `google-credentials.json` را در ریشه بگذارید و `GOOGLE_SERVICE_ACCOUNT_JSON=./google-credentials.json`

**ساختار شیت:** ربات به صورت خودکار برگه‌های `Orders, Customers, OrderDetails, FollowUps, Discounts, FAQ, KnowledgeSources, Settings, Campaigns` را با هدر می‌سازد. اگر از قبل دارید، هدرها باید دقیق باشند (به `app/services/sheets_service.py` HEADERS مراجعه کنید).

**بدون Sheets چه می‌شود؟** ربات همچنان کار می‌کند — سفارش‌ها در SQLite می‌مانند و در لاگ هشدار می‌دهد. برای تست محلی نیازی به Sheets نیست.

---

## 🧠 کلید هوش مصنوعی

**Groq (پیشنهادی):**
1. [console.groq.com](https://console.groq.com) → API Key بسازید
2. `.env`:
```ini
AI_PROVIDER=groq
AI_API_KEY=gsk_xxx
AI_MODEL=llama-3.3-70b-versatile
AI_BASE_URL=https://api.groq.com/openai/v1
```

**OpenAI:**
```ini
AI_PROVIDER=openai
AI_API_KEY=sk-xxx
AI_MODEL=gpt-4o-mini
```

**Gemini (رایگان):**
```ini
AI_PROVIDER=gemini
AI_API_KEY=AIza...
AI_MODEL=gemini-1.5-flash
AI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
```

**غیرفعال:** `AI_PROVIDER=disabled` یا خالی گذاشتن `AI_API_KEY` → ربات فقط با FAQ و فرم کار می‌کند (مطابق الزام پایداری).

---

## ☁️ استقرار ابری

### گزینه A — Render (ساده‌ترین)

1. پروژه را به GitHub push کنید
2. در [render.com](https://render.com) → New → Web Service → repo را انتخاب کنید
3. فایل `render.yaml` به صورت خودکار شناسایی می‌شود
4. Environment Variables را در پنل Render پر کنید (`BOT_TOKEN`, `ADMIN_USER_IDS`, `AI_*`, `GOOGLE_*`)
5. Deploy → لاگ را چک کنید (`🤖 Bot: @username`)
6. **نکته مهم (پلن رایگان):** سرویس بعد از ۱۵ دقیقه بدون درخواست می‌خوابد. برای ربات تلگرام که polling می‌کند، این یعنی **قطع می‌شود**. راهکارها:
   - **پولی ($7/mo):** Starter + Persistent Disk → اجرای دائمی تضمین‌شده ✅ (توصیه‌شده)
   - **رایگان + Cron:** از [cron-job.org](https://cron-job.org) هر ۱۰ دقیقه به `https://your-app.onrender.com/` پینگ بزنید — ولی **تضمین ۱۰۰٪ نیست** (Render ممکن است بخوابد، FS پاک می‌شود). طبق الزام پروژه، این را به عنوان راهکار تضمین‌شده معرفی نمی‌کنیم.
   - **جایگزین:** Railway / Fly.io (حجم رایگان بهتر، sleep کمتر) یا VPS ارزان Hetzner.

| نوع سرویس Render | مناسب برای polling؟ | توضیح |
|-----------------|---------------------|--------|
| Free Web Service | ❌ (می‌خوابد) | فقط برای تست |
| Paid Web Service ($7) | ✅ | همیشه روشن |
| Background Worker ($7) | ✅✅ | بهترین برای ربات (بدون HTTP) |

> برای polling، **Web Service** کافی است (Flask health روی پورت 10000). اگر Worker می‌خواهید، `healthCheckPath` را حذف کنید.

### گزینه B — Railway / Fly.io

- Railway: `railway.app` → New Project → Deploy from GitHub → متغیرها را ست کنید → Deploy
- Fly.io: `fly launch` → `fly deploy` (نیاز به Dockerfile ساده — در صورت نیاز اضافه می‌شود)

---

## ⚙️ پنل مدیریت (داخل تلگرام)

1. با اکانت ادمین (`ADMIN_USER_IDS`) به ربات `/start` بزنید → دکمه `⚙️ پنل مدیریت` ظاهر می‌شود
2. امکانات:
   - **داشبورد:** آمار لحظه‌ای
   - **سفارش‌ها:** فیلتر + صفحه‌بندی → مشاهده → تغییر وضعیت/ثبت قیمت
   - **جستجو:** شناسه/نام/تلفن
   - **انواع بیمه:** افزودن نوع جدید (`➕ افزودن`) — سپس فیلدها را از DB یا کد اضافه کنید
   - **FAQ:** افزودن سریع `/add_faq سوال | جواب`
   - **منابع دانش:** ارسال فایل + `/reload_kb`
   - **تست شیت:** بررسی اتصال
   - **پیام همگانی:** `/campaign متن` (با تاخیر ۰.۰۵ ثانیه بین پیام‌ها)

**افزودن ادمین جدید:** `ADMIN_USER_IDS` را در `.env` (محلی) یا Render Env Vars ویرایش کنید و redeploy کنید. در آینده از پنل قابل افزودن خواهد شد.

---

## 🗂 افزودن نوع بیمه جدید

### روش ۱ — از پنل تلگرام

`⚙️ پنل → 🗂 انواع بیمه → ➕ افزودن نوع جدید` → نام و slug را وارد کنید.

سپس برای افزودن فیلدها (در فاز فعلی از طریق کد/DB):

```python
from app.database import create_field
# مثال: افزودن فیلد به بیمه باربری
create_field(
    insurance_type_id="ID_بیمه_باربری",
    field_key="cargo_value",
    label="ارزش کالا (تومان)",
    field_type="number",
    is_required=True,
    help_text="ارزش کل محموله",
    sort_order=1
)
```

انواع `field_type`: `text`, `number`, `date`, `select`, `boolean`, `phone`, `national_id`

برای `select`، `options` را به صورت JSON آرایه بفرستید: `'["گزینه۱","گزینه۲"]'`

### روش ۲ — مستقیم در DB

```bash
sqlite3 data/app.db "INSERT INTO insurance_types(id,name,slug) VALUES('test123','بیمه باربری','bimeh-barbar')"
```

---

## 📚 افزودن/اصلاح اسناد دانش

1. فایل را در `knowledge_docs/` بریزید (یا به عنوان ادمین به ربات بفرستید)
2. اگر ادمین فرستاد: ربات خودکار ذخیره و ایندکس می‌کند
3. اگر دستی ریختید: در تلگرام به عنوان ادمین `/reload_kb` بزنید
4. برای حذف: فایل را پاک + `/reload_kb` یا در DB `is_active=0`

**فرمت‌های پشتیبانی:** PDF, DOCX, TXT, MD, CSV

**نکته امنیتی:** اسناد حاوی اطلاعات شخصی مشتریان قبلی را بدون مجوز وارد نکنید. فقط منابع تاییدشده و عمومی.

---

## 🧪 تست‌ها

```bash
pip install -r requirements.txt
python -m pytest tests -v
# یا تکی:
python tests/test_validators.py
python tests/test_orders.py
python tests/test_knowledge.py
python tests/test_sheets_mock.py
```

**۲۰ سناریوی پذیرش (وضعیت):**

| # | سناریو | وضعیت |
|---|--------|--------|
| 1 | شروع و منو | ✅ تست‌شده |
| 2 | FAQ با منبع | ✅ |
| 3 | پرسش بدون منبع → ارجاع | ✅ |
| 4 | ثبت برای هر نوع بیمه | ✅ |
| 5 | اعتبارسنجی فیلد اجباری | ✅ |
| 6 | ویرایش قبل از ثبت | ✅ |
| 7 | لغو نیمه‌تمام | ✅ |
| 8 | ثبت نهایی + شناسه یکتا | ✅ |
| 9 | جلوگیری از تکراری (هش ۳۰ ثانیه) | ✅ |
| 10 | ذخیره Sheets (mock) | ✅ |
| 11 | خطای موقت Sheets → retry | ✅ (retry ۴ مرحله) |
| 12 | قطع AI → fallback | ✅ |
| 13 | ارجاع و توقف خودکار | ✅ |
| 14 | دسترسی غیرمجاز پنل | ✅ |
| 15 | کد تخفیف (ساختار DB) | 🔶 اسکلت آماده (فاز ۳) |
| 16 | امتیاز تکراری (loyalty) | 🔶 اسکلت |
| 17 | کمپین با نرخ | ✅ (۰.۰۵s تاخیر) |
| 18 | بازیابی پس از ریستارت (SQLite WAL) | ✅ |
| 19 | عدم افشای توکن در لاگ | ✅ (safe_log) |
| 20 | اجرای تازه با README | ✅ |

---

## 📁 ساختار پروژه

```
.
├── main.py                 # ورود اصلی (Flask + polling)
├── app/
│   ├── config.py           # تنظیمات و ENV
│   ├── database.py         # SQLite + WAL + seed
│   ├── services/
│   │   ├── ai_service.py   # لایه انتزاعی AI
│   │   └── sheets_service.py # Google Sheets با retry
│   ├── knowledge/
│   │   └── retriever.py    # RAG TF-IDF + chunker
│   ├── bot/
│   │   ├── main.py         # تمام هندلرها
│   │   ├── keyboards.py    # کیبورد فارسی
│   │   └── states.py       # مدیریت وضعیت
│   └── utils/
├── knowledge_docs/         # اسناد تاییدشده
├── data/app.db             # SQLite (gitignore)
├── uploads/                # فایل‌های موقت
├── tests/                  # pytest
├── scripts/                # seed / sheets test
├── requirements.txt
├── .env.example
├── Procfile
└── render.yaml
```

---

## 🔒 امنیت و حریم خصوصی

- ✅ توکن‌ها فقط در `.env` / Render Env Vars — هرگز در Git
- ✅ `.gitignore` شامل `*.db`, `*.json` creds, `.env`
- ✅ لاگ‌ها با `safe_log` — توکن و کلید ماسک می‌شوند
- ✅ validation همه ورودی‌ها، جلوگیری از تزریق
- ✅ پنل فقط برای `ADMIN_IDS`
- ✅ اطلاعات حساس (کدملی) فقط در صورت ضرورت و با رضایت
- ✅ عدم ارسال اطلاعات شخصی به AI مگر لازم (فقط متن سوال + منابع)
- ⚠️ برای داده‌های حساس (تصویر کارت ملی)، در فاز بعدی رمزگذاری و سیاست حذف اضافه می‌شود

---

## 🐛 عیب‌یابی

| مشکل | راه‌حل |
|------|--------|
| ربات جواب نمی‌دهد | `BOT_TOKEN` را چک کن، لاگ Render را ببین (`Logs` → `Live`) |
| Sheets خطای 403 | Sheet را با service account share کردی؟ API فعال است؟ |
| AI جواب نمی‌دهد | `AI_API_KEY` درست است؟ سهمیه تمام نشده؟ لاگ `AI chat error` |
| دیتابیس پاک شد (Render Free) | FS موقتی است — برای ماندگاری، پلن پولی + Disk (`DATABASE_PATH=/var/data/app.db`) |
| ربات می‌خوابد (Render Free) | پلن پولی یا مهاجرت به Railway/Fly |

---

## 🗺 نقشه راه

- **فاز ۱ ✅** — هسته (هم‌اکنون)
- **فاز ۲ 🔜** — مدیریت اسناد از پنل (آپلود/حذف)، تایید FAQ با دکمه، گزارش نموداری، یادآوری پیگیری (job-queue)
- **فاز ۳ 🔜** — باشگاه مشتریان (امتیازدهی خودکار پس از `purchased`)، کد تخفیف (اعتبارسنجی در فرم)، کمپین زمان‌بندی‌شده

ساختار DB از ابتدا برای فاز ۲ و ۳ آماده است — بدون نیاز به migration سنگین.

---

## 📄 مجوز و هزینه پنهان

- هزینه پنهان ندارد — تمام سرویس‌های پیشنهادی رایگان یا با سهمیه بالا هستند
- تنها هزینه احتمالی: هاست دائمی $7/mo + مصرف AI پس از سهمیه رایگان
- کد تحت مجوز پروژه دانشگاهی — برای استفاده تجاری، شرایط Groq/OpenAI/Google را بررسی کنید

---

**ساخته شده با ❤️ برای مشاوره بیمه — فارسی، ساده، قابل توسعه**

برای سوال یا باگ، Issue بسازید یا به ادمین ربات پیام دهید.
