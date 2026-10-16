"""Download 15m USDT-perpetual candles from OKX public history and save them aligned on one 15m grid.

Output (.npz, compressed): syms (e.g. 'BTC'), times (int64 ms, bar open), open/high/low/close/volume_quote
(float32, NaN where the coin has no bar), list_ms (OKX listing time per coin, int64), notes (skipped/failed coins).
Same keys/shape convention as scripts/ap_data.py (coins × time), only price/volume columns.

Runs in GitHub Actions (this container's proxy blocks exchange APIs): .github/workflows/research-data.yml
Usage: python scripts/fetch_history.py [--out okx15m.npz] [--start 2022-11-20] [--coins BTC,ETH,...]
Start defaults to 2022-11-20, not 2023-01-01: rule B needs 30 days of volatility warm-up before 2023-01-01."""
import argparse, datetime as dt, json, sys, threading, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor
import numpy as np

BASE = "https://www.okx.com"
CORE18 = "BTC ETH SOL BNB XRP DOGE ADA AVAX LINK LTC DOT NEAR BCH TRX SUI APT ARB OP".split()
EXTRA = "TON PEPE SHIB WIF ATOM FIL INJ SEI TIA UNI AAVE ETC HBAR ICP RENDER FET STX IMX ORDI WLD JUP ENA ONDO TAO BONK FLOKI".split()
BAR_MS = 900_000
MIN_INTERVAL = 0.085         # ≤ ~12 req/s shared by all threads; OKX allows 20 req / 2 s per IP for history-candles
WORKERS = 4                  # requests overlap network latency, the limiter keeps the total rate
_last = [0.0]
_lock = threading.Lock()


def get(path, tries=8):
    for a in range(tries):
        with _lock:
            slot = max(time.time(), _last[0] + MIN_INTERVAL); _last[0] = slot
        if slot > time.time(): time.sleep(slot - time.time())
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
    ap.add_argument("--coins", default="")

    a = ap.parse_args()
    a.coins = a.coins or ",".join(CORE18 + EXTRA)
    start_ms = int(dt.datetime.strptime(a.start, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    end_ms = (int(time.time() * 1000) // BAR_MS) * BAR_MS          # bars opening before the current one = closed
    ins = instruments()
    T0 = time.time()
    notes = []

    def warn(c, msg):   # also as a workflow annotation: run logs are not readable from the research container
        notes.append(f"{c}: {msg}"); print(f"::warning title=fetch {c}::{c}: {msg}", flush=True); return c, None

    def one(c):
        inst = f"{c}-USDT-SWAP"
        if inst not in ins: return warn(c, "no OKX USDT swap — skipped")
        try:
            rows = fetch(inst, start_ms, end_ms)
        except Exception as e:  # one bad coin must not kill the whole run
            return warn(c, f"FAILED {e}")
        if not rows: return warn(c, "no candles — skipped")
        print(f"{c}: {len(rows)} bars from {dt.datetime.utcfromtimestamp(min(rows) / 1000):%Y-%m-%d} (t={time.time() - T0:.0f}s)", flush=True)
        return c, rows

    with ThreadPoolExecutor(WORKERS) as ex:
        res = list(ex.map(one, a.coins.split(",")))
    data = {c: r for c, r in res if r}; syms = [c for c, r in res if r]
    lst = [int(ins[f"{c}-USDT-SWAP"].get("listTime") or 0) for c in syms]
    times = np.arange(start_ms, end_ms, BAR_MS, dtype=np.int64)
    cols = ["open", "high", "low", "close", "volume_quote"]
    A = {k: np.full((len(syms), len(times)), np.nan, dtype=np.float32) for k in cols}
    for k, s in enumerate(syms):
        ts = np.fromiter(data[s].keys(), dtype=np.int64); v = np.array(list(data[s].values()), dtype=np.float64)
        ii = (ts - start_ms) // BAR_MS; ok = (ii >= 0) & (ii < len(times)) & ((ts - start_ms) % BAR_MS == 0)
        for j, col in enumerate(cols): A[col][k, ii[ok]] = v[ok, j]
    np.savez_compressed(a.out, syms=np.array(syms), times=times, list_ms=np.array(lst, dtype=np.int64),
                        notes=np.array(notes, dtype=str), **A)
    print(f"saved {a.out}: {len(syms)} coins × {len(times)} bars", flush=True)
    if not syms: sys.exit("no coins fetched")


if __name__ == "__main__":
    main()
