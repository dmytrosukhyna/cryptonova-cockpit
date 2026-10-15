"""Load the 18-coin 15m research set (Binance futures, with liquidations / OI / funding / CVD) into aligned numpy arrays.
Source CSVs: export of the research parquet set (see scripts/pq2csv.mjs). Cached as .npz next to the CSVs."""
import os, csv, numpy as np
SRC = os.environ.get("CNQ_PQ_CSV", "/tmp/claude-0/-home-claude/5491f1a8-ca5d-58c4-b6e0-e27b29449aba/scratchpad/pq/csv")
COLS = ["open", "high", "low", "close", "volume_quote", "long_liq_usd", "short_liq_usd", "open_interest_usd", "funding_rate_pct", "future_cvd_15m",
        "spot_cvd_15m", "ls_ratio_top", "taker_volume_ratio", "is_imputed_metrics", "basis_index_bps"]


def load():
    cache = os.path.join(SRC, "aligned18.npz")
    if os.path.exists(cache):
        z = np.load(cache, allow_pickle=True)
        return list(z["syms"]), z["times"], {c: z[c] for c in COLS}
    files = sorted(f for f in os.listdir(SRC) if f.endswith("_15m.csv"))
    syms = [f.split("_")[0] for f in files]
    raw = {}
    for s, f in zip(syms, files):
        with open(os.path.join(SRC, f)) as fh:
            r = csv.reader(fh); hdr = next(r); ix = [hdr.index("open_time_ms")] + [hdr.index(c) for c in COLS]
            rows = []
            for row in r:
                try:
                    rows.append([float(row[i]) if row[i] not in ("", "nan", "None") else np.nan for i in ix])
                except ValueError:
                    pass
        raw[s] = np.array(rows)
    times = np.unique(raw["BTCUSDT"][:, 0]).astype(np.int64)
    pos = {int(t): i for i, t in enumerate(times)}
    A = {c: np.full((len(syms), len(times)), np.nan) for c in COLS}
    for k, s in enumerate(syms):
        d = raw[s]; ii = np.array([pos.get(int(t), -1) for t in d[:, 0]]); ok = ii >= 0
        for j, c in enumerate(COLS):
            A[c][k, ii[ok]] = d[ok, j + 1]
    np.savez(cache, syms=np.array(syms), times=times, **A)
    return syms, times, A


if __name__ == "__main__":
    syms, times, A = load()
    print(len(syms), syms, len(times))
    for c in ("long_liq_usd", "open_interest_usd", "funding_rate_pct", "is_imputed_metrics"):
        x = A[c]; print(c, "finite share", np.isfinite(x).mean().round(3), "nonzero share", (np.nan_to_num(x) != 0).mean().round(3))
    imp = A["is_imputed_metrics"]
    import datetime as dt
    for y in range(2020, 2027):
        m = np.array([dt.datetime.utcfromtimestamp(t / 1000).year == y for t in times])
        print(y, "imputed share", np.nanmean(imp[:, m]).round(3), "liq>0 share", (np.nan_to_num(A["long_liq_usd"][:, m]) > 0).mean().round(3))
