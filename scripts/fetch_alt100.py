"""Data for the top-100 altcoin research (docs/research/alt100.md). Runs in GitHub Actions only
(.github/workflows/research-alt100.yml): this container's proxy blocks exchange APIs.

Subcommands
  probe     what the public sources give from CI: CoinGecko / CoinPaprika ranking, Binance public archive
            (data.binance.vision: 15m klines, 15m index-price klines, 5-min open interest «metrics»), OKX index candles
            and OKX rubik open-interest history depth. Printed + emitted as annotations.
  universe  top coins by market cap (CoinGecko, fallback CoinPaprika), minus stablecoins / wrapped / liquid-staking /
            gold tokens, mapped to OKX USDT-SWAP (must be listed) and to a Binance USDT-M perpetual with archive history
            from ≤ 2024-01 (same source as the 18-coin autopilot set and as the live D/F signals). Writes universe.json.
  fetch     one shard of coins (--shard i --nshards n): 15m klines, 15m index klines, metrics (OI value) from the
            Binance archive, aligned on one 15m grid from 2022-11-20. Writes shard_i.npz.
  merge     shards → alt100.npz with the same keys as scripts/ap_data.py (open/high/low/close/volume_quote,
            open_interest_usd, basis_index_bps, is_imputed_metrics) + syms/times/rank/notes.

Alignment (no look-ahead): bar t (open time) gets OI from the last 5-min metrics row with create_time ≤ t + 10 min
(5 minutes before the bar closes; the 18-coin set uses the value at the bar close), basis = close / index_close − 1 of the
same 15m bar, in bp. Rows with no metrics → is_imputed_metrics = 1 (rules F and D skip them, as in autopilot_v2)."""
import argparse, csv, datetime as dt, io, json, os, re, sys, threading, time, urllib.error, urllib.request, zipfile
from concurrent.futures import ThreadPoolExecutor
import numpy as np

VISION = "https://data.binance.vision/data/futures/um"
OKX = "https://www.okx.com"
START = "2022-11-20"
BAR = 900_000
CORE18 = "BTC ETH SOL BNB XRP DOGE ADA AVAX LINK LTC DOT NEAR BCH TRX SUI APT ARB OP".split()
# not tradeable "alts" for this test: stablecoins / fiat-backed, wrapped & bridged, liquid staking & restaking, tokenised gold,
# yield-bearing dollars, exchange-internal tokens without perps are dropped later by the OKX/Binance mapping anyway
EXCL = set("""USDT USDC DAI USDE USDS FDUSD PYUSD TUSD USDD USD0 USD1 USDX USDY USDB USDG RLUSD FRAX LUSD GHO CRVUSD EURC EURS
SUSDE SUSDS SDAI BUIDL USYC OUSG USTB USDF USDTB BFUSD SRUSD USDO AUSD DOLA USR USDP GUSD BUSD USTC XUSD LISUSD
WBTC WETH WBETH WEETH STETH WSTETH CBBTC CBETH RETH RSETH EZETH METH LBTC SOLVBTC BTCB JITOSOL MSOL BNSOL JUPSOL SAVAX
OSETH ETHX SWETH PUFETH EBTC TBTC FBTC CLBTC UNIBTC PUMPBTC SFRXETH FRXETH STKAAVE WHYPE KHYPE STHYPE LSETH TETH WSTUSR
XAUT PAXG KAU XAUM BTC.B WBNB WTRX WSOL WAVAX WMATIC WPOL WFTM WS STS WEETH.BASE CMETH BBSOL HSOL DSOL INF LST""".split())
_lock = threading.Lock(); _last = [0.0]


def http(url, tries=6, min_interval=0.0, binary=False, head=False):
    """GET (or HEAD) with retries; returns (status, body). 404 is returned, not raised."""
    for a in range(tries):
        if min_interval:
            with _lock:
                slot = max(time.time(), _last[0] + min_interval); _last[0] = slot
            if slot > time.time(): time.sleep(slot - time.time())
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "cnq-research/1.0", "Accept": "*/*"}, method="HEAD" if head else "GET")
            with urllib.request.urlopen(req, timeout=40) as r:
                b = r.read()
                return r.status, (b if binary else b.decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code in (404, 403, 451, 400): return e.code, b""
            if e.code == 429: time.sleep(10 + 10 * a); continue
            if a == tries - 1: return e.code, b""
        except Exception as e:  # noqa: BLE001 — network hiccups
            if a == tries - 1: return -1, str(e)
        time.sleep(min(30, 1.5 * 2 ** a))
    return -1, b""


def note(title, msg, level="notice"):
    print(f"{title}: {msg}", flush=True)
    m = str(msg).replace("%", "%25").replace("\r", "").replace("\n", "%0A")
    print(f"::{level} title={title}::{m}", flush=True)


def ms(s): return int(dt.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


# ------------------------------------------------------------------------------------------------------------- probe
def probe(_a):
    out = []
    s, b = http("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page=1")
    out.append(f"coingecko markets: HTTP {s}, {len(json.loads(b)) if s == 200 else 0} rows")
    s, b = http("https://api.coinpaprika.com/v1/tickers?quotes=USD")
    out.append(f"coinpaprika tickers: HTTP {s}, {len(json.loads(b)) if s == 200 else 0} rows")
    for path in ("monthly/klines/BTCUSDT/15m/BTCUSDT-15m-2023-01.zip", "monthly/indexPriceKlines/BTCUSDT/15m/BTCUSDT-15m-2023-01.zip",
                 "daily/metrics/BTCUSDT/BTCUSDT-metrics-2023-01-01.zip", "daily/metrics/INJUSDT/INJUSDT-metrics-2023-01-01.zip",
                 "monthly/premiumIndexKlines/BTCUSDT/15m/BTCUSDT-15m-2023-01.zip"):
        s, b = http(f"{VISION}/{path}", binary=True)
        line = f"vision {path}: HTTP {s}, {len(b)} bytes"
        if s == 200:
            with zipfile.ZipFile(io.BytesIO(b)) as z:
                txt = z.read(z.namelist()[0]).decode()[:300].replace("\n", " | ")
            line += f" :: {txt}"
        out.append(line)
    s, b = http("https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?delimiter=/&prefix=data/futures/um/monthly/klines/")
    out.append(f"vision S3 listing: HTTP {s}, {len(re.findall('<Prefix>', b)) if s == 200 else 0} prefixes")
    s, b = http("https://fapi.binance.com/fapi/v1/time")
    out.append(f"fapi.binance.com: HTTP {s}")
    for inst, after in (("BTC-USDT", ms("2023-01-02")), ("BTC-USDT", ms("2024-01-02")), ("INJ-USDT", ms("2024-01-02"))):
        s, b = http(f"{OKX}/api/v5/market/history-index-candles?instId={inst}&bar=15m&limit=100&after={after}")
        try:
            d = json.loads(b)["data"]; rng = f"{len(d)} rows {dt.datetime.utcfromtimestamp(int(d[-1][0]) / 1000):%Y-%m-%d %H:%M}…" if d else "0 rows"
        except Exception as e:  # noqa: BLE001
            rng = f"err {e} {str(b)[:120]}"
        out.append(f"okx history-index-candles {inst} before {dt.datetime.utcfromtimestamp(after / 1000):%Y-%m-%d}: HTTP {s} {rng}")
    for q in ("open-interest-history?instId=BTC-USDT-SWAP&period=5m&limit=100", "open-interest-history?instId=BTC-USDT-SWAP&period=1H&limit=100",
              f"open-interest-history?instId=BTC-USDT-SWAP&period=1H&limit=100&end={ms('2024-06-01')}",
              f"open-interest-history?instId=BTC-USDT-SWAP&period=1D&limit=100&end={ms('2024-01-01')}",
              "open-interest-volume?ccy=BTC&period=1D"):
        s, b = http(f"{OKX}/api/v5/rubik/stat/contracts/{q}")
        try:
            j = json.loads(b); d = j.get("data") or []
            ts = [int(x[0]) for x in d]
            rng = f"{len(d)} rows {dt.datetime.utcfromtimestamp(min(ts) / 1000):%Y-%m-%d %H:%M} → {dt.datetime.utcfromtimestamp(max(ts) / 1000):%Y-%m-%d %H:%M}" if ts else f"0 rows {j.get('code')} {j.get('msg')}"
        except Exception as e:  # noqa: BLE001
            rng = f"err {e} {str(b)[:120]}"
        out.append(f"okx rubik {q[:60]}: HTTP {s} {rng}")
    for j, line in enumerate(out): note(f"probe {j + 1}", line, "notice" if j < 10 else "warning")   # ≤ 10 per level per step


# ---------------------------------------------------------------------------------------------------------- universe
def vision_symbols():
    """All USDT-M perpetual symbols in the Binance archive (S3 listing of monthly/klines)."""
    syms, marker = set(), ""
    for _ in range(10):
        s, b = http("https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?delimiter=/&prefix=data/futures/um/monthly/klines/"
                    + (f"&marker={marker}" if marker else ""))
        if s != 200: break
        p = re.findall(r"<Prefix>data/futures/um/monthly/klines/([A-Z0-9]+)/</Prefix>", b); syms |= set(p)
        if "<IsTruncated>true</IsTruncated>" not in b or not p: break
        marker = f"data/futures/um/monthly/klines/{p[-1]}/"
    return syms


def first_month(sym, lo="2019-09", hi=None):
    """First month with a 15m kline archive (binary search over months; archives are contiguous after listing)."""
    hi = hi or dt.date.today().strftime("%Y-%m")
    months = []
    y, m = map(int, lo.split("-"))
    while f"{y:04d}-{m:02d}" <= hi:
        months.append(f"{y:04d}-{m:02d}"); m += 1
        if m == 13: y, m = y + 1, 1
    def has(mo): return http(f"{VISION}/monthly/klines/{sym}/15m/{sym}-15m-{mo}.zip", head=True)[0] == 200
    if not has(months[-2]): return None
    a, b = 0, len(months) - 2
    while a < b:
        c = (a + b) // 2
        if has(months[c]): b = c
        else: a = c + 1
    return months[a]


def universe(a):
    rows, src = [], ""
    for page in (1, 2):
        s, b = http(f"https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=125&page={page}")
        if s != 200: rows = []; break
        rows += [dict(sym=x["symbol"].upper(), name=x["name"], id=x["id"], rank=x.get("market_cap_rank") or 9999, mcap=x.get("market_cap") or 0,
                      price=x.get("current_price") or 0) for x in json.loads(b)]
        time.sleep(3)
    if rows: src = "CoinGecko /coins/markets"
    else:
        s, b = http("https://api.coinpaprika.com/v1/tickers?quotes=USD")
        rows = [dict(sym=x["symbol"].upper(), name=x["name"], id=x["id"], rank=x.get("rank") or 9999, mcap=x["quotes"]["USD"]["market_cap"],
                     price=x["quotes"]["USD"]["price"]) for x in json.loads(b)] if s == 200 else []
        src = "CoinPaprika /tickers"
    if not rows: sys.exit("no ranking source reachable")
    rows.sort(key=lambda r: r["rank"])
    seen, top = set(), []
    for r in rows:
        stable_like = (0.97 <= (r["price"] or 0) <= 1.03 and ("USD" in r["sym"] or "usd" in r["name"].lower() or "dollar" in r["name"].lower()))
        if r["sym"] in EXCL or stable_like or r["sym"] in seen: continue
        if re.search(r"wrapped|bridged|staked|liquid staking|restaked", r["name"], re.I): continue
        seen.add(r["sym"]); top.append(r)
        if len(top) >= a.top: break
    okx = {}
    s, b = http(f"{OKX}/api/v5/public/instruments?instType=SWAP")
    for d in json.loads(b)["data"] if s == 200 else []:
        if d["instId"].endswith("-USDT-SWAP"): okx[d["instId"].split("-")[0]] = int(d.get("listTime") or 0)
    vs = vision_symbols()
    lim = a.min_month
    if a.mode == "all2023":                     # survivorship check: every Binance perp listed by `lim`, delisted ones included
        rk = {}
        for r in sorted(rows, key=lambda r: r["rank"]): rk.setdefault(r["sym"], r["rank"])
        cand = sorted(x for x in vs if x.endswith("USDT") and "_" not in x)
        def chk(bs): return bs, http(f"{VISION}/monthly/klines/{bs}/15m/{bs}-15m-{lim}.zip", head=True)[0] == 200
        with ThreadPoolExecutor(16) as ex:
            listed = [bs for bs, ok in ex.map(chk, cand) if ok]
        coins = []
        for bs in listed:
            base = bs[:-4]; m = re.match(r"^(1000000|1000|1M)([A-Z0-9]+)$", base); sym = m.group(2) if m else base
            if sym in EXCL or base in ("BTCDOM", "DEFI", "FOOTBALL", "BLUEBIRD", "USDC", "BTCST") or sym in CORE18: continue
            coins.append(dict(sym=sym, name="", id="", rank=rk.get(sym, 9999), mcap=0, price=0, okx=sym in okx, okx_list="", bsym=bs, bfirst=lim,
                              use=True, why=""))
        json.dump(dict(source=src + " (ranks only)", mode="all2023", made=dt.datetime.utcnow().isoformat(timespec="minutes"), min_month=lim,
                       coins=coins), open(a.out, "w"), indent=1)
        txt = (f"all Binance USDT-M perps with a {lim} archive (delisted included), minus core/stables/indices: {len(coins)}; "
               f"in today's top-{a.top} ranking: {sum(c['rank'] < 9999 for c in coins)} || "
               + " ".join(f"{c['sym']}#{c['rank'] if c['rank'] < 9999 else '-'}" for c in coins))
        for k in range(0, len(txt), 3500): note(f"universe {k // 3500 + 1}", txt[k:k + 3500])
        return
    def mapone(r):
        sym = r["sym"]; okx_list = okx.get(sym)
        cand = [f"{sym}USDT", f"1000{sym}USDT", f"1000000{sym}USDT", f"1M{sym}USDT"]
        bsym = next((c for c in cand if c in vs), None)
        fm = first_month(bsym) if bsym else None
        return dict(r, okx=okx_list is not None, okx_list=(dt.datetime.utcfromtimestamp(okx_list / 1000).strftime("%Y-%m-%d") if okx_list else ""),
                    bsym=bsym or "", bfirst=fm or "")
    with ThreadPoolExecutor(8) as ex:
        mapped = list(ex.map(mapone, top))
    for r in mapped:
        why = []
        if not r["okx"]: why.append("no OKX USDT-SWAP")
        if not r["bsym"]: why.append("no Binance USDT-M perp")
        elif not r["bfirst"] or r["bfirst"] > lim: why.append(f"Binance history from {r['bfirst'] or '—'}")
        r["use"] = not why; r["why"] = "; ".join(why)
    used = [r for r in mapped if r["use"]]
    json.dump(dict(source=src, made=dt.datetime.utcnow().isoformat(timespec="minutes"), top=a.top, min_month=lim, coins=mapped),
              open(a.out, "w"), indent=1)
    lines = [f"ranking: {src}; top {a.top} non-stable; usable (OKX swap listed + Binance perp history ≤ {lim}): {len(used)}"]
    lines.append("USED: " + " ".join(f"{r['rank']}:{r['sym']}" + ("" if r["bsym"] == r["sym"] + "USDT" else f"({r['bsym']})") for r in used))
    lines.append("DROPPED: " + "; ".join(f"{r['rank']}:{r['sym']} — {r['why']}" for r in mapped if not r["use"]))
    for j, l in enumerate(lines):
        for k in range(0, len(l), 3500): note(f"universe {j + 1}.{k // 3500 + 1}", l[k:k + 3500])


# ------------------------------------------------------------------------------------------------------------- fetch
def months_between(a, b):
    y, m = int(a[:4]), int(a[5:7]); out = []
    while f"{y:04d}-{m:02d}" <= b:
        out.append(f"{y:04d}-{m:02d}"); m += 1
        if m == 13: y, m = y + 1, 1
    return out


def read_zip_csv(b):
    with zipfile.ZipFile(io.BytesIO(b)) as z:
        txt = z.read(z.namelist()[0]).decode()
    rows = list(csv.reader(io.StringIO(txt)))
    if rows and not rows[0][0].replace(".", "").isdigit(): rows = rows[1:]          # newer files carry a header row
    return rows


def kl_series(sym, kind, start_ms, end_ms):
    """kind = klines | indexPriceKlines. Monthly archives for complete months, daily archives for the current month."""
    today = dt.datetime.utcfromtimestamp(end_ms / 1000).date()
    cur_month = today.strftime("%Y-%m")
    out = {}
    def add(rows):
        for x in rows:
            t = int(x[0])
            if t > 10 ** 14: t //= 1000                                               # µs timestamps in 2025+ spot files (safety)
            if start_ms <= t < end_ms: out[t] = (float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[7]) if kind == "klines" else 0.0)
    missing = []
    prev_month = (dt.date(today.year, today.month, 1) - dt.timedelta(days=1)).strftime("%Y-%m")
    for mo in months_between(dt.datetime.utcfromtimestamp(start_ms / 1000).strftime("%Y-%m"), cur_month):
        if mo < cur_month:
            s, b = http(f"{VISION}/monthly/{kind}/{sym}/15m/{sym}-15m-{mo}.zip", binary=True)
            if s == 200: add(read_zip_csv(b)); continue
            missing.append(mo)
            if mo < prev_month: continue        # not listed yet / delisted; only the last month may lack its monthly archive
        d0 = dt.date(int(mo[:4]), int(mo[5:7]), 1)
        d = d0
        while d.strftime("%Y-%m") == mo and d < today:
            s, b = http(f"{VISION}/daily/{kind}/{sym}/15m/{sym}-15m-{d:%Y-%m-%d}.zip", binary=True)
            if s == 200: add(read_zip_csv(b))
            d += dt.timedelta(days=1)
    return out


def metrics_series(sym, start_ms, end_ms):
    """5-min open interest (USD value) from daily metrics archives → {create_time_ms: oi_usd}."""
    d = dt.datetime.utcfromtimestamp(start_ms / 1000).date(); end = dt.datetime.utcfromtimestamp(end_ms / 1000).date()
    days = []
    while d < end: days.append(d); d += dt.timedelta(days=1)
    out = {}
    def one(day):
        s, b = http(f"{VISION}/daily/metrics/{sym}/{sym}-metrics-{day:%Y-%m-%d}.zip", binary=True)
        if s != 200: return []
        res = []
        for x in read_zip_csv(b):
            try:
                t = int(dt.datetime.strptime(x[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
                res.append((t, float(x[3])))
            except (ValueError, IndexError):
                pass
        return res
    with ThreadPoolExecutor(16) as ex:
        for res in ex.map(one, days):
            for t, v in res: out[t] = v
    return out


def fetch(a):
    U = json.load(open(a.universe))
    coins = [r for r in U["coins"] if r["use"]]
    core = {c: f"{c}USDT" for c in CORE18}
    todo = [(c, s) for c, s in core.items()] + [(r["sym"], r["bsym"]) for r in coins if r["sym"] not in core]
    todo = todo[a.shard::a.nshards]
    start_ms = ms(START); end_ms = (int(time.time() * 1000) // BAR) * BAR
    times = np.arange(start_ms, end_ms, BAR, dtype=np.int64)
    cols = ["open", "high", "low", "close", "volume_quote", "index_close", "open_interest_usd"]
    A = {k: np.full((len(todo), len(times)), np.nan, dtype=np.float32) for k in cols}
    notes, t0 = [], time.time()
    for k, (c, bs) in enumerate(todo):
        kl = kl_series(bs, "klines", start_ms, end_ms)
        ix = kl_series(bs, "indexPriceKlines", start_ms, end_ms)
        mt = metrics_series(bs, start_ms, end_ms)
        if kl:
            ts = np.fromiter(kl, dtype=np.int64); v = np.array(list(kl.values())); ii = (ts - start_ms) // BAR
            for j, col in enumerate(cols[:5]): A[col][k, ii] = v[:, j]
        if ix:
            ts = np.fromiter(ix, dtype=np.int64); v = np.array(list(ix.values())); A["index_close"][k, (ts - start_ms) // BAR] = v[:, 3]
        if mt:
            ts = np.array(sorted(mt), dtype=np.int64); v = np.array([mt[t] for t in ts])
            q = np.searchsorted(ts, times + 600_000, side="right") - 1          # last row with create_time ≤ bar open + 10 min
            ok = (q >= 0); ok[ok] &= (times[ok] + 600_000 - ts[q[ok]]) <= 900_000   # stale (> 15 min old) → missing
            A["open_interest_usd"][k, ok] = v[q[ok]]
        mult = 1000.0 if bs.startswith("1000") and not bs.startswith("1000000") else (1e6 if bs.startswith("1000000") or bs.startswith("1M") else 1.0)
        fb = np.argmax(np.isfinite(A["close"][k])) if np.isfinite(A["close"][k]).any() else -1
        msg = (f"{c} ({bs}{', price ×1/' + str(int(mult)) if mult > 1 else ''}): klines {len(kl)}, index {len(ix)}, metrics {len(mt)}, "
               f"from {dt.datetime.utcfromtimestamp(times[fb] / 1000):%Y-%m-%d}" if fb >= 0 else f"{c}: no klines")
        print(f"{msg} (t={time.time() - t0:.0f}s)", flush=True); notes.append(msg)
    np.savez_compressed(a.out, syms=np.array([c for c, _ in todo]), bsyms=np.array([s for _, s in todo]), times=times,
                        notes=np.array(notes, dtype=str), **A)


def merge(a):
    parts = [np.load(f, allow_pickle=True) for f in sorted(a.parts)]
    U = json.load(open(a.universe)); rank = {r["sym"]: r["rank"] for r in U["coins"]}
    times = parts[0]["times"]; T = min(len(p["times"]) for p in parts); times = times[:T]
    syms = sum([list(map(str, p["syms"])) for p in parts], []); bsyms = sum([list(map(str, p["bsyms"])) for p in parts], [])
    order = sorted(range(len(syms)), key=lambda i: (syms[i] not in CORE18, rank.get(syms[i], 9999)))
    def cat(k): return np.concatenate([p[k][:, :T] for p in parts])[order]
    o, h, l, c, v, ixc, oi = (cat(k) for k in ("open", "high", "low", "close", "volume_quote", "index_close", "open_interest_usd"))
    with np.errstate(invalid="ignore", divide="ignore"):
        basis = (c / ixc - 1) * 1e4
    imp = np.where(np.isfinite(oi) & np.isfinite(basis), 0, 1).astype(np.float32)
    notes = sum([list(map(str, p["notes"])) for p in parts], [])
    np.savez_compressed(a.out, syms=np.array([syms[i] for i in order]), bsyms=np.array([bsyms[i] for i in order]), times=times,
                        rank=np.array([rank.get(syms[i], 0) for i in order]), notes=np.array(notes, dtype=str),
                        open=o, high=h, low=l, close=c, volume_quote=v, open_interest_usd=oi, basis_index_bps=basis.astype(np.float32),
                        is_imputed_metrics=imp, index_close=ixc)
    print(f"merged {len(syms)} coins × {T} bars → {a.out}")
    txt = " || ".join(notes)
    for j in range(0, min(len(txt), 10 * 3800), 3800): note(f"fetch notes {j // 3800 + 1}", txt[j:j + 3800])


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("probe")
    u = sp.add_parser("universe"); u.add_argument("--top", type=int, default=100); u.add_argument("--min-month", default="2024-01")
    u.add_argument("--mode", default="cap", choices=["cap", "all2023"])
    u.add_argument("--out", default="universe.json")
    f = sp.add_parser("fetch"); f.add_argument("--universe", default="universe.json"); f.add_argument("--shard", type=int, default=0)
    f.add_argument("--nshards", type=int, default=1); f.add_argument("--out", default="shard.npz")
    m = sp.add_parser("merge"); m.add_argument("parts", nargs="+"); m.add_argument("--universe", default="universe.json")
    m.add_argument("--out", default="alt100.npz")
    a = ap.parse_args()
    dict(probe=probe, universe=universe, fetch=fetch, merge=merge)[a.cmd](a)
