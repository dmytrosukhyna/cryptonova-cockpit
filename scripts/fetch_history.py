"""Download 15m USDT-perpetual candles from OKX public history and save them aligned on one 15m grid.

Output (.npz, compressed): syms (e.g. 'BTC'), times (int64 ms, bar open), open/high/low/close/volume_quote
(float32, NaN where the coin has no bar), list_ms (OKX listing time per coin, int64).
Same keys/shape convention as scripts/ap_data.py (coins × time), only price/volume columns.

Runs in GitHub Actions (this container's proxy blocks exchange APIs): .github/workflows/research-data.yml
Usage: python scripts/fetch_history.py [--out okx15m.npz] [--start 2022-11-20] [--coins BTC,ETH,...]
Start defaults to 2022-11-20, not 2023-01-01: rule B needs 30 days of volatility warm-up before 2023-01-01."""
import argparse, datetime as dt, json, sys, time, urllib.error, urllib.request
import numpy as np

BASE = "https://www.okx.com"
CORE18 = "BTC ETH SOL BNB XRP DOGE ADA AVAX LINK LTC DOT NEAR BCH TRX SUI APT ARB OP".split()
EXTRA = "TON PEPE SHIB WIF ATOM FIL INJ SEI TIA UNI AAVE ETC HBAR ICP RENDER FET STX IMX ORDI WLD JUP ENA ONDO TAO BONK FLOKI".split()
BAR_MS = 900_000
MIN_INTERVAL = 0.11          # ≤ ~9 req/s; OKX allows 20 req / 2 s per IP for history-candles
_last = [0.0]


def get(path, tries=8):
    for a in range(tries):
        wait = _last[0] + MIN_INTERVAL - time.time()
        if wait > 0: time.sleep(wait)
        _last[0] = time.time()
        try:
            req = urllib.request.Request(BASE + path, headers={"User-Agent": "cnq-research/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                d = json.loads(r.read())
            if d.get("code") == "0": return d["data"]
            if d.get("code") == "50011": time.sleep(2 + a); continue        # rate limited
            raise RuntimeError(f"OKX {d.get('code')}: {d.get('msg')}")
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
            if a == tries - 1: raise
            time.sleep(min(30, 1.5 * 2 ** a)); print(f"  retry {path[:80]}: {e}", flush=True)
    raise RuntimeError(f"gave up on {path}")


def instruments():
    return {d["instId"]: d for d in get("/api/v5/public/instruments?instType=SWAP")}


def fetch(inst, start_ms, end_ms):
    """Paginate backwards with `after` (records older than the cursor) until start_ms or no more data."""
    rows, cur = {}, end_ms
    while True:
        d = get(f"/api/v5/market/history-candles?instId={inst}&bar=15m&limit=100&after={cur}")
        if not d: break
        for x in d:   # ts, o, h, l, c, vol(contracts), volCcy(base), volCcyQuote(USDT), confirm
            t = int(x[0])
            if x[8] == "1" and start_ms <= t < end_ms:
                rows[t] = (float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[7]))
        oldest = min(int(x[0]) for x in d)
        if oldest <= start_ms or oldest >= cur: break
        cur = oldest
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="okx15m.npz")
    ap.add_argument("--start", default="2022-11-20")
    ap.add_argument("--coins", default=",".join(CORE18 + EXTRA))
    a = ap.parse_args()
    start_ms = int(dt.datetime.strptime(a.start, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    end_ms = (int(time.time() * 1000) // BAR_MS) * BAR_MS          # bars opening before the current one = closed
    ins = instruments()
    data, syms, lst = {}, [], []
    for c in a.coins.split(","):
        inst = f"{c}-USDT-SWAP"
        if inst not in ins: print(f"{c}: no OKX USDT swap — skipped", flush=True); continue
        t0 = time.time()
        try:
            rows = fetch(inst, start_ms, end_ms)
        except Exception as e:  # one bad coin must not kill the whole run
            print(f"{c}: FAILED {e}", flush=True); continue
        if not rows: print(f"{c}: no candles — skipped", flush=True); continue
        data[c] = rows; syms.append(c); lst.append(int(ins[inst].get("listTime") or 0))
        print(f"{c}: {len(rows)} bars from {dt.datetime.utcfromtimestamp(min(rows) / 1000):%Y-%m-%d} ({time.time() - t0:.0f}s)", flush=True)
    times = np.arange(start_ms, end_ms, BAR_MS, dtype=np.int64)
    cols = ["open", "high", "low", "close", "volume_quote"]
    A = {k: np.full((len(syms), len(times)), np.nan, dtype=np.float32) for k in cols}
    for k, s in enumerate(syms):
        ts = np.fromiter(data[s].keys(), dtype=np.int64); v = np.array(list(data[s].values()), dtype=np.float64)
        ii = (ts - start_ms) // BAR_MS; ok = (ii >= 0) & (ii < len(times)) & ((ts - start_ms) % BAR_MS == 0)
        for j, col in enumerate(cols): A[col][k, ii[ok]] = v[ok, j]
    np.savez_compressed(a.out, syms=np.array(syms), times=times, list_ms=np.array(lst, dtype=np.int64), **A)
    print(f"saved {a.out}: {len(syms)} coins × {len(times)} bars", flush=True)
    if len(syms) < len(CORE18): sys.exit("too few coins fetched")


if __name__ == "__main__":
    main()
