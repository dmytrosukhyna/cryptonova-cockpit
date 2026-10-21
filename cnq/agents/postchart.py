"""Chart image for channel posts (Pillow, optional). Dark CRYPTO NOVA look: 1h wave of the last ~5 days built from the
15-minute candles in terminal.json, support/resistance zones, scenario A/B targets from calibration.json, price tag,
fear & greed. Everything drawn comes from the same JSON the post text uses. Returns the PNG path or None."""
from __future__ import annotations

import datetime as dt
import os

try:
    from PIL import Image, ImageDraw, ImageFont
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


def render(coin: dict, cal_open: dict | None, fng: dict | None, out_path: str, title: str | None = None, chg24: float | None = None,
           when: str | None = None, conclusion: list[str] | None = None) -> str | None:
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
        d.text((L + 8, edge - 16), f"{'поддержка' if s_ else 'сопротивление'} {_fmt(lv['lo'])}–{_fmt(lv['hi'])}", font=f11, fill=col + (220,))
    # history wave
    pts = [(X(i), Y(k[4])) for i, k in enumerate(bars)]
    d.polygon(pts + [(pts[-1][0], T + ph), (pts[0][0], T + ph)], fill=(124, 196, 255, 30))
    for w, a in ((9, 26), (5, 60), (2, 255)):
        d.line(pts, fill=(200, 230, 255, a), width=w, joint="curve")
    vmax = max(k[5] for k in bars) or 1
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
            for w, a in ((wdt + 8, 30), (wdt, 235)):
                d.line(path, fill=col + (a,), width=w, joint="curve")
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
