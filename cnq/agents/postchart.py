"""Chart image for channel posts (Pillow, optional). Dark CRYPTO NOVA look: 1h wave of the last ~5 days built from the
15-minute candles in terminal.json, support/resistance zones, scenario A/B targets from calibration.json, price tag,
fear & greed. Everything drawn comes from the same JSON the post text uses. Returns the PNG path or None."""
from __future__ import annotations

import datetime as dt
import os

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
except Exception:  # noqa: BLE001
    Image = None

FONT_DIRS = ("/usr/share/fonts/truetype/dejavu/", "/usr/share/fonts/dejavu/", "/Library/Fonts/", "C:/Windows/Fonts/")


def _font(size: int, bold: bool = False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for d in FONT_DIRS:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _fmt(p: float) -> str:
    if p >= 1000:
        return f"{p:,.0f}".replace(",", " ")
    if p >= 1:
        return f"{p:.2f}"
    return f"{p:.4f}"


def _hourly(c15: list[list]) -> list[list]:
    out = []
    for k in c15:
        h = k[0] - k[0] % 3_600_000
        if out and out[-1][0] == h:
            o = out[-1]
            o[2] = max(o[2], k[2]); o[3] = min(o[3], k[3]); o[4] = k[4]; o[5] += k[5]
        else:
            out.append([h, k[1], k[2], k[3], k[4], k[5]])
    return out


def _bridge(n: int, y0: float, y1: float, amp: float, seed: int) -> list[float]:
    """Rugged path from y0 to y1 (Brownian bridge, deterministic) — looks like a real price track, not a smooth arc."""
    import random
    rnd = random.Random(seed); w = [0.0]
    for _ in range(n):
        w.append(w[-1] + rnd.gauss(0, 1))
    return [y0 + (y1 - y0) * (k / n) ** 0.85 + amp * (w[k] - w[-1] * k / n) / (n ** 0.5) for k in range(n + 1)]


MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def _smooth(pts: list[tuple], k: int = 6) -> list[tuple]:
    """Catmull-Rom through the points — the soft «wave» line of the panel."""
    if len(pts) < 3:
        return pts
    out = []
    P = [pts[0]] + pts + [pts[-1]]
    for i in range(1, len(P) - 2):
        p0, p1, p2, p3 = P[i - 1], P[i], P[i + 1], P[i + 2]
        for j in range(k):
            t = j / k; t2 = t * t; t3 = t2 * t
            out.append(tuple(0.5 * ((2 * p1[c]) + (-p0[c] + p2[c]) * t + (2 * p0[c] - 5 * p1[c] + 4 * p2[c] - p3[c]) * t2 + (-p0[c] + 3 * p1[c] - 3 * p2[c] + p3[c]) * t3) for c in (0, 1)))
    out.append(pts[-1])
    return out


def _glow(img, pts: list[tuple], col: tuple, width: int = 10, blur: float = 6, alpha: int = 150) -> None:
    """Neon glow under a line: draw it on a transparent layer of the same colour, blur, paste. No dotted overlaps."""
    if len(pts) < 2:
        return
    layer = Image.new("RGBA", img.size, col[:3] + (0,))
    ImageDraw.Draw(layer).line(pts, fill=col[:3] + (alpha,), width=width, joint="curve")
    layer = layer.filter(ImageFilter.GaussianBlur(blur))
    img.paste(layer, (0, 0), layer)


def _label(d, xy: tuple, text: str, font, col: tuple) -> None:
    """Small text on a dark pill so a line crossing it never makes it unreadable."""
    x, y = xy; w = d.textlength(text, font=font)
    d.rounded_rectangle([x - 5, y - 2, x + w + 5, y + font.size + 4], radius=5, fill=(6, 11, 22, 200))
    d.text((x, y), text, font=font, fill=col)


def _ema(xs: list[float], n: int) -> list[float]:
    a = 2 / (n + 1); out = []; e = xs[0]
    for x in xs:
        e = a * x + (1 - a) * e; out.append(e)
    return out


def render(coin: dict, cal_open: dict | None, fng: dict | None, out_path: str, title: str | None = None, chg24: float | None = None,
           when: str | None = None, conclusion: list[str] | None = None, mode: str = "line") -> str | None:
    if Image is None or not coin.get("candles"):
        return None
    W, H = 1280, 720
    img = Image.new("RGB", (W, H), (6, 11, 22))
    d = ImageDraw.Draw(img, "RGBA")
    for i in range(40):
        d.rectangle([0, i * 6, W, i * 6 + 6], fill=(124, 196, 255, int(24 * (1 - i / 40))))
    bars = _hourly(coin["candles"])[-96:]
    if len(bars) < 20:
        return None
    L, R, T, B = 36, 92, 96, 64
    pw, ph = W - L - R, H - T - B
    HIST = 0.56                                     # history takes 56 % of the width, the prediction zone the rest
    price = coin.get("price") or bars[-1][4]
    lo = min(k[3] for k in bars); hi = max(k[2] for k in bars)
    near = [lv for lv in coin.get("levels", []) if lv.get("status") in (None, "ACTIVE") and abs(lv["center"] / price - 1) <= 0.08]
    sup = [lv for lv in near if lv["center"] < price]; res = [lv for lv in near if lv["center"] > price]
    if sup: lo = min(lo, max(sup, key=lambda l: l["center"])["lo"])
    if res: hi = max(hi, min(res, key=lambda l: l["center"])["hi"])
    if cal_open:
        for t in (cal_open.get("up"), cal_open.get("dn")):
            if t: lo, hi = min(lo, t), max(hi, t)
    pad = (hi - lo) * 0.10; lo -= pad; hi += pad
    X = lambda i: L + i / (len(bars) - 1) * pw * HIST
    Y = lambda p: T + (hi - p) / (hi - lo) * ph
    xz = X(len(bars) - 1)                            # start of the prediction zone
    f11, f12, f13, f15, f24 = _font(11), _font(12), _font(13), _font(15), _font(24, True)
    # prediction zone: tinted, hatched, labelled
    d.rectangle([xz, T, L + pw, T + ph], fill=(124, 196, 255, 16))
    for x in range(int(xz) - ph, int(L + pw), 14):
        d.line([max(x, xz), T + (max(x, xz) - x), min(x + ph, L + pw), T + (min(x + ph, L + pw) - x)], fill=(124, 196, 255, 14))
    d.line([xz, T, xz, T + ph], fill=(124, 196, 255, 150), width=1)
    d.text((xz + 12, T + 8), "ЗОНА ПРОГНОЗА · следующие 24 часа", font=_font(14, True), fill=(124, 196, 255))
    # grid + axis
    taken = [Y(price)] + ([Y(t) for t in (cal_open.get("up"), cal_open.get("dn")) if t] if cal_open else [])
    for k in range(6):
        p = lo + (hi - lo) * k / 5; y = Y(p)
        d.line([L, y, L + pw, y], fill=(160, 190, 230, 24))
        if all(abs(y - t) > 16 for t in taken):
            d.text((L + pw + 8, y - 7), _fmt(p), font=f12, fill=(125, 147, 173))
    # zones (history side only — labels never enter the prediction zone)
    for lv in coin.get("levels", [])[:6]:
        if lv.get("status") not in (None, "ACTIVE") or lv["hi"] < lo or lv["lo"] > hi:
            continue
        s_ = lv["center"] < price; col = (143, 211, 255) if s_ else (234, 246, 255); strong = lv.get("touches", 1) >= 3
        d.rectangle([L, Y(lv["hi"]), L + pw, Y(lv["lo"])], fill=col + (20 if strong else 10,))
        edge = Y(lv["hi"] if s_ else lv["lo"])
        d.line([L, edge, L + pw, edge], fill=col + (160 if strong else 80,), width=1)
        _label(d, (L + 8, edge - (17 if not s_ else -5)), f"{'поддержка' if s_ else 'сопротивление'} {_fmt(lv['lo'])}–{_fmt(lv['hi'])}", f11, col + (230,))
    # history
    pts = [(X(i), Y(k[4])) for i, k in enumerate(bars)]
    vmax = max(k[5] for k in bars) or 1
    if mode == "wave":
        # the panel's «Волна»: price as a soft glowing wave, the 20- and 50-hour averages behind it, volume as a gold wave
        closes_all = [k[4] for k in _hourly(coin["candles"])]
        e20 = _ema(closes_all, 20)[-len(bars):]; e50 = _ema(closes_all, 50)[-len(bars):]
        p20 = _smooth([(X(i), Y(v)) for i, v in enumerate(e20)][::2] + [(X(len(bars) - 1), Y(e20[-1]))])
        p50 = _smooth([(X(i), Y(v)) for i, v in enumerate(e50)][::2] + [(X(len(bars) - 1), Y(e50[-1]))])
        d.polygon(p20 + [(p20[-1][0], T + ph), (p20[0][0], T + ph)], fill=(143, 211, 255, 26))
        d.line(p20, fill=(143, 211, 255, 170), width=2, joint="curve")
        d.line(p50, fill=(184, 167, 255, 190), width=2, joint="curve")
        vs = [k[5] for k in bars]; vs = [sum(vs[max(0, i - 2):i + 3]) / len(vs[max(0, i - 2):i + 3]) for i in range(len(vs))]
        pv = _smooth([(X(i), T + ph - v / vmax * ph * 0.18) for i, v in enumerate(vs)][::2] + [(X(len(vs) - 1), T + ph - vs[-1] / vmax * ph * 0.18)])
        d.polygon(pv + [(pv[-1][0], T + ph), (pv[0][0], T + ph)], fill=(255, 190, 90, 40))
        d.line(pv, fill=(255, 200, 110, 150), width=2, joint="curve")
        cl = [k[4] for k in bars]; cl = [sum(cl[max(0, i - 1):i + 2]) / len(cl[max(0, i - 1):i + 2]) for i in range(len(cl))]
        cl[-1] = bars[-1][4]
        sp = _smooth([(X(i), Y(v)) for i, v in enumerate(cl)][::2] + [(X(len(cl) - 1), Y(cl[-1]))], k=8)
        d.polygon(sp + [(sp[-1][0], T + ph), (sp[0][0], T + ph)], fill=(200, 230, 255, 22))
        _glow(img, sp, (150, 215, 255), width=12, blur=7, alpha=170)
        d = ImageDraw.Draw(img, "RGBA")
        d.line(sp, fill=(236, 247, 255), width=3, joint="curve")
        # legend under the header
        lx, ly = L, 76
        for col, txt in (((236, 247, 255), "цена"), ((143, 211, 255), "средняя за 20 ч"), ((184, 167, 255), "средняя за 50 ч"), ((255, 200, 110), "объём торгов")):
            d.line([lx, ly + 7, lx + 22, ly + 7], fill=col, width=3)
            d.text((lx + 28, ly), txt, font=f12, fill=(190, 205, 228)); lx += 28 + d.textlength(txt, font=f12) + 22
        d.text((lx + 4, ly), "цена выше средних — импульс вверх, ниже — вниз", font=f12, fill=MUTED)
    else:
        d.polygon(pts + [(pts[-1][0], T + ph), (pts[0][0], T + ph)], fill=(124, 196, 255, 30))
        _glow(img, pts, (170, 220, 255), width=9, blur=5, alpha=150)
        d = ImageDraw.Draw(img, "RGBA")
        d.line(pts, fill=(214, 236, 255), width=2, joint="curve")
        for i, k in enumerate(bars):
            vh = k[5] / vmax * ph * 0.12
            d.rectangle([X(i) - 2, T + ph - vh, X(i) + 2, T + ph], fill=(96, 200, 214, 60))
    # day labels under the history
    seen = set()
    for i, k in enumerate(bars):
        t = dt.datetime.utcfromtimestamp(k[0] / 1000) + dt.timedelta(hours=3)
        if t.hour == 0 and t.day not in seen:
            seen.add(t.day); d.line([X(i), T + ph, X(i), T + ph + 5], fill=(125, 147, 173))
            d.text((X(i) - 18, T + ph + 8), f"{t.day} {MONTHS[t.month - 1][:3]}", font=f11, fill=(125, 147, 173))
    # scenarios: rugged paths into the zone, big labels
    x0, y0 = pts[-1]
    if cal_open and cal_open.get("p_up") is not None:
        p = cal_open["p_up"]; a_up = p >= 0.5; pa = round((p if a_up else 1 - p) * 100)
        ta = cal_open["up"] if a_up else cal_open["dn"]; tb = cal_open["dn"] if a_up else cal_open["up"]
        xe = L + pw - 6; n = 40
        amp = (hi - lo) / (hi - lo) * ph * 0.10
        for tgt, lab, col, wdt, seed in ((tb, f"B {100 - pa}%", (170, 190, 215), 3, 7), (ta, f"A {pa}%", (124, 196, 255), 5, 3)):
            if tgt is None:
                continue
            ye = Y(min(hi, max(lo, tgt)))
            ys = _bridge(n, y0, ye, amp, seed)
            path = [(x0 + (xe - x0) * k / n, ys[k]) for k in range(n + 1)]
            if mode == "wave":
                path = _smooth(path[::4] + [path[-1]])
            _glow(img, path, col, width=wdt + 8, blur=6, alpha=120)
            d = ImageDraw.Draw(img, "RGBA")
            d.line(path, fill=col + (240,), width=wdt, joint="curve")
            d.ellipse([xe - 7, ye - 7, xe + 7, ye + 7], fill=col)
            tx = xe - 230; ty = ye - (40 if ye < y0 else -14)
            ty = max(T + 30, min(T + ph - 30, ty))
            d.rounded_rectangle([tx - 8, ty - 4, tx + 222, ty + 26], radius=7, fill=(6, 11, 22, 210), outline=col + (200,))
            d.text((tx, ty), f"{lab} → {_fmt(tgt)}", font=_font(18, True), fill=col)
            taken.append(ye)
    # price tag
    y = Y(price)
    for x in range(L, int(xz), 8):
        d.line([x, y, x + 3, y], fill=(124, 196, 255, 120))
    d.rounded_rectangle([L + pw + 2, y - 13, W - 4, y + 13], radius=5, fill=(124, 196, 255))
    d.text((L + pw + 8, y - 8), _fmt(price), font=_font(13, True), fill=(3, 9, 20))
    # header
    d.text((L, 22), "CRYPTO NOVA", font=f24, fill=(234, 246, 255))
    d.text((L + 210, 30), title or "Биткоин · часовой график", font=f15, fill=(170, 195, 225))
    if when:
        d.text((L + 210, 52), when, font=f13, fill=(125, 147, 173))
    chg = chg24 if chg24 is not None else coin.get("chg24")
    if chg is not None:
        col = (111, 207, 151) if chg >= 0 else (239, 125, 107)
        d.text((W - 330, 26), f"${_fmt(price)}   {chg:+.1f}% за сутки", font=_font(20, True), fill=col)
    # conclusion box (inside the prediction zone, bottom)
    if conclusion:
        bx0, bx1 = xz + 14, L + pw - 14
        lines = []
        fb = _font(14)
        for ln in conclusion:
            words = ln.split(); cur = ""
            for w_ in words:
                if d.textlength((cur + " " + w_).strip(), font=fb) > bx1 - bx0 - 24:
                    lines.append(cur); cur = w_
                else:
                    cur = (cur + " " + w_).strip()
            lines.append(cur)
        bh = 34 + 20 * len(lines)
        by1 = T + ph - 8; by0 = by1 - bh
        # 21.10: the box hid the «B …%» label when B pointed down — go to the top of the zone if a label sits low
        if any(t_ > by0 - 40 for t_ in taken) and not any(t_ < T + 34 + bh + 40 for t_ in taken):
            by0 = T + 34; by1 = by0 + bh
        d.rounded_rectangle([bx0, by0, bx1, by1], radius=10, fill=(8, 16, 30, 225), outline=(124, 196, 255, 120))
        d.text((bx0 + 12, by0 + 8), "ВЫВОД CRYPTO NOVA", font=_font(13, True), fill=(124, 196, 255))
        for k, ln in enumerate(lines):
            d.text((bx0 + 12, by0 + 30 + 20 * k), ln, font=fb, fill=(226, 238, 250))
    foot = []
    if fng and fng.get("value") is not None:
        foot.append(f"страх и жадность {fng['value']} · {fng.get('label', '')}")
    r = coin.get("regime") or {}
    if r.get("long"):
        foot.append("режим: " + ("бычий (выше 200-дневной средней)" if r["long"] == "BULL" else "медвежий (ниже 200-дневной средней)"))
    d.text((L, H - 30), " · ".join(foot) + " · сценарии — оценка, не гарантия · не финансовый совет", font=f12, fill=(125, 147, 173))
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    return out_path


# ---------------------------------------------------------------------------------------------------------------- board
# 21.10 owner: «можно более интересный график в группе показывать или разное совмещать» — the BTC chart with scenarios
# stays on top; under it a strip of four live tiles: ETH and SOL with their 4-day waves, the fear & greed gauge, and
# either our day result (evening) or the day's leaders (morning).
UP, DOWN, ICE, MUTED, TXT = (61, 220, 151), (255, 110, 134), (124, 196, 255), (125, 147, 173), (234, 246, 255)


def _tile(d, box, title: str):
    x0, y0, x1, y1 = box
    d.rounded_rectangle(box, radius=16, fill=(10, 19, 38), outline=(40, 62, 98))
    d.text((x0 + 16, y0 + 12), title, font=_font(15, True), fill=(170, 195, 225))


def _spark_tile(d, box, name: str, coin: dict | None, chg24: float | None):
    _tile(d, box, name)
    x0, y0, x1, y1 = box
    if not coin or not coin.get("candles"):
        d.text((x0 + 16, y0 + 50), "нет данных", font=_font(14), fill=MUTED)
        return
    bars = _hourly(coin["candles"])[-96:]
    closes = [b[4] for b in bars]
    price = coin.get("price") or closes[-1]
    chg = chg24 if chg24 is not None else coin.get("chg24")
    col = UP if (chg or 0) >= 0 else DOWN
    d.text((x0 + 16, y0 + 38), f"${_fmt(price)}", font=_font(26, True), fill=TXT)
    if chg is not None:
        d.text((x0 + 16, y0 + 72), f"{chg:+.1f}% за сутки", font=_font(15, True), fill=col)
    lo, hi = min(closes), max(closes); sp = (hi - lo) or 1
    gx0, gx1, gy0, gy1 = x0 + 16, x1 - 16, y0 + 104, y1 - 18
    pts = [(gx0 + (gx1 - gx0) * i / (len(closes) - 1), gy1 - (c - lo) / sp * (gy1 - gy0)) for i, c in enumerate(closes)]
    d.polygon(pts + [(gx1, gy1), (gx0, gy1)], fill=col + (38,))
    d.line(pts, fill=col + (255,), width=3, joint="curve")
    d.ellipse([pts[-1][0] - 5, pts[-1][1] - 5, pts[-1][0] + 5, pts[-1][1] + 5], fill=col)


def _fng_tile(d, box, fng: dict | None):
    import math
    _tile(d, box, "Страх и жадность")
    x0, y0, x1, y1 = box
    if not fng or fng.get("value") is None:
        d.text((x0 + 16, y0 + 50), "нет данных", font=_font(14), fill=MUTED)
        return
    v = max(0, min(100, int(fng["value"])))
    cx, cy, r = (x0 + x1) / 2, y1 - 74, min((x1 - x0) / 2 - 30, (y1 - y0) - 120)
    segs = [((239, 90, 90), 0, 25), ((255, 160, 90), 25, 45), ((230, 220, 120), 45, 55), ((150, 220, 120), 55, 75), ((61, 220, 151), 75, 100)]
    for col, a, b in segs:
        d.arc([cx - r, cy - r, cx + r, cy + r], 180 + a * 1.8, 180 + b * 1.8, fill=col, width=14)
    ang = math.radians(180 + v * 1.8)
    d.line([cx, cy, cx + (r - 22) * math.cos(ang), cy + (r - 22) * math.sin(ang)], fill=TXT, width=4)
    d.ellipse([cx - 7, cy - 7, cx + 7, cy + 7], fill=TXT)
    d.text((cx, cy + 12), str(v), font=_font(24, True), fill=TXT, anchor="mt")
    lab = {"Extreme Fear": "крайний страх", "Fear": "страх", "Neutral": "нейтрально", "Greed": "жадность", "Extreme Greed": "крайняя жадность"}.get(fng.get("label"), fng.get("label") or "")
    sub = lab + (f" · вчера {fng['yesterday']}" if fng.get("yesterday") is not None else "")
    d.text((cx, cy + 42), sub, font=_font(13), fill=MUTED, anchor="mt")


def _rows_tile(d, box, title: str, rows: list[tuple[str, str, tuple]]):
    _tile(d, box, title)
    x0, y0, x1, y1 = box
    if not rows:
        d.text((x0 + 16, y0 + 50), "нет данных", font=_font(14), fill=MUTED)
        return
    step = min(44, (y1 - y0 - 56) // max(1, len(rows)))
    for k, (a, b, col) in enumerate(rows):
        y = y0 + 52 + k * step
        d.text((x0 + 16, y), a, font=_font(17), fill=(200, 215, 238))
        d.text((x1 - 16, y), b, font=_font(19, True), fill=col, anchor="ra")


def board(chart_png: str, out_path: str, eth: dict | None = None, sol: dict | None = None, eth_chg: float | None = None,
          sol_chg: float | None = None, fng: dict | None = None, result: list[tuple[str, str, tuple]] | None = None,
          result_title: str = "Итог дня по счетам") -> str | None:
    """The post picture: the BTC chart (as rendered by render()) on top, four live tiles under it. 1280 × 1000."""
    if Image is None or not os.path.exists(chart_png):
        return None
    top = Image.open(chart_png).convert("RGB")
    W = top.width; H = top.height + 280
    img = Image.new("RGB", (W, H), (6, 11, 22))
    img.paste(top, (0, 0))
    d = ImageDraw.Draw(img, "RGBA")
    y0, y1, g = top.height + 4, H - 20, 16
    tw = (W - 36 * 2 - g * 3) / 4
    boxes = [(36 + i * (tw + g), y0, 36 + i * (tw + g) + tw, y1) for i in range(4)]
    _spark_tile(d, boxes[0], "Эфир · ETH", eth, eth_chg)
    _spark_tile(d, boxes[1], "Солана · SOL", sol, sol_chg)
    _fng_tile(d, boxes[2], fng)
    _rows_tile(d, boxes[3], result_title, result or [])
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    return out_path



# ------------------------------------------------------------------------------------------------------------ heatmap
def _squarify(items: list[tuple], x: float, y: float, w: float, h: float) -> list[tuple]:
    """Squarified treemap: items = [(weight, payload)] sorted desc → [(x, y, w, h, payload)]."""
    out = []
    items = [it for it in items if it[0] > 0]
    total = sum(v for v, _ in items) or 1
    scale = w * h / total
    rest = [(v * scale, p) for v, p in items]

    def worst(row, side):
        s_ = sum(r for r, _ in row)
        return max(max(side * side * r / (s_ * s_), (s_ * s_) / (side * side * r)) for r, _ in row)

    while rest:
        side = min(w, h); row = [rest.pop(0)]
        while rest and worst(row + [rest[0]], side) <= worst(row, side):
            row.append(rest.pop(0))
        s_ = sum(r for r, _ in row)
        if w >= h:
            cw = s_ / h; yy = y
            for r, pl in row:
                ch = r / cw; out.append((x, yy, cw, ch, pl)); yy += ch
            x += cw; w -= cw
        else:
            rh = s_ / w; xx = x
            for r, pl in row:
                cw_ = r / rh; out.append((xx, y, cw_, rh, pl)); xx += cw_
            y += rh; h -= rh
    return out


def heatmap(markets: dict, out_path: str, when: str | None = None, n: int = 30) -> str | None:
    """The crypto market map: the top coins by market cap as tiles sized by cap and coloured by the 24 h change. 1280 × 720."""
    if Image is None:
        return None
    skip = {"XAUT", "PAXG", "WBT", "LEO", "WBTC", "WETH", "STETH", "WSTETH", "WEETH", "CBBTC"}
    coins = [c for c in markets.get("top", []) if not c.get("stable") and not c.get("wrapped") and c.get("mcap") and c.get("chg24") is not None
             and c.get("symbol") not in skip and str(c.get("symbol", "")).isalnum()][:n]
    if len(coins) < 8:
        return None
    W, H = 1280, 720
    img = Image.new("RGB", (W, H), (6, 11, 22)); d = ImageDraw.Draw(img, "RGBA")
    for i in range(40):
        d.rectangle([0, i * 6, W, i * 6 + 6], fill=(124, 196, 255, int(24 * (1 - i / 40))))
    d.text((36, 22), "CRYPTO NOVA", font=_font(24, True), fill=TXT)
    d.text((246, 30), "Карта рынка · топ-30 монет за сутки", font=_font(15), fill=(170, 195, 225))
    if when:
        d.text((246, 52), when, font=_font(13), fill=MUTED)
    g = markets.get("global") or {}
    ups = sum(1 for c in coins if c["chg24"] >= 0)
    d.text((W - 36, 24), f"растут {ups} из {len(coins)}", font=_font(22, True), fill=UP if ups * 2 >= len(coins) else DOWN, anchor="ra")
    sub = []
    if g.get("total_mcap"):
        sub.append(f"рынок ${g['total_mcap'] / 1e12:.2f} трлн")
    if g.get("btc_dom"):
        sub.append(f"доля биткоина {g['btc_dom']:.1f}%")
    if sub:
        d.text((W - 36, 56), " · ".join(sub), font=_font(13), fill=MUTED, anchor="ra")
    # area ∝ sqrt(cap) so altcoins stay readable next to bitcoin
    items = [(c["mcap"] ** 0.5, c) for c in coins]
    for x, y, w, h, c in _squarify(items, 36, 92, W - 72, H - 92 - 46):
        ch = max(-8.0, min(8.0, c["chg24"])); k = abs(ch) / 8
        base = (24, 150, 100) if ch >= 0 else (190, 55, 75)
        col = tuple(int(14 + (b_ - 14) * (0.25 + 0.75 * k)) for b_ in base)
        d.rounded_rectangle([x + 2, y + 2, x + w - 2, y + h - 2], radius=8, fill=col + (255,), outline=(255, 255, 255, 30))
        big = min(w, h)
        fs = max(11, min(40, int(big / 4.2)))
        if w > 46 and h > 30:
            d.text((x + w / 2, y + h / 2 - fs * 0.25), c["symbol"], font=_font(fs, True), fill=(245, 250, 255), anchor="mb")
            d.text((x + w / 2, y + h / 2 + fs * 0.15), ("0.0%" if abs(c["chg24"]) < 0.05 else f"{c['chg24']:+.1f}%"), font=_font(max(10, int(fs * 0.62))), fill=(235, 245, 255, 230), anchor="mt")
    d.text((36, H - 30), "площадь — капитализация монеты, цвет — изменение за сутки · данные CoinGecko · не финансовый совет", font=_font(12), fill=MUTED)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    return out_path


# -------------------------------------------------------------------------------------------------------- engine card
def _turbine(d, cx: float, cy: float, r: float, blades: int = 10, phase: float = 0.0):
    import math
    d.ellipse([cx - r * 1.28, cy - r * 1.28, cx + r * 1.28, cy + r * 1.28], fill=(0, 180, 216, 22))
    for k in range(48):
        a = k * math.pi / 24; l = 0.12 if k % 4 == 0 else 0.06
        d.line([cx + math.cos(a) * r * 1.1, cy + math.sin(a) * r * 1.1, cx + math.cos(a) * r * (1.1 + l), cy + math.sin(a) * r * (1.1 + l)], fill=(127, 211, 255, 110), width=1)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(8, 16, 44, 255), outline=(127, 211, 255, 150), width=2)
    for i in range(blades):
        a0 = phase + i * 2 * math.pi / blades
        poly = []
        for t, off in ((0.26, -0.02), (0.56, -0.30), (0.86, -0.10), (0.86, 0.04), (0.55, -0.04), (0.26, 0.10)):
            ang = a0 + off; rr = r * t
            poly.append((cx + math.cos(ang) * rr, cy + math.sin(ang) * rr))
        d.polygon(poly, fill=(127, 211, 255, 70), outline=(170, 225, 255, 160))
    for k in range(10, 0, -1):
        rr = r * 0.24 * k / 10
        col = (int(14 + 220 * (1 - k / 10)), int(120 + 125 * (1 - k / 10)), int(200 + 55 * (1 - k / 10)))
        d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=col + (255,))


IRIS = [(127, 227, 255), (154, 166, 255), (210, 156, 255), (255, 194, 166)]


def _fit(d, text: str, maxw: float, size: int, bold: bool = True, min_size: int = 14):
    while size > min_size and d.textlength(text, font=_font(size, bold)) > maxw:
        size -= 1
    return _font(size, bold)


def _wrap(d, text: str, font, maxw: float) -> list[str]:
    lines, cur = [], ""
    for w_ in text.split():
        if cur and d.textlength(cur + " " + w_, font=font) > maxw:
            lines.append(cur); cur = w_
        else:
            cur = (cur + " " + w_).strip()
    return lines + ([cur] if cur else [])


def _viz_pulse(img, d, box, cycle: int):
    import math
    x0, y0, x1, y1 = box; mid = (y0 + y1) / 2; pts = []
    n = 140
    for i in range(n + 1):
        x = x0 + (x1 - x0) * i / n; ph_ = (i % 23) / 23
        y = mid
        if 0.40 < ph_ < 0.46: y = mid - (y1 - y0) * 0.42
        elif 0.46 <= ph_ < 0.52: y = mid + (y1 - y0) * 0.30
        elif 0.30 < ph_ <= 0.40: y = mid - (y1 - y0) * 0.06 * math.sin((ph_ - 0.3) * 31)
        pts.append((x, y))
    _glow(img, pts, ICE, width=6, blur=4, alpha=140)
    d = ImageDraw.Draw(img, "RGBA")
    d.line(pts, fill=ICE + (230,), width=2)
    d.ellipse([x1 - 6, mid - 6, x1 + 6, mid + 6], fill=ICE)
    return d


def _viz_funnel(d, box, nums: list[int]):
    x0, y0, x1, y1 = box; n = len(nums); g = 10; w = (x1 - x0 - g * (n - 1)) / n; top = max(nums) or 1
    for i, v in enumerate(nums):
        h = max(8, (y1 - y0 - 18) * (v / top) ** 0.5); cx = x0 + i * (w + g)
        col = IRIS[i % len(IRIS)]
        d.rounded_rectangle([cx, y1 - 18 - h, cx + w, y1 - 18], radius=5, fill=col + (200,))
        d.text((cx + w / 2, y1 - 14), f"{v}", font=_font(13, True), fill=TXT, anchor="mt")
        if i < n - 1:
            d.text((cx + w + g / 2, y1 - 18 - 10), "›", font=_font(14, True), fill=MUTED, anchor="mm")


def _viz_stages(d, box, stages: list[tuple[str, int]]):
    x0, y0, x1, y1 = box; n = len(stages); g = 18; w = (x1 - x0 - g * (n - 1)) / n
    for i, (lab, v) in enumerate(stages):
        cx = x0 + i * (w + g); col = IRIS[i % len(IRIS)]
        d.rounded_rectangle([cx, y0 + 4, cx + w, y1], radius=10, fill=col + (34,), outline=col + (200,), width=2)
        d.text((cx + w / 2, y0 + 10), str(v), font=_font(20, True), fill=TXT, anchor="mt")
        d.text((cx + w / 2, y1 - 6), lab, font=_font(11), fill=(200, 215, 238), anchor="mb")
        if i < n - 1:
            d.text((cx + w + g / 2, (y0 + y1) / 2 + 2), "→", font=_font(15, True), fill=ICE, anchor="mm")


def _viz_limits(d, box, limits: list[dict]):
    short = {"daily_loss": "день", "drawdown": "просадка", "leverage": "плечо", "coin": "монета", "cluster": "кластер", "data_age": "данные"}
    x0, y0, x1, y1 = box; n = max(1, len(limits)); g = 8; w = (x1 - x0 - g * (n - 1)) / n; hh = y1 - y0 - 18
    for i, l in enumerate(limits):
        p = max(0.0, min(100.0, float(l.get("pct_of_limit") or 0))); cx = x0 + i * (w + g)
        col = UP if p < 70 else ((255, 190, 90) if p < 90 else DOWN)
        d.rounded_rectangle([cx, y0, cx + w, y0 + hh], radius=5, fill=(20, 32, 58))
        if p > 0:
            d.rounded_rectangle([cx, y0 + hh * (1 - p / 100), cx + w, y0 + hh], radius=5, fill=col + (220,))
        d.text((cx + w / 2, y1), short.get(l.get("key"), str(l.get("key", ""))[:7]), font=_font(11), fill=(190, 205, 228), anchor="mb")


def _viz_spark(img, d, box, vals: list[float]):
    x0, y0, x1, y1 = box
    if len(vals) < 3:
        return d
    lo, hi = min(vals), max(vals); sp = (hi - lo) or 1
    pts = [(x0 + (x1 - x0) * i / (len(vals) - 1), y1 - (v - lo) / sp * (y1 - y0)) for i, v in enumerate(vals)]
    col = UP if vals[-1] >= vals[0] else DOWN
    d.polygon(pts + [(x1, y1), (x0, y1)], fill=col + (34,))
    _glow(img, pts, col, width=6, blur=4, alpha=120)
    d = ImageDraw.Draw(img, "RGBA")
    d.line(pts, fill=col, width=2, joint="curve")
    d.ellipse([pts[-1][0] - 4, pts[-1][1] - 4, pts[-1][0] + 4, pts[-1][1] + 4], fill=col)
    return d


def _viz_chain(d, box, lines: int, head: str):
    x0, y0, x1, y1 = box; n = 4; g = 16; w = (x1 - x0 - g * (n - 1)) / n
    for i in range(n):
        cx = x0 + i * (w + g); last = i == n - 1; col = IRIS[i % len(IRIS)]
        d.rounded_rectangle([cx, y0, cx + w, y1], radius=8, fill=(14, 26, 50), outline=col + (230 if last else 120,), width=2)
        d.text((cx + w / 2, y0 + 8), f"№{lines - n + 1 + i}", font=_font(12, True), fill=TXT, anchor="mt")
        d.text((cx + w / 2, y1 - 6), (head[:6] if last and head else "· · ·"), font=_font(11), fill=col, anchor="mb")
        if i < n - 1:
            d.line([cx + w, (y0 + y1) / 2, cx + w + g, (y0 + y1) / 2], fill=ICE + (200,), width=2)


def engine_card(home: dict, conveyor: dict | None, risk: dict | None, scorecard: dict | None, out_path: str, when: str | None = None) -> str | None:
    """«Двигатели CRYPTO NOVA сегодня»: six drawn turbines, each with its live number and a small live picture of what it
    does (pulse, funnel, conveyor, risk bars, portfolio curve, hash chain). Shows the technology people get. 1280 × 720."""
    if Image is None or not home:
        return None
    W, H = 1280, 720
    img = Image.new("RGB", (W, H), (6, 11, 22)); d = ImageDraw.Draw(img, "RGBA")
    for i in range(40):
        d.rectangle([0, i * 6, W, i * 6 + 6], fill=(124, 196, 255, int(24 * (1 - i / 40))))
    d.text((36, 22), "CRYPTO NOVA", font=_font(24, True), fill=TXT)
    d.text((246, 30), "Шесть двигателей системы · работают 24/7", font=_font(15), fill=(170, 195, 225))
    if when:
        d.text((246, 52), when, font=_font(13), fill=MUTED)
    e = home.get("engine") or {}; pf = home.get("portfolio") or {}
    cnt = (conveyor or {}).get("counts") or {}; rk = risk or {}; sc = (scorecard or {}).get("headline") or {}
    limits = rk.get("limits") or []
    ok = sum(1 for l in limits if l.get("status") == "ok")
    chain = (((scorecard or {}).get("chain") or {}).get("ledger") or {})
    curve = [c[1] for c in (pf.get("curve") or []) if isinstance(c, (list, tuple)) and len(c) > 1]
    cards = [
        ("Сканер рынка", f"цикл {int(e.get('cycle_sec') or 60)} секунд", f"проверено сигналов по настоящим ценам: {e.get('signals_checked', 0)}", "pulse"),
        ("Лаборатория стратегий", f"{e.get('lab_tried', 0)} правил за ночь", "придумано → на истории → экзамен → тест на везение", "funnel"),
        ("Конвейер стратегий", f"{cnt.get('paper', 0)} на демо-счетах", "переход дальше — только через ворота с порогами", "stages"),
        ("Риск-щит", f"{ok} из {len(limits)} лимитов в норме" if limits else "лимиты в норме", "проверка каждую минуту, при нарушении — стоп", "limits"),
        ("Публичный портфель", f"${_fmt(pf['value_usd'])}" if pf.get("value_usd") else "—",
         f"{pf.get('pnl_pct', 0):+.2f}% на демо-счёте $10 000, 7 источников" if pf.get("value_usd") else "", "spark"),
        ("Журнал прогнозов", f"{sc.get('recorded', 0)} записей", "каждая сцеплена хешем SHA-256 — прошлое не исправить", "chain"),
    ]
    g = 18; cw = (W - 72 - 2 * g) / 3; chh = 236; y0 = 92
    for i, (t, big, sub, viz) in enumerate(cards):
        cx = 36 + (i % 3) * (cw + g); cy = y0 + (i // 3) * (chh + g); acc = IRIS[i % len(IRIS)]
        d.rounded_rectangle([cx, cy, cx + cw, cy + chh], radius=18, fill=(10, 19, 38), outline=acc + (90,))
        d.ellipse([cx + 16, cy + 18, cx + 96, cy + 98], fill=acc + (26,))
        _turbine(d, cx + 56, cy + 58, 32, blades=8 + i, phase=i * 0.4)
        d.text((cx + 112, cy + 22), t, font=_font(16, True), fill=(200, 220, 245))
        d.text((cx + 112, cy + 46), big, font=_fit(d, big, cw - 128, 25), fill=acc)
        fs = _font(13)
        for k, ln in enumerate(_wrap(d, sub, fs, cw - 128)[:2]):
            d.text((cx + 112, cy + 80 + 18 * k), ln, font=fs, fill=(175, 192, 218))
        box = (cx + 20, cy + 134, cx + cw - 20, cy + chh - 18)
        d.rounded_rectangle([box[0] - 8, box[1] - 8, box[2] + 8, box[3] + 8], radius=12, fill=(7, 14, 30))
        if viz == "pulse":
            d = _viz_pulse(img, d, box, int(e.get("cycle_sec") or 60))
        elif viz == "funnel":
            _viz_funnel(d, box, [int(e.get(k) or 0) for k in ("lab_tried", "lab_tested", "lab_passed", "lab_reliable")])
        elif viz == "stages":
            _viz_stages(d, box, [("исследование", int(cnt.get("lab") or 0)), ("демо-счета", int(cnt.get("paper") or 0)), ("прошли ворота", int(cnt.get("ready") or 0))])
        elif viz == "limits":
            _viz_limits(d, box, limits[:6])
        elif viz == "spark":
            d = _viz_spark(img, d, box, curve[-160:])
        elif viz == "chain":
            _viz_chain(d, box, int(chain.get("lines") or sc.get("recorded") or 4), str(chain.get("head") or ""))
    by = y0 + 2 * chh + g + 14
    d.rounded_rectangle([36, by, W - 36, by + 56], radius=14, fill=(14, 24, 48), outline=(127, 211, 255, 110))
    d.text((56, by + 9), "Зачем это вам", font=_font(14, True), fill=ICE)
    d.text((56, by + 30), "система круглые сутки проверяет рынок и отсеивает слабые стратегии — результат и каждую проверку видно в приложении",
           font=_fit(d, "система круглые сутки проверяет рынок и отсеивает слабые стратегии — результат и каждую проверку видно в приложении", W - 120, 15, False, 11), fill=TXT)
    d.text((36, H - 26), "CRYPTO NOVA AI · демо-счета, цифры с сервера на момент поста · не финансовый совет", font=_font(12), fill=MUTED)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    return out_path


# ------------------------------------------------------------------------------------------------------------ trade card
def trade_card(coin: dict | None, trade: dict, out_path: str, when: str | None = None, footer: str | None = None) -> str | None:
    """«Сделка закрыта» (owner 21.10: «в канал добавлять результаты сделок … жирным дата … скриншот»): the coin's hourly
    price around the trade with the entry and the exit marked, the result in $ and %, entry / exit / hold / how it closed.
    trade = {symbol, side, entry, exit, opened, closed, net_usd, pct, exit_ru, date_ru}; coin = terminal.json coin (candles
    optional — without them the card is drawn without the chart). 1280 × 720."""
    if Image is None:
        return None
    W, H = 1280, 720
    img = Image.new("RGB", (W, H), (6, 11, 22)); d = ImageDraw.Draw(img, "RGBA")
    for i in range(40):
        d.rectangle([0, i * 6, W, i * 6 + 6], fill=(124, 196, 255, int(24 * (1 - i / 40))))
    win = (trade.get("net_usd") or 0) >= 0
    col = UP if win else DOWN
    long_ = str(trade.get("side", "LONG")).upper() == "LONG"
    d.text((36, 22), "CRYPTO NOVA", font=_font(24, True), fill=TXT)
    d.text((246, 30), "Сделка закрыта", font=_font(16, True), fill=(170, 195, 225))
    if when:
        d.text((246, 52), when, font=_font(13), fill=MUTED)
    d.text((W - 36, 26), trade.get("date_ru") or "", font=_font(26, True), fill=TXT, anchor="ra")
    # headline: coin + side, result
    d.text((36, 92), f"{trade.get('symbol', '?')} · {'лонг (на рост)' if long_ else 'шорт (на падение)'}", font=_font(34, True), fill=TXT)
    net = trade.get("net_usd") or 0
    res = f"{'+' if net >= 0 else '−'}${abs(net):,.0f}".replace(",", " ")
    pct = trade.get("pct")
    d.text((36, 140), res, font=_font(64, True), fill=col)
    if pct is not None:
        w = d.textlength(res, font=_font(64, True))
        d.text((36 + w + 20, 166), f"{pct:+.1f}%".replace("-", "−"), font=_font(34, True), fill=col)
    # right column: facts
    rows = [("Вход", _fmt(trade["entry"]) if trade.get("entry") else "—"), ("Выход", _fmt(trade["exit"]) if trade.get("exit") else "—")]
    if trade.get("opened") and trade.get("closed"):
        hrs = (trade["closed"] - trade["opened"]) / 3_600_000
        rows.append(("Держали", f"{hrs:.0f} ч" if hrs >= 1 else f"{hrs * 60:.0f} мин"))
    if trade.get("exit_ru"):
        rows.append(("Как вышли", trade["exit_ru"]))
    bx0, by0, bx1 = 760, 92, W - 36
    d.rounded_rectangle([bx0, by0, bx1, by0 + 44 * len(rows) + 24], radius=16, fill=(10, 19, 38), outline=(40, 62, 98))
    for k, (a, b) in enumerate(rows):
        y = by0 + 16 + k * 44
        d.text((bx0 + 18, y), a, font=_font(17), fill=(170, 195, 225))
        d.text((bx1 - 18, y), b, font=_font(19, True), fill=TXT, anchor="ra")
    # chart: hourly closes from 24 h before the entry to 6 h after the exit
    L, R, T, B = 36, 110, 300, 70
    # 15-minute bars plotted at their CLOSE time (open + 15 min), so a fill at a bar close sits on that point
    bars = [[k[0] + 900_000] + list(k[1:]) for k in ((coin or {}).get("candles") or [])]
    if trade.get("opened") and trade.get("closed") and bars:
        a = trade["opened"] - 24 * 3_600_000; b = trade["closed"] + 6 * 3_600_000
        bars = [k for k in bars if a <= k[0] <= b]
    if len(bars) >= 6:
        closes = [k[4] for k in bars]; ts = [k[0] for k in bars]
        lo = min(min(closes), trade.get("entry") or closes[0], trade.get("exit") or closes[-1])
        hi = max(max(closes), trade.get("entry") or closes[0], trade.get("exit") or closes[-1])
        pad = (hi - lo) * 0.12 or hi * 0.01; lo -= pad; hi += pad
        pw, ph = W - L - R, H - T - B
        X = lambda t: L + (t - ts[0]) / ((ts[-1] - ts[0]) or 1) * pw
        Y = lambda p: T + (hi - p) / (hi - lo) * ph
        for k in range(5):
            p = lo + (hi - lo) * k / 4; y = Y(p)
            d.line([L, y, L + pw, y], fill=(160, 190, 230, 24))
            d.text((L + pw + 8, y - 7), _fmt(p), font=_font(12), fill=MUTED)
        xe = max(L, min(L + pw, X(trade["opened"]))); xx = max(L, min(L + pw, X(trade["closed"])))
        d.rectangle([xe, T, xx, T + ph], fill=col + (22,))
        pts = [(X(t), Y(c)) for t, c in zip(ts, closes)]
        d.line(pts, fill=(124, 196, 255, 255), width=2, joint="curve")
        if trade.get("entry"):
            ye = Y(trade["entry"])
            for x in range(int(xe), int(xx), 14):
                d.line([x, ye, min(x + 7, xx), ye], fill=(234, 246, 255, 150), width=2)
        for x, p, lab, c_ in ((xe, trade.get("entry"), "ВХОД", ICE), (xx, trade.get("exit"), "ВЫХОД", col)):
            if p is None:
                continue
            y = Y(p)
            d.line([x, T, x, T + ph], fill=c_ + (90,), width=1)
            d.ellipse([x - 8, y - 8, x + 8, y + 8], fill=c_, outline=(6, 11, 22), width=2)
            d.text((x, T - 6), lab, font=_font(13, True), fill=c_, anchor="md")
        for t in (ts[0], trade["opened"], trade["closed"]):
            if ts[0] <= t <= ts[-1]:
                try:
                    from zoneinfo import ZoneInfo
                    tz = ZoneInfo("Europe/Kyiv")
                except Exception:  # noqa: BLE001
                    tz = dt.timezone(dt.timedelta(hours=3))
                lt = dt.datetime.fromtimestamp(t / 1000, tz)
                d.text((X(t), T + ph + 10), f"{lt:%d.%m %H:%M}", font=_font(12), fill=MUTED, anchor="la" if X(t) < L + 40 else "ma")
    else:
        d.text((36, T + 40), "график цены за время сделки недоступен", font=_font(15), fill=MUTED)
    d.text((36, H - 30), footer or "Демо-счёт $10 000 · результат после комиссий · не инвестиционный совет", font=_font(13), fill=MUTED)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    return out_path
