"""
لایه انتزاعی هوش مصنوعی
- پشتیبانی از OpenAI, Groq, Gemini (via OpenAI-compat), OpenRouter
- قابلیت تعویض ارائه‌دهنده بدون تغییر کد ربات
- fallback: اگر AI در دسترس نبود، ربات با فرم و FAQ ثابت ادامه می‌دهد
"""
import logging
from typing import List, Dict, Optional
from ..config import AI_PROVIDER, AI_API_KEY, AI_MODEL, AI_BASE_URL, AI_ENABLED

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """تو یک مشاور هوشمند بیمه به زبان فارسی هستی.
وظیفه تو:
- پاسخ کوتاه، روشن، مؤدبانه و دقیق به فارسی
- فقط بر اساس منابع تاییدشده‌ای که در پیام کاربر (بخش «منابع») آمده پاسخ بده
- اگر پاسخ در منابع نیست، صریح بگو «این مورد در منابع تاییدشده یافت نشد» و پیشنهاد بده با کارشناس صحبت کند
- هرگز پوشش، مبلغ، تخفیف یا شرایط را بدون منبع جعل نکن
- از لحن صمیمی ولی حرفه‌ای استفاده کن
- اگر کاربر نوع بیمه را نگفته، تشخیص بده و پیشنهاد مسیر بده
"""

# نگاشت base_url پیش‌فرض برای هر ارائه‌دهنده
PROVIDER_DEFAULTS = {
    "openai": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
}

class AIProvider:
    def __init__(self, provider: str = AI_PROVIDER, api_key: str = AI_API_KEY, model: str = AI_MODEL, base_url: str = None):
        self.provider = provider
        self.api_key = api_key
        self.model = model
        self.base_url = base_url or AI_BASE_URL or PROVIDER_DEFAULTS.get(provider)
        self._client = None
        if self.api_key:
            try:
                from openai import OpenAI
                self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)
                log.info("AI provider %s initialized (model=%s, base=%s)", provider, model, self.base_url)
            except Exception as e:
                log.error("AI init failed: %s", e)

    def is_available(self) -> bool:
        return bool(self._client and self.api_key)

    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.3, max_tokens: int = 800) -> Optional[str]:
        """ارسال پیام به مدل و دریافت پاسخ"""
        if not self.is_available():
            return None
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            log.error("AI chat error (%s): %s", self.provider, e)
            return None

    def answer_with_context(self, user_question: str, contexts: List[str], chat_history: List[Dict[str, str]] = None) -> Dict:
        """
        پاسخ RAG:
        - contexts: قطعات بازیابی شده
        - برمی‌گرداند dict با answer, has_source, should_handoff
        """
        if not contexts:
            # بدون منبع - نباید جعل کند
            return {
                "answer": "متأسفانه پاسخ این پرسش در منابع تاییدشده ما یافت نشد. برای اطلاعات دقیق‌تر می‌تونم شما رو به کارشناس وصل کنم — مایلید؟",
                "has_source": False,
                "should_handoff": True,
                "sources": []
            }
        context_text = "\n---\n".join([f"[منبع {i+1}]\n{c[:1500]}" for i, c in enumerate(contexts)])
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        if chat_history:
            # فقط 4 پیام آخر برای حفظ زمینه
            messages.extend(chat_history[-4:])
        messages.append({
            "role": "user",
            "content": f"منابع تاییدشده:\n{context_text}\n\nپرسش کاربر: {user_question}\n\nدستور: فقط بر اساس منابع بالا پاسخ بده. اگر پاسخ نیست بگو یافت نشد."
        })
        ans = self.chat(messages)
        if not ans:
            return {
                "answer": None,
                "has_source": False,
                "should_handoff": False,
                "sources": contexts,
                "error": "AI unavailable"
            }
        # تشخیص عدم قطعیت
        uncertain_phrases = ["یافت نشد", "موجود نیست", "اطلاعاتی ندارم", "در منابع نیست"]
        has_source = not any(p in ans for p in uncertain_phrases)
        should_handoff = not has_source
        return {
            "answer": ans,
            "has_source": has_source,
            "should_handoff": should_handoff,
            "sources": contexts
        }

    def detect_intent(self, text: str) -> str:
        """تشخیص نیت کاربر: consultation|order|handoff|faq|other"""
        lowered = text.lower()
        if any(w in lowered for w in ["کارشناس", "اپراتور", "انسانی", "تماس", "صحبت"]):
            return "handoff"
        if any(w in lowered for w in ["ثبت", "سفارش", "استعلام", "قیمت", "خرید", "بیمه"]):
            # اگر نوع بیمه ذکر شده
            return "order"
        if "؟" in text or "?" in text or any(w in lowered for w in ["چیست", "چطور", "چگونه", "آیا", "شرایط"]):
            return "consultation"
        return "other"

    def extract_entities(self, text: str) -> Dict:
        """استخراج اطلاعات از پیام فارسی (ساده - بدون AI هم کار می‌کند با regex)"""
        # اگر AI در دسترس است، می‌توان با LLM استخراج کرد، وگرنه regex
        import re
        entities = {}
        # شماره تماس ایرانی
        m = re.search(r"(09\d{9}|\+98\d{10}|0\d{10})", text)
        if m:
            entities["phone"] = m.group(1)
        # کد ملی
        m = re.search(r"\b\d{10}\b", text)
        if m:
            entities["national_id"] = m.group(0)
        # سال
        m = re.search(r"(13\d{2}|14\d{2})", text)
        if m:
            entities["year"] = m.group(0)
        return entities

    def summarize_conversation(self, messages: List[Dict[str, str]]) -> str:
        """خلاصه برای کارشناس"""
        if not self.is_available() or not messages:
            # خلاصه ساده
            return "\n".join([f"{m['role']}: {m['content'][:200]}" for m in messages[-6:]])
        prompt = "مکالمه زیر را در 3-4 جمله فارسی برای کارشناس بیمه خلاصه کن (نوع بیمه درخواستی، اطلاعات داده شده، سوال اصلی):\n\n" + "\n".join([f"{m['role']}: {m['content']}" for m in messages])
        ans = self.chat([{"role": "user", "content": prompt}], temperature=0.2, max_tokens=400)
        return ans or "خلاصه‌ای در دسترس نیست."

    def generate_faq_drafts(self, docs: List[str]) -> List[Dict]:
        """تولید پیش‌نویس FAQ از اسناد"""
        if not self.is_available():
            return []
        joined = "\n\n".join(docs[:5])[:6000]
        prompt = f"از متن زیر 5 سوال متداول با پاسخ کوتاه (هر پاسخ حداکثر 3 جمله) استخراج کن. خروجی را به صورت JSON آرایه‌ای با کلیدهای question و answer بده. فقط JSON:\n\n{joined}"
        ans = self.chat([{"role": "user", "content": prompt}], temperature=0.4, max_tokens=1200)
        if not ans:
            return []
        import json, re
        try:
            # استخراج JSON از پاسخ
            m = re.search(r"\[.*\]", ans, re.DOTALL)
            if m:
                return json.loads(m.group(0))
        except Exception as e:
            log.warning("FAQ parse failed: %s", e)
        return []

# نمونه سراسری
ai = AIProvider()

def get_ai() -> AIProvider:
    return ai
