"""A trader's public Telegram channel — honest backtest of every NEW call (owner 22.11.2026 22:36). RESEARCH ONLY.
The channel handle comes from the run input (env INF_HANDLE) and is never printed: annotations show «канал A», no post ids.

Pre-registered (22.11 ~23:00 Athens):
- Posts: t.me/s/<handle>, paged back with ?before=<id> to 13 months or as far as Telegram serves. Every post kept, incl. edited.
- Calls: cnq/agents/influencer.classify — a coin with an OKX spot pair + a buy / sell intent now; RECAPS (an old entry against the
  price now, «уже x2», «+% от входа») are not calls. The same coin + side again within 7 days = the same call.
- Entry = the open of the first OKX 1h candle after the post time (we could not have bought earlier). Returns at 7 / 30 / 90 days
  and to date from daily closes; max drawdown after the call (lowest low to date / to 90 d); his targets (×N of entry, or a price)
  «hit» if a daily high reached them; BTC over the same windows for comparison. Spot, no costs beyond 0.2 % a round trip.
- Recaps: each recap's claimed entry is checked against the calls before it (same coin, an entry within ±25 % of the claimed one);
  a recap with no such earlier call = «claimed but never called in the channel» (survivorship signal).
- Method notes: counts of the tools / sources he names and the link domains (affiliate / paid offers).
Usage: INF_HANDLE=… influencer_study.py OUT.json"""
import json, os, re, statistics as st, sys, time, urllib.request
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from cnq.agents import influencer as I  # noqa: E402

DAY, H = 86_400_000, 3_600_000
OKX = "https://www.okx.com/api/v5"
UA = {"User-Agent": "Mozilla/5.0 (cnq-research)"}
TOOLS = ["cryptoquant", "glassnode", "arkham", "nansen", "coinglass", "defillama", "tokenunlocks", "token unlocks", "santiment",
         "tradingview", "lookonchain", "whale alert", "coingecko", "coinmarketcap", "dune", "messari", "funding", "фандинг", "ончейн",
         "on-chain", "разлок", "unlock", "нарратив", "портфел", "исследован", "топ-50", "top-50", "дивергенц", "ema", "rsi", "p3"]


def get(url, raw=False):
    for k in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                b = r.read()
                return b.decode("utf-8", "replace") if raw else json.loads(b)
        except Exception:  # noqa: BLE001
            time.sleep(2 + 2 * k)
    return None if not raw else ""


def candles(inst, bar, t_from):
    out, after = {}, None
    for _ in range(12):
        d = get(f"{OKX}/market/history-candles?instId={inst}&bar={bar}&limit=100" + (f"&after={after}" if after else ""))
        rows = (d or {}).get("data") or []
        if not rows:
            break
        for k in rows:
            out[int(k[0])] = (float(k[1]), float(k[2]), float(k[3]), float(k[4]))
        after = min(int(k[0]) for k in rows)
        if after < t_from:
            break
        time.sleep(0.15)
    return out


def main():
    import base64
    handle = base64.b64decode(os.environ.get("INF_HANDLE_B64", "")).decode().strip().lstrip("@")     # not in any file / log
    outp = sys.argv[1]
    now = int(time.time() * 1000)
    posts = I.fetch_channel(handle, lambda u: get(u, raw=True), max_pages=250, stop_before_ms=now - 400 * DAY)
    print(f"posts {len(posts)}", flush=True)
    ins = (get(f"{OKX}/public/instruments?instType=SPOT") or {}).get("data") or []
    coins = {x["baseCcy"].upper() for x in ins if x.get("quoteCcy") in ("USDT", "USDC")}
    calls, recaps, last = [], [], {}
    for p in posts:
        if not p["t"]:
            continue
        for c in I.classify(p, coins):
            c = {**c, "t": p["t"], "p3": "p3" in (p["text"] or "").lower(), "snip": (p["text"] or "")[:90].replace("\n", " ")}
            if c["kind"] == "recap":
                recaps.append(c)
            elif c["kind"] == "call":
                k = (c["coin"], c["side"])
                if k in last and c["t"] - last[k] < 7 * DAY:
                    continue
                last[k] = c["t"]
                calls.append(c)
    t0 = min([c["t"] for c in calls] + [now]) - 2 * DAY
    cache = {}

    def series(coin):
        if coin not in cache:
            cache[coin] = candles(f"{coin}-USDT", "1D", t0)
        return cache[coin]
    btc = series("BTC")
    res = []
    for c in calls:
        hd = get(f"{OKX}/market/history-candles?instId={c['coin']}-USDT&bar=1H&limit=100&after={c['t'] + 48 * H}")   # the hours around the post
        hr = {int(k[0]): (float(k[1]), float(k[2]), float(k[3]), float(k[4])) for k in ((hd or {}).get("data") or [])}
        time.sleep(0.12)
        nxt = sorted(t for t in hr if c["t"] <= t <= c["t"] + 2 * H)
        if not nxt:
            continue
        a = hr[nxt[0]][0]
        D = series(c["coin"])
        days = sorted(t for t in D if t >= c["t"] - DAY)
        if not days:
            continue
        s = 1 if c["side"] == "LONG" else -1
        r = {"coin": c["coin"], "side": c["side"], "t": c["t"], "entry": a, "p3": c["p3"], "claimed_entry": c.get("entry"), "targets_x": c["targets_x"],
             "targets_px": c["targets_px"], "horizon": c.get("horizon")}
        for w in (7, 30, 90):
            tt = [t for t in days if t <= c["t"] + w * DAY]
            if tt and c["t"] + w * DAY <= now:
                r[f"r{w}"] = round(s * (D[tt[-1]][3] / a - 1) * 100 - 0.2, 2)
                bt = sorted(t for t in btc if t <= c["t"] + w * DAY)
                b0 = sorted(t for t in btc if t >= c["t"] - DAY)
                if bt and b0:
                    r[f"btc{w}"] = round((btc[bt[-1]][3] / btc[b0[0]][0] - 1) * 100, 2)
        r["r_now"] = round(s * (D[days[-1]][3] / a - 1) * 100 - 0.2, 2)
        lows = [D[t][2] for t in days if t <= c["t"] + 90 * DAY]
        highs = [D[t][1] for t in days]
        r["mdd90"] = round((min(lows) / a - 1) * 100, 1) if lows and s > 0 else None
        tg = [a * x for x in c["targets_x"]] + [x for x in c["targets_px"] if x > a * 1.02]
        r["targets_n"] = len(tg)
        r["targets_hit"] = sum(1 for x in tg if max(highs) >= x) if tg and s > 0 else None
        res.append(r)
    # recaps vs calls (survivorship)
    unmatched = 0
    for rc in recaps:
        ce = rc.get("entry")
        prior = [c for c in calls if c["coin"] == rc["coin"] and c["t"] < rc["t"]]
        if ce and not any(r_["coin"] == rc["coin"] and r_["t"] < rc["t"] and abs(r_["entry"] / ce - 1) <= 0.25 for r_ in res):
            unmatched += 1
        elif not ce and not prior:
            unmatched += 1
    txt = "\n".join(p["text"] for p in posts).lower()
    tools = {k: txt.count(k) for k in TOOLS if txt.count(k)}
    own = {handle.lower()[i:i + 5] for i in range(max(1, len(handle) - 4))}       # never print a domain that carries his name
    doms = Counter("<свой>" if any(x in d.lower() for x in own) else d
                   for d in re.findall(r"https?://(?:www\.)?([a-z0-9.-]+)", "\n".join(p["text"] for p in posts)))
    span = [time.strftime("%Y-%m-%d", time.gmtime(posts[0]["t"] / 1000)) if posts and posts[0]["t"] else None,
            time.strftime("%Y-%m-%d", time.gmtime(posts[-1]["t"] / 1000)) if posts and posts[-1]["t"] else None]

    def agg(key, bkey=None):
        xs = [r[key] for r in res if r.get(key) is not None]
        if not xs:
            return {"n": 0}
        o = {"n": len(xs), "mean": round(st.mean(xs), 2), "median": round(st.median(xs), 2), "win": round(sum(x > 0 for x in xs) / len(xs), 3)}
        if bkey:
            bs = [r[key] - r[bkey] for r in res if r.get(key) is not None and r.get(bkey) is not None]
            o["vs_btc_mean"] = round(st.mean(bs), 2) if bs else None
            o["beat_btc"] = round(sum(x > 0 for x in bs) / len(bs), 3) if bs else None
        return o
    th = [r for r in res if r.get("targets_n")]

    def sub(sel):
        keep = [r for r in res if sel(r)]
        out = {}
        for w in ("r7", "r30", "r90"):
            xs = [r[w] for r in keep if r.get(w) is not None]
            bs = [r[w] - r["btc" + w[1:]] for r in keep if r.get(w) is not None and r.get("btc" + w[1:]) is not None]
            out[w] = {"n": len(xs), "mean": round(st.mean(xs), 2) if xs else None, "win": round(sum(x > 0 for x in xs) / len(xs), 3) if xs else None,
                      "vs_btc": round(st.mean(bs), 2) if bs else None}
        return out
    splits = {"long": sub(lambda r: r["side"] == "LONG"), "short": sub(lambda r: r["side"] == "SHORT"),
              "p3": sub(lambda r: r["p3"]), "not_p3": sub(lambda r: not r["p3"]),
              "majors": sub(lambda r: r["coin"] in ("BTC", "ETH", "SOL")), "alts": sub(lambda r: r["coin"] not in ("BTC", "ETH", "SOL"))}
    own = {handle.lower()[i:i + 5] for i in range(max(1, len(handle) - 4))}
    snips = [("C " + c["coin"] + " " + c["side"][0] + ": " + c["snip"]) for c in calls[-12:]] + [("R " + c["coin"] + ": " + c["snip"]) for c in recaps[-8:]]
    snips = ["<скрыто>" if any(x in t.lower() for x in own) else t for t in snips]
    O = {"posts": len(posts), "span": span, "calls": len(calls), "scored": len(res), "recaps": len(recaps), "recaps_without_prior_call": unmatched,
         "short_calls": sum(1 for c in calls if c["side"] == "SHORT"),
         "r7": agg("r7", "btc7"), "r30": agg("r30", "btc30"), "r90": agg("r90", "btc90"), "r_now": agg("r_now"),
         "mdd90_median": round(st.median([r["mdd90"] for r in res if r.get("mdd90") is not None]), 1) if any(r.get("mdd90") is not None for r in res) else None,
         "targets": {"calls_with_targets": len(th), "targets": sum(r["targets_n"] for r in th),
                     "hit": sum(r["targets_hit"] or 0 for r in th)},
         "tools": tools, "domains": dict(doms.most_common(15)), "splits": splits, "calls_list": res}
    json.dump(O, open(outp, "w"), ensure_ascii=False, indent=1)
    print("::notice title=inf summary::" + json.dumps({k: v for k, v in O.items() if k not in ("calls_list", "tools", "domains")}, ensure_ascii=False))
    print("::notice title=inf method::" + json.dumps({"tools": tools, "domains": O["domains"]}, ensure_ascii=False)[:4000])
    print("::notice title=inf splits::" + json.dumps(splits, ensure_ascii=False)[:4000])
    print("::notice title=inf sample::" + json.dumps(snips, ensure_ascii=False)[:4000])
    rows = [[time.strftime("%y-%m-%d", time.gmtime(r["t"] / 1000)), r["coin"], r["side"][0], round(r["entry"], 6), r.get("r7"), r.get("r30"),
             r.get("r90"), r.get("r_now"), r.get("btc30"), r.get("mdd90"), r.get("targets_n"), r.get("targets_hit")] for r in res]
    for i in range(0, min(len(rows), 240), 40):
        print(f"::notice title=inf calls {i // 40 + 1}::" + json.dumps(rows[i:i + 35], ensure_ascii=False)[:4000])


if __name__ == "__main__":
    main()
