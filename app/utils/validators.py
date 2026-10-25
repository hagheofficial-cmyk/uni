import re

def validate_phone(v: str) -> bool:
    return bool(re.match(r"^(09\d{9}|\+98\d{10})$", re.sub(r"\D","", v)))

def validate_national_id(v: str) -> bool:
    return bool(re.match(r"^\d{10}$", v.strip()))

def validate_date(v: str) -> bool:
    return bool(re.match(r"^\d{4}[/-]\d{1,2}[/-]\d{1,2}$", v.strip()))
