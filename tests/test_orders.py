import sys, tempfile, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

# استفاده از DB موقت
import app.config as cfg
orig_db = cfg.DATABASE_PATH
cfg.DATABASE_PATH = tempfile.mktemp(suffix=".db")
import app.database as db
# monkey patch DATABASE_PATH در database module
db.DATABASE_PATH = cfg.DATABASE_PATH
# patch get_conn to use new path
from pathlib import Path as P
import sqlite3

def get_conn_tmp():
    p = P(cfg.DATABASE_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn

db.get_conn = get_conn_tmp

db.init_db()

def test_create_order():
    oid = db.create_order(123456, "type123", "تست بیمه", {"name":"علی","phone":"09123456789"}, contact_name="علی", contact_phone="09123456789")
    assert oid.startswith("ORD-")
    o = db.get_order(oid)
    assert o is not None
    assert o["telegram_id"] == 123456
    assert o["status"] == "new"
    print("create order OK", oid)

def test_status_flow():
    oid = db.create_order(123457, "type123", "تست", {"a":"1"})
    ok = db.update_order_status(oid, "assigned", actor_id=999, note="test")
    assert ok
    o = db.get_order(oid)
    assert o["status"] == "assigned"
    print("status flow OK")

def test_idempotency_simulation():
    # دو سفارش با داده متفاوت باید شناسه متفاوت داشته باشند
    oid1 = db.create_order(123458, "t1", "نوع1", {"phone":"09120000001"})
    oid2 = db.create_order(123458, "t1", "نوع1", {"phone":"09120000002"})
    assert oid1 != oid2
    print("idempotency OK")

if __name__ == "__main__":
    test_create_order()
    test_status_flow()
    test_idempotency_simulation()
    print("All order tests passed")
    # cleanup
    try:
        os.unlink(cfg.DATABASE_PATH)
    except:
        pass
