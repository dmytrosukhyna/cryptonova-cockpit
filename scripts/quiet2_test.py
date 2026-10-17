"""Quiet-market rules, round 2 (17.10.2026): after mean reversion failed (docs/research/quiet_market.md), test
momentum and relative-value ideas that can trade most days.
  M1 intraday breakout of the 24 h high/low on volume, quiet market, exit on the 4 h low/high (trailing) or 48 h;
  M2 cross-sectional momentum: once a day long the 3 strongest / short the 3 weakest coins by 3/7/14-day return;
  M3 volatility squeeze → breakout: 20 h Bollinger width in the lowest part of its 30-day range, then close
     outside 2σ → trade the breakout, exit when price closes back through the 20 h mean or after 48 h;
  M4 pairs: log price ratio of correlated coins 2.5σ from its 7-day mean → long the cheap / short the rich leg,
     exit at the mean, stop at 5σ, max 3 days.
Data/costs as scripts/quiet_test.py: OKX 15m, 43 coins, market orders 0.14 % round trip (two legs: 0.14 % of the
pair notional). Choose on 2023–2024, 2025–26 blind. Usage: python scripts/quiet2_test.py okx15m.npz [--md out.md]"""
import sys, collections, datetime as dt, numpy as np
from numpy.lib.stride_tricks import sliding_window_view as sw

z = np.load(sys.argv[1], allow_pickle=True)
syms = [str(s) for s in z['syms']]; times = z['times'].astype(np.int64)
O, H, L, C, V = (z[c].astype(np.float64) for c in ('open', 'high', 'low', 'close', 'volume_quote'))
NS, T = C.shape
CORE18 = "BTC ETH SOL BNB XRP DOGE ADA AVAX LINK LTC DOT NEAR BCH TRX SUI APT ARB OP".split()
core = [k for k, s in enumerate(syms) if s in CORE18]; BTC = syms.index('BTC')
COST = 2 * (0.0005 + 0.0002)
START = int(np.searchsorted(times, 1672531200000)); OOS = int(np.searchsorted(times, 1735689600000))
YEAR = np.array([dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).year for t in times])
DAYI = (times - times[0]) // 86_400_000; HOUR = (times // 3_600_000) % 24
first = np.array([np.argmax(np.isfinite(C[k])) for k in range(NS)]); WARM = 30 * 96
out = []
P = lambda s='': (print(s, flush=True), out.append(s))


def roll_mean(x, n):
    o = np.full_like(x, np.nan); m = np.isfinite(x); cs = np.cumsum(np.where(m, x, 0), -1); cn = np.cumsum(m, -1)
    s = cs[..., n:] - cs[..., :-n]; c = cn[..., n:] - cn[..., :-n]
    with np.errstate(invalid='ignore', divide='ignore'):
        o[..., n:] = np.where(c >= n * 0.8, s / c, np.nan)
    return o


def ret(n): r = np.full_like(C, np.nan); r[:, n:] = C[:, n:] / C[:, :-n] - 1; return r


def lagmax(x, n):
    o = np.full_like(x, np.nan)
    for k in range(NS): o[k, n:] = np.max(sw(x[k], n), 1)[:-1]
    return o


def lagmin(x, n):
    o = np.full_like(x, np.nan)
    for k in range(NS): o[k, n:] = np.min(sw(x[k], n), 1)[:-1]
    return o


with np.errstate(all='ignore'):
    r96 = ret(96)
    av = np.isfinite(r96[core]).sum(0); BR = np.where(av >= 8, (r96[core] < -0.03).sum(0) / np.maximum(av, 1), 1)
    QUIET = (np.abs(r96[BTC]) < 0.03) & (BR < 0.2)
    vav = roll_mean(V, 96); vav[:, 1:] = vav[:, :-1].copy()
    hi96, lo96, hi16, lo16 = lagmax(H, 96), lagmin(L, 96), lagmax(H, 16), lagmin(L, 16)
    m80 = roll_mean(C, 80); sd80 = np.sqrt(np.maximum(roll_mean(C * C, 80) - m80 ** 2, 0))
    width = 4 * sd80 / m80; wlo = lagmin(np.where(np.isfinite(width), width, np.inf), 2880); whi = lagmax(np.nan_to_num(width), 2880)


def summ(name, tr, notional=0.10):
    """tr: (entry bar, exit bar, net return). Daily rows: sum of net × notional (no compounding)."""
    if not tr: P(f"| {name} | 0 | | | | | | | | |"); return None
    a = np.array([t[2] for t in tr]); j = np.array([t[0] for t in tr])
    ys = collections.defaultdict(list)
    for t in tr: ys[YEAR[t[0]]].append(t[2])
    yr = " ".join(f"{y}:{np.mean(v)*100:+.2f}" for y, v in sorted(ys.items()))
    isx, oos = a[j < OOS], a[j >= OOS]
    d0, d1 = DAYI[START], DAYI[T - 1]; day = collections.defaultdict(float); act = set()
    for t in tr: day[DAYI[t[1]]] += t[2] * notional; act.add(DAYI[t[0]])
    dv = np.array([day.get(d, 0.0) for d in range(d0, d1 + 1)]); eq = np.cumsum(dv)
    dd = (eq - np.maximum.accumulate(np.r_[0, eq])[1:]).min(); hit = [d for d in range(d0, d1 + 1) if d in day]
    P(f"| {name} | {len(a)/(d1-d0+1):.1f} | {len(act)/(d1-d0+1)*100:.0f}% | {a.mean()*100:+.3f}% | {(a>0).mean()*100:.0f}% | "
      f"{isx.mean()*100 if len(isx) else float('nan'):+.3f}% | {oos.mean()*100 if len(oos) else float('nan'):+.3f}% | {yr} | "
      f"{np.mean([day[d] > 0 for d in hit])*100:.0f}% | {eq[-1]*100:+.1f}% / {dd*100:.1f}% |")
    return dict(name=name, tr=tr, is_=isx.mean() if len(isx) else -1, oos=oos.mean() if len(oos) else -1,
                years={y: np.mean(v) for y, v in ys.items()}, notional=notional)


def trail_walk(k, j, sg, stop, exit_lvl, hold):
    """Market entry at O[j]; catastrophe stop; exit at next open after close crosses exit_lvl[k, b] (trailing)."""
    e = O[k, j]; end = min(T - 2, j + hold)
    for b in range(j, end + 1):
        if not np.isfinite(L[k, b]): continue
        if sg > 0 and L[k, b] <= stop or sg < 0 and H[k, b] >= stop: return b, sg * (stop / e - 1)
        x = exit_lvl[k, b]
        if np.isfinite(x) and (C[k, b] < x if sg > 0 else C[k, b] > x) and np.isfinite(O[k, b + 1]): return b + 1, sg * (O[k, b + 1] / e - 1)
    while end > j and not np.isfinite(C[k, end]): end -= 1
    return end, sg * (C[k, end] / e - 1)


def events(mask, sg, stop_pct, exit_lvl, hold, gap=16):
    tr = []
    for k in range(NS):
        busy = -1
        for i in np.where(mask[k])[0]:
            if i < START or i <= busy or i - first[k] < WARM or i + hold + 3 >= T or not np.isfinite(O[k, i + 1]): continue
            e = O[k, i + 1]; x, g = trail_walk(k, i + 1, sg, e * (1 - sg * stop_pct), exit_lvl, hold)
            tr.append((i + 1, x, g - COST)); busy = max(x, i + gap)
    return tr


HDR = ("| вариант | сделок/день | дней со входом | средняя | в плюс | 2023–24 | 2025–26 (вслепую) | по годам, % | дней в плюс | итог/просадка, % капитала |\n"
       "|---|---|---|---|---|---|---|---|---|---|")
RNG = np.random.default_rng(7)
res = []
with np.errstate(all='ignore'):
    P(f"# Тихий рынок, раунд 2: импульс и относительная стоимость — {dt.date.today()}\n\nМонет: {NS}; 2023-01-01 → "
      f"{dt.datetime.fromtimestamp(times[-1]/1000, dt.timezone.utc):%Y-%m-%d}; издержки 0.14% за круг. 10% капитала на сделку (M2 — 5% на ногу, M4 — 10% на пару).\n")

    P("## M1. Пробой максимума/минимума суток на объёме (тихий рынок), выход по 4-часовому минимуму/максимуму или через 48 ч\n"); P(HDR)
    for vm in (1.5, 3.0):
        for sg in (1, -1):
            brk = (C > hi96) if sg > 0 else (C < lo96)
            for q in (True, False):
                mask = brk & (V >= vm * vav) & (QUIET[None, :] if q else True)
                r = summ(f"M1 {'лонг' if sg>0 else 'шорт'} объём≥{vm}× {'тихо' if q else 'всегда'}",
                         events(mask, sg, 0.04, lo16 if sg > 0 else hi16, 192, gap=32))
                if r: res.append(r)

    P("\n## M2. Межмонетный импульс: раз в сутки лонг 3 сильнейших / шорт 3 слабейших\n"); P(HDR)
    for lb in (3, 7, 14):
        rl = ret(lb * 96)
        for hold in (1, 3):
            for mode in ('нейтрально', 'только лонг', 'случайные 3 (эталон)'):
                tr = []
                for i in range(START, T - hold * 96 - 2, 96 * hold):
                    col = rl[:, i]; ok = np.isfinite(col) & np.isfinite(O[:, i + 1]) & (i - first >= WARM) & np.isfinite(C[:, min(T - 1, i + hold * 96)])
                    if ok.sum() < 20: continue
                    idx = np.where(ok)[0]; srt = idx[np.argsort(col[idx])]
                    legs = [(k, 1) for k in (srt[-3:] if mode != 'случайные 3 (эталон)' else RNG.choice(idx, 3, replace=False))] + ([(k, -1) for k in srt[:3]] if mode == 'нейтрально' else [])
                    for k, sg in legs:
                        e = O[k, i + 1]; x = i + hold * 96
                        st = e * (1 - sg * 0.15); lo, hi = np.nanmin(L[k, i + 1:x + 1]), np.nanmax(H[k, i + 1:x + 1])
                        g = sg * (st / e - 1) if (sg > 0 and lo <= st or sg < 0 and hi >= st) else sg * (C[k, x] / e - 1)
                        tr.append((i + 1, x, g - COST))
                r = summ(f"M2 {lb} дн, держать {hold} дн, {mode}", tr, notional=0.05)
                if r: res.append(r)

    P("\n## M3. Сжатие волатильности → пробой (ширина полос в нижних 15% диапазона за 30 дней)\n"); P(HDR)
    sq = width <= wlo + 0.15 * (whi - wlo)
    sq_recent = np.zeros_like(sq)
    for s_ in range(1, 9): sq_recent[:, s_:] |= sq[:, :-s_]
    for zz in (2.0, 2.5):
        for sg in (1, -1):
            for q in (True, False):
                brk = (C > m80 + zz * sd80) if sg > 0 else (C < m80 - zz * sd80)
                mask = sq_recent & brk & (QUIET[None, :] if q else True)
                r = summ(f"M3 {zz}σ {'лонг' if sg>0 else 'шорт'} {'тихо' if q else 'всегда'}", events(mask, sg, 0.05, m80, 192, gap=32))
                if r: res.append(r)

    P("\n## M4. Пары монет: отношение цен отошло на 2.5σ от средней за 7 дней → вход на возврат\n"); P(HDR)
    pairs = [("ETH", "BTC"), ("SOL", "ETH"), ("BNB", "BTC"), ("LTC", "BCH"), ("AVAX", "SOL"), ("ARB", "OP"), ("LINK", "ETH"),
             ("DOT", "ADA"), ("NEAR", "APT"), ("UNI", "AAVE"), ("FIL", "ICP"), ("ATOM", "DOT")]
    for zin in (2.5, 3.0):
        tr_all = []
        for a_, b_ in pairs:
            if a_ not in syms or b_ not in syms: continue
            ka, kb = syms.index(a_), syms.index(b_)
            lr = np.log(C[ka]) - np.log(C[kb]); mu = roll_mean(lr[None, :], 672)[0]; sd = np.sqrt(np.maximum(roll_mean((lr * lr)[None, :], 672)[0] - mu ** 2, 0))
            zs = (lr - mu) / sd; i = max(START, first[ka] + WARM, first[kb] + WARM)
            while i < T - 300:
                if not (np.isfinite(zs[i]) and abs(zs[i]) >= zin and np.isfinite(O[ka, i + 1]) and np.isfinite(O[kb, i + 1])): i += 4; continue
                sg = -1 if zs[i] > 0 else 1     # sg=+1: long A / short B
                ea, eb = O[ka, i + 1], O[kb, i + 1]; x = None
                for b in range(i + 1, min(T - 2, i + 288)):
                    if not np.isfinite(zs[b]): continue
                    if sg * zs[b] >= 0 or abs(zs[b]) >= 5: x = b + 1; break
                x = x or min(T - 2, i + 288)
                if not (np.isfinite(O[ka, x]) and np.isfinite(O[kb, x])): i = x; continue
                g = 0.5 * sg * (O[ka, x] / ea - 1) - 0.5 * sg * (O[kb, x] / eb - 1)
                tr_all.append((i + 1, x, g - COST)); i = x + 4
        r = summ(f"M4 вход {zin}σ, 12 пар", sorted(tr_all), notional=0.10)
        if r: res.append(r)

    P("\n## Отбор (только по 2023–2024: оба года в плюсе, средняя ≥ +0.10%)\n")
    picked = [r for r in res if 'эталон' not in r['name'] and r['is_'] >= 0.001 and all(r['years'].get(y, -1) > 0 for y in (2023, 2024))]
    if not picked: P("Ни один вариант не прошёл отбор на 2023–2024.")
    for r in picked:
        P(f"- {r['name']}: 2023–24 {r['is_']*100:+.3f}%, вслепую 2025–26 {r['oos']*100:+.3f}% "
          f"(2025 {r['years'].get(2025, float('nan'))*100:+.2f}%, 2026 {r['years'].get(2026, float('nan'))*100:+.2f}%) → "
          f"{'держится' if r['oos'] > 0 and r['years'].get(2025, -1) > 0 and r['years'].get(2026, -1) > 0 else 'НЕ держится'}")

if '--md' in sys.argv:
    open(sys.argv[sys.argv.index('--md') + 1], 'w').write("\n".join(out) + "\n")
