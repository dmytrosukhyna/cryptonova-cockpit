"""Whale-rules study data (04.11.2026, research only — no orders, no keys). Runs in GitHub Actions
(.github/workflows/research-whale-rules.yml): the research container cannot reach Hyperliquid / OKX / Binance.

What it collects
  hl      Hyperliquid leaderboard → the biggest real traders (account ≥ $100k, all-time ≥ +$1M, not market makers) plus
          every HL address in web/whales_top.json; for each: all fills since 2025-01-01 (userFillsByTime, the API keeps the
          newest 10 000), the newest 2 000 orders (historicalOrders — stops / take-profits) and the portfolio P&L history.
  okx     OKX copy-trading lead traders (SWAP), 5 pages sorted by P&L + the OKX names in whales_top.json; for each all closed
          sub-positions the API gives (last 3 months: entry, exit, leverage, P&L, open/close time).
  mkt     for every coin the whales traded: Binance USDT-M 15m klines 2025-01 → now (data.binance.vision, newest days from
          OKX swap or HL candles), funding (Binance 8h + HL hourly for the last 40 days) and open interest (Binance 5-min
          metrics aggregated to 1h) for the whale window.
  fund    Binance funding history 2023-01 → 2026-10 for the 155 coins of lab-data-all2023 (backtest universe).
Output: whale_rules_data.json.gz (one file)."""
import csv, datetime as dt, gzip, io, json, os, sys, threading, time, urllib.error, urllib.request, zipfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HL = "https://api.hyperliquid.xyz/info"
LB = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
OKX = "https://www.okx.com"
V = "https://data.binance.vision/data/futures/um"
UA = {"User-Agent": "cnq-research/whale-rules", "Content-Type": "application/json"}
NOW = int(time.time() * 1000)
TODAY = dt.datetime.now(dt.timezone.utc).date()
HL_START = int(dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
MKT_START = dt.date(2025, 1, 1)
FUND_START = dt.date(2023, 1, 1)
OUT = os.environ.get("OUT", "whale_rules_data.json.gz")
LOG = []


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    LOG.append(s)


def http(url, body=None, tries=5, timeout=60):
    for k in range(tries):
        try:
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(url, data=data, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            if e.code in (404, 403, 451, 400):
                return e.code, b""
            if e.code == 429:
                time.sleep(5 + 10 * k)
                continue
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1 + 2 * k)
    return 0, b""


def hl(body):
    c, b = http(HL, body)
    time.sleep(0.6)
    return json.loads(b) if c == 200 and b else None


def okx(path, params):
    q = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
    c, b = http(f"{OKX}{path}?{q}")
    time.sleep(0.45)
    if c != 200 or not b:
        return None
    j = json.loads(b)
    return j.get("data") if str(j.get("code")) in ("0", "") else None


def f(x, d=0.0):
    try:
        return float(x)
    except Exception:  # noqa: BLE001
        return d


# ------------------------------------------------------------------------------------------------ Hyperliquid
def hl_part(top):
    c, b = http(LB, timeout=120)
    lb = json.loads(b) if c == 200 else {}
    rows = []
    for x in lb.get("leaderboardRows", []) or []:
        m = {k: v for k, v in (x.get("windowPerformances") or [])}
        av = f(x.get("accountValue"))
        al, mo = m.get("allTime") or {}, m.get("month") or {}
        if av < 100_000 or f(al.get("pnl")) < 1_000_000:
            continue
        if f(mo.get("vlm")) > 400 * av:          # market maker / HFT
            continue
        rows.append({"addr": (x.get("ethAddress") or "").lower(), "name": x.get("displayName") or "", "account": av,
                     "pnl_all": f(al.get("pnl")), "pnl_m": f(mo.get("pnl")), "vlm_m": f(mo.get("vlm")),
                     "pnl_w": f((m.get("week") or {}).get("pnl"))})
    rows.sort(key=lambda r: -r["pnl_all"])
    pick = rows[:70]
    # + those with the best month (fresh skill), not yet in
    seen = {r["addr"] for r in pick}
    for r in sorted(rows, key=lambda r: -r["pnl_m"])[:30]:
        if r["addr"] not in seen:
            pick.append(r); seen.add(r["addr"])
    for w in (top.get("top") or []) + (top.get("others") or []) + (top.get("following") or []):
        a = (w.get("addr") or "").lower()
        if a.startswith("0x") and a not in seen:
            pick.append({"addr": a, "name": w.get("name") or "", "from_panel": True}); seen.add(a)
    log(f"HL leaderboard rows {len(lb.get('leaderboardRows', []) or [])}, passed {len(rows)}, picked {len(pick)}")
    out = []
    for i, r in enumerate(pick):
        a = r["addr"]
        fills, start = [], HL_START
        for _ in range(6):
            page = hl({"type": "userFillsByTime", "user": a, "startTime": start, "endTime": NOW, "aggregateByTime": True}) or []
            fills += page
            if len(page) < 2000:
                break
            last = max(int(x.get("time") or 0) for x in page)
            if last <= start:
                break
            start = last + 1
        fl = [[int(x["time"]), x["coin"], x["side"], f(x["px"]), f(x["sz"]), x.get("dir") or "", f(x.get("startPosition")),
               f(x.get("closedPnl")), f(x.get("fee")), str(x.get("tid") or "")] for x in fills
              if x.get("coin") and not str(x["coin"]).startswith("@") and ":" not in str(x["coin"]) and "/" not in str(x["coin"])]
        orders = []
        if len(fl) >= 8:
            for o in hl({"type": "historicalOrders", "user": a}) or []:
                oo = o.get("order") or {}
                if oo.get("isTrigger") or oo.get("orderType") not in ("Limit", None):
                    orders.append({"t": oo.get("timestamp"), "coin": oo.get("coin"), "side": oo.get("side"), "trig": f(oo.get("triggerPx")),
                                   "px": f(oo.get("limitPx")), "type": oo.get("orderType"), "cond": oo.get("triggerCondition"),
                                   "ro": oo.get("reduceOnly"), "tpsl": oo.get("isPositionTpsl"), "status": o.get("status"),
                                   "st": o.get("statusTimestamp")})
        pf = hl({"type": "portfolio", "user": a}) if len(fl) >= 8 else None
        st = hl({"type": "clearinghouseState", "user": a}) if len(fl) >= 8 else None
        out.append({**r, "fills": fl, "orders": orders, "portfolio": pf, "state": st, "capped": len(fills) >= 10000})
        log(f"HL {i+1}/{len(pick)} {a[:10]} {r.get('name','')[:14]}: fills {len(fl)}, trigger orders {len(orders)}")
    return out


# ------------------------------------------------------------------------------------------------ OKX
def okx_part(top):
    traders = {}
    for sort in ("pnl", "winRatio", "pnlRatio"):
        for page in range(1, 6 if sort == "pnl" else 3):
            d = okx("/api/v5/copytrading/public-lead-traders", {"instType": "SWAP", "sortType": sort, "minLeadDays": 3, "page": page, "limit": 20})
            ranks = (d[0] if d else {}).get("ranks") or []
            for x in ranks:
                code = x.get("uniqueCode")
                if code and code not in traders:
                    traders[code] = {"code": code, "name": x.get("nickName") or "", "aum": f(x.get("aum")), "pnl_all": f(x.get("pnl")),
                                     "pnl_ratio": f(x.get("pnlRatio")), "win_ratio": f(x.get("winRatio")), "followers": int(f(x.get("copyTraderNum"))),
                                     "lead_days": int(f(x.get("leadDays"))), "sort": sort}
            if len(ranks) < 20:
                break
    for w in (top.get("top") or []) + (top.get("others") or []):
        a = w.get("addr") or ""
        if a.startswith("okx:") and a[4:] not in traders:
            traders[a[4:]] = {"code": a[4:], "name": w.get("name") or "", "from_panel": True}
    log(f"OKX lead traders {len(traders)}")
    out = []
    for i, t in enumerate(traders.values()):
        rows, after = [], None
        for _ in range(40):
            d = okx("/api/v5/copytrading/public-subpositions-history", {"instType": "SWAP", "uniqueCode": t["code"], "limit": 100, "after": after})
            if not d:
                break
            for x in d:
                side = (x.get("posSide") or "").lower()
                if side == "net":
                    side = "long" if f(x.get("subPos")) > 0 else "short"
                rows.append([x.get("subPosId"), x.get("instId"), side, int(f(x.get("openTime"))), int(f(x.get("closeTime"))), f(x.get("openAvgPx")),
                             f(x.get("closeAvgPx")), f(x.get("lever"), 1), f(x.get("margin")), f(x.get("pnl")), f(x.get("pnlRatio")),
                             x.get("closeType") or x.get("type") or "", f(x.get("subPos"))])
            if len(d) < 100:
                break
            after = d[-1].get("subPosId")
        cur = okx("/api/v5/copytrading/public-current-subpositions", {"instType": "SWAP", "uniqueCode": t["code"], "limit": 100}) or []
        out.append({**t, "closed": rows, "current": cur})
        log(f"OKX {i+1}/{len(traders)} {t['name'][:16]}: closed {len(rows)}, open {len(cur)}")
    return out


# ------------------------------------------------------------------------------------------------ market data
def zrows(b):
    z = zipfile.ZipFile(io.BytesIO(b))
    rows = list(csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]))))
    return [r for r in rows if r and r[0][:1].isdigit()]


def months(start, end):
    y, m = start.year, start.month
    while (y, m) < (end.year, end.month):
        yield f"{y}-{m:02d}"
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def bsym(coin):
    c = coin.upper()
    if coin.startswith("k") and coin[1:].isupper():
        return f"1000{coin[1:]}USDT"
    return f"{c}USDT"


def kl15(coin):
    s = bsym(coin)
    K = {}
    for mo in months(MKT_START, TODAY):
        c, b = http(f"{V}/monthly/klines/{s}/15m/{s}-15m-{mo}.zip", tries=3)
        if c == 200:
            for r in zrows(b): K[int(r[0])] = [float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7])]
        elif K:   # month file missing after listing → daily files
            y, m = map(int, mo.split("-")); d = dt.date(y, m, 1)
            while d.month == m:
                c2, b2 = http(f"{V}/daily/klines/{s}/15m/{s}-15m-{d}.zip", tries=2)
                if c2 == 200:
                    for r in zrows(b2): K[int(r[0])] = [float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7])]
                d += dt.timedelta(days=1)
    d = TODAY.replace(day=1)
    while d < TODAY:
        c, b = http(f"{V}/daily/klines/{s}/15m/{s}-15m-{d}.zip", tries=2)
        if c == 200:
            for r in zrows(b): K[int(r[0])] = [float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7])]
        d += dt.timedelta(days=1)
    src = "binance" if K else ""
    # newest bars / coins not on Binance: HL candles (15m: last ~52 days; 1h: ~208 days, stored as 4 equal 15m bars is wrong → keep separate)
    last = max(K) if K else 0
    h15 = hl({"type": "candleSnapshot", "req": {"coin": coin, "interval": "15m", "startTime": max(last + 1, NOW - 52 * 86_400_000), "endTime": NOW}}) or []
    for x in h15:
        t = int(x["t"])
        if t not in K and t + 900_000 <= NOW:
            K[t] = [f(x["o"]), f(x["h"]), f(x["l"]), f(x["c"]), f(x.get("v")) * f(x["c"])]
    h1 = {}
    if not src:
        for x in hl({"type": "candleSnapshot", "req": {"coin": coin, "interval": "1h", "startTime": HL_START, "endTime": NOW}}) or []:
            h1[int(x["t"])] = [f(x["o"]), f(x["h"]), f(x["l"]), f(x["c"]), f(x.get("v")) * f(x["c"])]
        src = "hl"
    return s, K, h1, src


def funding(sym, start):
    F = {}
    for mo in months(start, TODAY):
        c, b = http(f"{V}/monthly/fundingRate/{sym}/{sym}-fundingRate-{mo}.zip", tries=2)
        if c == 200:
            for r in zrows(b): F[int(r[0])] = float(r[2])
    return F


def oi_1h(sym, start):
    O = {}
    d = start
    while d < TODAY:
        c, b = http(f"{V}/daily/metrics/{sym}/{sym}-metrics-{d}.zip", tries=2)
        if c == 200:
            z = zipfile.ZipFile(io.BytesIO(b))
            for r in csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]))):
                if not r or not r[0][:1].isdigit():
                    continue
                try:
                    t = int(dt.datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
                except Exception:  # noqa: BLE001
                    continue
                if t % 3_600_000 == 0:
                    O[t] = f(r[3])           # sum_open_interest_value (USD)
        d += dt.timedelta(days=1)
    return O


def mkt_part(coins, oi_coins, oi_start):
    out = {}
    lock = threading.Lock()

    def one(coin):
        try:
            s, K, h1, src = kl15(coin)
            F = funding(s, MKT_START) if src == "binance" else {}
            hf = {}
            for x in hl({"type": "fundingHistory", "coin": coin, "startTime": NOW - 40 * 86_400_000}) or []:
                hf[int(x["time"])] = f(x["fundingRate"])
            O = oi_1h(s, oi_start) if src == "binance" and coin in oi_coins else {}
            with lock:
                out[coin] = {"sym": s, "src": src, "k15": sorted([[t] + v for t, v in K.items()]), "k1h_hl": sorted([[t] + v for t, v in h1.items()]),
                             "fund": sorted(F.items()), "fund_hl": sorted(hf.items()), "oi": sorted(O.items())}
            log(f"mkt {coin} {s} {src}: 15m {len(K)}, 1h(hl) {len(h1)}, funding {len(F)}/{len(hf)}, OI {len(O)}")
        except Exception as e:  # noqa: BLE001
            log(f"mkt {coin}: ERROR {e}")

    with ThreadPoolExecutor(6) as ex:
        list(ex.map(one, coins))
    return out


def fund_part(syms):
    out = {}

    def one(s):
        out[s] = sorted(funding(s, FUND_START).items())

    with ThreadPoolExecutor(12) as ex:
        list(ex.map(one, syms))
    log(f"universe funding: {sum(1 for v in out.values() if v)} of {len(syms)} symbols")
    return out


def main():
    top = json.load(open("web/whales_top.json"))
    steps = (sys.argv[1] if len(sys.argv) > 1 else "hl,okx,mkt,fund").split(",")
    res = {"made": NOW, "log": LOG}
    if "hl" in steps:
        res["hl"] = hl_part(top)
    if "okx" in steps:
        res["okx"] = okx_part(top)
    if "mkt" in steps:
        cnt = {"BTC": 10**6, "ETH": 10**6}
        for w in res.get("hl", []):
            if len(w["fills"]) >= 8:
                for x in w["fills"]:
                    if x[0] >= NOW - 300 * 86_400_000 and "Open" in x[5]:
                        cnt[x[1]] = cnt.get(x[1], 0) + 1
        for t in res.get("okx", []):
            for r in t["closed"]:
                c = (r[1] or "").split("-")[0]
                cnt[c] = cnt.get(c, 0) + 1
        coins = sorted(c for c in cnt if c)
        oi_coins = set(sorted(cnt, key=lambda c: -cnt[c])[:45])
        log(f"market coins {len(coins)}: {' '.join(coins)}")
        res["mkt"] = mkt_part(coins, oi_coins, dt.date(2026, 3, 1))
    if "fund" in steps:
        syms = os.environ.get("UNIVERSE", "").split()
        u = os.path.join(os.path.dirname(os.path.abspath(__file__)), "whale_rules_universe.txt")
        if not syms and os.path.exists(u):
            syms = open(u).read().split()
        res["fund"] = fund_part(syms)
    with gzip.open(OUT, "wt") as g:
        json.dump(res, g, separators=(",", ":"))
    log(f"wrote {OUT} {os.path.getsize(OUT)/1e6:.1f} MB")


if __name__ == "__main__":
    main()
