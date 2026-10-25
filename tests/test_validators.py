import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

def test_phone_validation():
    from app.bot.main import validate_field
    field = {"label":"شماره","field_type":"phone","is_required":True}
    ok,_ = validate_field(field, "09123456789")
    assert ok
    ok,_ = validate_field(field, "123")
    assert not ok

def test_required():
    from app.bot.main import validate_field
    field = {"label":"نام","field_type":"text","is_required":True}
    ok,_ = validate_field(field, "")
    assert not ok
    ok,_ = validate_field(field, "علی")
    assert ok

def test_number():
    from app.bot.main import validate_field
    field = {"label":"سال","field_type":"number","is_required":True, "validation_min":1300, "validation_max":1500}
    ok,_ = validate_field(field, "1402")
    assert ok
    ok,_ = validate_field(field, "1200")
    assert not ok

def test_select():
    from app.bot.main import validate_field
    import json
    field = {"label":"نوع","field_type":"select","is_required":True, "options": json.dumps(["سواری","وانت"], ensure_ascii=False)}
    ok,_ = validate_field(field, "سواری")
    assert ok
    ok,_ = validate_field(field, "کامیون")
    assert not ok

if __name__ == "__main__":
    test_phone_validation()
    test_required()
    test_number()
    test_select()
    print("validators OK")
