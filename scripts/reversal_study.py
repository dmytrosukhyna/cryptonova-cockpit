"""11.11.2026 (owner 11:30, PAPER ONLY): the «Разворот» desk — after a clearly successful trade closes, open the OPPOSITE side.
Backtest, variants registered here BEFORE the run (54 = 3 entries × 3 minimum profits × 3 holds × 2 stops):
  events  — every closed trade of the archive data/whale_trades/*.jsonl (the whales' own history, ~24 000 trades since 07.2026) whose
            price moved ≥ MIN (5 / 10 / 20 %) in the trader's favour; one event per coin + side + closing hour (the largest);
            and, separately, our own closed copies (web/whales_live.json desk + web/fut_owner.json);
  side    — opposite to the closed trade;
  entry   — «сразу»: the close of the hourly bar of the exit;
            «уровень»: the same, only when that price is within 2 % of the 20-day extreme the trade ran into (a short closed at
                       the 20-day low = support, a long at the 20-day high = resistance). Order-book walls have no history —
                       they are NOT tested;
            «свеча»:  the close of the first 4-hour block within 24 h after the exit that moves ≥ 1.5 % in the reversal direction;
  exit    — after 1 / 3 / 7 days, or at a stop −8 % / −16 % (hourly high / low);
  costs   — 0.10 % per round trip + the real OKX funding over the hold;
  null    — the same entries with a random side, 300 draws: «beats» = the share of draws with a lower mean.
  best    — the highest mean among variants with ≥ 40 trades (chosen by this rule, whatever it says).
Prices: OKX {COIN}-USDT-SWAP hourly candles. Stdlib only; results as job annotations."""
import glob
import json
import random
import statistics as st
import time
import urllib.parse
import urllib.request

H = 3_600_000
DAY = 24 * H
NOW = int(time.time() * 1000)
COST = 0.001
OKX = "https://www.okx.com"


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
            req = urllib.request.Request(f"{OKX}{path}?{urllib.parse.urlencode(q)}", headers={"User-Agent": "cnq-research/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                d = json.loads(r.read().decode())
            if str(d.get("code")) == "0":
                return d.get("data") or []
            if str(d.get("code")) in ("51001", "51000"):
                return None
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.6 * (k + 1))
    return []


def candles(coin, t_from):
    inst, out, after = f"{coin}-USDT-SWAP", {}, None
    for _ in range(60):
        d = okx("/api/v5/market/history-candles", instId=inst, bar="1H", limit=100, **({"after": after} if after else {}))
        if d is None:
            return None
        if not d:
            break
        for r in d:
            out[int(r[0])] = (float(r[1]), float(r[2]), float(r[3]), float(r[4]))
        after = d[-1][0]
        if int(after) < t_from:
            break
        time.sleep(0.11)
    return out


def funding(coin, t_from):
    inst, out, after = f"{coin}-USDT-SWAP", {}, None
    for _ in range(8):
        d = okx("/api/v5/public/funding-rate-history", instId=inst, limit=100, **({"after": after} if after else {}))
        if not d:
            break
        for r in d:
            out[int(r["fundingTime"])] = float(r.get("realizedRate") or r.get("fundingRate") or 0)
        after = d[-1]["fundingTime"]
        if int(after) < t_from:
            break
        time.sleep(0.11)
    return out


def load_events():
    seen, ev = set(), {}
    for f in sorted(glob.glob("data/whale_trades/*.jsonl")):
        for line in open(f, encoding="utf-8"):
            try:
                r = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if r.get("kind") != "closed" or not r.get("entry") or not r.get("exit") or not r.get("close_t"):
                continue
            key = (r["trader"], r["coin"], r["side"], r["open_t"], r["close_t"])
            if key in seen:
                continue
            seen.add(key)
            s = 1 if r["side"] == "LONG" else -1
            m = s * (r["exit"] / r["entry"] - 1)
            if m < 0.05:
                continue
            k2 = (r["coin"], r["side"], int(r["close_t"]) // H)
            if k2 not in ev or abs(r.get("notional") or 0) > abs(ev[k2].get("notional") or 0):
                ev[k2] = {"coin": r["coin"], "side": r["side"], "t": int(r["close_t"]), "m": m, "notional": r.get("notional") or 0,
                          "trader": r.get("name") or r.get("trader")}
    return list(ev.values()), len(seen)


def own_events():
    out = []
    try:
        d = json.load(open("web/whales_live.json", encoding="utf-8"))["desk"]
        for c in d.get("closed") or []:
            s = 1 if c["side"] == "LONG" else -1
            m = s * (c["exit"] / c["entry"] - 1) if c.get("entry") and c.get("exit") else 0
            if m >= 0.05:
                out.append({"coin": c["coin"], "side": c["side"], "t": int(c["closed_t"]), "m": m, "src": "desk"})
    except Exception:  # noqa: BLE001
        pass
    try:
        for c in json.load(open("web/fut_owner.json", encoding="utf-8")).get("trades") or []:
            if c.get("closed") and (c.get("move_pct") or 0) >= 5:
                out.append({"coin": c["coin"], "side": c["side"], "t": int(c["closed_t"]), "m": c["move_pct"] / 100, "src": "real"})
    except Exception:  # noqa: BLE001
        pass
    return out


def trade(K, F, t_in, px, side, hold_d, stop):
    """→ net return of one paper trade (price move − costs − funding)"""
    s = 1 if side == "LONG" else -1
    t_end = t_in + hold_d * DAY
    t, last, ret = t_in + H, px, None
    while t <= t_end:
        b = K.get(t)
        if b:
            o, hi, lo, c = b
            if (lo if s == 1 else hi) * s <= px * (1 - stop * s) * s:
                ret = -stop
                break
            last = c
        t += H
    if ret is None:
        ret = s * (last / px - 1)
        t = t_end
    fund = sum(r for ft, r in F.items() if t_in < ft <= t) * s
    return ret - COST - fund


def entry(K, e, how):
    """→ (t, px) of the entry, or None"""
    hb = e["t"] // H * H
    b = K.get(hb)
    if not b:
        return None
    px = b[3]
    rev = "SHORT" if e["side"] == "LONG" else "LONG"
    if how == "now":
        return hb, px
    if how == "level":
        win = [K[t] for t in range(hb - 20 * DAY, hb, H) if t in K]
        if len(win) < 200:
            return None
        if e["side"] == "SHORT":
            ext = min(x[2] for x in win)
            return (hb, px) if px <= ext * 1.02 else None
        ext = max(x[1] for x in win)
        return (hb, px) if px >= ext * 0.98 else None
    for j in range(6):                                          # «свеча»: 4-hour blocks after the exit, within 24 h
        t0 = hb + H + j * 4 * H
        blk = [K.get(t0 + i * H) for i in range(4)]
        if any(x is None for x in blk):
            continue
        mv = blk[-1][3] / blk[0][0] - 1
        if (mv >= 0.015 and rev == "LONG") or (mv <= -0.015 and rev == "SHORT"):
            return t0 + 3 * H, blk[-1][3]
    return None


def run_set(name, events, KS, FS, rnd):
    res, best = {}, None
    t_mid = st.median([e["t"] for e in events]) if events else 0
    for how in ("now", "level", "candle"):
        ents = {}
        for i, e in enumerate(events):
            K = KS.get(e["coin"])
            if K:
                x = entry(K, e, how)
                if x and x[0] + 7 * DAY <= NOW:
                    ents[i] = x
        for mn in (0.05, 0.10, 0.20):
            idx = [i for i in ents if events[i]["m"] >= mn]
            for hold in (1, 3, 7):
                for stop in (0.08, 0.16):
                    rows = []
                    for i in idx:
                        e = events[i]
                        K, F = KS[e["coin"]], FS.get(e["coin"]) or {}
                        rev = "SHORT" if e["side"] == "LONG" else "LONG"
                        r_rev = trade(K, F, ents[i][0], ents[i][1], rev, hold, stop)
                        r_same = trade(K, F, ents[i][0], ents[i][1], e["side"], hold, stop)
                        rows.append((r_rev, r_same, e["t"] < t_mid, (e["coin"], ents[i][0] // DAY)))
                    key = f"{how}|min{int(mn * 100)}|{hold}d|stop{int(stop * 100)}"
                    if len(rows) < 8:
                        res[key] = [len(rows)]
                        continue
                    real = st.mean(r[0] for r in rows)
                    beat = 0
                    for _ in range(300):
                        beat += real > st.mean(r[0] if rnd.random() < 0.5 else r[1] for r in rows)
                    h1 = [r[0] for r in rows if r[2]]; h2 = [r[0] for r in rows if not r[2]]
                    row = [len(rows), len({r[3] for r in rows}), round(real * 100, 2), round(st.median(r[0] for r in rows) * 100, 2),
                           round(sum(r[0] > 0 for r in rows) / len(rows) * 100), round(beat / 3),
                           round(st.mean(h1) * 100, 2) if h1 else None, round(st.mean(h2) * 100, 2) if h2 else None]
                    res[key] = row
                    if len(rows) >= 40 and (best is None or real > best[1]):
                        best = (key, real, row)
    note(f"reversal · {name} [n, coin-days, mean %, median %, win %, beats random side %, 1st half %, 2nd half %]", res)
    ok = [k for k, v in res.items() if len(v) > 5 and v[5] >= 95 and v[2] > 0]
    note(f"reversal · {name} · summary", {"events": len(events), "best_by_rule": best and [best[0], best[2]], "variants_tested": len(res),
                                          "positive_and_beat_95pct": ok,
                                          "positive_both_halves_and_95": [k for k in ok if (res[k][6] or 0) > 0 and (res[k][7] or 0) > 0]})


if __name__ == "__main__":
    events, n_all = load_events()
    own = own_events()
    t_from = min([e["t"] for e in events + own] or [NOW]) - 21 * DAY
    cnt = {}
    for e in events + own:
        cnt[e["coin"]] = cnt.get(e["coin"], 0) + 1
    coins = [c for c, n in sorted(cnt.items(), key=lambda x: -x[1]) if n >= 3 or any(o["coin"] == c for o in own)][:120]
    KS, FS, miss = {}, {}, []
    for c in coins:
        K = candles(c, t_from)
        if K:
            KS[c] = K
            FS[c] = funding(c, t_from)
        else:
            miss.append(c)
    note("reversal · data", {"archive_closed_trades": n_all, "events_ge5pct_deduped": len(events), "own_copies_ge5pct": len(own),
                             "coins_with_okx_candles": len(KS), "coins_without": miss[:40],
                             "events_priced": sum(1 for e in events if e["coin"] in KS), "from": time.strftime("%Y-%m-%d", time.gmtime(t_from / 1000))})
    rnd = random.Random(11)
    run_set("whales' own history", [e for e in events if e["coin"] in KS], KS, FS, rnd)
    run_set("our own closed copies", [e for e in own if e["coin"] in KS], KS, FS, rnd)
