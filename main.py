#!/usr/bin/env python3
"""
ربات هوشمند بیمه - نقطه ورود اصلی
- Flask برای health-check روی Render/Railway
- polling تلگرام با retry
همه منطق در app/bot/main.py است تا تست‌پذیر و ماژولار بماند
"""
import os
import time
import threading
import logging
import asyncio
from pathlib import Path

from flask import Flask, jsonify
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# Flask app (برای Render healthCheck)
flask_app = Flask(__name__)

@flask_app.route("/")
def health():
    return jsonify({"status": "ok", "service": "bimeh-insurance-bot", "message": "ربات بیمه فعال است"}), 200

@flask_app.route("/health")
def health2():
    return jsonify({"status": "ok"}), 200

@flask_app.route("/admin")
def old_admin():
    return "<div dir='rtl' style='font-family:Tahoma;text-align:center;padding:40px'>⚙️ پنل مدیریت داخل ربات تلگرام است — دکمه «پنل مدیریت» را در ربات بزنید.</div>"

def run_flask():
    port = int(os.getenv("PORT") or os.getenv("WEBAPP_PORT") or "10000")
    log.info(f"🌐 Flask health server on 0.0.0.0:{port}")
    # host 0.0.0.0 برای Render
    flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

async def run_bot_with_retry():
    # import دیرهنگام تا اگر وابستگی‌ها نصب نشده‌اند خطا واضح باشد
    from app.bot.main import build_app
    from app.config import BOT_TOKEN
    import requests

    # تست توکن
    try:
        r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe", timeout=10)
        j = r.json()
        if j.get("ok"):
            os.environ["BOT_USERNAME"] = j["result"]["username"]
            log.info(f"🤖 Bot: @{j['result']['username']} ({j['result'].get('first_name')})")
        else:
            log.error(f"getMe failed: {j}")
    except Exception as e:
        log.warning(f"getMe error (will retry in polling): {e}")

    while True:
        try:
            app = build_app()
            await app.initialize()
            await app.start()
            await app.updater.start_polling(allowed_updates=["message","callback_query","inline_query"], drop_pending_updates=True)
            log.info("✅ Polling started — ربات آماده دریافت پیام است")
            break
        except Exception as e:
            log.error(f"⚠️ polling start failed, retry in 10s: {e}")
            try:
                await app.shutdown()
            except:
                pass
            await asyncio.sleep(10)

    # حلقه نگهدارنده
    while True:
        await asyncio.sleep(3600)

def main():
    # اجرای Flask در thread جدا
    threading.Thread(target=run_flask, daemon=True).start()
    time.sleep(1.5)
    # اجرای ربات
    try:
        asyncio.run(run_bot_with_retry())
    except KeyboardInterrupt:
        log.info("Shutdown")
    except Exception as e:
        log.exception("Fatal: %s", e)

if __name__ == "__main__":
    main()
