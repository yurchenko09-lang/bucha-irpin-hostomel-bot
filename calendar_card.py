"""
«Відривний календар» (render — веселий сезонний дизайн, render_classic — попередній строгий): картинка-листок на день.
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


def _as_list(v) -> list:
    return [] if v is None else v if isinstance(v, list) else [v]


def holidays_for(d: date) -> dict:
    k = d.strftime("%m-%d")
    out = {"state": [], "professional": [], "church": [], "world": []}
    for cat in out:
        out[cat] += _as_list(HOL.get(cat, {}).get(k))
    out["state"] += [r["name"] for r in HOL["state_rules"] if nth_weekday(d.year, r["m"], r["weekday"], r["nth"]) == d]
    out["professional"] += [r["name"] for r in HOL["professional_rules"]
                            if nth_weekday(d.year, r["m"], r["weekday"], r["nth"]) == d]
    shift = (d - orthodox_easter(d.year)).days
    out["church"] += _as_list(HOL["church_easter"].get(str(shift)))
    # однакові назви з різних розділів (напр. День молоді 12.08) — лише раз
    seen = set()
    for cat in ("state", "church", "professional", "world"):
        out[cat] = [x for x in out[cat] if not (x in seen or seen.add(x))]
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
    return (d.weekday() == 6 or any(h in (
        "Новий рік", "День Незалежності України", "День Конституції України", "День захисників і захисниць України",
        "День Української Державності") for h in hol["state"]) or any(h in big_church for h in hol["church"]))


# ───────────── малювання ─────────────

FONT_URLS = {
    "Unbounded.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/unbounded/Unbounded%5Bwght%5D.ttf",
    "Montserrat.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/montserrat/Montserrat%5Bwght%5D.ttf",
    # кольорові емодзі (бітмапна версія — її вміє малювати Pillow)
    "NotoColorEmoji.ttf": "https://raw.githubusercontent.com/googlefonts/noto-emoji/v2.047/fonts/NotoColorEmoji.ttf",
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


def render_classic(d: date, sunrise: str = "", sunset: str = "", daylen: str = "", namedays: list | None = None,
           area: str = "Буча · Ірпінь · Гостомель", birthdays: list | None = None) -> tuple[bytes, dict]:
    W, H = 1080, 1350
    hol = holidays_for(d)
    hol["birthday"] = list(birthdays or [])          # особисті дні народження (з секрету BIRTHDAYS)
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
    n_hol = sum(len(hol.get(k, [])) for k in ("birthday", "state", "church", "professional", "world"))
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
    items = [(t, (200, 60, 130)) for t in hol.get("birthday", [])] + [(t, RED) for t in hol["state"]] + [(t, (150, 90, 20)) for t in hol["church"]] + \
            [(t, NAVY) for t in hol["professional"]] + [(t, (40, 110, 90)) for t in hol.get("world", [])]
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


# ───────────── веселий дизайн (за замовчуванням) ─────────────
import random
from PIL import ImageFilter

SEASONS = {   # фон-градієнт (верх, низ), основний колір, яскравий акцент, декор
    "winter": ((150, 200, 245), (80, 100, 210), (35, 60, 170), (255, 90, 140), "❄⛄✨❄🎿"),
    "spring": ((170, 232, 190), (250, 175, 205), (25, 130, 85), (240, 80, 140), "🌸🌷🐝🌼🌱"),
    "summer": ((255, 222, 100), (255, 135, 95), (220, 85, 25), (20, 145, 200), "☀🌻🍉🌈🍓"),
    "autumn": ((255, 190, 95), (205, 85, 65), (195, 70, 30), (115, 60, 160), "🍂🍁🍄🌰🍎"),
}
SEASON_OF = {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
             6: "summer", 7: "summer", 8: "summer", 9: "autumn", 10: "autumn", 11: "autumn"}
MOOD = ["Понеділок: каву в руки — і вперед! ☕", "Вівторок — тиждень набирає обертів 🚲",
        "Середа — екватор тижня! 💪", "Четвер — до вихідних рукою подати 🙌",
        "Пʼятниця! Усміхаємось 😄", "Субота — час для себе і рідних 🧡",
        "Неділя — відпочиваємо і набираємось сил 🌿"]
NO_HOLIDAY = ["Свят немає — зробімо день особливим самі ✨", "Звичайний день для незвичайних справ 😉",
              "Сьогодні можна радіти просто так 🙂", "День без свята — свято саме по собі 🎉",
              "Запишіть сьогодні у щасливі 🍀", "Привід для усмішки знайдеться! 😊",
              "Найкраще свято — світло, тиша і рідні поруч 💡", "Без свят, зате разом із вами 🧡"]
MOON_EMO = {"Молодик": "🌑", "Зростаючий серп": "🌒", "Перша чверть": "🌓", "Зростаючий місяць": "🌔",
            "Повня": "🌕", "Спадний місяць": "🌖", "Остання чверть": "🌗", "Спадний серп": "🌘"}
HOL_EMO = {"birthday": "🎂", "state": "💙", "church": "⛪", "professional": "🛠", "world": "🌍"}
_EMO_CACHE: dict = {}


def _is_emo(ch: str) -> bool:
    return ord(ch) >= 0x2190 and ch not in "—–…«»ʼ’"


def _emoji(ch: str, size: int) -> Image.Image:
    key = (ch, size)
    if key not in _EMO_CACHE:
        f = _font("NotoColorEmoji.ttf", 109, "")
        im = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
        ImageDraw.Draw(im).text((10, 10), ch, font=f, embedded_color=True)
        bb = im.getbbox() or (0, 0, 1, 1)
        im = im.crop(bb)
        _EMO_CACHE[key] = im.resize((max(1, round(im.width * size / im.height)), size), Image.LANCZOS)
    return _EMO_CACHE[key]


def _tokens(text: str):
    """Текст → [("t", "слова"), ("e", "🍂"), …]; варіаційні селектори викидаємо."""
    out = []
    for ch in text.replace("️", ""):
        kind = "e" if _is_emo(ch) else "t"
        if kind == "t" and out and out[-1][0] == "t":
            out[-1] = ("t", out[-1][1] + ch)
        else:
            out.append((kind, ch))
    return out


def _rich_w(dr, text, font, es):
    return sum(dr.textlength(t, font=font) if k == "t" else _emoji(t, es).width + 4 for k, t in _tokens(text))


def _rich(img, dr, cx, y, text, font, fill, es=None):
    """Рядок тексту з кольоровими емодзі, по центру cx; y — верх рядка."""
    es = es or int(font.size * 1.05)
    x = cx - _rich_w(dr, text, font, es) / 2
    asc = font.getmetrics()[0]
    for k, t in _tokens(text):
        if k == "t":
            dr.text((x, y), t, font=font, fill=fill)
            x += dr.textlength(t, font=font)
        else:
            e = _emoji(t, es)
            img.paste(e, (int(x + 2), int(y + asc - es * 0.88)), e)
            x += e.width + 4


def _wrap_rich(dr, text, font, es, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if _rich_w(dr, t, font, es) <= width:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def render(d: date, sunrise: str = "", sunset: str = "", daylen: str = "", namedays: list | None = None,
           area: str = "Буча · Ірпінь · Гостомель", birthdays: list | None = None) -> tuple[bytes, dict]:
    W, H = 1080, 1350
    hol = holidays_for(d)
    hol["birthday"] = list(birthdays or [])
    red = is_red(d, hol)
    top_c, bot_c, main, pop, deco = SEASONS[SEASON_OF[d.month]]
    if d.month == 10:
        deco = "🎃🍂🍁🍄🍂"
    if d.month == 12 and d.day >= 15:
        deco = "🎄❄✨🎁⛄"
    if hol["birthday"]:
        deco = "🎈🎉🎂🎁🥳"                                   # день народження — свято на весь листок
    rnd = random.Random(d.toordinal())                    # той самий день — той самий малюнок
    # фон: градієнт + конфеті
    img = Image.new("RGB", (W, H))
    grad = ImageDraw.Draw(img)
    for yy in range(H):
        t = yy / H
        grad.line((0, yy, W, yy), fill=tuple(int(top_c[i] + (bot_c[i] - top_c[i]) * t) for i in range(3)))
    dr = ImageDraw.Draw(img, "RGBA")
    for _ in range(70):
        x, y, r = rnd.randint(0, W), rnd.randint(0, H), rnd.randint(4, 12)
        col = rnd.choice([(255, 255, 255, 120), (*pop, 150), (*main, 110), (255, 230, 120, 160)])
        dr.ellipse((x - r, y - r, x + r, y + r), fill=col)
    # декор-емодзі по краях (частково ховаються за листок)
    spots = [(20, 30), (930, 20), (-20, 600), (985, 520), (10, 1180), (950, 1200), (480, 1270)]
    for (x, y), ch in zip(spots, (deco * 2)[:len(spots)]):
        e = _emoji(ch, rnd.randint(90, 130)).rotate(rnd.randint(-25, 25), expand=True, resample=Image.BICUBIC)
        img.paste(e, (x, y), e)
    # листок з м'якою тінню
    x0, y0, x1, y1 = 75, 95, W - 75, H - 85
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((x0 + 6, y0 + 18, x1 + 6, y1 + 18), radius=46, fill=(40, 20, 40, 110))
    sh = sh.filter(ImageFilter.GaussianBlur(16))
    img.paste(sh, (0, 0), sh)
    dr = ImageDraw.Draw(img, "RGBA")
    dr.rounded_rectangle((x0, y0, x1, y1), radius=46, fill=(255, 253, 247))
    # пружинки зверху
    for i in range(7):
        cx = x0 + 110 + i * (x1 - x0 - 220) / 6
        dr.ellipse((cx - 16, y0 + 22, cx + 16, y0 + 54), fill=(225, 220, 210))
        dr.rounded_rectangle((cx - 6, y0 - 26, cx + 6, y0 + 40), radius=6, fill=(*main, 255))
    # місяць · рік — кольорова «стрічка»
    fm = _font("Unbounded.ttf", 34, "Bold")
    label = f"{MONTHS[d.month - 1]} · {d.year}"
    lw = dr.textlength(label, font=fm) + 70
    dr.rounded_rectangle((W / 2 - lw / 2, y0 + 78, W / 2 + lw / 2, y0 + 140), radius=31, fill=pop)
    dr.text((W / 2, y0 + 109), label, font=fm, fill=(255, 255, 255), anchor="mm")
    # велике число з кольоровою «тінню»
    n_hol = sum(len(hol.get(k, [])) for k in ("birthday", "state", "church", "professional", "world"))
    size = 300 if n_hol <= 2 else 250 if n_hol == 3 else 210
    fnum = _font("Unbounded.ttf", size, "Black")
    num_col = RED if red else main
    ny = y0 + 165 + (300 - size) // 3
    b = dr.textbbox((W / 2, ny), str(d.day), font=fnum, anchor="mt")
    dr.text((W / 2 + 12, ny + 12), str(d.day), font=fnum, fill=(*pop, 90), anchor="mt")
    dr.text((W / 2, ny), str(d.day), font=fnum, fill=num_col, anchor="mt")
    y = b[3] + 30
    # день тижня — «пігулка» і настрій дня
    fw = _font("Montserrat.ttf", 44, "ExtraBold")
    wd = WEEKDAYS[d.weekday()].upper()
    ww = dr.textlength(wd, font=fw) + 80
    dr.rounded_rectangle((W / 2 - ww / 2, y, W / 2 + ww / 2, y + 70), radius=35, fill=num_col)
    dr.text((W / 2, y + 35), wd, font=fw, fill=(255, 255, 255), anchor="mm")
    y += 88
    _rich(img, dr, W / 2, y, MOOD[d.weekday()], _font("Montserrat.ttf", 30, "SemiBold"), main)
    y += 58
    # сонце, місяць, день року
    _, phase = moon_phase(d)
    fi = _font("Montserrat.ttf", 29, "SemiBold")
    left = (date(d.year, 12, 31) - d).days
    if sunrise and sunset:
        _rich(img, dr, W / 2, y, f"🌅 {sunrise}    🌇 {sunset}" + (f"    ☀ {daylen}" if daylen else ""), fi, MUTED)
        y += 46
    _rich(img, dr, W / 2, y, f"{MOON_EMO.get(phase, '🌙')} {phase}   ·   до Нового року {left} дн. 🎄", fi, MUTED)
    y += 56
    # пунктир з кольорових крапок
    for x in range(x0 + 70, x1 - 60, 26):
        dr.ellipse((x, y, x + 9, y + 9), fill=rnd.choice([pop, main, (255, 200, 60)]))
    y += 34
    # свята
    items = [(f"{HOL_EMO[k]} {t}", c) for k, c in (("birthday", (215, 60, 140)), ("state", RED),
             ("church", (160, 95, 20)), ("professional", NAVY), ("world", (35, 120, 95))) for t in hol.get(k, [])]
    bottom = (y1 - 215) if namedays else (y1 - 80)
    hidden = 0
    width = x1 - x0 - 110
    if items:
        for fsz in (36, 32, 29, 26):
            fh = _font("Montserrat.ttf", fsz, "Bold")
            lh = int(fsz * 1.35)
            need = sum(len(_wrap_rich(dr, t, fh, fsz, width)) * lh + 8 for t, _ in items)
            if y + need <= bottom:
                break
        for i, (t, col) in enumerate(items):
            lines = _wrap_rich(dr, t, fh, fsz, width)
            rest = len(items) - i
            if y + len(lines) * lh > bottom - (lh if rest > 1 else 0):
                hidden = rest
                break
            for ln in lines:
                _rich(img, dr, W / 2, y, ln, fh, col, fsz)
                y += lh
            y += 8
        if hidden:
            _rich(img, dr, W / 2, y, f"…та ще {hidden} — у підписі 👇", _font("Montserrat.ttf", 26, "SemiBold"), MUTED)
    else:
        # свят немає: велика «смайлик-картинка» і фраза дня посередині вільного місця
        phrase = NO_HOLIDAY[d.toordinal() % len(NO_HOLIDAY)]
        fp = _font("Montserrat.ttf", 36, "Bold")
        lines = _wrap_rich(dr, phrase, fp, 38, width)
        big = _emoji(rnd.choice("😊🥳🤗😎🌞"), 120)
        block = big.height + 24 + len(lines) * 50
        yy = y + max(0, (bottom - y - block) // 2)
        img.paste(big, (int(W / 2 - big.width / 2), int(yy)), big)
        yy += big.height + 24
        for ln in lines:
            _rich(img, dr, W / 2, yy, ln, fp, pop, 38)
            yy += 50
    # іменини — у кольоровій плашці
    names = list(namedays or [])
    if namedays:
        py = y1 - 205
        dr.rounded_rectangle((x0 + 40, py, x1 - 40, y1 - 62), radius=28, fill=(*main, 28))
        _rich(img, dr, W / 2, py + 16, "🎈 Іменини", _font("Montserrat.ttf", 28, "ExtraBold"), main)
        fs = _font("Montserrat.ttf", 30, "SemiBold")
        while True:
            txt = ", ".join(names) + ("" if len(names) == len(namedays) else " та ін.")
            lines = _wrap(dr, txt, fs, x1 - x0 - 140)
            if len(lines) <= 2 or len(names) <= 1:
                break
            names.pop()
        yy = py + 62
        for ln in lines[:2]:
            _center(dr, yy, ln, fs, INK, W)
            yy += 40
    dr.text((W / 2, y1 - 30), area, font=_font("Montserrat.ttf", 24, "Bold"), fill=(185, 178, 165), anchor="mm")
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue(), {"holidays": hol, "hidden": hidden, "names_cut": bool(namedays) and len(names) < len(namedays)}
