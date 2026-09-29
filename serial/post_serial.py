"""Публікує повість «Одіссея восьмого класу» по одній частині на день.

Стан зберігається в serial/state.json:
  last_part — скільки частин уже вийшло (0 — лише анонс);
  last_date — дата (Київ), коли вийшла остання частина.
Запуск за розкладом публікує наступну частину, якщо в Києві вже 15:00
і сьогодні ще нічого не виходило. Пропущений день нічого не губить:
наступна частина просто вийде наступного запуску.
Ручний запуск з PART=0..14 публікує конкретну частину.
"""
import json, os, sys, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
STATE = HERE / "state.json"
POST_HOUR = 15

def kyiv_now():
    try:
        from zoneinfo import ZoneInfo
        try:
            return datetime.now(ZoneInfo("Europe/Kyiv"))
        except Exception:
            return datetime.now(ZoneInfo("Europe/Kiev"))
    except Exception:
        return datetime.now(timezone.utc) + timedelta(hours=3)

def send(token, chat_id, text):
    data = urllib.parse.urlencode({
        "chat_id": chat_id, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    with urllib.request.urlopen(req, timeout=30) as r:
        resp = json.loads(r.read())
    if not resp.get("ok"):
        print(resp); sys.exit(1)

def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["CHAT_ID"]
    parts = {p["n"]: p["text"] for p in json.loads((HERE / "odyssey.json").read_text(encoding="utf-8"))}
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"last_part": 0, "last_date": ""}
    now = kyiv_now()
    today = now.date().isoformat()

    forced = os.environ.get("PART", "").strip()
    if forced:
        n = int(forced)
    else:
        if now.hour < POST_HOUR:
            print(f"Ще рано ({now:%H:%M} за Києвом)."); return
        if state.get("last_date") == today:
            print("Сьогодні частина вже виходила."); return
        n = state.get("last_part", 0) + 1
        if n > 14:
            print("Повість завершено."); return

    send(token, chat_id, parts[n])
    print(f"Опубліковано частину {n}")
    if n >= 1 and n >= state.get("last_part", 0):
        state = {"last_part": n, "last_date": today}
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")

if __name__ == "__main__":
    main()
