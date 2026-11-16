"""16.11.2026 (backlog п. 181 + owner 11:38: «в спорных случаях выбирать сторону по тренду рынка — на спаде шорт»). RESEARCH ONLY.
The case: we hold a copy of trader A on a coin, and another followed trader B opens the OPPOSITE side of that coin while A's is open.
Variants registered here BEFORE the run (per case, unlevered price move, t0 = B's open + 2 min):
  a  keep first (today's rule): A's side from t0 to A's exit ................................ cost 0.05 % (the exit only)
  b  switch to the newer signal: close A at t0, B's side to B's exit + 2 min ............... cost 0.15 %
  c  regime side: the side that matches the BTC regime at t0 (BTC above its 200-day average and above 90 days ago → long,
     else short) — that is «a» or «b» for the case
  d  flat: close at t0, stand aside while they disagree; when one of them exits, follow the one still in to his exit .. 0.15 %
  e1 source priority «отобранный кит важнее Ивана»: A is Ivan → b, B is Ivan → a, no Ivan → a
  e2 the reverse («Иван важнее»): B is Ivan → b, A is Ivan → a, no Ivan → a
Sets: REAL — our desk's actual copies since 04.11 (web/whales_live.json desk open + closed) against the followed traders'
positions (data/whale_trades, the desk's own copies, the desk's «монета уже скопирована» notes for Ivan);
SIM — the same on the traders' longer history: every pair of traders we follow / followed (no Ivan — he has no archive) whose
positions on one coin overlapped with opposite sides; «our exit» = A's own exit + 2 min.
Prices: OKX {COIN}-USDT-SWAP 1-minute candles at the needed minutes. Threshold from the backlog: switching better in total and in
≥ 55 % of cases, not worse for the Ivan cases; n < 15 → forward logging only. Stdlib only; results as job annotations."""
import glob
import json
import random
import statistics as st
import time
import urllib.parse
import urllib.request

MIN = 60_000
DAY = 86_400_000
NOW = int(time.time() * 1000)
DELAY = 2 * MIN
T_REAL = 1793750400000                     # 04.11.2026
IVAN = "ivan:bybit"


def note(title, msg):
    s = msg if isinstance(msg, str) else json.dumps(msg, ensure_ascii=False)
    parts, cur = [], ""
    for ch in s:
        if len((cur + ch).encode()) > 3500:
            parts.append(cur); cur = ""
        cur += ch
    parts.append(cur)
    for i, p_ in enumerate(parts[:14]):
        print(f"::notice title={title} {i + 1}/{len(parts)}::" + p_.replace("%", "%25").replace("\n", "%0A"), flush=True)


def okx(path, **q):
    for k in range(4):
        try:
            req = urllib.request.Request(f"https://www.okx.com{path}?{urllib.parse.urlencode(q)}", headers={"User-Agent": "cnq-research/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                d = json.loads(r.read().decode())
            if str(d.get("code")) == "0":
                return d.get("data") or []
            if str(d.get("code")) in ("51001", "51000"):
                return None
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5 * (k + 1))
    return []


_PX = {}


def px(coin, t):
    """the close of the 1-minute bar that contains t (None = no OKX swap / no data)"""
    t = min(t, NOW - 2 * MIN)
    k = (coin, t // MIN)
    if k not in _PX:
        d = okx("/api/v5/market/history-candles", instId=f"{coin}-USDT-SWAP", bar="1m", limit=3, after=str(t // MIN * MIN + MIN + 1))
        time.sleep(0.06)
        _PX[k] = float(d[0][4]) if d else None
    return _PX[k]


def btc_regime():
    rows, after = {}, None
    for _ in range(4):
        d = okx("/api/v5/market/history-candles", instId="BTC-USDT", bar="1Dutc", limit=100, **({"after": after} if after else {})) or []
        if not d:
            break
        for r in d:
            rows[int(r[0])] = float(r[4])
        after = d[-1][0]
    ts = sorted(rows)
    out = {}
    for i, t in enumerate(ts):
        if i >= 200:
            c = [rows[x] for x in ts[i - 199:i + 1]]
            out[t + DAY] = rows[t] > sum(c) / 200 and rows[t] > rows[ts[i - 90]]      # known after that day's close
    return out


def bull_at(reg, t):
    ks = [k for k in reg if k <= t]
    return reg[max(ks)] if ks else None


def merge(trips):
    """closed sub-positions of one trader / coin / side → positions (overlapping intervals joined)"""
    out = []
    for a, b in sorted(trips):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def load():
    desk = json.load(open("web/whales_live.json", encoding="utf-8"))["desk"]
    wt = json.load(open("web/whales_top.json", encoding="utf-8"))
    names = {x["addr"]: x.get("name") for x in wt.get("following") or []}
    rot = wt.get("rotation") or {}
    for x in (rot.get("a") or []) + (rot.get("b") or []):
        names.setdefault(x["addr"], x.get("name"))
    copies = []
    for c in (desk.get("closed") or []) + (desk.get("open") or []):
        copies.append({"whale": c["whale"], "coin": c["coin"], "side": c["side"], "t0": int(c["opened_t"]),
                       "t1": int(c.get("closed_t") or NOW), "open": not c.get("closed_t"), "why": c.get("why")})
        names.setdefault(c["whale"], c["whale"][:12])
    followed = set(names)
    by = {}
    seen = set()
    for f in sorted(glob.glob("data/whale_trades/*.jsonl")):
        for line in open(f, encoding="utf-8"):
            try:
                r = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if r.get("kind") != "closed" or r.get("trader") not in followed:
                continue
            k = (r["trader"], r["coin"], r["side"], r["open_t"], r["close_t"])
            if k in seen:
                continue
            seen.add(k)
            if r.get("name"):
                names[r["trader"]] = r["name"]
            by.setdefault((r["trader"], r["coin"], r["side"]), []).append((int(r["open_t"]), int(r["close_t"])))
    pos = [{"whale": w, "coin": c, "side": s, "t0": a, "t1": b, "open": False} for (w, c, s), v in by.items() for a, b in merge(v)]
    notes = [e for e in desk.get("events") or [] if "другого трейдера" in str(e.get("why")) and e.get("whale") == IVAN]
    return copies, pos, names, notes


def cases(holds, sigs, real):
    out, seen = [], set()
    for h in holds:
        for p in sigs:
            if p["whale"] == h["whale"] or p["coin"] != h["coin"] or p["side"] == h["side"]:
                continue
            if not (h["t0"] < p["t0"] < h["t1"]):
                continue
            k = (h["whale"], h["coin"], h["t0"], p["whale"])
            if k in seen:
                continue
            seen.add(k)
            out.append({"A": h, "B": p, "real": real})
    return out


def evaluate(c, reg):
    A, B = c["A"], c["B"]
    coin = A["coin"]
    t0 = B["t0"] + DELAY
    tA = A["t1"] if c["real"] else A["t1"] + DELAY               # REAL: when OUR copy closed; SIM: his exit + 2 min
    tB = (B["t1"] + DELAY) if not B.get("open") else NOW
    p0, pA, pB = px(coin, t0), px(coin, tA), px(coin, tB)
    if not (p0 and pA and pB):
        return None
    sA = 1 if A["side"] == "LONG" else -1
    sB = -sA
    a = sA * (pA / p0 - 1) - 0.0005
    b = sB * (pB / p0 - 1) - 0.0015
    if tA <= tB:
        d = -0.0005 + sB * (pB / pA - 1) - 0.001
    else:
        d = -0.0005 + sA * (pA / pB - 1) - 0.001
    bull = bull_at(reg, t0)
    reg_long = bool(bull)
    cc = a if (sA == 1) == reg_long else b
    ivA, ivB = A["whale"] == IVAN, B["whale"] == IVAN
    e1 = b if ivA else a
    e2 = b if ivB else a
    return {"coin": coin, "t": t0, "A": A["whale"], "B": B["whale"], "A_side": A["side"], "bull": bull, "ivan": ivA or ivB,
            "open": bool(A.get("open") or B.get("open")), "a": a, "b": b, "c": cc, "d": d, "e1": e1, "e2": e2}


def table(rows):
    if not rows:
        return {"n": 0}
    rows = sorted(rows, key=lambda r: r["t"])
    h = len(rows) // 2
    out = {"n": len(rows), "still_open": sum(r["open"] for r in rows), "bull_cases": sum(1 for r in rows if r["bull"])}
    for k in ("a", "b", "c", "d", "e1", "e2"):
        xs = [r[k] for r in rows]
        out[k] = {"total_pct": round(sum(xs) * 100, 2), "mean_pct": round(st.mean(xs) * 100, 2), "win_pct": round(sum(x > 0 for x in xs) / len(xs) * 100),
                  "half1_pct": round(sum(xs[:h]) * 100, 2), "half2_pct": round(sum(xs[h:]) * 100, 2),
                  "better_than_a_pct": round(sum(r[k] > r["a"] for r in rows) / len(rows) * 100) if k != "a" else None}
    return out


if __name__ == "__main__":
    copies, pos, names, notes = load()
    reg = btc_regime()
    nm = lambda a: names.get(a) or a[:12]   # noqa: E731
    # REAL: our copies since 04.11 as «held», the followed traders' positions + our other copies + Ivan's blocked signals as «new»
    holds = [c for c in copies if c["t0"] >= T_REAL]
    sigs = pos + copies + [{"whale": IVAN, "coin": e["coin"], "side": e["side"], "t0": int(e["t"]), "t1": NOW, "open": True} for e in notes]
    real = [r for r in (evaluate(c, reg) for c in cases(holds, sigs, True)) if r]
    note("conflict · REAL cases since 04.11", table(real))
    note("conflict · REAL · Ivan involved", table([r for r in real if r["ivan"]]))
    note("conflict · REAL · list", [[time.strftime("%d.%m %H:%M", time.gmtime(r["t"] / 1000)), r["coin"], nm(r["A"]), r["A_side"], nm(r["B"]),
                                     "bull" if r["bull"] else "bear", round(r["a"] * 100, 2), round(r["b"] * 100, 2), round(r["d"] * 100, 2),
                                     "open" if r["open"] else ""] for r in sorted(real, key=lambda r: r["t"])])
    # SIM: the followed traders' longer history, pairs of their own positions
    cs = cases(pos, pos, False)
    random.Random(16).shuffle(cs)
    sim = [r for r in (evaluate(c, reg) for c in cs[:700]) if r]
    note("conflict · SIM (followed traders' history)", {"pairs_found": len(cs), "priced": len(sim), **table(sim)})
    note("conflict · SIM · bear regime only", table([r for r in sim if r["bull"] is False]))
    note("conflict · SIM · bull regime only", table([r for r in sim if r["bull"]]))
    note("conflict · data", {"copies_since_0411": len(holds), "followed": len(names), "archive_positions": len(pos),
                             "from": time.strftime("%Y-%m-%d", time.gmtime(min([p["t0"] for p in pos] or [NOW]) / 1000)),
                             "regime_days": len(reg), "ivan_notes": len(notes)})
