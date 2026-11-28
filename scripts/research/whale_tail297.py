"""Item 297 (28.11): decompose the tail of desk «Киты» copies since 04.11 and compare with the real account.
Read-only. Inputs: web/whales_live.json (desk.closed), web/public_real.json (closed futures).
Accounting only: no rule search, no variants -> no multiple-testing correction needed."""
import json, statistics as S, collections as C, sys, datetime as D

d = json.load(open("web/whales_live.json"))["desk"]
pr = json.load(open("web/public_real.json"))
T0 = D.datetime(2026, 11, 4, tzinfo=D.timezone.utc).timestamp() * 1000
day = lambda t: D.datetime.fromtimestamp(t / 1000, D.timezone.utc).strftime("%d.%m")


def summ(name, v):
    p = [c["pnl"] for c in v]
    if not p:
        print(name, 0); return
    print(f"{name:42s} n={len(p):4d} sum$={sum(p):9.1f} med$={S.median(p):7.2f} win={sum(x > 0 for x in p):3d}")


cl = [c for c in d["closed"] if c["opened_t"] >= T0]
early = [c for c in d["closed"] if c["opened_t"] < T0]
summ("ALL closed", d["closed"]); summ("before 04.11", early); summ("since 04.11", cl)
newin = [c for c in cl if not c.get("join") and c.get("gap_pct") is not None and -3 <= c["gap_pct"] <= 1]
summ("since 04.11 new in window", newin)

print("\n-- by exit reason (since 04.11)")
g = C.defaultdict(list)
for c in cl:
    w = c["why"]
    k = ("стоп" if w.startswith("стоп") else "быстрый выход" if w.startswith("быстрый") else
         "владелец" if "владел" in w else w[:30])
    g[k].append(c)
for k, v in sorted(g.items(), key=lambda kv: sum(c["pnl"] for c in kv[1])):
    summ(k, v)

print("\n-- loss size buckets by pnl_pct on margin (since 04.11)")
b = C.defaultdict(list)
for c in cl:
    x = c["pnl_pct"]
    b["<= -40%" if x <= -40 else "-40..-20%" if x <= -20 else "-20..-5%" if x <= -5 else "-5..+5%" if x < 5 else "+5..+20%" if x < 20 else ">= +20%"].append(c)
for k in ["<= -40%", "-40..-20%", "-20..-5%", "-5..+5%", "+5..+20%", ">= +20%"]:
    summ(k, b[k])

print("\n-- 5 biggest losses since 04.11")
for c in sorted(cl, key=lambda c: c["pnl"])[:5]:
    print(day(c["opened_t"]), "->", day(c["closed_t"]), c["whale"][:16], c["coin"], c["side"], "lev", c["lev"], "margin", round(c["margin"]),
          "pnl$", round(c["pnl"], 1), f"{c['pnl_pct']}%", c["why"][:40])
loss5 = sum(c["pnl"] for c in sorted(cl, key=lambda c: c["pnl"])[:5])
print("sum 5 biggest losses", round(loss5, 1), "| rest", round(sum(c["pnl"] for c in cl) - loss5, 1))

print("\n-- by leverage (since 04.11)")
lv = C.defaultdict(list)
for c in cl:
    lv["<=3x" if c["lev"] <= 3 else "4-5x" if c["lev"] <= 5 else "6-10x" if c["lev"] <= 10 else ">10x"].append(c)
for k in ["<=3x", "4-5x", "6-10x", ">10x"]:
    summ(k, lv[k])

print("\n-- by trader (since 04.11)")
tr = C.defaultdict(list)
for c in cl:
    tr[c["whale"]].append(c)
for k, v in sorted(tr.items(), key=lambda kv: sum(c["pnl"] for c in kv[1])):
    summ(k[:24], v)

print("\n-- by side (since 04.11)")
for s in ("LONG", "SHORT"):
    summ(s, [c for c in cl if c["side"] == s])

print("\n-- halves by open time (since 04.11)")
cl.sort(key=lambda c: c["opened_t"]); h = len(cl) // 2
summ("1st half " + day(cl[0]["opened_t"]) + "-" + day(cl[h - 1]["opened_t"]), cl[:h]); summ("2nd half " + day(cl[h]["opened_t"]) + "-" + day(cl[-1]["opened_t"]), cl[h:])

# daily sum t (item 291 measure)
ds = C.defaultdict(float)
for c in cl:
    ds[day(c["closed_t"])] += c["pnl"]
v = list(ds.values())
t = S.mean(v) / (S.stdev(v) / len(v) ** 0.5) if len(v) > 2 else float("nan")
print("\ndays", len(v), "mean day$", round(S.mean(v), 1), "t by day sum", round(t, 2))

print("\n== real account closed futures (all since 04.11)")
rc = pr["closed"]
pp = [c["pnl_usd"] for c in rc]
print("n", len(rc), "sum$", round(sum(pp), 2), "med$", round(S.median(pp), 2), "win", sum(x > 0 for x in pp))
# match real trades to desk copies: same coin+side, open within 10 min
m = []
for r in rc:
    coin = r["inst"].split("-")[0]
    side = "LONG" if r["side"] == "long" else "SHORT"
    best = [c for c in d["closed"] if c["coin"].upper().lstrip("K") in (coin, coin.lstrip("K")) or c["coin"].upper() == coin]
    best = [c for c in best if c["side"] == side and abs(c["opened_t"] - r["opened"]) < 15 * 60 * 1000]
    m.append((r, best[0] if best else None))
print("matched to desk", sum(1 for _, c in m if c), "of", len(m))
print("real move% x lev (return on margin) vs desk pnl_pct, matched:")
rr, dd = [], []
for r, c in m:
    if not c:
        print("  unmatched real", r["inst"], r["side"], day(r["opened"]), r["pnl_usd"]); continue
    rom = r["move_pct"] * r["lev"]
    rr.append(rom); dd.append(c["pnl_pct"])
    print(f"  {r['inst']:18s} {r['side']:5s} {day(r['opened'])} real {rom:7.1f}% (${r['pnl_usd']:6.2f}, lev {r['lev']})  desk {c['pnl_pct']:7.1f}% (${c['pnl']:7.1f}, lev {c['lev']}) {c['why'][:22]}")
if rr:
    print("mean return on margin: real", round(S.mean(rr), 2), "desk", round(S.mean(dd), 2), "| sum real", round(sum(rr), 1), "desk", round(sum(dd), 1))
real_stops = [r for r in rc if r["move_pct"] * r["lev"] <= -40]
print("real trades with return on margin <= -40%:", len(real_stops), round(sum(r["pnl_usd"] for r in real_stops), 2))

print("\n== same window as the public real account (closed since pr['since'])")
w = [c for c in d["closed"] if c["closed_t"] >= pr["since"]]
summ("desk closed since real 'since'", w)
for k in ("стоп", "быстрый"):
    summ("  of which " + k, [c for c in w if c["why"].startswith(k)])
matched_ids = {id(c) for _, c in m if c}
summ("  desk copies with a real twin", [c for c in w if id(c) in matched_ids])
summ("  desk copies without a real twin", [c for c in w if id(c) not in matched_ids])
nt = C.Counter(c["whale"][:20] for c in w if id(c) not in matched_ids)
print("  no-twin by trader:", nt.most_common(8))
