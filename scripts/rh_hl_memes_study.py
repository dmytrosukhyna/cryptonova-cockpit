"""11.11.2026 (owner, RESEARCH ONLY — paper, no orders, no wallets): «Robinhood Chain / Arc meme season + Hyperliquid ecosystem».
Stage 1, run from GitHub (research-rh-hl-memes.yml); results go out as job annotations.
  A. probe — which free sources answer: DefiLlama (DEX volume by chain), GeckoTerminal (networks, new pools), DexScreener, HL API;
  B. chains — daily DEX volume of every DefiLlama chain whose name contains «robinhood» / «arc»: now vs the September 2026 peak;
  C. Hyperliquid — every spot pair and every HIP-3 (builder-deployed) perp market with its first daily candle = listing day:
     listings per month, survival (30-day volume vs the first 3 days), median return from the close of day 1 / 3 / 7;
     rule «new listing momentum» (first day closed up → buy that close, hold 7 days, 0.3 % costs) and «sharp dump» (a day ≤ −25 %
     within the first 30 days → buy the close, hold 3 days) against a fake-dates null (the same coin, random entry days, 500 draws);
  D. GeckoTerminal — new pools of the Robinhood / Arc networks seen NOW (count, liquidity, age) — the free API has no history of
     launches, so survival of DEX tokens needs a daily collector (stage 2), said in the output.
Stdlib only."""
import json
import random
import statistics as st
import sys
import time
import urllib.request

UA = {"User-Agent": "cnq-research/1.0", "Accept": "application/json"}
DAY = 86_400_000
NOW = int(time.time() * 1000)


def note(title, msg):
    s = msg if isinstance(msg, str) else json.dumps(msg, ensure_ascii=False)
    parts, cur = [], ""
    for ch in s:
        if len((cur + ch).encode()) > 3500:
            parts.append(cur); cur = ""
        cur += ch
    parts.append(cur)
    for i, p_ in enumerate(parts[:12]):
        print(f"::notice title={title} {i + 1}/{len(parts)}::" + p_.replace("%", "%25").replace("\n", "%0A").replace("\r", ""), flush=True)


def get(url, body=None, timeout=25):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={**UA, **({"Content-Type": "application/json"} if body is not None else {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def hl(body):
    for k in range(4):
        try:
            return get("https://api.hyperliquid.xyz/info", body)
        except Exception as e:  # noqa: BLE001
            err = e
            time.sleep(1.5 * (k + 1))
    raise err


def probe():
    out = {}
    for name, fn in (("defillama dexs overview", lambda: len(get("https://api.llama.fi/overview/dexs?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true").get("allChains") or [])),
                     ("defillama chains", lambda: len(get("https://api.llama.fi/v2/chains"))),
                     ("geckoterminal networks", lambda: len(get("https://api.geckoterminal.com/api/v2/networks?page=1").get("data") or [])),
                     ("dexscreener latest profiles", lambda: len(get("https://api.dexscreener.com/token-profiles/latest/v1"))),
                     ("hyperliquid spotMeta", lambda: len(hl({"type": "spotMeta"}).get("universe") or [])),
                     ("hyperliquid perpDexs (HIP-3)", lambda: len(hl({"type": "perpDexs"}) or []))):
        try:
            out[name] = {"ok": True, "n": fn()}
        except Exception as e:  # noqa: BLE001
            out[name] = {"ok": False, "error": str(e)[:120]}
        time.sleep(0.5)
    note("rh-hl A · sources", out)
    return out


def chains():
    res = {}
    try:
        allc = get("https://api.llama.fi/overview/dexs?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true").get("allChains") or []
    except Exception as e:  # noqa: BLE001
        note("rh-hl B · chains", {"error": str(e)[:150]}); return
    want = [c for c in allc if any(w in c.lower() for w in ("robinhood", "arc"))]
    res["matched_chains"] = want
    for c in want[:8] + ["Hyperliquid L1", "Solana", "Base"]:
        try:
            d = get(f"https://api.llama.fi/overview/dexs/{urllib.request.quote(c)}?excludeTotalDataChartBreakdown=true")
            ch = [(int(t) * 1000, float(v)) for t, v in d.get("totalDataChart") or []]
            if not ch:
                res[c] = "no chart"; continue
            sep = [v for t, v in ch if time.strftime("%Y-%m", time.gmtime(t / 1000)) == "2026-09"]
            last7 = [v for t, v in ch[-7:]]
            peak_t, peak = max(ch, key=lambda x: x[1])
            res[c] = {"first_day": time.strftime("%Y-%m-%d", time.gmtime(ch[0][0] / 1000)), "avg7_now_musd": round(st.mean(last7) / 1e6, 1),
                      "sep2026_avg_musd": round(st.mean(sep) / 1e6, 1) if sep else None, "sep2026_peak_musd": round(max(sep) / 1e6, 1) if sep else None,
                      "all_time_peak_musd": round(peak / 1e6, 1), "peak_day": time.strftime("%Y-%m-%d", time.gmtime(peak_t / 1000)),
                      "now_vs_sep_peak_pct": round(st.mean(last7) / max(sep) * 100) if sep else None}
        except Exception as e:  # noqa: BLE001
            res[c] = {"error": str(e)[:100]}
        time.sleep(0.6)
    note("rh-hl B · DEX volume by chain (DefiLlama)", res)


def candles(coin):
    try:
        d = hl({"type": "candleSnapshot", "req": {"coin": coin, "interval": "1d", "startTime": NOW - 900 * DAY, "endTime": NOW}})
        return [(int(k["t"]), float(k["o"]), float(k["c"]), float(k["v"]) * float(k["c"])) for k in d or []]
    except Exception:  # noqa: BLE001
        return []


def study(name, series, t_from):
    """series = {coin: [(t, o, c, usd_vol)]}"""
    rnd = random.Random(7)
    new = {c: k for c, k in series.items() if k and k[0][0] >= t_from and len(k) >= 9}
    by_month = {}
    for c, k in new.items():
        m = time.strftime("%Y-%m", time.gmtime(k[0][0] / 1000))
        by_month[m] = by_month.get(m, 0) + 1
    out = {"markets_total": len(series), "new_since_2025": len(new), "listings_by_month": dict(sorted(by_month.items())[-14:])}
    ok30 = [k for k in new.values() if len(k) >= 31]
    if ok30:
        surv = [st.mean(x[3] for x in k[24:31]) >= 0.10 * max(1e-9, st.mean(x[3] for x in k[:3])) for k in ok30]
        out["alive_after_30d_pct (week-4 volume ≥ 10% of first 3 days)"] = round(sum(surv) / len(surv) * 100)
    ok7 = [k for k in new.values() if len(k) >= 8]
    if ok7:
        out["alive_after_7d_pct (day-7 volume ≥ 10% of day 1)"] = round(sum(k[6][3] >= 0.10 * max(1e-9, k[0][3]) for k in ok7) / len(ok7) * 100)
    for frm in (1, 3, 7):
        for hold in (7, 30):
            r = [k[frm - 1 + hold][2] / k[frm - 1][2] - 1 for k in new.values() if len(k) > frm - 1 + hold and k[frm - 1][2] > 0]
            if len(r) >= 8:
                out[f"from close of day {frm}, +{hold}d"] = {"n": len(r), "median_pct": round(st.median(r) * 100, 1), "mean_pct": round(st.mean(r) * 100, 1),
                                                           "win_pct": round(sum(x > 0 for x in r) / len(r) * 100)}
    COST = 0.003

    def rule(pick, hold):
        tr, nulls = [], []
        for c, k in new.items():
            ent = pick(k)
            for i in ent:
                if i + hold < len(k) and k[i][2] > 0:
                    tr.append(k[i + hold][2] / k[i][2] - 1 - COST)
        if len(tr) < 8:
            return {"n": len(tr)}
        real = st.mean(tr)
        beat = 0
        for _ in range(500):
            fk = []
            for c, k in new.items():
                n_e = len([i for i in pick(k) if i + hold < len(k)])
                pool = list(range(0, len(k) - hold))
                for i in (rnd.sample(pool, min(n_e, len(pool))) if pool else []):
                    if k[i][2] > 0:
                        fk.append(k[i + hold][2] / k[i][2] - 1 - COST)
            beat += bool(fk) and real > st.mean(fk)
        return {"n": len(tr), "mean_pct": round(real * 100, 2), "median_pct": round(st.median(tr) * 100, 2),
                "win_pct": round(sum(x > 0 for x in tr) / len(tr) * 100), "beats_fake_dates_pct": round(beat / 5)}
    out["rule · first day up → buy its close, hold 7d"] = rule(lambda k: [0] if k[0][2] > k[0][1] else [], 7)
    out["rule · first day up → buy its close, hold 3d"] = rule(lambda k: [0] if k[0][2] > k[0][1] else [], 3)
    out["rule · sharp dump ≤ −25% in a day (first 30d) → buy close, hold 3d"] = rule(
        lambda k: [i for i in range(1, min(30, len(k))) if k[i - 1][2] > 0 and k[i][2] / k[i - 1][2] - 1 <= -0.25][:1], 3)
    out["rule · sharp dump ≤ −25% → hold 7d"] = rule(
        lambda k: [i for i in range(1, min(30, len(k))) if k[i - 1][2] > 0 and k[i][2] / k[i - 1][2] - 1 <= -0.25][:1], 7)
    note(f"rh-hl C · {name}", out)


def hyperliquid():
    t_from = 1735689600000                                  # 2025-01-01
    try:
        sm = hl({"type": "spotMeta"})
        toks = {t["index"]: t["name"] for t in sm.get("tokens") or []}
        pairs = [(u["name"], toks.get(u["tokens"][0], u["name"])) for u in sm.get("universe") or []]
    except Exception as e:  # noqa: BLE001
        note("rh-hl C · HL spot", {"error": str(e)[:150]}); pairs = []
    ser = {}
    for i, (coin, nm) in enumerate(pairs[:450]):
        k = candles(coin)
        if k:
            ser[nm + "|" + coin] = k
        time.sleep(0.12)
    if ser:
        study("Hyperliquid spot listings", ser, t_from)
    try:
        dexs = [d for d in hl({"type": "perpDexs"}) or [] if d]
    except Exception as e:  # noqa: BLE001
        note("rh-hl C · HIP-3", {"error": str(e)[:150]}); return
    ser, info = {}, []
    for d in dexs[:30]:
        nm = d.get("name")
        try:
            uni = hl({"type": "meta", "dex": nm}).get("universe") or []
        except Exception:  # noqa: BLE001
            uni = []
        info.append({"dex": nm, "markets": len(uni)})
        for u in uni[:60]:
            k = candles(u["name"])
            if k:
                ser[u["name"]] = k
            time.sleep(0.12)
    note("rh-hl C · HIP-3 dexs", info)
    if ser:
        study("HIP-3 perp markets", ser, t_from)


def gecko():
    out = {}
    try:
        nets, page = [], 1
        while page <= 6:
            d = get(f"https://api.geckoterminal.com/api/v2/networks?page={page}").get("data") or []
            if not d:
                break
            nets += [(x["id"], (x.get("attributes") or {}).get("name")) for x in d]
            page += 1
            time.sleep(2.2)
        want = [n for n in nets if any(w in (str(n[0]) + " " + str(n[1])).lower() for w in ("robinhood", "arc", "hyper"))]
        out["networks_matched"] = want
        for nid, nm in want[:5]:
            try:
                pools = get(f"https://api.geckoterminal.com/api/v2/networks/{nid}/new_pools?page=1").get("data") or []
                liq = [float((p_.get("attributes") or {}).get("reserve_in_usd") or 0) for p_ in pools]
                vol = [float(((p_.get("attributes") or {}).get("volume_usd") or {}).get("h24") or 0) for p_ in pools]
                out[nid] = {"new_pools_page1": len(pools), "median_liquidity_usd": round(st.median(liq)) if liq else None,
                            "with_liq_over_50k": sum(x >= 50_000 for x in liq), "median_vol24_usd": round(st.median(vol)) if vol else None}
            except Exception as e:  # noqa: BLE001
                out[nid] = {"error": str(e)[:100]}
            time.sleep(2.2)
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:150]
    out["note"] = "free GeckoTerminal / DexScreener give only the newest pools — launch history and 7/30-day survival of DEX tokens need a daily collector (stage 2)"
    note("rh-hl D · GeckoTerminal new pools now", out)


if __name__ == "__main__":
    probe()
    chains()
    gecko()
    if "--no-hl" not in sys.argv:
        hyperliquid()
