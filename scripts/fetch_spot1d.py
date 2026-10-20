"""Daily closes + quote volume of EVERY Binance spot USDT pair (delisted included) from the public archive data.binance.vision.

Runs in GitHub Actions only (.github/workflows/research-spot-averaging.yml): this container's proxy blocks data.binance.vision.
Monthly 1d archives 2021-01 → last complete month, daily 1d archives for the current month. Stablecoins, fiat, wrapped coins,
gold tokens and the old leveraged tokens (BTCUP, ETHBEAR …) are dropped. Output: spot1d.npz with
  syms  (n,)     base asset (NEAR, BTC …)
  days  (d,)     UTC midnight of each day, ms
  close (n, d)   float32, NaN where the pair did not trade
  qvol  (n, d)   float32 quote volume in USDT, NaN where the pair did not trade
Usage: python scripts/fetch_spot1d.py [--start 2021-01] [--out spot1d.npz] [--threads 32]
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor

import numpy as np

VISION = "https://data.binance.vision/data/spot"
LIST = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?delimiter=/&prefix=data/spot/monthly/klines/"
DAY = 86_400_000
# not portfolio coins: dollars and other fiat, wrapped / staked copies, tokenised gold
EXCL = set("""USDT USDC BUSD TUSD USDP PAX DAI FDUSD USDE USDS PYUSD USD1 USDD UST USTC AEUR EUR EURI GBP AUD BRL TRY RUB UAH NGN ZAR
IDRT BIDR BKRW BVND VAI SUSD XUSD BFUSD RLUSD FRAX LUSD GUSD EURC USDX USDB WBTC WETH WBETH BETH STETH WSTETH CBETH RETH BNSOL
PAXG XAUT BTCB BTCST LBTC SOLVBTC TBTC BBTC""".split())
LEVERAGED = re.compile(r"^(BTC|ETH|BNB|XRP|ADA|LINK|DOT|LTC|TRX|EOS|XTZ|BCH|YFI|FIL|SUSHI|AAVE|UNI|SXP|XLM|1INCH)(UP|DOWN|BULL|BEAR)$")


def http(url: str, tries: int = 6) -> tuple[int, bytes]:
    for a in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "cnq-research/1.0"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            if e.code in (400, 403, 404, 451):
                return e.code, b""
            if e.code == 429:
                time.sleep(10 + 10 * a)
                continue
        except Exception:  # noqa: BLE001 — network hiccups
            pass
        time.sleep(min(30, 1.5 * 2 ** a))
    return -1, b""


def symbols() -> list[str]:
    out, marker = set(), ""
    for _ in range(40):
        s, b = http(LIST + (f"&marker={marker}" if marker else ""))
        if s != 200:
            break
        txt = b.decode("utf-8", "replace")
        p = re.findall(r"<Prefix>data/spot/monthly/klines/([A-Z0-9]+)/</Prefix>", txt)
        out |= set(p)
        if "<IsTruncated>true</IsTruncated>" not in txt or not p:
            break
        marker = f"data/spot/monthly/klines/{p[-1]}/"
    keep = []
    for s in sorted(out):
        if not s.endswith("USDT"):
            continue
        base = s[:-4]
        if not base or base in EXCL or LEVERAGED.match(base):
            continue
        keep.append(s)
    return keep


def months(a: str, b: str) -> list[str]:
    y, m = int(a[:4]), int(a[5:7])
    out = []
    while f"{y:04d}-{m:02d}" <= b:
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def rows_of(b: bytes) -> list[list[str]]:
    with zipfile.ZipFile(io.BytesIO(b)) as z:
        txt = z.read(z.namelist()[0]).decode()
    rows = list(csv.reader(io.StringIO(txt)))
    if rows and not rows[0][0].replace(".", "").isdigit():
        rows = rows[1:]
    return rows


def one(sym: str, start: str, today: dt.date) -> dict[int, tuple[float, float]]:
    cur = today.strftime("%Y-%m")
    out: dict[int, tuple[float, float]] = {}

    def add(rows):
        for x in rows:
            t = int(x[0])
            if t > 10 ** 14:          # microseconds in the 2025+ spot files
                t //= 1000
            out[t // DAY * DAY] = (float(x[4]), float(x[7]))

    got_prev = False
    for mo in months(start, cur):
        if mo == cur:
            break
        s, b = http(f"{VISION}/monthly/klines/{sym}/1d/{sym}-1d-{mo}.zip")
        if s == 200:
            add(rows_of(b))
            got_prev = True
        else:
            got_prev = False
    prev = (dt.date(today.year, today.month, 1) - dt.timedelta(days=1)).strftime("%Y-%m")
    if got_prev or any(dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).strftime("%Y-%m") == prev for t in out):
        d = dt.date(today.year, today.month, 1)
        while d < today:
            s, b = http(f"{VISION}/daily/klines/{sym}/1d/{sym}-1d-{d:%Y-%m-%d}.zip")
            if s == 200:
                add(rows_of(b))
            d += dt.timedelta(days=1)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-01")
    ap.add_argument("--out", default="spot1d.npz")
    ap.add_argument("--threads", type=int, default=32)
    ap.add_argument("--limit", type=int, default=0, help="debug: only the first N symbols")
    a = ap.parse_args()
    syms = symbols()
    if a.limit:
        syms = syms[: a.limit]
    print(f"{len(syms)} USDT spot pairs in the archive after exclusions", flush=True)
    if not syms:
        sys.exit("no symbols — archive listing failed")
    today = dt.datetime.now(dt.timezone.utc).date()
    t0 = time.time()
    with ThreadPoolExecutor(a.threads) as ex:
        res = list(ex.map(lambda s: one(s, a.start, today), syms))
    print(f"fetched in {time.time() - t0:.0f} s", flush=True)
    keep = [(s, r) for s, r in zip(syms, res) if len(r) >= 30]
    d0 = int(dt.datetime.strptime(a.start + "-01", "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    d1 = max(max(r) for _, r in keep)
    days = np.arange(d0, d1 + DAY, DAY, dtype=np.int64)
    close = np.full((len(keep), len(days)), np.nan, np.float32)
    qvol = np.full((len(keep), len(days)), np.nan, np.float32)
    for i, (_, r) in enumerate(keep):
        for t, (c, q) in r.items():
            j = (t - d0) // DAY
            if 0 <= j < len(days):
                close[i, j], qvol[i, j] = c, q
    np.savez_compressed(a.out, syms=np.array([s[:-4] for s, _ in keep]), days=days, close=close, qvol=qvol)
    last = dt.datetime.fromtimestamp(d1 / 1000, dt.timezone.utc).strftime("%Y-%m-%d")
    alive = int(np.isfinite(close[:, -1]).sum())
    print(f"::notice title=spot1d data::{len(keep)} USDT pairs with ≥ 30 days, {len(days)} days {a.start} → {last}, "
          f"{alive} trade on the last day, {len(keep) - alive} delisted or halted", flush=True)


if __name__ == "__main__":
    main()
