"""Options study for docs/research/options-2026-10-30.md: python3 scripts/lab/options_study.py web/app_btc.json. BTC daily closes;
regime = capital.regime (SMA200 + 60-day return). Option prices: Black-Scholes, IV proxy = max(FLOOR, k * realized30) + put skew (no DVOL access)."""
import json, math, datetime as dt, sys

d = json.load(open(sys.argv[1]))
c = d["closes"]; start = d["start_day"]
days = [dt.date(1970, 1, 1) + dt.timedelta(n) for n in range(start, start + len(c))]
px = dict(zip(days, c))
N = len(c)

def ncdf(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))
def bs_put(S, K, T, s):
    if T <= 0 or s <= 0: return max(K - S, 0.0)
    d1 = (math.log(S / K) + 0.5 * s * s * T) / (s * math.sqrt(T)); d2 = d1 - s * math.sqrt(T)
    return K * ncdf(-d2) - S * ncdf(-d1)

# regime per day (same rule as cnq/agents/capital.py regime(): price > SMA200 and r60 > 0 -> on)
reg = {}
for i in range(N):
    if i < 200: continue
    sma = sum(c[i - 199:i + 1]) / 200; r60 = c[i] / c[i - 60] - 1
    reg[days[i]] = "on" if (c[i] > sma and r60 > 0) else "off"
# realized vol 30d
rv = {}
for i in range(30, N):
    xs = [math.log(c[j] / c[j - 1]) for j in range(i - 29, i + 1)]
    m = sum(xs) / len(xs); rv[days[i]] = math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs) * 365)

def iv_of(day, k, floor, skew): return max(floor, k * rv[day]) + skew

def run(mode, i0, i1, k=1.15, floor=0.35, skew=0.05, otm=0.10, T=30, budget=None, cash_rate=0.05, exp_on=1.0, exp_off=0.2, fee_prem=0.07):
    """mode: hold | ours | hold_put | ours_put | hold_bet. budget = premium as share of equity per 30-day roll (bet mode).
    Returns equity curve dict, yearly stats, put cost ledger."""
    eq = 1.0; curve = {}; cost = 0.0; payoff = 0.0; rolls = 0; itm = 0
    puts = []  # (expiry_index, K, qty_notional_units) qty = BTC units
    for i in range(i0, i1 + 1):
        day = days[i]; S = c[i]
        if i > i0:
            r = c[i] / c[i - 1] - 1
            e = exp_on if (mode in ("hold", "hold_put", "hold_bet") or reg[days[i - 1]] == "on") else exp_off
            eq *= (1 + e * r + (1 - e) * cash_rate / 365)
        # settle expiring puts
        for p in [p for p in puts if p[0] == i]:
            po = max(p[1] - S, 0) * p[2]
            if po > 0:
                itm += 1; po -= 0.00015 * S * p[2]  # exercise fee ~0.015 % of notional
            payoff += po / eq; eq += po
        puts = [p for p in puts if p[0] > i]
        # new put each 30 days while regime off (and no open put)
        want = mode in ("hold_put", "ours_put", "hold_bet") and reg[day] == "off" and not puts and i + T <= i1
        if want:
            K = S * (1 - otm); prem = bs_put(S, K, T / 365, iv_of(day, k, floor, skew))
            prem_frac = prem / S  # premium per 1 BTC notional as fraction of spot
            if mode == "hold_bet":
                notional = eq * budget / (prem_frac * (1 + fee_prem))  # USD notional
            else:
                notional = eq * (exp_on if mode == "hold_put" else exp_off)
            qty = notional / S
            spend = prem * qty * (1 + fee_prem)  # bid-ask + trading fee ~7 % of premium
            cost += spend / eq; eq -= spend; rolls += 1
            puts.append((i + T, K, qty))
        curve[day] = eq
    return curve, dict(cost=cost, payoff=payoff, rolls=rolls, itm=itm)

def stats(curve):
    v = list(curve.values()); ks = list(curve)
    peak = v[0]; mdd = 0
    for x in v:
        peak = max(peak, x); mdd = min(mdd, x / peak - 1)
    yrs = {}
    for y in sorted({k.year for k in ks}):
        ys = [curve[k] for k in ks if k.year == y]
        prev = [curve[k] for k in ks if k.year < y]
        base = prev[-1] if prev else ys[0]
        yrs[y] = ys[-1] / base - 1
    rets = [v[i] / v[i - 1] - 1 for i in range(1, len(v))]
    m = sum(rets) / len(rets); sd = math.sqrt(sum((x - m) ** 2 for x in rets) / len(rets))
    return dict(total=v[-1] - 1, mdd=mdd, yrs=yrs, sharpe=m / sd * math.sqrt(365) if sd else 0)

i0 = days.index(dt.date(2023, 1, 1)); i1 = N - 1
print("window", days[i0], days[i1], "regime off days:", sum(1 for i in range(i0, i1 + 1) if reg[days[i]] == "off"), "of", i1 - i0 + 1)
for y in range(2023, 2027):
    n = [i for i in range(i0, i1 + 1) if days[i].year == y]
    print(y, "off share %.0f%%" % (100 * sum(1 for i in n if reg[days[i]] == "off") / len(n)),
          "avg rv30 %.0f%%" % (100 * sum(rv[days[i]] for i in n) / len(n)))

def show(name, curve, led, k=None):
    s = stats(curve)
    print(f"{name:34s} total {s['total']*100:+7.1f}%  mdd {s['mdd']*100:6.1f}%  sharpe {s['sharpe']:.2f}  "
          + " ".join(f"{y}:{r*100:+.0f}%" for y, r in s["yrs"].items())
          + (f"  puts: {led['rolls']} rolls, {led['itm']} ITM, paid {led['cost']*100:.1f}% of equity (~{led['cost']*100/3.83:.1f}%/yr), got back {led['payoff']*100:.1f}%" if led['rolls'] else ""))

for k, floor, skew, tag in ((1.15, 0.35, 0.05, "base IV"), (1.0, 0.30, 0.03, "cheap IV"), (1.35, 0.45, 0.08, "rich IV")):
    print("---", tag, f"(IV = max({floor:.0%}, {k}×RV30) + {skew:.0%} skew)")
    show("hold BTC 100%", *run("hold", i0, i1))
    show("ours 70%/20% + USDT 5%", *run("ours", i0, i1, exp_on=0.7))
    show("hold + put 100% notional (off)", *run("hold_put", i0, i1, k=k, floor=floor, skew=skew))
    show("ours + put on 20% (off)", *run("ours_put", i0, i1, k=k, floor=floor, skew=skew, exp_on=0.7))
    show("hold + bet 5%/yr premium (off)", *run("hold_bet", i0, i1, k=k, floor=floor, skew=skew, budget=0.05 / 12))
    show("hold + bet 2%/month premium (off)", *run("hold_bet", i0, i1, k=k, floor=floor, skew=skew, budget=0.02))

# typical premium for 30d 10 % OTM put under base IV
import statistics
prem = [bs_put(c[i], 0.9 * c[i], 30 / 365, iv_of(days[i], 1.15, 0.35, 0.05)) / c[i] for i in range(i0, i1 + 1)]
print("30d 10pct-OTM put premium, pct of spot: median %.2f, p10 %.2f, p90 %.2f -> per year if rolled ~%.1f" % (
    statistics.median(prem) * 100, sorted(prem)[len(prem) // 10] * 100, sorted(prem)[9 * len(prem) // 10] * 100, statistics.median(prem) * 100 * 12))
# always-hedged variant
def run_always(**kw):
    saved = dict(reg)
    for dd in reg: reg[dd] = "off"
    out = run("hold_put", i0, i1, **kw); reg.update(saved); return out
show("hold + put 100% always (base IV)", *run_always(k=1.15, floor=0.35, skew=0.05))
H, _ = run("hold", i0, i1); O, _ = run("ours", i0, i1, exp_on=0.7); P, _ = run("hold_put", i0, i1); B, _ = run("hold_bet", i0, i1, budget=0.05 / 12)
for a, b, name in ((dt.date(2024, 3, 13), dt.date(2024, 9, 6), "2024 peak->Sep low"), (dt.date(2025, 1, 20), dt.date(2025, 4, 8), "2025 Jan->Apr"),
                   (dt.date(2025, 10, 6), days[i1], "2025 Oct peak->now")):
    def ch(cv): return (cv[b] / cv[a] - 1) * 100
    print(f"{name:22s} BTC {ch(H):+.0f}%  ours {ch(O):+.0f}%  hold+put {ch(P):+.0f}%  hold+bet5%/yr {ch(B):+.0f}%  regime at start: {reg[a]}")
# regime-off 30-day windows: how often BTC ended > 10 % lower (put in the money)
offs = [i for i in range(i0, i1 - 30) if reg[days[i]] == "off"]
print("regime-off days %d: 30 days later BTC < -10%% in %.0f%% of them, > +10%% in %.0f%%" % (
    len(offs), 100 * sum(1 for i in offs if c[i + 30] / c[i] < 0.9) / len(offs), 100 * sum(1 for i in offs if c[i + 30] / c[i] > 1.1) / len(offs)))
ons = [i for i in range(i0, i1 - 30) if reg[days[i]] == "on"]
print("regime-on  days %d: 30 days later BTC < -10%% in %.0f%% of them, > +10%% in %.0f%%" % (
    len(ons), 100 * sum(1 for i in ons if c[i + 30] / c[i] < 0.9) / len(ons), 100 * sum(1 for i in ons if c[i + 30] / c[i] > 1.1) / len(ons)))
print("BTC now", c[i1], "peak 2025", max(c[days.index(dt.date(2025, 1, 1)):]), "regime now", reg[days[i1]])
