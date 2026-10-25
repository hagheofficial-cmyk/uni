"""
Google Sheets Service
- منبع حقیقت برای سفارش‌ها و گزارش
- با retry و مدیریت خطای سهمیه
- اگر تنظیم نشده باشد، به صورت mock در SQLite می‌ماند و لاگ می‌دهد
"""
import json
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List

from ..config import GOOGLE_SHEET_ID, GOOGLE_SA_JSON, GOOGLE_SA_CONTENT, SHEETS_ENABLED

log = logging.getLogger(__name__)

# نام شیت‌ها مطابق مستندات
SHEET_NAMES = {
    "customers": "Customers",
    "orders": "Orders",
    "order_details": "OrderDetails",
    "followups": "FollowUps",
    "discounts": "Discounts",
    "faq": "FAQ",
    "knowledge": "KnowledgeSources",
    "settings": "Settings",
    "campaigns": "Campaigns",
}

HEADERS = {
    "customers": ["telegram_id", "username", "first_name", "phone", "joined_at", "last_seen", "score"],
    "orders": ["order_id", "telegram_id", "customer_name", "phone", "insurance_type", "status", "assigned_to", "quoted_price", "discount_code", "source", "created_at", "updated_at", "details_json"],
    "order_details": ["order_id", "field_key", "field_label", "value"],
    "followups": ["order_id", "actor_id", "from_status", "to_status", "note", "created_at"],
    "discounts": ["code", "type", "value", "start_at", "end_at", "max_uses", "used_count", "is_active"],
    "faq": ["question", "answer", "is_approved", "is_active"],
    "knowledge": ["title", "file_path", "is_official", "chunk_count"],
    "settings": ["key", "value"],
    "campaigns": ["title", "message", "status", "sent_count"],
}

_client = None
_sheet = None

def _get_creds():
    import json as js
    from google.oauth2.service_account import Credentials
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    if GOOGLE_SA_CONTENT:
        try:
            info = js.loads(GOOGLE_SA_CONTENT)
            return Credentials.from_service_account_info(info, scopes=scopes)
        except Exception as e:
            log.error("Invalid GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT: %s", e)
            return None
    if GOOGLE_SA_JSON:
        p = Path(GOOGLE_SA_JSON)
        if p.exists():
            return Credentials.from_service_account_file(str(p), scopes=scopes)
        else:
            # شاید خود JSON باشد
            try:
                info = js.loads(GOOGLE_SA_JSON)
                return Credentials.from_service_account_info(info, scopes=scopes)
            except:
                log.warning("GOOGLE_SERVICE_ACCOUNT_JSON path not found: %s", GOOGLE_SA_JSON)
                return None
    return None

def get_client():
    global _client, _sheet
    if not SHEETS_ENABLED:
        return None
    if _client:
        return _client
    try:
        import gspread
        creds = _get_creds()
        if not creds:
            log.warning("Sheets: no credentials")
            return None
        _client = gspread.authorize(creds)
        # تست اتصال
        _sheet = _client.open_by_key(GOOGLE_SHEET_ID)
        log.info("✓ Google Sheets connected: %s", _sheet.title)
        _ensure_headers()
        return _client
    except Exception as e:
        log.error("Sheets connection failed: %s", e)
        return None

def _ensure_headers():
    """اگر شیت خالی است، هدرها را بساز"""
    if not _sheet:
        return
    try:
        for key, title in SHEET_NAMES.items():
            try:
                ws = _sheet.worksheet(title)
            except Exception:
                ws = _sheet.add_worksheet(title=title, rows=1000, cols=len(HEADERS[key])+2)
            # بررسی هدر
            headers = HEADERS.get(key, [])
            if headers:
                first_row = ws.row_values(1)
                if not first_row or first_row[0] != headers[0]:
                    ws.update("A1", [headers])
                    log.info("Sheet %s header created", title)
    except Exception as e:
        log.warning(" _ensure_headers failed: %s", e)

def _retry(func, *args, **kwargs):
    """retry با backoff برای خطاهای 429 و 5xx"""
    for attempt in range(4):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            msg = str(e).lower()
            if "429" in msg or "quota" in msg or "rate" in msg:
                wait = (2 ** attempt) * 2
                log.warning("Sheets quota hit, retry in %ss: %s", wait, e)
                time.sleep(wait)
                continue
            if attempt < 3 and ("500" in msg or "503" in msg or "timeout" in msg):
                wait = (2 ** attempt)
                log.warning("Sheets transient error, retry in %ss: %s", wait, e)
                time.sleep(wait)
                continue
            raise
    raise Exception("Sheets retry exhausted")

def append_order(order: Dict[str, Any]):
    """ثبت سفارش در شیت Orders + جزئیات در OrderDetails"""
    if not SHEETS_ENABLED:
        log.info("Sheets disabled - order %s kept in SQLite only", order.get("id"))
        return False
    client = get_client()
    if not client or not _sheet:
        log.warning("Sheets not connected, skipping append")
        return False
    try:
        def _do():
            ws = _sheet.worksheet(SHEET_NAMES["orders"])
            data = order.get("data") or {}
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except:
                    data = {}
            row = [
                order.get("id", ""),
                str(order.get("telegram_id", "")),
                order.get("contact_name") or data.get("name") or "",
                order.get("contact_phone") or data.get("phone") or "",
                order.get("insurance_type_name", ""),
                order.get("status", ""),
                str(order.get("assigned_to") or ""),
                str(order.get("quoted_price") or ""),
                order.get("discount_code") or "",
                order.get("source") or "",
                order.get("created_at", ""),
                order.get("updated_at", ""),
                json.dumps(data, ensure_ascii=False),
            ]
            ws.append_row(row, value_input_option="USER_ENTERED")
            # جزئیات فیلد به فیلد
            try:
                ws2 = _sheet.worksheet(SHEET_NAMES["order_details"])
                for k, v in data.items():
                    ws2.append_row([order.get("id"), k, k, str(v)], value_input_option="USER_ENTERED")
            except Exception as e:
                log.warning("OrderDetails append failed: %s", e)
            return True
        return _retry(_do)
    except Exception as e:
        log.error("append_order failed: %s", e)
        return False

def update_order_status_sheet(order_id: str, new_status: str, note: str = ""):
    if not SHEETS_ENABLED:
        return False
    client = get_client()
    if not client or not _sheet:
        return False
    try:
        def _do():
            ws = _sheet.worksheet(SHEET_NAMES["orders"])
            # پیدا کردن سطر با order_id
            try:
                cell = ws.find(order_id)
                if cell:
                    # ستون status = 6 (1-indexed), updated_at = 12
                    ws.update_cell(cell.row, 6, new_status)
                    ws.update_cell(cell.row, 12, datetime.now().isoformat())
                else:
                    log.warning("order %s not found in sheet", order_id)
            except Exception as e:
                log.warning("find order failed: %s", e)
            # لاگ در FollowUps
            try:
                ws2 = _sheet.worksheet(SHEET_NAMES["followups"])
                ws2.append_row([order_id, "", "", new_status, note, datetime.now().isoformat()], value_input_option="USER_ENTERED")
            except:
                pass
        return _retry(_do)
    except Exception as e:
        log.error("update_order_status_sheet failed: %s", e)
        return False

def append_customer(user: Dict[str, Any]):
    if not SHEETS_ENABLED:
        return False
    client = get_client()
    if not client or not _sheet:
        return False
    try:
        def _do():
            ws = _sheet.worksheet(SHEET_NAMES["customers"])
            # بررسی تکراری
            try:
                ws.find(str(user.get("telegram_id")))
                return True  # موجود است
            except:
                pass
            row = [
                str(user.get("telegram_id", "")),
                user.get("username") or "",
                user.get("first_name") or "",
                user.get("phone") or "",
                user.get("joined_at") or "",
                user.get("last_seen") or "",
                str(user.get("score") or 0),
            ]
            ws.append_row(row, value_input_option="USER_ENTERED")
            return True
        return _retry(_do)
    except Exception as e:
        log.error("append_customer failed: %s", e)
        return False

def test_connection():
    """برای پنل مدیریت: تست اتصال"""
    if not GOOGLE_SHEET_ID:
        return False, "GOOGLE_SHEET_ID تنظیم نشده"
    if not (GOOGLE_SA_JSON or GOOGLE_SA_CONTENT):
        return False, "حساب سرویس Google تنظیم نشده"
    c = get_client()
    if not c:
        return False, "اتصال برقرار نشد - لاگ را بررسی کنید"
    return True, f"متصل به شیت: {_sheet.title if _sheet else 'OK'}"
