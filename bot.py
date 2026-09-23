"""
Бот для Telegram-каналу Бучанського району (Буча · Ірпінь · Гостомель).

Що робить (повністю автоматично):
  • Ранковий пост  — погода, якість повітря, курс НБУ, тривоги за ніч.
  • Тижнева статистика тривог — щопонеділка.
  • Попередження про погане повітря — якщо індекс AQI вищий за поріг (раз на день).

Запускається за розкладом (GitHub Actions кожні 30 хв). Сам вирішує, чи час
щось публікувати, і пам'ятає, що вже опубліковано, у файлі state.json.

Змінні середовища:
  BOT_TOKEN      — токен бота від @BotFather
  CHAT_ID        — куди публікувати: @назва_каналу, або ваш числовий ID для тестів
  ALERTS_TOKEN   — токен alerts.in.ua (необов'язково: без нього блок тривог пропускається)
  DRY_RUN=1      — нічого не надсилати, лише надрукувати пост у лог
  FORCE=morning|weekly|air — примусово зібрати пост зараз (для перевірки)
"""

import html
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

BASE = Path(__file__).parent
CFG = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
STATE_FILE = BASE / "state.json"
TZ = ZoneInfo(CFG["timezone"])

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
ALERTS_TOKEN = os.getenv("ALERTS_TOKEN", "").strip()
DRY_RUN = os.getenv("DRY_RUN", "").strip() in ("1", "true", "yes") or not BOT_TOKEN
FORCE = os.getenv("FORCE", "").strip().lower()

HTTP = requests.Session()
HTTP.headers["User-Agent"] = "bucha-region-bot/1.0"
TIMEOUT = 20


# ───────────────────────────── утиліти ─────────────────────────────

def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now_kyiv() -> datetime:
    return datetime.now(TZ)


def esc(s) -> str:
    return html.escape(str(s), quote=False)


def fmt_duration(td: timedelta) -> str:
    mins = int(round(td.total_seconds() / 60))
    h, m = divmod(mins, 60)
    if h and m:
        return f"{h} год {m} хв"
    if h:
        return f"{h} год"
    return f"{m} хв"


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


UA_WEEKDAYS = ["понеділок", "вівторок", "середа", "четвер", "пʼятниця", "субота", "неділя"]
UA_MONTHS_GEN = ["січня", "лютого", "березня", "квітня", "травня", "червня", "липня",
                 "серпня", "вересня", "жовтня", "листопада", "грудня"]


def ua_date(d: date) -> str:
    return f"{d.day} {UA_MONTHS_GEN[d.month - 1]}"


def send(text: str) -> None:
    if DRY_RUN:
        print("──── DRY RUN: пост ────")
        print(text)
        print("──────────────────────")
        return
    if not CHAT_ID:
        raise RuntimeError("CHAT_ID не задано")
    r = HTTP.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": "true"},
        timeout=TIMEOUT,
    )
    if not r.ok:
        raise RuntimeError(f"Telegram помилка {r.status_code}: {r.text[:300]}")


# ───────────────────────────── погода ─────────────────────────────

WMO = {
    0: ("☀️", "ясно"), 1: ("🌤", "переважно ясно"), 2: ("⛅️", "мінлива хмарність"),
    3: ("☁️", "хмарно"), 45: ("🌫", "туман"), 48: ("🌫", "туман з памороззю"),
    51: ("🌦", "легка мряка"), 53: ("🌦", "мряка"), 55: ("🌧", "сильна мряка"),
    56: ("🌧", "крижана мряка"), 57: ("🌧", "крижана мряка"),
    61: ("🌦", "невеликий дощ"), 63: ("🌧", "дощ"), 65: ("🌧", "сильний дощ"),
    66: ("🌧", "крижаний дощ"), 67: ("🌧", "сильний крижаний дощ"),
    71: ("🌨", "невеликий сніг"), 73: ("🌨", "сніг"), 75: ("❄️", "сильний сніг"),
    77: ("🌨", "снігова крупа"), 80: ("🌦", "короткочасний дощ"), 81: ("🌧", "зливи"),
    82: ("⛈", "сильні зливи"), 85: ("🌨", "снігопад"), 86: ("❄️", "сильний снігопад"),
    95: ("⛈", "гроза"), 96: ("⛈", "гроза з градом"), 99: ("⛈", "сильна гроза з градом"),
}


def get_weather() -> dict:
    p = CFG["weather_point"]
    r = HTTP.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": p["lat"], "longitude": p["lon"], "timezone": CFG["timezone"],
        "forecast_days": 1, "wind_speed_unit": "ms",
        "current": "temperature_2m,apparent_temperature,weather_code",
        "daily": ",".join([
            "weather_code", "temperature_2m_max", "temperature_2m_min",
            "precipitation_sum", "precipitation_probability_max",
            "wind_speed_10m_max", "wind_gusts_10m_max", "sunrise", "sunset",
        ]),
    }, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def weather_block(w: dict) -> str:
    d = {k: v[0] for k, v in w["daily"].items()}
    cur = w.get("current", {})
    icon, desc = WMO.get(int(d["weather_code"]), ("🌡", ""))
    tmin, tmax = round(d["temperature_2m_min"]), round(d["temperature_2m_max"])
    lines = [f"{icon} <b>Погода:</b> {desc}, від {tmin:+d}° до {tmax:+d}°"]
    if cur:
        lines.append(f"Зараз {round(cur['temperature_2m']):+d}°, "
                     f"відчувається як {round(cur['apparent_temperature']):+d}°")
    rain_p = d.get("precipitation_probability_max")
    if rain_p is not None and rain_p >= 30:
        lines.append(f"☔️ Імовірність опадів {int(rain_p)}%"
                     + (f", до {d['precipitation_sum']:.0f} мм" if d["precipitation_sum"] >= 1 else ""))
    lines.append(f"💨 Вітер до {d['wind_speed_10m_max']:.0f} м/с, пориви до {d['wind_gusts_10m_max']:.0f} м/с")
    sr, ss = d["sunrise"][-5:], d["sunset"][-5:]
    lines.append(f"🌅 Схід {sr} · захід {ss}")

    ww = CFG["weather_warnings"]
    warn = []
    if d["wind_gusts_10m_max"] >= ww["gusts_ms"]:
        warn.append(f"сильні пориви вітру до {d['wind_gusts_10m_max']:.0f} м/с")
    if d["temperature_2m_max"] >= ww["heat_c"]:
        warn.append(f"спека до {tmax:+d}°")
    if d["temperature_2m_min"] <= ww["frost_c"]:
        warn.append(f"сильний мороз до {tmin:+d}°")
    if d["precipitation_sum"] >= ww["precip_mm"]:
        warn.append(f"сильні опади, до {d['precipitation_sum']:.0f} мм")
    if int(d["weather_code"]) in (95, 96, 99):
        warn.append("гроза")
    if warn:
        lines.append("⚠️ <b>Увага:</b> " + "; ".join(warn))
    return "\n".join(lines)


# ─────────────────────────── якість повітря ───────────────────────────

def get_air() -> dict:
    p = CFG["weather_point"]
    r = HTTP.get("https://air-quality-api.open-meteo.com/v1/air-quality", params={
        "latitude": p["lat"], "longitude": p["lon"], "timezone": CFG["timezone"],
        "current": "european_aqi,pm2_5,pm10",
    }, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()["current"]


def aqi_label(aqi: float) -> tuple[str, str, str]:
    """Європейський індекс якості повітря (AQI) → значок, опис, порада."""
    if aqi <= 20:
        return "🟢", "чисте", "можна сміливо провітрювати й гуляти"
    if aqi <= 40:
        return "🟢", "нормальне", "можна провітрювати"
    if aqi <= 60:
        return "🟡", "трохи забруднене", "людям з астмою чи алергією краще менше бути надворі"
    if aqi <= 80:
        return "🟠", "забруднене", "зачиніть вікна й скоротіть прогулянки"
    if aqi <= 100:
        return "🔴", "сильно забруднене", "зачиніть вікна, без потреби не виходьте надвір"
    return "🟣", "небезпечно забруднене", "залишайтеся вдома з зачиненими вікнами"


AIR_WARNED_IN_MORNING = False


def air_line(a: dict) -> str:
    global AIR_WARNED_IN_MORNING
    aqi = a.get("european_aqi")
    if aqi is None:
        return ""
    icon, label, advice = aqi_label(aqi)
    if aqi > CFG["air"]["aqi_threshold"]:
        AIR_WARNED_IN_MORNING = True
    return f"{icon} <b>Повітря {label}</b> — {advice}"


# ───────────────────────────── курс НБУ ─────────────────────────────

def nbu_rate(code: str, d: date) -> float | None:
    r = HTTP.get("https://bank.gov.ua/NBUStatService/v1/statdirectory/exchange",
                 params={"valcode": code, "date": d.strftime("%Y%m%d"), "json": ""},
                 timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    return float(data[0]["rate"]) if data else None


def currency_block(today: date) -> str:
    flags = {"USD": "🇺🇸", "EUR": "🇪🇺", "PLN": "🇵🇱", "GBP": "🇬🇧"}
    parts = []
    for code in CFG["currencies"]:
        now = nbu_rate(code, today)
        prev = nbu_rate(code, today - timedelta(days=1))
        if now is None:
            continue
        s = f"{flags.get(code, '')} {code} {now:.2f}"
        if prev:
            diff = now - prev
            if abs(diff) >= 0.005:
                s += f" ({'▲' if diff > 0 else '▼'}{abs(diff):.2f})"
        parts.append(s)
    return "💵 <b>Курс НБУ:</b> " + " · ".join(parts) if parts else ""


# ───────────────────────────── тривоги ─────────────────────────────

_alerts_cache: list | None = None


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(TZ)


def is_relevant(a: dict) -> bool:
    ac = CFG["alerts"]
    if a.get("alert_type") != "air_raid":
        return False
    if a.get("location_type") == "oblast":
        return ac.get("count_whole_oblast", True)
    text = " ".join(str(a.get(k) or "") for k in ("location_title", "location_raion"))
    return any(w.lower() in text.lower() for w in ac["match_words"])


def get_alert_history() -> list[tuple[datetime, datetime | None]]:
    """Повітряні тривоги за ~місяць, що стосуються нашого району: список (початок, кінець)."""
    global _alerts_cache
    if _alerts_cache is not None:
        return _alerts_cache
    uid = CFG["alerts"]["oblast_uid"]
    r = HTTP.get(f"https://api.alerts.in.ua/v1/regions/{uid}/alerts/month_ago.json",
                 headers={"Authorization": f"Bearer {ALERTS_TOKEN}"}, timeout=TIMEOUT)
    r.raise_for_status()
    items = r.json().get("alerts", [])
    # історія може не містити тривог, що тривають просто зараз — додаємо активні
    try:
        ra = HTTP.get("https://api.alerts.in.ua/v1/alerts/active.json",
                      headers={"Authorization": f"Bearer {ALERTS_TOKEN}"}, timeout=TIMEOUT)
        ra.raise_for_status()
        seen = {a.get("id") for a in items}
        items += [a for a in ra.json().get("alerts", []) if a.get("id") not in seen]
    except Exception as e:
        print(f"[warn] active alerts: {e}", file=sys.stderr)
    _alerts_cache = [(_parse_dt(a["started_at"]), _parse_dt(a.get("finished_at")))
                     for a in items if is_relevant(a) and a.get("started_at")]
    return _alerts_cache


def merge_intervals(alerts, start: datetime, end: datetime):
    """Обрізає тривоги до вікна [start, end] і зливає ті, що перекриваються
    (напр. тривога по області + по району одночасно = одна тривога)."""
    iv = []
    for s, f in alerts:
        f = f or end  # ще триває
        s2, f2 = max(s, start), min(f, end)
        if f2 > s2:
            iv.append([s2, f2])
    iv.sort()
    merged = []
    for s, f in iv:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], f)
        else:
            merged.append([s, f])
    return merged


def night_alerts_block(now: datetime) -> str:
    if not ALERTS_TOKEN:
        return ""
    start = (now - timedelta(days=1)).replace(hour=CFG["morning"]["night_from_hour"],
                                              minute=0, second=0, microsecond=0)
    alerts = get_alert_history()
    merged = merge_intervals(alerts, start, now)
    active_now = any(s <= now and (f is None or f > now) for s, f in alerts)
    if not merged:
        return "🌙 <b>Ніч минула без тривог</b>"
    total = sum((f - s for s, f in merged), timedelta())
    n = len(merged)
    txt = (f"🌙 <b>За ніч:</b> {n} {plural(n, 'тривога', 'тривоги', 'тривог')}, "
           f"загалом {fmt_duration(total)}")
    if active_now:
        txt += "\n🔴 <b>Тривога триває зараз</b>"
    return txt


# ───────────────────────────── пости ─────────────────────────────

def safe(fn, *args):
    """Виконує блок; якщо джерело недоступне — пропускає його, а не весь пост."""
    try:
        return fn(*args)
    except Exception as e:
        print(f"[warn] {fn.__name__}: {e}", file=sys.stderr)
        return ""


def greeting(now: datetime) -> str:
    h = now.hour
    if 5 <= h < 12:
        return "☀️ <b>Доброго ранку, громадо!</b>"
    if 12 <= h < 18:
        return "🌤 <b>Доброго дня, громадо!</b>"
    if 18 <= h < 23:
        return "🌆 <b>Доброго вечора, громадо!</b>"
    return "🌙 <b>Доброї ночі, громадо!</b>"


def namedays_line(d: date) -> str:
    """Іменини за новим церковним календарем (файл namedays.json)."""
    data = json.loads((BASE / "namedays.json").read_text(encoding="utf-8"))
    names = data.get(f"{d.month:02d}-{d.day:02d}")
    return f"🎂 <b>Іменини:</b> {esc(', '.join(names))}" if names else ""


def build_morning(now: datetime) -> str:
    today = now.date()
    head = (f"{greeting(now)}\n"
            f"{UA_WEEKDAYS[today.weekday()].capitalize()}, {ua_date(today)} · {esc(CFG['area_name'])}")
    w = safe(get_weather)
    blocks = [
        safe(night_alerts_block, now),
        weather_block(w) if w else "",
        (air_line(a) if (a := safe(get_air)) else ""),
        safe(currency_block, today),
        safe(namedays_line, today),
    ]
    body = "\n\n".join(b for b in blocks if b)
    if not body:
        raise RuntimeError("жодне джерело не відповіло — пост не публікуємо")
    return f"{head}\n\n{body}"


def build_weekly(now: datetime) -> str:
    if not ALERTS_TOKEN:
        raise RuntimeError("немає ALERTS_TOKEN")
    # попередній тиждень: понеділок 00:00 — понеділок 00:00
    this_monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    start, end = this_monday - timedelta(days=7), this_monday
    merged = merge_intervals(get_alert_history(), start, end)
    period = f"{ua_date(start.date())} — {ua_date((end - timedelta(days=1)).date())}"
    if not merged:
        return f"📊 <b>Тиждень {period}</b>\n\nЖодної повітряної тривоги в районі 🙏"

    total = sum((f - s for s, f in merged), timedelta())
    longest = max(merged, key=lambda x: x[1] - x[0])
    night = sum(1 for s, _ in merged if s.hour >= 22 or s.hour < 6)
    by_day = {}
    for s, f in merged:
        by_day.setdefault(s.date(), []).append(f - s)
    worst_day, worst = max(by_day.items(), key=lambda kv: sum(kv[1], timedelta()))
    n = len(merged)
    prev = merge_intervals(get_alert_history(), start - timedelta(days=7), start)
    prev_total = sum((f - s for s, f in prev), timedelta())

    lines = [
        f"📊 <b>Тривоги за тиждень</b>\n{period} · {esc(CFG['area_name'])}",
        "",
        f"🔔 {n} {plural(n, 'тривога', 'тривоги', 'тривог')}, загалом <b>{fmt_duration(total)}</b>",
        f"🌙 З них нічних: {night}",
        f"⏱ Найдовша: {fmt_duration(longest[1] - longest[0])} "
        f"({UA_WEEKDAYS[longest[0].weekday()]}, {longest[0]:%H:%M}–{longest[1]:%H:%M})",
        f"📅 Найважчий день: {UA_WEEKDAYS[worst_day.weekday()]} — {fmt_duration(sum(worst, timedelta()))}",
    ]
    if prev:
        diff = total - prev_total
        mins = int(diff.total_seconds() // 60)
        if abs(mins) >= 30:
            word = "більше" if mins > 0 else "менше"
            lines.append(f"📈 Це на {fmt_duration(abs(diff))} {word}, ніж минулого тижня")
    return "\n".join(lines)


def build_air_warning(a: dict) -> str:
    aqi = a["european_aqi"]
    icon, label, advice = aqi_label(aqi)
    return (f"{icon} <b>Увага: повітря {label}</b>\n{esc(CFG['area_name'])}\n\n"
            f"Рівень забруднення зараз {aqi:.0f} (норма — до 40).\n\n"
            f"Що робити: {advice}. Особливо це стосується дітей, "
            "літніх людей і тих, хто має проблеми з диханням.")


# ───────────────────────────── розклад ─────────────────────────────

def main() -> int:
    now = now_kyiv()
    today = now.date().isoformat()
    state = load_state()
    print(f"Запуск {now:%Y-%m-%d %H:%M} ({CFG['timezone']}), dry_run={DRY_RUN}, force={FORCE or '-'}")
    errors = 0

    # 1. Ранковий пост
    m = CFG["morning"]
    due = m["hour"] <= now.hour < m["latest_hour"] and state.get("last_morning") != today
    if FORCE == "morning" or due:
        try:
            send(build_morning(now))
            if not FORCE:
                state["last_morning"] = today
                if AIR_WARNED_IN_MORNING:  # попередження вже було в ранковому пості
                    state["last_air"] = today
            print("✓ ранковий пост")
        except Exception as e:
            errors += 1
            print(f"✗ ранковий пост: {e}", file=sys.stderr)

    # 2. Тижнева статистика
    wk = CFG["weekly"]
    week_id = f"{now.isocalendar().year}-W{now.isocalendar().week}"
    due = (ALERTS_TOKEN and now.weekday() == wk["weekday"] and wk["hour"] <= now.hour < wk["latest_hour"]
           and state.get("last_weekly") != week_id)
    if FORCE == "weekly" or due:
        try:
            send(build_weekly(now))
            if not FORCE:
                state["last_weekly"] = week_id
            print("✓ тижнева статистика")
        except Exception as e:
            errors += 1
            print(f"✗ тижнева статистика: {e}", file=sys.stderr)

    # 3. Попередження про якість повітря
    ac = CFG["air"]
    if ac["enabled"] and (FORCE == "air" or (ac["from_hour"] <= now.hour <= ac["to_hour"]
                                             and state.get("last_air") != today)):
        try:
            a = get_air()
            aqi = a.get("european_aqi")
            print(f"AQI зараз: {aqi}")
            if aqi is not None and (aqi > ac["aqi_threshold"] or FORCE == "air"):
                send(build_air_warning(a))
                if not FORCE:
                    state["last_air"] = today
                print("✓ попередження про повітря")
        except Exception as e:
            errors += 1
            print(f"✗ якість повітря: {e}", file=sys.stderr)

    save_state(state)
    return 1 if errors and FORCE else 0


if __name__ == "__main__":
    sys.exit(main())
