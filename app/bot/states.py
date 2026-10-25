"""
مدیریت وضعیت مکالمه
- ذخیره در SQLite برای بازیابی پس از ریستارت
- جلوگیری از درخواست دوباره اطلاعات
"""
from typing import Dict, Any, Optional
from ..database import get_state, set_state, clear_state

# وضعیت‌های ممکن
STATE_IDLE = None
STATE_AWAITING_CONSULT = "awaiting_consult"  # در حال گفتگوی مشاوره
STATE_ORDER_SELECT_TYPE = "order_select_type"
STATE_ORDER_FILLING = "order_filling"  # پر کردن فرم مرحله‌ای
STATE_ORDER_CONFIRM = "order_confirm"
STATE_ORDER_EDIT_FIELD = "order_edit_field"
STATE_AWAITING_PHONE = "awaiting_phone"
STATE_HANDOFF = "handoff"  # متوقف شده برای اپراتور
STATE_ADMIN_ADD_TYPE = "admin_add_type"
STATE_ADMIN_ADD_FIELD = "admin_add_field"
STATE_ADMIN_BROADCAST = "admin_broadcast"
STATE_ADMIN_SEARCH = "admin_search"

def get_user_state(telegram_id: int) -> Dict[str, Any]:
    return get_state(telegram_id)

def set_user_state(telegram_id: int, state: str, data: Dict[str, Any] = None):
    set_state(telegram_id, state, data or {})

def update_user_data(telegram_id: int, **kwargs):
    st = get_state(telegram_id)
    data = st.get("data") or {}
    data.update(kwargs)
    set_state(telegram_id, st.get("state"), data)

def clear_user_state(telegram_id: int):
    clear_state(telegram_id)

def is_handoff_paused(telegram_id: int) -> bool:
    st = get_state(telegram_id)
    return st.get("state") == STATE_HANDOFF

def set_handoff(telegram_id: int, paused: bool = True):
    if paused:
        set_state(telegram_id, STATE_HANDOFF, {"paused_at": __import__("datetime").datetime.now().isoformat()})
    else:
        clear_state(telegram_id)
