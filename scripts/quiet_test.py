"""Quiet-market rules (17.10.2026): can the autopilot trade every day when panic/cascade rules are silent?

Data: OKX 15m USDT-swap candles, 44 coins (scripts/fetch_history.py → okx15m.npz, workflow research-data.yml).
Costs, two execution models:
  taker — market in and out: 0.05 % fee + 0.02 % slippage per side (0.14 % round trip, as all earlier tests);
  maker — entry by a resting limit order (0.02 %, filled only if the bar trades THROUGH the limit by 0.05 %),
          take-profit by limit (0.02 %), stop and time exit by market (0.05 % + 0.02 %).
          Same-bar ambiguity is resolved against us: in the fill bar only the stop is checked, never the target.
«Quiet» = BTC |24 h| < 3 % and fewer than 20 % of the 18 core coins down > 3 % over 24 h (panic rules are off).
Parameters are chosen on 2023–2024 only; 2025-01-01 → now is reported blind (out of sample).
Usage: python scripts/quiet_test.py okx15m.npz [--md out.md]"""
import sys, collections, datetime as dt, numpy as np

NPZ = sys.argv[1]
z = np.load(NPZ, allow_pickle=True)
syms = [str(s) for s in z['syms']]; times = z['times'].astype(np.int64)
O, H, L, C, V = (z[c].astype(np.float64) for c in ('open', 'high', 'low', 'close', 'volume_quote'))
NS, T = C.shape
CORE18 = "BTC ETH SOL BNB XRP DOGE ADA AVAX LINK LTC DOT NEAR BCH TRX SUI APT ARB OP".split()
core = [k for k, s in enumerate(syms) if s in CORE18]; BTC = syms.index('BTC')
TK, MK, SL = 0.0005, 0.0002, 0.0002
START = int(np.searchsorted(times, 1672531200000)); OOS = int(np.searchsorted(times, 1735689600000))
YEAR = np.array([dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).year for t in times])
DAYI = (times - times[0]) // 86_400_000
first = np.array([np.argmax(np.isfinite(C[k])) for k in range(NS)]); WARM = 30 * 96
out = []
P = lambda s='': (print(s, flush=True), out.append(s))


def roll_mean(x, n):
    o = np.full_like(x, np.nan); m = np.isfinite(x); cs = np.cumsum(np.where(m, x, 0), 1); cn = np.cumsum(m, 1)
    s = cs[:, n:] - cs[:, :-n]; c = cn[:, n:] - cn[:, :-n]
    with np.errstate(invalid='ignore', divide='ignore'):
        o[:, n:] = np.where(c >= n * 0.8, s / c, np.nan)
    return o


def ret(n): r = np.full_like(C, np.nan); r[:, n:] = C[:, n:] / C[:, :-n] - 1; return r


with np.errstate(all='ignore'):
    r1, r16, r96 = ret(1), ret(16), ret(96)
    av = np.isfinite(r96[core]).sum(0); BR = np.where(av >= 8, (r96[core] < -0.03).sum(0) / np.maximum(av, 1), 1)
    QUIET = (np.abs(r96[BTC]) < 0.03) & (BR < 0.2)
    m80 = roll_mean(C, 80); sd80 = np.sqrt(np.maximum(roll_mean(C * C, 80) - m80 ** 2, 0))
    vav = roll_mean(V, 96); vav[:, 1:] = vav[:, :-1].copy()
    hi96 = np.full_like(H, np.nan); lo96 = np.full_like(L, np.nan)
    from numpy.lib.stride_tricks import sliding_window_view as sw
    for k in range(NS):
        hi96[k, 96:] = np.max(sw(H[k], 96), 1)[:-1]; lo96[k, 96:] = np.min(sw(L[k], 96), 1)[:-1]
    trend20 = C / roll_mean(C, 1920) - 1


def walk(k, j, sg, e, stop, tgt, hold):
    """From fill bar j at price e. Returns (exit bar, gross return, exit kind). Fill bar: stop only."""
    end = min(T - 1, j + hold)
    for b in range(j, end + 1):
        if not np.isfinite(L[k, b]): continue
        if stop is not None and (L[k, b] <= stop if sg > 0 else H[k, b] >= stop): return b, sg * (stop / e - 1), 'stop'
        if b > j and tgt is not None and (H[k, b] >= tgt if sg > 0 else L[k, b] <= tgt): return b, sg * (tgt / e - 1), 'tp'
    while end > j and not np.isfinite(C[k, end]): end -= 1
    return end, sg * (C[k, end] / e - 1), 'time'


def cost(entry, kind):
    ce = MK if entry == 'maker' else TK + SL
    cx = MK if (kind == 'tp' and entry == 'maker') else TK + SL
    return ce + cx


def run(mask, sg, limit, stop_f, tgt_f, hold, entry, gap=16):
    """mask[k,i]: signal at close of bar i. limit[k,i]: limit price for bar i+1 (maker) — None → market at O[i+1]."""
    tr = []
    for k in range(NS):
        busy = -1
        for i in np.where(mask[k])[0]:
            if i < START or i <= busy or i - first[k] < WARM or i + hold + 2 >= T: continue
            j = i + 1
            if entry == 'maker':
                lp = limit[k, i]
                if not np.isfinite(lp) or not np.isfinite(L[k, j]): continue
                if not (L[k, j] < lp * (1 - 0.0005) if sg > 0 else H[k, j] > lp * (1 + 0.0005)): continue
                e = lp
            else:
                e = O[k, j]
                if not np.isfinite(e): continue
            st, tg = stop_f(k, i, e), tgt_f(k, i, e)
            if tg is not None and (sg > 0 and tg <= e or sg < 0 and tg >= e): continue
            x, g, kind = walk(k, j, sg, e, st, tg, hold)
            tr.append((k, j, x, g - cost(entry, kind)))
            busy = max(x, i + gap)
    return tr


def summ(name, tr, notional=0.10):
    """notional: share of equity per trade, for the daily PnL rows (no compounding)."""
    if not tr: P(f"| {name} | 0 | | | | | | | |"); return None
    a = np.array([t[3] for t in tr]); j = np.array([t[1] for t in tr])
    ys = collections.defaultdict(list)
    for t in tr: ys[YEAR[t[1]]].append(t[3])
    yr = " ".join(f"{y}:{np.mean(v)*100:+.2f}" for y, v in sorted(ys.items()))
    isx, oos = a[j < OOS], a[j >= OOS]
    ndays = (DAYI[T - 1] - DAYI[START] + 1)
    day = collections.defaultdict(float)
    for t in tr: day[DAYI[t[2]]] += t[3] * notional
    dv = np.array([day.get(d, 0.0) for d in range(DAYI[START], DAYI[T - 1] + 1)])
    eq = np.cumsum(dv); dd = (eq - np.maximum.accumulate(np.r_[0, eq])[1:]).min()
    active = (np.array([day.get(d, None) is not None for d in range(DAYI[START], DAYI[T - 1] + 1)]))
    pos_days = (dv[active] > 0).mean() * 100 if active.any() else 0
    P(f"| {name} | {len(a)/ndays:.1f} | {active.mean()*100:.0f}% | {a.mean()*100:+.3f}% | {(a>0).mean()*100:.0f}% | "
      f"{isx.mean()*100 if len(isx) else float('nan'):+.3f}% | {oos.mean()*100 if len(oos) else float('nan'):+.3f}% | {yr} | "
      f"{pos_days:.0f}% | {eq[-1]*100:+.1f}% / {dd*100:.1f}% |")
    return dict(name=name, tr=tr, is_=isx.mean() if len(isx) else -1, oos=oos.mean() if len(oos) else -1,
                years={y: np.mean(v) for y, v in ys.items()})


HDR = ("| вариант | сделок/день | дней со сделкой | средняя | в плюс | 2023–24 | 2025–26 (вслепую) | по годам, % | дней в плюс | итог/просадка при 10% на сделку |\n"
       "|---|---|---|---|---|---|---|---|---|---|")
res = []
with np.errstate(all='ignore'):
    P(f"# Правила для тихого рынка — {dt.date.today()}\n\nМонет: {NS}; 15 мин, {dt.datetime.fromtimestamp(times[START]/1000, dt.timezone.utc):%Y-%m-%d} → "
      f"{dt.datetime.fromtimestamp(times[-1]/1000, dt.timezone.utc):%Y-%m-%d}. Тихий рынок: {QUIET[START:].mean()*100:.0f}% времени.\n")

    # Q1 Bollinger stretch, limit entry at the band, target = mean, stop k·σ beyond, hold 24 h; quiet only, coin not trending hard
    P("## Q1. Отскок от полосы Боллинджера (20 ч), лимитка на полосе, цель — средняя\n"); P(HDR)
    for z_ in (2.0, 2.5, 3.0):
        for sg in (1, -1):
            for entry in ('maker', 'taker'):
                band = m80 - sg * z_ * sd80
                if entry == 'maker':
                    mask = QUIET[None, :] & (np.abs(trend20) < 0.15) & np.isfinite(band) & (sd80 / C > 0.004)
                else:
                    mask = QUIET[None, :] & (np.abs(trend20) < 0.15) & ((C < band) if sg > 0 else (C > band))
                stopf = lambda k, i, e, sg=sg: e * (1 - sg * 0.03)
                tgtf = lambda k, i, e: m80[k, i]
                r = summ(f"Q1 {z_}σ {'лонг' if sg>0 else 'шорт'} {entry}", run(mask, sg, band, stopf, tgtf, 96, entry, gap=16))
                if r: res.append(r)

    # Q2 range edge: buy limit at the 24 h low + 10 % of range, target mid, stop below the low; range width 3–12 %
    P("\n## Q2. Коридор суток: покупка у нижней границы, цель — середина\n"); P(HDR)
    for edge in (0.05, 0.15):
        for sg in (1, -1):
            for entry in ('maker', 'taker'):
                w = (hi96 - lo96) / C
                lvl = lo96 + edge * (hi96 - lo96) if sg > 0 else hi96 - edge * (hi96 - lo96)
                base = QUIET[None, :] & (w > 0.03) & (w < 0.12) & (np.abs(trend20) < 0.15)
                mask = base if entry == 'maker' else base & ((C <= lvl) if sg > 0 else (C >= lvl))
                mid = (hi96 + lo96) / 2
                stopf = (lambda k, i, e: lo96[k, i] - 0.25 * (hi96[k, i] - lo96[k, i])) if sg > 0 else (lambda k, i, e: hi96[k, i] + 0.25 * (hi96[k, i] - lo96[k, i]))
                r = summ(f"Q2 край {edge:.0%} {'лонг' if sg>0 else 'шорт'} {entry}", run(mask, sg, lvl, stopf, lambda k, i, e: mid[k, i], 96, entry, gap=32))
                if r: res.append(r)

    # Q3 idiosyncratic flush in a quiet market: 15m candle ≤ −1.5 % on ≥ 3× volume while BTC's candle > −0.5 %
    P("\n## Q3. Одиночный пролив монеты при спокойном рынке (свеча ≤ −1.5% на объёме ≥3×, BTC спокоен)\n"); P(HDR)
    for drop in (-0.015, -0.025):
        for tp in (0.01, 0.02):
            for entry in ('maker', 'taker'):
                mask = QUIET[None, :] & (r1 <= drop) & (V >= 3 * vav) & (r1[BTC][None, :] > -0.005)
                mask[BTC] = False
                lim = C.copy()     # maker: rest a bid at the flush candle's close
                r = summ(f"Q3 свеча {drop:.1%} цель +{tp:.0%} {entry}",
                         run(mask, 1, lim, lambda k, i, e: e * 0.975, lambda k, i, e, tp=tp: e * (1 + tp), 32, entry, gap=16))
                if r: res.append(r)

    # Q4 market-neutral residual reversal every 4 h: long 3 worst / short 3 best 4 h residual vs median, hold 4 h
    P("\n## Q4. Рыночно-нейтральный откат: каждые 4 ч лонг 3 худших и шорт 3 лучших монеты за 4 ч (относительно медианы)\n"); P(HDR)
    for hold in (16, 32):
        for entry in ('taker', 'maker'):
            tr = []
            for i in range(START, T - hold - 2, 16):
                col = r16[:, i]; ok = np.isfinite(col) & np.isfinite(O[:, i + 1]) & (i - first >= WARM)
                if ok.sum() < 20 or not QUIET[i]: continue
                res_ = col - np.nanmedian(col[ok]); idx = np.where(ok)[0]; srt = idx[np.argsort(res_[idx])]
                for k, sg in [(k, 1) for k in srt[:3]] + [(k, -1) for k in srt[-3:]]:
                    j = i + 1
                    if entry == 'maker':
                        lp = C[k, i]
                        if not (L[k, j] < lp * 0.9995 if sg > 0 else H[k, j] > lp * 1.0005): continue
                        e = lp
                    else: e = O[k, j]
                    x, g, kind = walk(k, j, sg, e, e * (1 - sg * 0.05), None, hold)
                    tr.append((k, j, x, g - cost(entry, kind)))
            r = summ(f"Q4 держать {hold//4} ч {entry}", tr, notional=0.05)
            if r: res.append(r)

    # Pick on 2023–2024 only: every in-sample year > 0, avg > 0.05 %; then show blind 2025–26
    P("\n## Отбор (решение только по 2023–2024: оба года в плюсе и средняя ≥ +0.05%)\n")
    picked = [r for r in res if r['is_'] >= 0.0005 and all(r['years'].get(y, -1) > 0 for y in (2023, 2024))]
    if not picked: P("Ни один вариант не прошёл отбор на 2023–2024.")
    for r in picked:
        P(f"- {r['name']}: 2023–24 {r['is_']*100:+.3f}%, вслепую 2025–26 {r['oos']*100:+.3f}% → {'держится' if r['oos'] > 0 else 'НЕ держится'}")
    keep = [r for r in picked if r['oos'] > 0]
    if keep:
        P("\n## Вместе (прошедшие вслепую), 10% капитала на сделку\n"); P(HDR)
        summ("все прошедшие вместе", sorted([t for r in keep for t in r['tr']], key=lambda t: t[1]))
        oosk = [t for r in keep for t in r['tr'] if t[1] >= OOS]
        summ("только 2025–26", oosk)

if '--md' in sys.argv:
    open(sys.argv[sys.argv.index('--md') + 1], 'w').write("\n".join(out) + "\n")
