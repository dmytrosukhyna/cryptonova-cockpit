"""Public Telegram futures-signal channels — honest backtest of every call (owner 26.11.2026 18:24). RESEARCH ONLY.
The channels come as ONE masked run input (base64 of «handle1,handle2,…»); each gets an anonymous key in input order (B, C, D, …;
A is the channel studied on 22.11). No handle, post id or link is printed or written: annotations and the artifact carry keys only.

Pre-registered (26.11 ~19:30 Athens, before any result):
- Posts: t.me/s/<handle>, paged back up to 180 days (or as far as Telegram serves). Calls: cnq/agents/fut_calls.parse — a coin with an
  OKX USDT swap, a side, an entry (price / zone / market), a stop and ≥ 1 target that line up; result / «target hit» posts are not
  calls; the same coin + side within 24 h = the same call.
- Fill: market at the open of the first OKX 1h bar after the post when no zone is given or the open is within / beyond the zone our
  way (and ≤ 3 % from its middle); else the zone's near edge if a bar touches it within 48 h; else «no fill» (counted, not scored).
- Exit (primary, tp1): the stop or the first target, whichever a 1h bar reaches first — a bar touching both = the stop; else the close
  after 7 days. Secondary (trail): the last target, the stop moved to the entry after the first target. Costs 0.11 % a round trip;
  no funding (≤ 7 days). Result in % of notional (no leverage) and in R (stop distance).
- Per channel: calls, fills, win share, TP1-before-stop share, PF (% based), mean %, sum %, max drawdown of the cumulative %, the two
  halves of time (mean each), longs / shorts, calls a week and the last call's age, «result» posts vs calls (bragging ratio).
- Rank: n ≥ 20 fills, PF > 1.2 and mean > 0 in BOTH halves; then by PF. The top 2–3 → PAPER desks in the experiment factory only.
Usage: INF_HANDLES_B64=… community_calls.py OUT.json"""
import base64, json, os, re, statistics as st, sys, time, urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from cnq.agents import fut_calls as FC, influencer as I  # noqa: E402

DAY, H = 86_400_000, 3_600_000
OKX = "https://www.okx.com/api/v5"
UA = {"User-Agent": "Mozilla/5.0 (cnq-research)"}
KEYS = "BCDEFGHIJKLMNOPQRSTU"
SAMPLES = {}
SIGLIKE = re.compile(r"(long|short|buy|sell|entry|лонг|шорт|вход|stop|стоп|target|tp\b|тейк)", re.I)


def get(url, raw=False):
    for k in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                b = r.read()
                return b.decode("utf-8", "replace") if raw else json.loads(b)
        except Exception:  # noqa: BLE001
            time.sleep(2 + 2 * k)
    return "" if raw else None


BARS = {}


def bars(coin, t0, t1):
    """OKX 1h swap bars [[t, o, h, l, c]] covering [t0, t1], cached per coin (paged back from t1)"""
    have = BARS.get(coin)
    if have and have["t0"] <= t0 and have["t1"] >= t1:
        return have["b"]
    out, after = {}, t1 + H
    for _ in range(80):
        d = get(f"{OKX}/market/history-candles?instId={coin}-USDT-SWAP&bar=1H&limit=100&after={after}")
        rows = (d or {}).get("data") or []
        if not rows:
            break
        for r in rows:
            out[int(r[0])] = [int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])]
        after = min(int(r[0]) for r in rows)
        time.sleep(0.21)
        if after <= t0:
            break
    b = [out[k] for k in sorted(out)]
    BARS[coin] = {"t0": t0, "t1": t1, "b": b}
    return b


def stat(xs):
    if not xs:
        return {"n": 0}
    pos, neg = sum(x for x in xs if x > 0), -sum(x for x in xs if x < 0)
    cum, peak, dd = 0.0, 0.0, 0.0
    for x in xs:
        cum += x
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return {"n": len(xs), "win": round(sum(x > 0 for x in xs) / len(xs), 3), "pf": round(pos / neg, 2) if neg else None,
            "mean": round(st.mean(xs), 3), "sum": round(sum(xs), 1), "max_dd": round(dd, 1)}


def main():
    out = sys.argv[1]
    hs = [h.strip().lstrip("@") for h in base64.b64decode(os.environ.get("INF_HANDLES_B64", "")).decode().split(",") if h.strip()]
    now = int(time.time() * 1000)
    ins = (get(f"{OKX}/public/instruments?instType=SWAP") or {}).get("data") or []
    coins = {x["instId"][:-len("-USDT-SWAP")] for x in ins if str(x.get("instId", "")).endswith("-USDT-SWAP")}
    print(f"okx swaps {len(coins)}, channels {len(hs)}", flush=True)
    res = {}
    for k, h in zip(KEYS, hs):
        posts = I.fetch_channel(h, lambda u: get(u, raw=True), max_pages=120, stop_before_ms=now - 180 * DAY)
        posts = [p for p in posts if p.get("t") and p["t"] >= now - 180 * DAY]
        cl = FC.calls(posts, coins)
        sig = [p["text"][:500] for p in posts if p.get("text") and re.search(r"\d", p["text"]) and SIGLIKE.search(p["text"])]
        SAMPLES[k] = {"n_siglike": len(sig), "siglike": [x[:260] for x in sig[-4:]]}
        brag = sum(1 for p in posts if FC.RESULT.search(p.get("text") or ""))
        rows = []
        for c in cl:
            if c["t"] > now - 8 * DAY:
                continue                                              # not over yet
            b = [x for x in bars(c["coin"], c["t"] - 2 * H, min(now, c["t"] + 8 * DAY)) if x[0] >= c["t"] - c["t"] % H + H]
            r1 = FC.sim(c, b, mode="tp1")
            r2 = FC.sim(c, b, mode="trail")
            if not r1:
                continue
            rows.append({"t": c["t"], "coin": c["coin"], "side": c["side"], "market": c["market"], "tp1": r1, "trail": r2,
                         "risk_pct": round(abs((c["entry_hi"] or 0) and (c["stop"] / ((c["entry_lo"] + c["entry_hi"]) / 2) - 1) * 100), 2)})
        fills = [r for r in rows if r["tp1"].get("kind") != "no_fill"]
        x1 = [r["tp1"]["pct"] for r in fills]
        x2 = [r["trail"]["pct"] for r in fills if r["trail"] and "pct" in r["trail"]]
        mid = st.median([r["t"] for r in fills]) if fills else 0
        o = {"posts": len(posts), "days": round((now - min((p["t"] for p in posts), default=now)) / DAY), "calls": len(cl),
             "scored": len(rows), "no_fill": len(rows) - len(fills), "result_posts": brag,
             "calls_week": round(len(cl) / max(1, (now - min((p["t"] for p in posts), default=now)) / DAY / 7), 1),
             "last_call_d": round((now - max((c["t"] for c in cl), default=0)) / DAY, 1) if cl else None,
             "calls_30d": sum(1 for c in cl if c["t"] >= now - 30 * DAY), "tp1": stat(x1), "trail": stat(x2),
             "tp1_hit": round(sum(r["tp1"]["kind"] == "tp" for r in fills) / len(fills), 3) if fills else None,
             "mean_r": round(st.mean(r["tp1"]["r"] for r in fills), 3) if fills else None,
             "half1": stat([r["tp1"]["pct"] for r in fills if r["t"] < mid]), "half2": stat([r["tp1"]["pct"] for r in fills if r["t"] >= mid]),
             "long": stat([r["tp1"]["pct"] for r in fills if r["side"] == "LONG"]),
             "short": stat([r["tp1"]["pct"] for r in fills if r["side"] == "SHORT"]),
             "majors": stat([r["tp1"]["pct"] for r in fills if r["coin"] in ("BTC", "ETH", "SOL")])}
        h1, h2 = o["half1"], o["half2"]
        o["passes"] = bool(o["tp1"]["n"] >= 20 and (o["tp1"].get("pf") or 0) > 1.2 and h1.get("mean", -1) > 0 and h2.get("mean", -1) > 0)
        o["rows"] = [{k_: r[k_] for k_ in ("t", "coin", "side", "tp1", "trail")} for r in fills]
        res[k] = o
        print(f"cc {k} " + json.dumps({k_: v for k_, v in o.items() if k_ != "rows"}, ensure_ascii=False), flush=True)
    pk = os.environ.get("SAMPLE_PK", "").strip()
    if pk and SAMPLES:                                  # format check: post samples sealed to a one-off key of the researcher
        from cnq import sealed
        import zlib
        blob = json.dumps(sealed.seal(pk, zlib.compress(json.dumps(SAMPLES, ensure_ascii=False).encode(), 9)))
        with open("community_samples.chunks", "w") as f_:                  # printed as annotations by the next step (≤ 10 × 4000)
            for i in range(0, min(len(blob), 40000), 4000):
                f_.write(blob[i:i + 4000] + "\n")
    rank = sorted([k for k in res if res[k]["passes"]], key=lambda k: -(res[k]["tp1"].get("pf") or 0))
    json.dump({"t": now, "channels": res, "rank": rank}, open(out, "w"), ensure_ascii=False, indent=1)
    line = {k: [o["posts"], o["days"], o["calls"], o["tp1"].get("n"), o["tp1"].get("win"), o["tp1"].get("pf"), o["tp1"].get("mean"),
                o["tp1"].get("sum"), o["half1"].get("mean"), o["half2"].get("mean"), o["trail"].get("pf"), o["tp1_hit"], o["result_posts"],
                o["calls_week"], o["last_call_d"], o["short"].get("n"), o["calls_30d"]] for k, o in res.items()}
    print("::notice title=cc cols::posts, days, calls, fills, win, pf, mean %, sum %, half1 mean, half2 mean, trail pf, tp1 hit, "
          "result posts, calls/week, last call d, shorts, calls 30d")
    items = list(line.items())
    for i in range(0, len(items), 7):
        print(f"::notice title=cc stats {i // 7 + 1}::" + json.dumps(dict(items[i:i + 7]))[:4000])
    print("::notice title=cc rank::" + json.dumps({"pass": rank}))


if __name__ == "__main__":
    main()
