# Strategy lab (18.10.2026)

Goal (owner): try many strategy ideas — 20–30 if needed — and keep only what really works, then merge the survivors
into one worked-out algorithm: when to enter, how many targets (1, 3 or 5), where the stop is, when to close.

Everything is tested the same way (`harness.py`):
- data: 18 coins, Binance USDT-M futures, 15-minute bars, 2020-09 → 2026-09, with liquidations, open interest, funding,
  CVD, taker ratio, top-trader long/short, basis (derivatives partly imputed before 2023);
- signal on the close of a bar, entry at the next bar's open; costs 0.14 % per round trip (limit orders: 0.04 %);
- parameters chosen on TRAIN 2021–2024 only; verdict on TEST 2025-01 → 2026-09;
- pass = TEST avg > +0.05 % per trade, TEST n ≥ 60, ≥ 4 of 6 years positive, no coin > 50 % of the profit;
- every family writes `results/<family>.json` with how many variants it tried.

Families: `fam_fade.py` (app-like counter-trend signals and their flip), `fam_trend.py` (breakouts, pullbacks,
squeezes, BTC lead), `fam_deriv.py` (funding, open interest, liquidations, CVD, taker flow, basis),
`fam_scalp.py` (short holds, sessions, 1-minute BTC).

Wide universe (18.10, owner: "18 coins is too few — at least top-50"): `CNQ_LAB_SET=wide` loads
`alt100_all2023.npz` (every Binance USDT-M perpetual listed by 2023-01, delisted ones included — built by
`research-alt100.yml` stage=data universe=all2023 and published as release `lab-data-all2023`). A coin is traded only
while it is in the top-50 by trailing 30-day volume at that moment (`CNQ_LAB_TOPN`), so there is no look-ahead in the
choice of coins. Liquidations, funding and CVD are not in that file. Data starts 2022-11 → train 2023–2024,
test 2025–2026, year rule ≥ 3 of 4.
