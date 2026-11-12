"""12.11.2026 (owner 14:00, RESEARCH + PAPER ONLY): «шортить новые токены на листинге».
Variants registered here BEFORE the run (3 entries × 4 holds × 3 stops = 36):
  entry — short at the close of the 1st hour / after the first 4 hours / after day 1 (24 h) of a NEW USDT perpetual;
  hold  — 1 / 3 / 7 / 14 days;   stop — the price +20 / +35 / +50 % above the entry (hourly high);   leverage 1×;
  costs — 0.20 % per round trip (new listings trade with wide spreads) + the ACTUAL funding of the hold (a short receives positive
          funding, pays negative funding — new listings often have extreme rates);
  MAE   — the max adverse excursion over the hold without a stop (squeeze risk): its 50 / 90 / 99 % percentiles;
  null  — the same coins, a random entry hour within their first 60 days, same hold / stop, 300 draws: «beats» = share of draws
          the real entry beats;   pass = mean > 0 in BOTH halves of the listings (by date) AND beats ≥ 95 %.
Data (from a GitHub runner; Binance / Bybit APIs refuse US IPs):
  Binance USDT-M — data.binance.vision archive, EVERY perp incl. delisted: 1h klines of the first 3 months, monthly funding files;
  Hyperliquid perps (delisted included) — candleSnapshot 1h (only listings within the API's last 5000 hours) + fundingHistory;
  OKX swaps — live instruments' listTime, history-candles 1H (funding history: OKX keeps only ~3 months → older funding = 0, noted);
  Bybit — tried; reported if refused.
Split: «новый токен (TGE)» = the coin had no Binance SPOT market before the perp listing month (or none at all), a proxy for an
airdrop / points / new-token launch — the archive does not say «airdrop»; «существующий токен» = spot history before the listing.
By year: 2023 / 2024 / 2025–26. Stdlib only; results as job annotations."""
import csv
import io
import json
import random
import re
import statistics as st
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor

H = 3_600_000
DAY = 24 * H
NOW = int(time.time() * 1000)
T_FROM = 1672531200000                       # 2023-01-01
COST = 0.002
S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
VIS = "https://data.binance.vision"
UA = {"User-Agent": "cnq-research/1.0"}
ENTRIES = {"1h": 0, "4h": 3, "1d": 23}       # index of the hourly bar whose CLOSE is the entry
HOLDS = (1, 3, 7, 14)
STOPS = (0.20, 0.35, 0.50)
NOT_COINS = re.compile(r"^(BTCDOM|DEFI|USDC|BUSD|TUSD|FDUSD|USDP|DAI|EUR|GBP|XAU|XAG|PAXG|BLUEBIRD|FOOTBALL|1000)")
TRADFI = {"XAU", "XAG", "XPT", "XPD", "CL", "BZ", "NG", "HG", "TSLA", "NVDA", "AAPL", "AMZN", "GOOGL", "META", "MSFT", "MSTR", "COIN",
          "HOOD", "CRCL", "QQQ", "SPY", "INTC", "AMD", "PLTR", "NFLX", "BABA", "ORCL", "SPX", "NDX"}


def note(title, msg):
    s = msg if isinstance(msg, str) else json.dumps(msg, ensure_ascii=False)
    parts, cur = [], ""
    for ch in s:
        if len((cur + ch).encode()) > 3500:
            parts.append(cur); cur = ""
        cur += ch
    parts.append(cur)
    for i, p_ in enumerate(parts[:16]):
        print(f"::notice title={title} {i + 1}/{len(parts)}::" + p_.replace("%", "%25").replace("\n", "%0A"), flush=True)


def http(url, binary=False, body=None, timeout=30):
    for k in range(3):
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                         headers={**UA, **({"Content-Type": "application/json"} if body is not None else {})})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                b = r.read()
                return r.status, (b if binary else b.decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code in (403, 404, 451):
                return e.code, b"" if binary else ""
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1 + k)
    return 0, b"" if binary else ""


def zip_rows(b):
    try:
        z = zipfile.ZipFile(io.BytesIO(b))
        rows = list(csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]), "utf-8")))
        return [r for r in rows if r and r[0][:1].isdigit()]
    except Exception:  # noqa: BLE001
        return []


def s3_dirs(prefix):
    """sub-directory names under prefix («SYM» for …/klines/SYM/)"""
    out, marker = [], ""
    for _ in range(40):
        s, b = http(f"{S3}?delimiter=/&prefix={prefix}" + (f"&marker={urllib.parse.quote(marker)}" if marker else ""))
        if s != 200:
            break
        p = re.findall(r"<Prefix>" + re.escape(prefix) + r"([^<]+?)/</Prefix>", b)
        out += p
        if "<IsTruncated>true</IsTruncated>" not in b or not p:
            break
        marker = prefix + p[-1] + "/"
    return out


def s3_keys(prefix):
    s, b = http(f"{S3}?prefix={prefix}")
    return re.findall(r"<Key>([^<]+)</Key>", b) if s == 200 else []


# ------------------------------------------------------------------------------------------------------------- Binance
def bn_event(sym):
    files = [f for f in s3_keys(f"data/futures/um/monthly/klines/{sym}/1h/") if f.endswith(".zip")]
    months = sorted(set(re.findall(r"-1h-(\d{4}-\d{2})\.zip", " ".join(files))))
    if not months or months[0] < "2023-01":
        return None
    bars = {}
    for mo in months[:3]:
        s, b = http(f"{VIS}/data/futures/um/monthly/klines/{sym}/1h/{sym}-1h-{mo}.zip", binary=True)
        for r in zip_rows(b) if s == 200 else []:
            t = int(r[0]); t = t // 1000 if t > 10 ** 14 else t
            bars[t] = (float(r[1]), float(r[2]), float(r[3]), float(r[4]))
    if len(bars) < 24 * 30:
        return None
    t0 = min(bars)
    fund = {}
    for mo in months[:3]:
        s, b = http(f"{VIS}/data/futures/um/monthly/fundingRate/{sym}/{sym}-fundingRate-{mo}.zip", binary=True)
        for r in zip_rows(b) if s == 200 else []:
            try:
                t = int(r[0]); t = t // 1000 if t > 10 ** 14 else t
                fund[t] = float(r[-1])
            except Exception:  # noqa: BLE001
                pass
    spot = [f for f in s3_keys(f"data/spot/monthly/klines/{sym}/1d/") if f.endswith(".zip")]
    smon = sorted(set(re.findall(r"-1d-(\d{4}-\d{2})\.zip", " ".join(spot))))
    lmon = time.strftime("%Y-%m", time.gmtime(t0 / 1000))
    new = not smon or smon[0] >= lmon
    return {"src": "binance", "coin": sym[:-4], "t0": t0, "bars": bars, "fund": fund, "new": new, "funding_known": bool(fund)}


def binance_events():
    syms = sorted({x for x in s3_dirs("data/futures/um/monthly/klines/") if re.fullmatch(r"[A-Z0-9]+USDT", x)})
    syms = [s for s in syms if not NOT_COINS.match(s) and s[:-4] not in TRADFI]
    with ThreadPoolExecutor(24) as ex:
        ev = [e for e in ex.map(bn_event, syms) if e]
    return ev, len(syms)


# ------------------------------------------------------------------------------------------------------------- Hyperliquid
def hl(body):
    s, b = http("https://api.hyperliquid.xyz/info", body=body)
    return json.loads(b) if s == 200 and b else None


def hl_events():
    meta = hl({"type": "meta"}) or {}
    out = []
    for u in (meta.get("universe") or []):
        coin = u["name"]
        d = hl({"type": "candleSnapshot", "req": {"coin": coin, "interval": "1h", "startTime": NOW - 5000 * H, "endTime": NOW}}) or []
        if len(d) < 24 * 30:
            continue
        t0 = int(d[0]["t"])
        if t0 <= NOW - 4990 * H:                  # older than the API keeps → its listing is not in range
            continue
        bars = {int(k["t"]): (float(k["o"]), float(k["h"]), float(k["l"]), float(k["c"])) for k in d}
        fh = hl({"type": "fundingHistory", "coin": coin, "startTime": t0, "endTime": t0 + 75 * DAY}) or []
        fund = {int(x["time"]): float(x["fundingRate"]) for x in fh}
        out.append({"src": "hyperliquid", "coin": coin.replace("k", "") if coin.startswith("k") else coin, "t0": t0, "bars": bars,
                    "fund": fund, "new": None, "funding_known": bool(fund), "delisted": bool(u.get("isDelisted"))})
        time.sleep(0.15)
    return out


# ------------------------------------------------------------------------------------------------------------- OKX
def okx(path, **q):
    s, b = http(f"https://www.okx.com{path}?{urllib.parse.urlencode(q)}")
    try:
        d = json.loads(b)
        return d.get("data") if str(d.get("code")) == "0" else None
    except Exception:  # noqa: BLE001
        return None


def okx_events():
    inst = okx("/api/v5/public/instruments", instType="SWAP") or []
    out = []
    for x in inst:
        if not str(x.get("instId", "")).endswith("-USDT-SWAP"):
            continue
        lt = int(x.get("listTime") or 0)
        if lt < T_FROM or lt > NOW - 75 * DAY:
            continue
        bars, before = {}, str(lt - 1)
        for _ in range(25):                       # forward from the listing, 100 hours a page
            d = okx("/api/v5/market/history-candles", instId=x["instId"], bar="1H", limit=100, before=before)
            if not d:
                break
            for r in d:
                bars[int(r[0])] = (float(r[1]), float(r[2]), float(r[3]), float(r[4]))
            before = str(max(int(r[0]) for r in d))
            if len(bars) >= 24 * 75:
                break
            time.sleep(0.12)
        if len(bars) < 24 * 30:
            continue
        out.append({"src": "okx", "coin": x["instId"].split("-")[0], "t0": min(bars), "bars": bars, "fund": {}, "new": None,
                    "funding_known": False})
    return out


def bybit_probe():
    s, b = http("https://api.bybit.com/v5/market/instruments-info?category=linear&limit=5")
    return {"status": s, "ok": s == 200 and '"retCode":0' in (b or "")}


# ------------------------------------------------------------------------------------------------------------- simulation
def series(e):
    ts = sorted(e["bars"])
    return ts, [e["bars"][t] for t in ts]


def short(ts, bs, fund, i, hold_d, stop):
    """→ (net return, max adverse excursion without the stop) of a short entered at the close of bar i"""
    if i + 1 >= len(bs):
        return None
    px = bs[i][3]
    end = ts[i] + hold_d * DAY
    mae, ret, t_out = 0.0, None, end
    j = i + 1
    while j < len(bs) and ts[j] <= end:
        o, h, l, c = bs[j]
        mae = max(mae, h / px - 1)
        if ret is None and h >= px * (1 + stop):
            ret, t_out = -stop, ts[j]
        last = c
        j += 1
    if j - 1 <= i:
        return None
    if ts[j - 1] < end - 2 * H:                   # the data ends before the hold — not a complete trade
        return None
    if ret is None:
        ret = -(last / px - 1)
    f = sum(r for t, r in fund.items() if ts[i] < t <= t_out)      # a short RECEIVES positive funding
    return ret - COST + f, mae


def pct(xs, q):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))] * 100, 1) if xs else None


def study(name, evs, rnd):
    evs = sorted(evs, key=lambda e: e["t0"])
    mid = evs[len(evs) // 2]["t0"] if evs else 0
    S = {id(e): series(e) for e in evs}
    res, passing = {}, []
    for en, ei in ENTRIES.items():
        for hd in HOLDS:
            for sp in STOPS:
                rows = []
                for e in evs:
                    ts, bs = S[id(e)]
                    r = short(ts, bs, e["fund"], ei, hd, sp)
                    if r:
                        rows.append((r[0], r[1], e))
                key = f"{en}|{hd}d|stop+{int(sp * 100)}"
                if len(rows) < 15:
                    res[key] = {"n": len(rows)}
                    continue
                xs = [r[0] for r in rows]
                real = st.mean(xs)
                h1 = [r[0] for r in rows if r[2]["t0"] < mid]; h2 = [r[0] for r in rows if r[2]["t0"] >= mid]
                beat = 0
                for _ in range(300):
                    nx = []
                    for _r, _m, e in rows:
                        ts, bs = S[id(e)]
                        lim = min(len(bs) - 1, 60 * 24) - hd * 24
                        if lim <= 1:
                            continue
                        q = short(ts, bs, e["fund"], rnd.randrange(0, lim), hd, sp)
                        if q:
                            nx.append(q[0])
                    beat += bool(nx) and real > st.mean(nx)
                yr = {}
                for r in rows:
                    y = time.strftime("%Y", time.gmtime(r[2]["t0"] / 1000))
                    y = "2025–26" if y >= "2025" else y
                    yr.setdefault(y, []).append(r[0])
                tg = {"new_token": [r[0] for r in rows if r[2].get("new") is True], "existing": [r[0] for r in rows if r[2].get("new") is False]}
                row = {"n": len(rows), "mean_pct": round(real * 100, 2), "median_pct": round(st.median(xs) * 100, 2),
                       "win_pct": round(sum(x > 0 for x in xs) / len(xs) * 100), "stopped_pct": round(sum(x <= -sp - COST + 0.02 for x in xs) / len(xs) * 100),
                       "half1": round(st.mean(h1) * 100, 2) if h1 else None, "half2": round(st.mean(h2) * 100, 2) if h2 else None,
                       "beats_random_pct": round(beat / 3), "worst_pct": round(min(xs) * 100, 1),
                       "mae_p50_90_99": [pct([r[1] for r in rows], q) for q in (0.5, 0.9, 0.99)],
                       "by_year": {y: [len(v), round(st.mean(v) * 100, 2)] for y, v in sorted(yr.items())},
                       "by_kind": {k: [len(v), round(st.mean(v) * 100, 2)] for k, v in tg.items() if v}}
                res[key] = row
                if real > 0 and (row["half1"] or 0) > 0 and (row["half2"] or 0) > 0 and row["beats_random_pct"] >= 95:
                    passing.append(key)
    best = max((k for k, v in res.items() if "mean_pct" in v), key=lambda k: res[k]["mean_pct"], default=None)
    note(f"listing-short · {name} · variants", res)
    note(f"listing-short · {name} · summary", {"events": len(evs), "passing_both_halves_and_95": passing, "best_mean": best and [best, res[best]]})
    return res, passing


if __name__ == "__main__":
    rnd = random.Random(12)
    bn, n_syms = binance_events()
    note("listing-short · data", {"binance_perps_in_archive": n_syms, "binance_new_since_2023": len(bn),
                                  "binance_new_token_proxy": sum(1 for e in bn if e["new"]), "binance_with_funding": sum(1 for e in bn if e["funding_known"]),
                                  "bybit_probe": bybit_probe()})
    study("Binance", bn, rnd)
    try:
        hv = hl_events()
        note("listing-short · Hyperliquid data", {"listings_in_api_range": len(hv), "delisted": sum(1 for e in hv if e.get("delisted"))})
        if hv:
            study("Hyperliquid", hv, rnd)
    except Exception as e:  # noqa: BLE001
        note("listing-short · Hyperliquid", {"error": str(e)[:200]})
    try:
        ov = okx_events()
        note("listing-short · OKX data", {"live_listings_since_2023": len(ov), "note": "funding history not available for old listings — 0 used"})
        if ov:
            study("OKX", ov, rnd)
    except Exception as e:  # noqa: BLE001
        note("listing-short · OKX", {"error": str(e)[:200]})
