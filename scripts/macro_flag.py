"""23.11.2026 (owner 11:44, for the 24/7 watch): before a US inflation / Fed release (PCE, CPI, FOMC — scripts/macro_events*.json),
in the 30 h before it, flag «перед <событие>: признаки разворота вниз» when BTC's 4h trend flips down (the last completed 4h close
< EMA20 and its high < the previous 4h high) AND the crowd is long (funding > 0 or open interest up ≥ 3 % over 24 h). OKX public
data only. Writes web/macro_flag.json (public market data, no account data). Research / information only — no trading rule.
Usage: macro_flag.py [OUT]"""
import json, os, sys, time, urllib.request
from datetime import datetime, timezone

OKX = "https://www.okx.com/api/v5"
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
NAMES = {"PCE": "PCE", "CPI": "CPI", "FOMC": "ФРС"}


def get(path):
    with urllib.request.urlopen(urllib.request.Request(OKX + path, headers={"User-Agent": "cnq-macro-flag"}), timeout=20) as r:
        return json.loads(r.read()).get("data") or []


def next_event(now):
    best = None
    for fn in ("macro_events.json", "macro_events_ext.json"):
        for e in json.load(open(os.path.join(ROOT, "scripts", fn)))["events"]:
            if e.get("type") in NAMES and e.get("utc"):
                t = int(datetime.strptime(e["utc"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).timestamp() * 1000)
                if t > now - 3_600_000 and (best is None or t < best[0]):
                    best = (t, e["type"])
    return best


def flip_down(c4):
    if len(c4) < 22:
        return False, None
    e = None
    for k in c4:
        e = k[4] if e is None else e + (k[4] - e) * 2 / 21
    last, prev = c4[-1], c4[-2]
    return bool(last[4] < e and last[2] < prev[2]), round(e, 1)


def main(out="web/macro_flag.json"):
    now = int(time.time() * 1000)
    ev = next_event(now)
    doc = {"asof": now, "next_event": None, "hours_to": None, "flag": False, "why_ru": None}
    if ev:
        doc["next_event"] = {"type": ev[1], "t": ev[0]}
        doc["hours_to"] = round((ev[0] - now) / 3_600_000, 1)
    if ev and 0 <= ev[0] - now <= 30 * 3_600_000:
        c4 = sorted([(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4])) for k in get("/market/candles?instId=BTC-USDT-SWAP&bar=4H&limit=60")
                     if len(k) < 9 or str(k[8]) == "1"])
        down, ema = flip_down(c4)
        fr = float((get("/public/funding-rate?instId=BTC-USDT-SWAP") or [{}])[0].get("fundingRate") or 0)
        oi = sorted((int(x[0]), float(x[1])) for x in get("/rubik/stat/contracts/open-interest-volume?ccy=BTC&period=1H"))
        oiu = round((oi[-1][1] / oi[-25][1] - 1) * 100, 1) if len(oi) >= 25 and oi[-25][1] else None
        crowd = fr > 0 or (oiu is not None and oiu >= 3)
        doc.update({"btc_4h_close": c4[-1][4] if c4 else None, "ema20_4h": ema, "lower_high": bool(len(c4) > 1 and c4[-1][2] < c4[-2][2]),
                    "funding": fr, "oi_24h_pct": oiu, "flag": bool(down and crowd)})
        if doc["flag"]:
            doc["why_ru"] = (f"перед {NAMES[ev[1]]}: признаки разворота вниз — 4ч закрытие BTC {c4[-1][4]:.0f} ниже EMA20 {ema:.0f}, максимум ниже "
                             f"предыдущего; фандинг {fr * 100:+.4f}%, OI за сутки {oiu if oiu is not None else '—'}%")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    old = None
    try:
        old = json.load(open(out))
    except Exception:  # noqa: BLE001
        pass
    if not old or {k: v for k, v in old.items() if k != "asof"} != {k: v for k, v in doc.items() if k != "asof"} or now - int(old.get("asof") or 0) > 3 * 3_600_000:
        json.dump(doc, open(out, "w"), ensure_ascii=False)
    print("::notice title=macro flag::" + json.dumps(doc, ensure_ascii=False))


if __name__ == "__main__":
    main(*(sys.argv[1:2] or []))
