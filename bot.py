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
  FORCE=morning|weekly|air|news|cinema|fuel — примусово зібрати пост зараз (для перевірки)
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


# ───────────────────────────── новини ─────────────────────────────

import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime


def fetch_news() -> list[dict]:
    nc = CFG["news"]
    r = HTTP.get("https://news.google.com/rss/search",
                 params={"q": nc["query"], "hl": "uk", "gl": "UA", "ceid": "UA:uk"},
                 timeout=TIMEOUT)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    items = []
    for it in root.iter("item"):
        src = it.find("source")
        source = (src.text or "").strip() if src is not None else ""
        source_url = src.get("url", "") if src is not None else ""
        title = (it.findtext("title") or "").strip()
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3].strip()
        try:
            pub = parsedate_to_datetime(it.findtext("pubDate")).astimezone(TZ)
        except Exception:
            pub = None
        items.append({"title": title, "link": (it.findtext("link") or "").strip(),
                      "id": (it.findtext("guid") or it.findtext("link") or title).strip(),
                      "source": source, "source_url": source_url, "pub": pub})
    return items


def _words(t: str) -> set:
    return {w for w in re.findall(r"\w+", t.lower()) if len(w) > 3}


def similar(a: str, b: str) -> bool:
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return False
    return len(wa & wb) / len(wa | wb) >= 0.5


def select_news(items: list[dict], state: dict, now: datetime):
    """Повертає (відібрані, відкинуті з причиною)."""
    nc = CFG["news"]
    must = [re.compile(p, re.I) for p in nc["must_match"]]
    # стоп-слово = початок слова ("футбол" ловить "футболісти");
    # з пробілом у кінці ("гол ") — лише ціле слово (щоб не ловити "голова")
    stop = [re.compile(r"(?<!\w)" + re.escape(w.strip().lower()) + (r"\b" if w.endswith(" ") else ""))
            for w in nc["stop_words"]]
    seen = set(state.get("news_seen", []))
    recent_titles = [t for t, ts in state.get("news_recent", [])
                     if now.timestamp() - ts < 48 * 3600]
    picked, rejected = [], []
    for n in sorted(items, key=lambda x: x["pub"] or now):
        t, tl = n["title"], n["title"].lower()
        reason = None
        if n["id"] in seen:
            reason = "вже публікувалось"
        elif not n["pub"] or n["pub"].date() != now.date():
            reason = "не сьогодні"
        elif any(b in n["source_url"].lower() or b in n["source"].lower() for b in nc["blocked_sources"]):
            reason = "спортивне джерело"
        elif any(p.search(tl) for p in stop):
            reason = "спорт/стоп-слово"
        elif not any(p.search(t) for p in must):
            reason = "місто не в заголовку"
        elif any(similar(t, x) for x in recent_titles + [p["title"] for p in picked]):
            reason = "дубль"
        if reason:
            rejected.append((n, reason))
        else:
            picked.append(n)
    return picked, rejected


def format_news(n: dict) -> str:
    when = f" · {n['pub']:%H:%M}" if n["pub"] else ""
    return (f"📰 <b>{esc(n['title'])}</b>\n"
            f"{esc(n['source'])}{when}\n"
            f'<a href="{html.escape(n["link"], quote=True)}">Читати →</a>')


def run_news(state: dict, now: datetime) -> None:
    nc = CFG["news"]
    today = now.date().isoformat()
    picked, rejected = select_news(fetch_news(), state, now)

    if FORCE == "news":  # режим перегляду: показати, що відібрано і чому відкинуто
        print(f"\n=== ВІДІБРАНО: {len(picked)} ===")
        for n in picked:
            print(f"✅ [{n['pub']:%d.%m %H:%M}] {n['title']}  — {n['source']}")
        print(f"\n=== ВІДКИНУТО: {len(rejected)} ===")
        for n, why in rejected:
            ts = f"{n['pub']:%d.%m %H:%M}" if n["pub"] else "?"
            print(f"❌ ({why}) [{ts}] {n['title']}  — {n['source']}")
        print()
        if DRY_RUN:
            return

    if state.get("news_day") != today:
        state["news_day"], state["news_today"] = today, 0
    left = min(nc["max_per_run"], nc["max_per_day"] - state.get("news_today", 0))
    seen = state.setdefault("news_seen", [])
    recent = state.setdefault("news_recent", [])
    for n in picked[:max(left, 0)]:
        send(format_news(n))
        seen.append(n["id"])
        recent.append([n["title"], now.timestamp()])
        state["news_today"] = state.get("news_today", 0) + 1
        print(f"✓ новина: {n['title']}")
    state["news_seen"] = seen[-500:]
    state["news_recent"] = [x for x in recent if now.timestamp() - x[1] < 48 * 3600]


# ───────────────────────────── кіно ─────────────────────────────

from html.parser import HTMLParser

TIME_RE = re.compile(r"^\s*([01]?\d|2[0-3]):[0-5]\d\s*$")
DATE_RE = re.compile(r"(\d{1,2})\s+(січ|лют|бер|кві|тра|чер|лип|сер|вер|жов|лис|гру)\w*", re.I)
FMT_RE = re.compile(r"^\s*(2D|3D|4DX|IMAX|ScreenX)\s*$", re.I)
MONTH_BY_PREFIX = {p: i + 1 for i, p in enumerate(
    ["січ", "лют", "бер", "кві", "тра", "чер", "лип", "сер", "вер", "жов", "лис", "гру"])}


class VkinoParser(HTMLParser):
    """Іде по сторінці кінотеатру vkino.com.ua згори вниз:
    посилання на фільм (/show/...) задає поточний фільм, текст із датою — поточну дату,
    посилання з текстом «ГГ:ХХ» — сеанс поточного фільму на поточну дату."""

    def __init__(self, today: date):
        super().__init__()
        self.today = today
        self.film = None
        self.fmt = ""
        self.day = None          # дата блоку розкладу (None = ще не траплялась)
        self.a_href = None
        self.a_text = []
        self.result: dict[str, set] = {}
        self.time_links = 0

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.a_href = dict(attrs).get("href", "") or ""
            self.a_text = []

    def handle_endtag(self, tag):
        if tag != "a" or self.a_href is None:
            return
        text = " ".join("".join(self.a_text).split())
        href, self.a_href = self.a_href, None
        if TIME_RE.match(text):
            self.time_links += 1
            if self.film and (self.day is None or self.day == self.today):
                label = self.film + (f" ({self.fmt.upper()})" if self.fmt.upper() == "3D" else "")
                self.result.setdefault(label, set()).add(text.strip().zfill(5))
        elif "/show/" in href and text:
            self.film, self.fmt = text, ""
        else:
            self._check_text(text)

    def handle_data(self, data):
        if self.a_href is not None:
            self.a_text.append(data)
        else:
            self._check_text(data)

    def _check_text(self, data):
        if FMT_RE.match(data):
            self.fmt = data.strip()
            return
        m = DATE_RE.search(data)
        if m and len(data.strip()) < 40:
            try:
                self.day = date(self.today.year, MONTH_BY_PREFIX[m.group(2)[:3].lower()], int(m.group(1)))
            except ValueError:
                pass


def get_cinema_today(url: str, today: date) -> dict[str, list]:
    r = HTTP.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    p = VkinoParser(today)
    p.feed(r.text)
    print(f"[кіно] {url}: знайдено посилань-сеансів {p.time_links}, фільмів на сьогодні {len(p.result)}")
    return {f: sorted(t) for f, t in p.result.items()}


def build_cinema(now: datetime) -> str:
    today = now.date()
    parts = []
    for c in CFG["cinema"]["cinemas"]:
        try:
            films = get_cinema_today(c["url"], today)
        except Exception as e:
            print(f"[warn] кіно {c['name']}: {e}", file=sys.stderr)
            continue
        # лише сеанси, що ще попереду
        films = {f: [t for t in ts if t > f"{now:%H:%M}"] for f, ts in films.items()}
        films = {f: ts for f, ts in films.items() if ts}
        if not films:
            continue
        lines = [f"📍 <b>{esc(c['name'])}</b>"]
        for f, ts in sorted(films.items(), key=lambda kv: kv[1][0]):
            lines.append(f"• {esc(f)} — {', '.join(ts)}")
        lines.append(f'<a href="{html.escape(c["url"], quote=True)}">Квитки та деталі →</a>')
        parts.append("\n".join(lines))
    if not parts:
        raise RuntimeError("не вдалося отримати розклад жодного кінотеатру")
    head = f"🎬 <b>Кіно сьогодні</b> · {UA_WEEKDAYS[today.weekday()]}, {ua_date(today)}"
    return head + "\n\n" + "\n\n".join(parts)


# ───────────────────────────── пальне ─────────────────────────────

class TableParser(HTMLParser):
    """Збирає всі рядки всіх таблиць сторінки як списки тексту клітинок."""
    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.row:
                self.rows.append(self.row)
            self.row = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)


NUM_RE = re.compile(r"\d{2,3}[.,]\d{1,2}")


def get_fuel_prices() -> dict:
    """{мережа: {пальне: ціна}} з таблиці цін за мережами."""
    fc = CFG["fuel"]
    r = HTTP.get(fc["url"], timeout=TIMEOUT, headers={
        "Accept-Language": "uk",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"})
    r.raise_for_status()
    p = TableParser()
    p.feed(r.text)
    cols = None
    result = {}
    for row in p.rows:
        low = [c.lower() for c in row]
        # рядок-заголовок: шукаємо, в якій колонці яке пальне
        found = {}
        for fuel, aliases in fc["fuels"].items():
            for i, c in enumerate(low):
                # "а-95" не має збігтися з "а-95+" / "а-95 преміум"
                if any(c == a or c.startswith(a + " ") or c == a + "," for a in aliases) and "+" not in c:
                    found[fuel] = i
                    break
        if len(found) >= 2:
            cols = found
            continue
        if not cols or not row:
            continue
        net = next((n for n, al in fc["networks"].items() if any(a in low[0] for a in al)), None)
        if not net or net in result:
            continue
        prices = {}
        for fuel, i in cols.items():
            if i < len(row) and (m := NUM_RE.search(row[i])):
                prices[fuel] = float(m.group().replace(",", "."))
        if prices:
            result[net] = prices
    print(f"[пальне] рядків у таблицях: {len(p.rows)}, колонки: {cols}, мереж знайдено: {list(result)}")
    return result


def build_fuel(now: datetime, prices: dict, prev: dict) -> str:
    fuels = list(CFG["fuel"]["fuels"])
    w = max(len(n) for n in prices) + 1
    lines = [" " * w + "".join(f"{f:>8}" for f in fuels)]
    changes = []
    for net in CFG["fuel"]["networks"]:
        if net not in prices:
            continue
        cells = ""
        for f in fuels:
            v = prices[net].get(f)
            cells += f"{v:8.2f}" if v else f"{'—':>8}"
            old = prev.get(net, {}).get(f)
            if v and old and abs(v - old) >= 0.01:
                d = v - old
                changes.append(f"{'🔺' if d > 0 else '🔻'} {net}, {f}: {'+' if d > 0 else '−'}{abs(d):.2f} грн")
        lines.append(f"{net:<{w}}{cells}")
    txt = (f"⛽️ <b>Ціни на пальне</b> · {ua_date(now.date())}\n"
           f"<pre>{esc(chr(10).join(lines))}</pre>")
    if changes:
        txt += "\n<b>Зміни:</b>\n" + "\n".join(changes)
    elif prev:
        txt += "\nЦіни без змін ✅"
    txt += "\n<i>грн за літр · середні ціни мереж по Україні · дані Мінфіну</i>"
    return txt


def run_fuel(state: dict, now: datetime) -> None:
    prices = get_fuel_prices()
    if not prices:
        raise RuntimeError("не вдалося розібрати таблицю цін")
    prev = state.get("fuel_last", {})
    changed = prices != prev
    fc = CFG["fuel"]
    if FORCE == "fuel" or not fc["only_if_changed"] or changed or now.weekday() in fc["always_weekdays"]:
        send(build_fuel(now, prices, prev))
        print("✓ пальне")
    else:
        print("пальне: без змін, пост не потрібен")
    if not FORCE:
        state["fuel_last"] = prices
        state["last_fuel"] = now.date().isoformat()


# ───────────────────────────── розклад ─────────────────────────────

def main() -> int:
    now = now_kyiv()
    today = now.date().isoformat()
    state = load_state()
    print(f"Запуск {now:%Y-%m-%d %H:%M} ({CFG['timezone']}), dry_run={DRY_RUN}, force={FORCE or '-'}")
    errors = 0

    # 1. Ранковий пост
    m = CFG["morning"]
    due = not FORCE and m["hour"] <= now.hour < m["latest_hour"] and state.get("last_morning") != today
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
    due = (not FORCE and ALERTS_TOKEN and now.weekday() == wk["weekday"] and wk["hour"] <= now.hour < wk["latest_hour"]
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
    if FORCE == "air" or (not FORCE and ac["enabled"] and (ac["from_hour"] <= now.hour <= ac["to_hour"]
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

    # 4. Новини громади
    nc = CFG.get("news", {})
    if FORCE == "news" or (not FORCE and nc.get("enabled") and nc["from_hour"] <= now.hour < nc["to_hour"]):
        try:
            run_news(state, now)
        except Exception as e:
            errors += 1
            print(f"✗ новини: {e}", file=sys.stderr)

    # 5. Кіно
    cc = CFG.get("cinema", {})
    due = (not FORCE and cc.get("enabled") and now.weekday() in cc["weekdays"]
           and cc["hour"] <= now.hour < cc["latest_hour"] and state.get("last_cinema") != today)
    if FORCE == "cinema" or due:
        try:
            send(build_cinema(now))
            if not FORCE:
                state["last_cinema"] = today
            print("✓ кіно")
        except Exception as e:
            errors += 1
            print(f"✗ кіно: {e}", file=sys.stderr)

    # 6. Ціни на пальне
    fc = CFG.get("fuel", {})
    due = (not FORCE and fc.get("enabled") and fc["hour"] <= now.hour < fc["latest_hour"]
           and state.get("last_fuel") != today)
    if FORCE == "fuel" or due:
        try:
            run_fuel(state, now)
        except Exception as e:
            errors += 1
            print(f"✗ пальне: {e}", file=sys.stderr)

    if not (DRY_RUN and FORCE):
        save_state(state)
    return 1 if errors and FORCE else 0


if __name__ == "__main__":
    sys.exit(main())
