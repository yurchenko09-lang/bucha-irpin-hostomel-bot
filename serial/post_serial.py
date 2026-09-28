"""Публікує повість «Одіссея восьмого класу» по одній частині на день.

Частина 1 виходить START_DATE, частина 14 — через 13 днів.
Номер частини визначається за датою в Києві, тож стан зберігати не потрібно.
Ручний запуск (workflow_dispatch) з PART=0..14 публікує конкретну частину
(0 — анонс рубрики).
"""
import json, os, sys, urllib.parse, urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

START_DATE = date(2026, 9, 28)

def kyiv_today():
    try:
        from zoneinfo import ZoneInfo
        try:
            tz = ZoneInfo("Europe/Kyiv")
        except Exception:
            tz = ZoneInfo("Europe/Kiev")
        return datetime.now(tz).date()
    except Exception:
        return (datetime.now(timezone.utc) + timedelta(hours=3)).date()

def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["CHAT_ID"]
    parts = json.loads((Path(__file__).parent / "odyssey.json").read_text(encoding="utf-8"))

    forced = os.environ.get("PART", "").strip()
    if forced:
        n = int(forced)
    else:
        n = (kyiv_today() - START_DATE).days + 1
        if not 1 <= n <= 14:
            print(f"Сьогодні частини немає (n={n}). Нічого не публікую.")
            return

    text = next(p["text"] for p in parts if p["n"] == n)
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    with urllib.request.urlopen(req, timeout=30) as r:
        resp = json.loads(r.read())
    if not resp.get("ok"):
        print(resp); sys.exit(1)
    print(f"Опубліковано частину {n}")

if __name__ == "__main__":
    main()
