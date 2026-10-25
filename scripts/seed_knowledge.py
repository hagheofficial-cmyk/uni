#!/usr/bin/env python3
"""اسکریپت افزودن دانش نمونه"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from app.database import init_db
from app.knowledge.retriever import get_kb

init_db()
kb = get_kb()
print(f"دانش بارگذاری شد: {len(kb.chunks)} قطعه")
# اگر فایل نمونه نیست، بساز
sample = Path("knowledge_docs/nemone-bimeh.txt")
if not sample.exists():
    sample.write_text("""بیمه شخص ثالث
بیمه شخص ثالث برای جبران خسارت جانی و مالی به اشخاص ثالث است. این بیمه اجباری است.
مدارک لازم: کارت خودرو، کارت ملی، بیمه‌نامه قبلی.

بیمه بدنه
پوشش سرقت، آتش‌سوزی، بلایای طبیعی، شکست شیشه. برای صدور نیاز به بازدید خودرو است.

بیمه آتش‌سوزی
پوشش ساختمان و اثاثیه در برابر آتش‌سوزی، زلزله، سیل، ترکیدگی لوله.

بیمه درمان تکمیلی
پوشش بستری، جراحی، زایمان، دندانپزشکی. دوره انتظار 3-9 ماه.

بیمه عمر
پوشش فوت، نقص عضو، سرمایه‌گذاری. مدت 5 تا 30 سال.
""", encoding="utf-8")
    print("فایل نمونه ساخته شد")
    kb.reload()
    print(f"پس از reload: {len(kb.chunks)} قطعه")
