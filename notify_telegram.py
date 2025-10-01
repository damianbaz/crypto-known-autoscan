# -*- coding: utf-8 -*-
import os, requests


BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")




def send_message(text: str):
if not BOT_TOKEN or not CHAT_ID:
print("[telegram] deshabilitado (faltan creds)")
return
url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
r = requests.post(url, json={
"chat_id": CHAT_ID,
"text": text,
"parse_mode": "Markdown",
"disable_web_page_preview": True,
}, timeout=30)
if r.status_code >= 300:
print("[telegram] error:", r.text)
