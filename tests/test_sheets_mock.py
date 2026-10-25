"""تستSheets بدون اتصال واقعی - باید gracefully fail کند"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from app.services.sheets_service import append_order, SHEETS_ENABLED

def test_sheets_disabled_graceful():
    # اگر SHEETS_ENABLED False باشد، باید False برگرداند نه exception
    order = {"id":"ORD-TEST","telegram_id":123,"insurance_type_name":"تست","status":"new","data":{"a":"1"},"created_at":"2025-01-01","updated_at":"2025-01-01"}
    res = append_order(order)
    # اگر غیرفعال باشد False، اگر فعال ولی بدون creds هم False
    assert res == False
    print("sheets mock OK (graceful)")

if __name__ == "__main__":
    test_sheets_disabled_graceful()
    print("sheets test passed")
