"""Usage: python scripts/lab/horizons.py <alt100_all2023.npz>  → cnq/agents/horizons.json «stats» (the wide set; not in the repo)

What a spot holding did over its horizon, 2023-06 → 2026-10 (wide set, 155 coins, daily closes), by class and BTC regime:
  long  = BTC / ETH / SOL, 180 days
  mid   = large alts (point-in-time rank 4–30 by trailing 30-day turnover), 60 days
  short = smaller alts (rank 31–100), 14 days
Samples: every Monday, every coin of the class that has a price then and at the horizon. Regime: BTC close > SMA200 and its 90-day
return > 0 («рост»), otherwise «осторожность». Percentiles 25 / 50 / 75 and the share of samples above zero; also the same for
2025-01 → only (recency). Output: JSON for cnq/agents/horizons.json."""
import datetime as dt
import json
import sys

import numpy as np

Z = np.load(sys.argv[1], allow_pickle=True)
syms = list(Z["syms"])
times = Z["times"]
DAY = 86_400_000
end_mask = (times % DAY) == DAY - 900_000
d_t = times[end_mask] // DAY * DAY
C = Z["close"][:, end_mask].astype(float)
V = Z["volume_quote"].astype(float)
# daily turnover
nd = len(d_t)
day_idx = np.searchsorted(d_t, times // DAY * DAY)
turn = np.zeros((len(syms), nd))
for i in range(len(syms)):
    v = np.nan_to_num(V[i])
    np.add.at(turn[i], np.clip(day_idx, 0, nd - 1), v)
btc = C[syms.index("BTC")]
for j in range(1, nd):
    if not np.isfinite(btc[j]):
        btc[j] = btc[j - 1]
sma = np.array([np.nanmean(btc[max(0, j - 199):j + 1]) if j >= 199 else np.nan for j in range(nd)])
r90 = np.array([btc[j] / btc[j - 90] - 1 if j >= 90 else np.nan for j in range(nd)])
bull = (btc > sma) & (r90 > 0)
CORE = {"BTC", "ETH", "SOL"}
H = {"long": 180, "mid": 60, "short": 14}
res = {}
for cls, h in H.items():
    for reg in ("on", "off"):
        for era in ("all", "recent"):
            xs = []
            for j in range(199, nd - h):
                if dt.datetime.fromtimestamp(d_t[j] / 1000, dt.timezone.utc).weekday() != 0:
                    continue
                if bool(bull[j]) != (reg == "on"):
                    continue
                if era == "recent" and d_t[j] < 1_735_689_600_000:      # 2025-01-01
                    continue
                t30 = turn[:, max(0, j - 29):j + 1].sum(1)
                order = np.argsort(-t30)
                rank = {syms[k]: r + 1 for r, k in enumerate(order) if t30[k] > 0}
                for k, s in enumerate(syms):
                    rk = rank.get(s)
                    if rk is None:
                        continue
                    inc = (s in CORE) if cls == "long" else (s not in CORE and ((rk <= 30) if cls == "mid" else (31 <= rk <= 100)))
                    if not inc:
                        continue
                    a, b = C[k, j], C[k, j + h]
                    if np.isfinite(a) and np.isfinite(b) and a > 0:
                        xs.append(b / a - 1)
            xs = np.array(xs)
            res[f"{cls}_{reg}_{era}"] = None if len(xs) < 30 else {
                "n": int(len(xs)), "p25": round(float(np.percentile(xs, 25)) * 100, 1), "p50": round(float(np.percentile(xs, 50)) * 100, 1),
                "p75": round(float(np.percentile(xs, 75)) * 100, 1), "pos": round(float((xs > 0).mean()) * 100, 1)}
res["period"] = [dt.datetime.fromtimestamp(d_t[199] / 1000, dt.timezone.utc).strftime("%Y-%m-%d"),
                 dt.datetime.fromtimestamp(d_t[-1] / 1000, dt.timezone.utc).strftime("%Y-%m-%d")]
print(json.dumps(res, ensure_ascii=False, indent=1))
