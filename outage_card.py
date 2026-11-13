"""
Графік відключень картинкою: 12 черг, шкала 0–24 год.
Жовта смуга — світло є, темна — світла немає, сіра штрихована — можливе відключення.
Під кожною смугою — точні години, праворуч — скільки годин зі світлом.
Змінені черги (пост «Графік змінено») виділено жовтою рамкою.
"""
import io
from datetime import date

from PIL import Image, ImageDraw

from calendar_card import _font, PAPER, INK, MUTED, NAVY, YEL, RULE, WEEKDAYS

OFF = {"no": (0, 60), "first": (0, 30), "second": (30, 60)}
MAYBE = {"maybe": (0, 60), "mfirst": (0, 30), "msecond": (30, 60)}
DARK = (38, 44, 62)
GREY = (190, 186, 178)
MONTHS_GEN = ["січня", "лютого", "березня", "квітня", "травня", "червня", "липня",
              "серпня", "вересня", "жовтня", "листопада", "грудня"]


def _spans(hours: dict, kinds: dict) -> list[list[int]]:
    out = []
    for i in range(1, 25):
        k = kinds.get(hours.get(str(i), "yes"))
        if not k:
            continue
        a, b = (i - 1) * 60 + k[0], (i - 1) * 60 + k[1]
        if out and out[-1][1] == a:
            out[-1][1] = b
        else:
            out.append([a, b])
    return out


def _hm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def render(d: date, raw: dict, title: str, updated: str = "", changed: set | None = None,
           area: str = "Буча · Ірпінь · Гостомель") -> bytes:
    """raw: {"1.1": {"1": "yes", ..., "24": "no"}, ...} — як у даних ДТЕК."""
    changed = changed or set()
    queues = sorted(raw, key=lambda q: [int(x) for x in q.split(".")])
    W = 1080
    top, row_h, axis_h = 250, 92, 50
    H = top + axis_h + row_h * len(queues) + 150
    img = Image.new("RGB", (W, H), PAPER)
    dr = ImageDraw.Draw(img)

    # шапка
    dr.rectangle((0, 0, W, 190), fill=NAVY)
    dr.text((W // 2, 62), title.upper(), font=_font("Unbounded.ttf", 40, "Bold"), fill=YEL, anchor="mm")
    sub = f"{WEEKDAYS[d.weekday()]}, {d.day} {MONTHS_GEN[d.month - 1]}"
    dr.text((W // 2, 120), sub, font=_font("Montserrat.ttf", 38, "SemiBold"), fill=(255, 255, 255), anchor="mm")
    dr.text((W // 2, 165), area, font=_font("Montserrat.ttf", 26, "Medium"), fill=(170, 182, 210), anchor="mm")

    x0, x1 = 120, W - 165                     # смуга графіка
    px = lambda m: x0 + (x1 - x0) * m / 1440   # хвилини → пікселі
    f_ax = _font("Montserrat.ttf", 22, "SemiBold")
    f_q = _font("Unbounded.ttf", 30, "Bold")
    f_t = _font("Montserrat.ttf", 27, "SemiBold")
    f_h = _font("Unbounded.ttf", 28, "Bold")
    f_hs = _font("Montserrat.ttf", 18, "SemiBold")

    # шкала годин
    ya = top + 20
    for h in range(0, 25, 3):
        dr.text((px(h * 60), ya), f"{h:02d}", font=f_ax, fill=MUTED, anchor="mm")
    dr.text((x1 + 82, ya), "світло", font=f_hs, fill=MUTED, anchor="mm")

    y = top + axis_h
    for q in queues:
        hrs = raw[q]
        off, maybe = _spans(hrs, OFF), _spans(hrs, MAYBE)
        by0, by1 = y + 6, y + 44
        if q in changed:
            dr.rounded_rectangle((12, y - 2, W - 12, y + row_h - 6), radius=14, outline=YEL, width=4)
        dr.text((62, (by0 + by1) // 2), q, font=f_q, fill=NAVY, anchor="mm")
        # смуга малюється окремо й накладається з заокругленою маскою — рівні кути
        bw, bh = int(x1 - x0), by1 - by0
        bar = Image.new("RGB", (bw, bh), YEL)
        bd = ImageDraw.Draw(bar)
        bx = lambda m: bw * m / 1440
        for a, b in maybe:                                                 # сіра штрихована ділянка
            tw = max(1, int(bx(b)) - int(bx(a)))
            tile = Image.new("RGB", (tw, bh), GREY)
            td = ImageDraw.Draw(tile)
            for sx in range(-bh, tw, 12):
                td.line((sx, bh, sx + bh, 0), fill=(150, 146, 138), width=3)
            bar.paste(tile, (int(bx(a)), 0))
        for a, b in off:
            bd.rectangle((bx(a), 0, bx(b), bh), fill=DARK)
        # поділки щогодини (напівпрозорі), кожні 3 год — помітніші
        ov = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        od = ImageDraw.Draw(ov)
        for h in range(1, 24):
            x = bx(h * 60)
            od.line((x, 0, x, bh), fill=(255, 255, 255, 170 if h % 3 == 0 else 80), width=2 if h % 3 == 0 else 1)
        bar = Image.alpha_composite(bar.convert("RGBA"), ov).convert("RGB")
        mask = Image.new("L", (bw, bh), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, bw - 1, bh - 1), radius=10, fill=255)
        img.paste(bar, (int(x0), by0), mask)
        # години відключення — під своїм відрізком (якщо підписи налазять — розсуваємо)
        labels = [(f"{_hm(a)}–{_hm(b)}", a, b, INK) for a, b in off] + \
                 [(f"? {_hm(a)}–{_hm(b)}", a, b, MUTED) for a, b in maybe]
        labels.sort(key=lambda t: t[1])
        if not labels:
            dr.text(((x0 + x1) / 2, by1 + 6), "без відключень", font=f_t, fill=MUTED, anchor="ma")
        else:
            boxes = []
            for t, a, b, col in labels:
                w = dr.textlength(t, font=f_t)
                lx = (px(a) + px(b)) / 2 - w / 2                   # по центру відрізка
                lx = max(x0, min(lx, x1 - w))
                if boxes and lx < boxes[-1][1] + 14:                # налазить на попередній — зсуваємо праворуч
                    lx = boxes[-1][1] + 14
                boxes.append([lx, lx + w, t, col])
            if boxes[-1][1] > x1:                                   # вилізли за правий край — зсуваємо ліворуч
                d_ = boxes[-1][1] - x1
                boxes[-1][0] -= d_; boxes[-1][1] -= d_
                for i in range(len(boxes) - 2, -1, -1):
                    if boxes[i][1] > boxes[i + 1][0] - 14:
                        d_ = boxes[i][1] - (boxes[i + 1][0] - 14)
                        boxes[i][0] -= d_; boxes[i][1] -= d_
            if boxes[0][0] < x0:                                    # не вміщуються під відрізками — звичайним рядком
                txt = ", ".join(t for t, *_ in labels)
                boxes = [[x0, x1, txt, INK]]
            ft = f_t
            if len(boxes) == 1:                                     # довгий рядок — зменшуємо шрифт, щоб умістився
                for sz in (27, 24, 21, 19):
                    ft = _font("Montserrat.ttf", sz, "SemiBold")
                    if dr.textlength(boxes[0][2], font=ft) <= x1 - x0:
                        break
            if len(boxes) == 1 and dr.textlength(boxes[0][2], font=ft) > x1 - x0:
                # зовсім багато відрізків — у два рядки дрібнішим шрифтом
                parts, half = boxes[0][2].split(", "), None
                half = (len(parts) + 1) // 2
                dr.text((x0, by1 + 4), ", ".join(parts[:half]) + ",", font=ft, fill=INK, anchor="la")
                dr.text((x0, by1 + 26), ", ".join(parts[half:]), font=ft, fill=INK, anchor="la")
                boxes = []
            for lx, rx, t, col in boxes:
                dr.text((lx, by1 + 6), t, font=ft, fill=col, anchor="la")
        # години зі світлом
        light = (1440 - sum(b - a for a, b in off)) / 60
        dr.text((x1 + 82, by0 + 4), f"{light:g}".replace(".", ","), font=f_h, fill=INK, anchor="ma")
        dr.text((x1 + 82, by1 + 4), "год", font=f_hs, fill=MUTED, anchor="ma")
        y += row_h

    # легенда і підпис
    y += 18
    dr.line((40, y, W - 40, y), fill=RULE, width=2)
    y += 30
    f_l = _font("Montserrat.ttf", 24, "SemiBold")
    items = [(YEL, "світло є"), (DARK, "світла немає"), (GREY, "можливе відключення")]
    if changed:
        items.append((None, "змінені черги"))
    widths = [40 + dr.textlength(t, font=f_l) + 40 for _, t in items]
    x = (W - sum(widths)) / 2
    for (col, t), w in zip(items, widths):
        if col:
            dr.rounded_rectangle((x, y, x + 30, y + 26), radius=6, fill=col)
        else:
            dr.rounded_rectangle((x, y, x + 30, y + 26), radius=6, outline=YEL, width=4)
        dr.text((x + 40, y + 13), t, font=f_l, fill=INK, anchor="lm")
        x += w
    y += 52
    foot = "Дані ДТЕК" + (f" · оновлено {updated[:16]}" if updated else "") + " · свою чергу шукайте на сайті ДТЕК"
    dr.text((W // 2, y), foot, font=_font("Montserrat.ttf", 20, "Medium"), fill=MUTED, anchor="mm")

    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()
