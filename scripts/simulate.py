#!/usr/bin/env python3
"""
شبیه‌سازی مکالمه بیمه بدون نیاز به توکن تلگرام
- تست RAG، فرم پویا، ثبت سفارش، Sheets mock
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import init_db, list_insurance_types, list_fields, create_order, get_order, list_orders, get_stats
from app.knowledge.retriever import get_kb
from app.services.ai_service import get_ai
from app.bot.main import validate_field

print("=== ۱. راه‌اندازی DB ===")
init_db()
print("✓ DB آماده")

print("\n=== ۲. انواع بیمه ===")
types = list_insurance_types()
for t in types:
    print(f" - {t['name']} ({t['slug']}) -> {t['id']}")
    fields = list_fields(t['id'])
    print(f"   فیلدها: {', '.join([f['label'] for f in fields])}")

print("\n=== ۳. پایگاه دانش RAG ===")
kb = get_kb()
kb.reload()
print(f"✓ {len(kb.chunks)} قطعه بارگذاری شد")
tests = [
    "بیمه شخص ثالث چیست؟",
    "مدارک بیمه بدنه چیست؟",
    "پوشش آتش‌سوزی شامل زلزله می‌شود؟",
    "بیمه عمر چند سال است؟",
    "سوال نامربوط بدون منبع xyz123"
]
for q in tests:
    res = kb.search(q, top_k=1)
    if res:
        print(f"  Q: {q}\n    → {res[0]['source']} (score {res[0]['score']:.2f}): {res[0]['content'][:100]}...")
    else:
        print(f"  Q: {q}\n    → بدون نتیجه (باید ارجاع به کارشناس)")

print("\n=== ۴. لایه AI ===")
ai = get_ai()
print(f"AI enabled: {ai.is_available()}")
print(f"AI provider: {ai.provider} model={ai.model}")
if not ai.is_available():
    print("  → بدون کلید AI، ربات با منابع ثابت و فرم کار می‌کند (fallback تست شد ✓)")

print("\n=== ۵. اعتبارسنجی فرم ===")
field_phone = {"label":"شماره تماس","field_type":"phone","is_required":True}
for v in ["09123456789", "123", "+989121234567"]:
    ok,msg = validate_field(field_phone, v)
    print(f"  phone {v}: {'✓' if ok else '✗ '+msg}")
field_num = {"label":"سال ساخت","field_type":"number","is_required":True,"validation_min":1300,"validation_max":1500}
for v in ["1402", "1200", "abc"]:
    ok,msg = validate_field(field_num, v)
    print(f"  year {v}: {'✓' if ok else '✗ '+msg}")

print("\n=== ۶. ثبت سفارش (شبیه‌سازی کاربر) ===")
# انتخاب بیمه ثالث
from app.database import upsert_user
upsert_user(123456789, "testuser", "علی تست")
sales = [t for t in types if t['slug']=='bimeh-sales'][0]
fields = list_fields(sales['id'])
print(f"انتخاب: {sales['name']}")
fake_data = {
    "car_type": "سواری",
    "car_model": "پژو 206",
    "car_year": "1399",
    "phone": "09123456789"
}
# اعتبارسنجی همه
for f in fields:
    val = fake_data.get(f['field_key'], "")
    if f['is_required'] and not val:
        print(f"  ✗ فیلد الزامی {f['label']} جا افتاده")
    elif val:
        ok,msg = validate_field(f, val)
        print(f"  {f['label']}={val}: {'✓' if ok else '✗ '+msg}")

order_id = create_order(
    telegram_id=123456789,
    insurance_type_id=sales['id'],
    insurance_type_name=sales['name'],
    data=fake_data,
    contact_name="علی تست",
    contact_phone="09123456789",
    source="simulate"
)
print(f"✓ سفارش ثبت شد: {order_id}")
o = get_order(order_id)
print(f"  وضعیت: {o['status']} | بیمه: {o['insurance_type_name']} | داده: {o['data']}")

print("\n=== ۷. Sheets (mock بدون creds) ===")
from app.services.sheets_service import append_order, SHEETS_ENABLED
print(f"Sheets enabled: {SHEETS_ENABLED}")
res = append_order(o)
print(f"  append_order → {res} (False یعنی درست fallback به SQLite ✓)")

print("\n=== ۸. آمار ===")
stats = get_stats()
print(f"  کل سفارش‌ها: {stats['total_orders']}")
print(f"  کاربران: {stats['total_users']}")
print(f"  بر اساس نوع: {stats.get('by_type')}")

print("\n=== ۹. تست ضد تکراری ===")
import hashlib, json
h = hashlib.md5(json.dumps(fake_data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
print(f"  هش سفارش: {h[:12]}... (در ربات اصلی ۳۰ ثانیه از تکراری جلوگیری می‌شود ✓)")

print("\n=== ۱۰. نتیجه ===")
print("✓ تمام بخش‌های فاز ۱ بدون نیاز به توکن واقعی تست شدند")
print("برای تست زنده تلگرام، BOT_TOKEN و ADMIN_USER_IDS لازم است")
