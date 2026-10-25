import hashlib, json

def order_hash(data: dict) -> str:
    return hashlib.md5(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

def mask_phone(phone: str) -> str:
    if not phone or len(phone) < 7:
        return phone
    return phone[:4] + "****" + phone[-3:]

def safe_log(text: str) -> str:
    # حذف توکن‌ها
    import re
    text = re.sub(r"bot\d+:[A-Za-z0-9_-]+", "[REDACTED_TOKEN]", text)
    text = re.sub(r"sk-[A-Za-z0-9]+", "[REDACTED_KEY]", text)
    return text
