"""
«Відривний календар»: картинка-листок на день.
Великий номер дня, місяць, день тижня, схід/захід сонця, фаза місяця,
свята (державні, професійні, церковні за новим стилем), іменини.
Неділі та державні/великі церковні свята — червоним, як на старих календарях.
"""
import io
import json
import math
from datetime import date, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE = Path(__file__).parent
FONTS = BASE / "fonts"
HOL = json.loads((BASE / "holidays.json").read_text(encoding="utf-8"))

MONTHS = ["СІЧЕНЬ", "ЛЮТИЙ", "БЕРЕЗЕНЬ", "КВІТЕНЬ", "ТРАВЕНЬ", "ЧЕРВЕНЬ", "ЛИПЕНЬ",
          "СЕРПЕНЬ", "ВЕРЕСЕНЬ", "ЖОВТЕНЬ", "ЛИСТОПАД", "ГРУДЕНЬ"]
WEEKDAYS = ["понеділок", "вівторок", "середа", "четвер", "пʼятниця", "субота", "неділя"]

PAPER = (250, 247, 240); INK = (28, 30, 36); MUTED = (110, 108, 102); RED = (200, 38, 38)
NAVY = (14, 32, 74); YEL = (255, 204, 0); RULE = (222, 216, 204)


# ───────────── календарні розрахунки ─────────────

def orthodox_easter(y: int) -> date:
    """Великдень за юліанською пасхалією (ПЦУ зберегла її), у григоріанській даті."""
    a, b, c = y % 4, y % 7, y % 19
    d = (19 * c + 15) % 30
    e = (2 * a + 4 * b - d + 34) % 7
    m = (d + e + 114) // 31
    dd = (d + e + 114) % 31 + 1
    return date(y, m, dd) + timedelta(days=13)


def nth_weekday(y: int, m: int, wd: int, n: int) -> date:
    if n > 0:
        d = date(y, m, 1)
        d += timedelta(days=(wd - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    d = nxt - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - wd) % 7)


def holidays_for(d: date) -> dict:
    k = d.strftime("%m-%d")
    out = {"state": [], "professional": [], "church": []}
    if k in HOL["state"]:
        out["state"].append(HOL["state"][k])
    out["state"] += [r["name"] for r in HOL["state_rules"] if nth_weekday(d.year, r["m"], r["weekday"], r["nth"]) == d]
    if k in HOL["professional"]:
        out["professional"].append(HOL["professional"][k])
    out["professional"] += [r["name"] for r in HOL["professional_rules"]
                            if nth_weekday(d.year, r["m"], r["weekday"], r["nth"]) == d]
    if k in HOL["church"]:
        out["church"].append(HOL["church"][k])
    shift = (d - orthodox_easter(d.year)).days
    if str(shift) in HOL["church_easter"]:
        out["church"].append(HOL["church_easter"][str(shift)])
    return out


def moon_phase(d: date) -> tuple[str, str]:
    """Фаза місяця (наближено, точність ±1 день)."""
    age = ((d - date(2000, 1, 6)).days + 0.5) % 29.530588
    names = [(1.0, "🌑", "Молодик"), (6.4, "🌒", "Зростаючий серп"), (8.4, "🌓", "Перша чверть"),
             (13.8, "🌔", "Зростаючий місяць"), (15.8, "🌕", "Повня"), (21.1, "🌖", "Спадний місяць"),
             (23.1, "🌗", "Остання чверть"), (28.5, "🌘", "Спадний серп"), (30, "🌑", "Молодик")]
    for lim, ico, name in names:
        if age < lim:
            return ico, name
    return "🌑", "Молодик"


def is_red(d: date, hol: dict) -> bool:
    big_church = {"Різдво Христове", "Великдень — Воскресіння Христове", "Трійця (Зелені свята)",
                  "Богоявлення (Водохреще)", "Покрова Пресвятої Богородиці"}
    return (d.weekday() == 6 or any(h in HOL["state"].values() and h in (
        "Новий рік", "День Незалежності України", "День Конституції України", "День захисників і захисниць України",
        "День Української Державності") for h in hol["state"]) or any(h in big_church for h in hol["church"]))


# ───────────── малювання ─────────────

FONT_URLS = {
    "Unbounded.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/unbounded/Unbounded%5Bwght%5D.ttf",
    "Montserrat.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/montserrat/Montserrat%5Bwght%5D.ttf",
}


def _font(name: str, size: int, var: str) -> ImageFont.FreeTypeFont:
    path = FONTS / name
    if not path.exists():                       # перший запуск: завантажуємо шрифт з Google Fonts (OFL)
        import requests
        FONTS.mkdir(exist_ok=True)
        r = requests.get(FONT_URLS[name], timeout=60)
        r.raise_for_status()
        path.write_bytes(r.content)
    f = ImageFont.truetype(str(path), size)
    try:
        f.set_variation_by_name(var)
    except Exception:
        pass
    return f


def _wrap(draw, text, font, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= width:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _center(draw, y, text, font, fill, W):
    w = draw.textlength(text, font=font)
    draw.text(((W - w) / 2, y), text, font=font, fill=fill)


def render(d: date, sunrise: str = "", sunset: str = "", daylen: str = "", namedays: list | None = None,
           area: str = "Буча · Ірпінь · Гостомель") -> tuple[bytes, dict]:
    W, H = 1080, 1350
    hol = holidays_for(d)
    red = is_red(d, hol)
    accent = RED if red else INK
    im = Image.new("RGB", (W, H), (60, 64, 72))
    dr = ImageDraw.Draw(im)
    # листок з тінню
    x0, y0, x1, y1 = 70, 70, W - 70, H - 60
    dr.rounded_rectangle((x0 + 10, y0 + 14, x1 + 10, y1 + 14), radius=18, fill=(40, 42, 48))
    dr.rounded_rectangle((x0, y0, x1, y1), radius=18, fill=PAPER)
    # верхня планка з отворами (корінець відривного календаря)
    dr.rounded_rectangle((x0, y0, x1, y0 + 120), radius=18, fill=NAVY)
    dr.rectangle((x0, y0 + 60, x1, y0 + 120), fill=NAVY)
    for i in range(9):
        cx = x0 + 70 + i * (x1 - x0 - 140) / 8
        dr.ellipse((cx - 13, y0 + 34, cx + 13, y0 + 60), fill=(60, 64, 72))
    _center(dr, y0 + 70, f"{MONTHS[d.month - 1]} · {d.year}", _font("Montserrat.ttf", 36, "Bold"), YEL, W)
    # лінія перфорації
    for x in range(x0 + 10, x1 - 10, 22):
        dr.line((x, y0 + 138, x + 10, y0 + 138), fill=RULE, width=3)
    # число і день тижня
    n_hol = len(hol["state"]) + len(hol["church"]) + len(hol["professional"])
    size = 360 if n_hol <= 2 else 290 if n_hol == 3 else 230
    fnum = _font("Unbounded.ttf", size, "Black")
    b = dr.textbbox((0, 0), str(d.day), font=fnum)
    top = y0 + 175 + (360 - size) // 6
    _center(dr, top - b[1], str(d.day), fnum, accent, W)
    wy = top + (b[3] - b[1]) + 45
    _center(dr, wy, WEEKDAYS[d.weekday()].upper(), _font("Montserrat.ttf", 54, "Bold"), accent, W)
    # сонце, місяць, день року
    ico, phase = moon_phase(d)
    small = _font("Montserrat.ttf", 32, "SemiBold")
    doy = d.timetuple().tm_yday
    left = (date(d.year, 12, 31) - d).days
    info = []
    if sunrise and sunset:
        info.append(f"Схід {sunrise}  ·  Захід {sunset}" + (f"  ·  День {daylen}" if daylen else ""))
    info.append(f"{phase}  ·  {doy}-й день року, до кінця року {left}")
    y = wy + 85
    for line in info:
        _center(dr, y, line, small, MUTED, W)
        y += 46
    dr.line((x0 + 60, y + 16, x1 - 60, y + 16), fill=RULE, width=3)
    y += 42
    # свята: усі, скільки є; якщо не влазять — зменшуємо шрифт, а в крайньому разі «…та ще N»
    items = [(t, RED) for t in hol["state"]] + [(t, (150, 90, 20)) for t in hol["church"]] + \
            [(t, NAVY) for t in hol["professional"]]
    bottom = (y1 - 250) if namedays else (y1 - 90)
    hidden = 0
    if items:
        for size in (38, 34, 30, 27):
            fh = _font("Montserrat.ttf", size, "Bold")
            lh = int(size * 1.3)
            need = sum(len(_wrap(dr, t, fh, x1 - x0 - 140)) * lh + 8 for t, _ in items)
            if y + need <= bottom:
                break
        yy = y
        for i, (t, col) in enumerate(items):
            lines = _wrap(dr, t, fh, x1 - x0 - 140)
            rest = len(items) - i
            if yy + len(lines) * lh > bottom - (lh if rest > 1 else 0):
                hidden = rest
                break
            for ln in lines:
                _center(dr, yy, ln, fh, col, W)
                yy += lh
            yy += 8
        if hidden:
            _center(dr, yy, f"…та ще {hidden} — у підписі", _font("Montserrat.ttf", 28, "SemiBold"), MUTED, W)
            yy += 40
        y = yy
    else:
        _center(dr, y, "Свят сьогодні немає — гарного дня!", _font("Montserrat.ttf", 34, "Medium"), MUTED, W)
        y += 50
    fs = _font("Montserrat.ttf", 34, "Medium")
    # іменини
    names = list(namedays or [])
    if namedays:
        y = max(y + 14, y1 - 230)
        dr.line((x0 + 60, y - 18, x1 - 60, y - 18), fill=RULE, width=3)
        _center(dr, y, "ІМЕНИНИ", _font("Montserrat.ttf", 28, "Bold"), MUTED, W)
        y += 42
        while True:                                   # не більше двох рядків; решта — «та ін.»
            txt = ", ".join(names) + ("" if len(names) == len(namedays) else " та ін.")
            lines = _wrap(dr, txt, fs, x1 - x0 - 140)
            if len(lines) <= 2 or len(names) <= 1:
                break
            names.pop()
        for ln in lines[:2]:
            _center(dr, y, ln, fs, INK, W)
            y += 46
    # підпис каналу
    _center(dr, y1 - 52, area, _font("Montserrat.ttf", 26, "SemiBold"), (170, 165, 155), W)
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue(), {"holidays": hol, "hidden": hidden, "names_cut": bool(namedays) and len(names) < len(namedays)}
