# -*- coding: utf-8 -*-
import os
import requests

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

def send_message(text: str):
    """
    Envia un mensaje de texto a Telegram si hay credenciales.
    Si faltan credenciales, no falla: solo loguea y sale.
    """
    if not BOT_TOKEN or not CHAT_ID:
        print("[telegram] deshabilitado (faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID)")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            json={
                "chat_id": CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        if r.status_code >= 300:
            print("[telegram] error HTTP:", r.status_code, r.text[:200])
    except Exception as e:
        print("[telegram] excepción al enviar:", e)
