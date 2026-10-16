"""Short-side hypotheses (the Autopilot is long-only; can anything pay on the short side?). 18 coins, real metrics 2023-2026,
entry next 15m bar, time exits, catastrophe stop 12 %, costs 0.14 %. Day-clustered averages and per-year stability."""
import sys, numpy as np
sys.path.insert(0, 'scripts')
exec(open('scripts/deriv_signals.py').read().split('rng = np.random')[0])
btc = syms.index('BTCUSDT'); csb = np.cumsum(np.nan_to_num(C[btc])); n = 19200
smab = np.full(T, np.nan); smab[n:] = (csb[n:] - csb[:-n]) / n; bear = (C[btc] < smab)[None, :]
R24 = np.full_like(C, np.nan); R24[:, 96:] = C[:, 96:] / C[:, :-96] - 1
FR = A['funding_rate_pct']
print("короткие гипотезы (sg=-1 → шорт):")
test("медвежий режим: рост +5% за 4ч → шорт", bear & (R4h >= 0.05), -1)
test("медвежий режим: рост +8% за 4ч → шорт", bear & (R4h >= 0.08), -1)
test("медв.: рост +5%/4ч и ОИ +10% (лонги догоняют)", bear & (R4h >= 0.05) & (OI16 >= 0.10), -1)
test("фандинг ≥ 0.03% и рост +10% за сутки → шорт", (FR >= 0.03) & (R24 >= 0.10), -1)
test("рост +20% за сутки → шорт (перегрев)", R24 >= 0.20, -1)
test("рост +20% за сутки → ЛОНГ (импульс)", R24 >= 0.20, +1)
test("медв.: после откупа (24ч +8% после −10%) → шорт", bear & (R24 >= 0.08) & (np.roll(R24, 96, axis=1) <= -0.10), -1)
