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


def render(coin: dict, cal_open: dict | None, fng: dict | None, out_path: str, title: str | None = None, chg24: float | None = None) -> str | None:
    if Image is None or not coin.get("candles"):
        return None
    W, H = 1200, 675
    img = Image.new("RGB", (W, H), (6, 11, 22))
    d = ImageDraw.Draw(img, "RGBA")
    # soft blue glow at the top
    for i in range(40):
        a = int(26 * (1 - i / 40))
        d.rectangle([0, i * 6, W, i * 6 + 6], fill=(124, 196, 255, a))
    bars = _hourly(coin["candles"])[-120:]
    if len(bars) < 20:
        return None
    base = coin["symbol"].split("-")[0]
    L, R, T, B = 40, 150, 92, 70
    pw, ph = W - L - R, H - T - B
    price = coin.get("price") or bars[-1][4]
    lo = min(k[3] for k in bars); hi = max(k[2] for k in bars)
    # bring the nearest zones into view (within 8% of price) so the picture shows where the walls are
    near = [lv for lv in coin.get("levels", []) if lv.get("status") in (None, "ACTIVE") and abs(lv["center"] / price - 1) <= 0.08]
    sup = [lv for lv in near if lv["center"] < price]; res = [lv for lv in near if lv["center"] > price]
    if sup: lo = min(lo, max(sup, key=lambda l: l["center"])["lo"])
    if res: hi = max(hi, min(res, key=lambda l: l["center"])["hi"])
    if cal_open:
        for t in (cal_open.get("up"), cal_open.get("dn")):
            if t: lo, hi = min(lo, t), max(hi, t)
    pad = (hi - lo) * 0.08; lo -= pad; hi += pad
    X = lambda i: L + i / (len(bars) - 1) * pw * 0.78          # leave room on the right for the scenario
    Y = lambda p: T + (hi - p) / (hi - lo) * ph
    # grid
    f10, f12, f14, f22 = _font(11), _font(12), _font(14), _font(22, True)
    taken = [Y(price)] + ([Y(t) for t in (cal_open.get("up"), cal_open.get("dn")) if t] if cal_open else [])
    for k in range(6):
        p = lo + (hi - lo) * k / 5
        y = Y(p)
        d.line([L, y, W - R + 90, y], fill=(160, 190, 230, 28))
        if all(abs(y - t) > 16 for t in taken):
            d.text((W - R + 96, y - 7), _fmt(p), font=f12, fill=(125, 147, 173))
    # zones
    for lv in coin.get("levels", [])[:6]:
        if lv.get("status") not in (None, "ACTIVE") or lv["hi"] < lo or lv["lo"] > hi:
            continue
        sup = lv["center"] < price
        col = (143, 211, 255) if sup else (234, 246, 255)
        strong = lv.get("touches", 1) >= 3
        d.rectangle([L, Y(lv["hi"]), W - R + 90, Y(lv["lo"])], fill=col + (22 if strong else 12,))
        d.line([L, Y(lv["hi"] if sup else lv["lo"]), W - R + 90, Y(lv["hi"] if sup else lv["lo"])], fill=col + (170 if strong else 90,), width=1)
        d.text((L + 8, Y(lv["hi"] if sup else lv["lo"]) - 15), f"{'поддержка' if sup else 'сопротивление'} {_fmt(lv['lo'])}–{_fmt(lv['hi'])} · {lv.get('touches', 1)} кас.", font=f10, fill=col + (220,))
    # wave: filled area + glowing line
    pts = [(X(i), Y(k[4])) for i, k in enumerate(bars)]
    poly = pts + [(pts[-1][0], T + ph), (pts[0][0], T + ph)]
    d.polygon(poly, fill=(124, 196, 255, 34))
    for w, a in ((9, 30), (5, 70), (2, 255)):
        d.line(pts, fill=(200, 230, 255, a), width=w, joint="curve")
    # volume at the bottom
    vmax = max(k[5] for k in bars) or 1
    for i, k in enumerate(bars):
        vh = k[5] / vmax * ph * 0.14
        d.rectangle([X(i) - 2, T + ph - vh, X(i) + 2, T + ph], fill=(96, 200, 214, 70))
    # price tag + dotted line
    y = Y(price)
    for x in range(L, W - R + 90, 8):
        d.line([x, y, x + 3, y], fill=(124, 196, 255, 140))
    d.rounded_rectangle([W - R + 88, y - 13, W - 4, y + 13], radius=5, fill=(124, 196, 255))
    d.text((W - R + 94, y - 8), _fmt(price), font=_font(13, True), fill=(3, 9, 20))
    # scenarios A / B from the calibration file
    if cal_open and cal_open.get("p_up") is not None:
        p = cal_open["p_up"]; a_up = p >= 0.5
        pa = round((p if a_up else 1 - p) * 100)
        ta = cal_open["up"] if a_up else cal_open["dn"]; tb = cal_open["dn"] if a_up else cal_open["up"]
        x0, y0 = pts[-1]
        xe = L + pw * 0.98
        for tgt, lab, col, wdt in ((ta, f"A {pa}%", (124, 196, 255), 3), (tb, f"B {100 - pa}%", (150, 190, 220), 2)):
            if tgt is None:
                continue
            ye = Y(min(hi, max(lo, tgt)))
            # smooth curve to the target
            n = 24
            curve = [(x0 + (xe - x0) * t / n, y0 + (ye - y0) * (1 - (1 - t / n) ** 2)) for t in range(n + 1)]
            d.line(curve, fill=col + (230,), width=wdt, joint="curve")
            d.ellipse([xe - 4, ye - 4, xe + 4, ye + 4], fill=col)
            d.text((xe + 8, ye - 8), f"{lab} → {_fmt(tgt)}", font=_font(13, True), fill=col)
            taken.append(ye)
        d.text((x0 + 10, T + 6), "ПРОГНОЗ →", font=f10, fill=(124, 196, 255))
        d.line([x0, T, x0, T + ph], fill=(124, 196, 255, 90))
    # header / footer
    d.text((L, 22), "CRYPTO NOVA", font=f22, fill=(234, 246, 255))
    d.text((L + 190, 28), title or f"{base}/USDT · 1 час · {dt.datetime.utcnow():%d.%m %H:%M} UTC", font=f14, fill=(125, 147, 173))
    chg = chg24 if chg24 is not None else coin.get("chg24")
    if chg is not None:
        col = (111, 207, 151) if chg >= 0 else (239, 125, 107)
        d.text((W - R - 60, 24), f"{_fmt(price)}  {chg:+.1f}% за сутки", font=_font(16, True), fill=col)
    foot = []
    if fng and fng.get("value") is not None:
        foot.append(f"страх и жадность {fng['value']} · {fng.get('label', '')}")
    r = coin.get("regime") or {}
    if r.get("long"):
        foot.append("режим: " + ("бычий (выше 200-дневной)" if r["long"] == "BULL" else "медвежий (ниже 200-дневной)"))
    foot.append("зоны = где цена разворачивалась · A/B = сценарии с вероятностью · не финансовый совет")
    d.text((L, H - 46), " · ".join(foot[:2]), font=f12, fill=(170, 195, 225))
    d.text((L, H - 26), foot[-1], font=f10, fill=(110, 130, 160))
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    return out_path
