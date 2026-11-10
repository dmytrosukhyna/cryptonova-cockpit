"""Audit of every real whale copy since 04.11 against the trader's own decision and the desk (owner/coordinator 10.11, item 6 of
docs/research/whale-profiles-2026-11-10.md). Research only. Reads web/fut_owner.json (real trades with attribution), the git history of
web/whales_live.json (desk trades, exit reasons, skip events), web/whales_top.json (who was followed when) and data/whale_trades.
Usage (repo root): python scripts/lab/copy_audit.py [out.json]   → docs/research/copy-audit-2026-11-10.md"""
import sys, os, json, subprocess, collections, statistics as st, datetime as dt
sys.path.insert(0, os.path.dirname(__file__))
import whale_profiles as W  # noqa: E402

SINCE = 1793743200000                      # 04.11.2026 00:00 Athens = switch_t in fut_owner.json
COST = 0.0011                              # taker 0.05% × 2 + ~0.01% spread
TRADFI = {"MU", "SNDK", "AAPL", "INTC", "KORU", "SOXL", "NVDA", "XAU", "XAG", "TSLA", "MSTR", "COIN", "HOOD", "AMD", "GOOGL", "META",
          "AMZN", "MSFT", "PLTR", "CRCL", "DRAM", "QQQ", "SPY", "XAUT", "PAXG", "SKHYNIX", "SPCX", "ARM", "SAMSUNG", "TSM", "AVGO", "ORCL",
          "NFLX", "BABA", "COPPER", "WTI", "BRENT", "NATGAS", "SILVER", "GOLD", "TQQQ", "SOXS", "SQQQ", "USO"}
DESK_WHY = [("кит закрыл", "трейдер закрыл"), ("кит развернулся", "трейдер развернулся"), ("убран из списка", "трейдер убран из списка"),
            ("стоп", "наш стоп"), ("владел", "владелец"), ("исчез", "данные о трейдере пропали")]


def f(t):
    return dt.datetime.fromtimestamp(t / 1000, dt.UTC).strftime("%d.%m %H:%M") if t else "—"


def git_versions(path, step):
    out = subprocess.run(["git", "log", "--since=2026-11-03", "--format=%h %ct", "--", path], capture_output=True, text=True).stdout.split("\n")
    hs = [h.split() for h in out if h.strip()]
    for h, ct in hs[::step] + hs[-1:]:
        try:
            yield int(ct) * 1000, json.loads(subprocess.run(["git", "show", f"{h}:{path}"], capture_output=True, text=True).stdout)
        except Exception:
            continue


def desk_union():
    ev, cl = {}, {}
    for _, d in git_versions("web/whales_live.json", 4):
        d = d.get("desk") or {}
        for e in d.get("events") or []:
            ev[json.dumps(e, sort_keys=True, ensure_ascii=False)] = e
        for c in (d.get("closed") or []) + [dict(o, open=True) for o in d.get("open") or []]:
            k = (c["whale"], c["coin"], c["side"], c["opened_t"])
            if k not in cl or not c.get("open"):
                cl[k] = c
    return list(ev.values()), list(cl.values())


def follow_windows():
    win = collections.defaultdict(list)                     # addr → [(t_seen_following)]
    for t, d in git_versions("web/whales_top.json", 6):
        for x in d.get("following") or []:
            win[x["addr"]].append(t)
    return {a: (min(ts), max(ts)) for a, ts in win.items()}


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else None
    fo = json.load(open("web/fut_owner.json"))
    real = [t for t in fo["trades"] if t["opened"] >= SINCE - 86_400_000]
    dec, names, _ = W.load()
    events, desk = desk_union()
    fw = follow_windows()
    by_tr = collections.defaultdict(list)
    for x in dec:
        by_tr[x["trader"]].append(x)
    rows = []
    for r in sorted(real, key=lambda r: r["opened"]):
        sg = 1 if r["side"] == "LONG" else -1
        mv = (r["move_pct"] or 0) / 100
        row = {"opened": r["opened"], "closed_t": r.get("closed_t"), "coin": r["coin"], "side": r["side"], "trader": r.get("whale"),
               "name": r.get("whale_name") or ("без источника" if r.get("strategy") == "unknown" else ""), "match": r.get("match"),
               "entry": r["entry"], "exit": r.get("exit"), "lev": r["lev"], "pnl": r["pnl_usd"], "move": mv, "closed": r["closed"],
               "strategy": r.get("strategy")}
        row["notional"] = abs(r["pnl_usd"] / mv) if abs(mv) >= 0.002 else None
        # desk trade behind it
        dk = [c for c in desk if c["whale"] == r.get("whale") and c["coin"] == r["coin"] and c["side"] == r["side"]
              and -120_000 <= r["opened"] - c["opened_t"] <= 1_800_000]
        dk = max(dk, key=lambda c: c["opened_t"]) if dk else None
        row["desk"] = bool(dk)
        why = None
        if dk and dk.get("why"):
            why = next((ru for k, ru in DESK_WHY if k in dk["why"]), dk["why"][:40])
        if r["closed"] and dk and dk.get("closed_t") and abs(dk["closed_t"] - r["closed_t"]) > 15 * 60_000:
            why = f"счёт закрыл отдельно от стола ({why or 'стол ещё держал'})"
        elif r["closed"] and dk and not dk.get("closed_t"):
            why = "счёт закрыл, стол ещё держал"
        elif r["closed"] and not dk:
            why = "нет сделки стола" if r.get("strategy") == "whales" else "не копия трейдера"
        row["why"] = why if r["closed"] else "открыта"
        # the trader's own decision: the latest one opened before our entry (+10 min) and still open at our entry (−10 min)
        cand = [x for x in by_tr.get(r.get("whale"), []) if x["coin"] == r["coin"] and x["side"] == r["side"]
                and x["open_t"] <= r["opened"] + 600_000 and x["close_t"] >= r["opened"] - 600_000]
        x = max(cand, key=lambda x: x["open_t"]) if cand else None
        if x:
            row.update({"his_open": x["open_t"], "his_close": x["close_t"], "his_entry": x["entry"], "his_exit": x["exit"],
                        "delay_s": round((r["opened"] - x["open_t"]) / 1000), "his_move": x["move"], "his_pnl": round(x["pnl"], 2),
                        "dec_id": (x["trader"], x["coin"], x["side"], x["open_t"])})
            row["gap_pct"] = round(sg * (r["entry"] / x["entry"] - 1) * 100, 2)          # + = we paid worse than him
            if r["closed"] and r.get("exit"):
                oe = r["entry"]
                row["c_his"] = sg * (x["exit"] - x["entry"]) / oe                         # his move, measured on our entry
                row["c_entry"] = sg * (x["entry"] - oe) / oe
                row["c_exit"] = sg * (r["exit"] - x["exit"]) / oe
        rows.append(row)
    ns = [r["notional"] for r in rows if r["notional"]]
    med_n = st.median(ns)
    for r in rows:
        r["N"] = r["notional"] or med_n
        r["fee"] = r["N"] * COST
    closed = [r for r in rows if r["closed"]]
    res = {"n_real": len(rows), "n_closed": len(closed), "sum_closed": round(sum(r["pnl"] for r in closed), 2),
           "pf_closed": W.pf([r["pnl"] for r in closed]), "med_notional": round(med_n, 1)}
    groups = collections.OrderedDict()
    groups["не копия трейдера (без источника)"] = [r for r in closed if r["strategy"] != "whales"]
    groups["копия, решение трейдера найдено"] = [r for r in closed if r["strategy"] == "whales" and "c_his" in r]
    groups["копия, решения трейдера нет в архиве"] = [r for r in closed if r["strategy"] == "whales" and "c_his" not in r]
    res["groups"] = {k: {"n": len(v), "sum": round(sum(r["pnl"] for r in v), 2), "pf": W.pf([r["pnl"] for r in v])} for k, v in groups.items()}
    m = groups["копия, решение трейдера найдено"]
    dcmp = {"his": sum(r["N"] * r["c_his"] for r in m), "entry": sum(r["N"] * r["c_entry"] for r in m), "exit": sum(r["N"] * r["c_exit"] for r in m),
            "fees_est": sum(r["fee"] for r in closed), "reported": sum(r["pnl"] for r in m)}
    dcmp = {k: round(v, 2) for k, v in dcmp.items()}
    dcmp["his_pf"] = W.pf([r["N"] * r["c_his"] for r in m])
    dcmp["his_win"] = round(sum(r["c_his"] > 0 for r in m) / max(1, len(m)) * 100)
    by_why = collections.defaultdict(lambda: [0, 0.0, 0.0, 0.0])
    for r in m:
        b = by_why[r["why"]]
        b[0] += 1; b[1] += r["pnl"]; b[2] += r["N"] * r["c_his"]; b[3] += r["N"] * r["c_exit"]
    dcmp["by_exit_reason"] = {k: {"n": v[0], "ours": round(v[1], 2), "his_at_our_size": round(v[2], 2), "exit_effect": round(v[3], 2)} for k, v in by_why.items()}
    by_trader = collections.defaultdict(lambda: [0, 0.0, 0.0])
    for r in closed:
        b = by_trader[r["name"] or r["trader"]]
        b[0] += 1; b[1] += r["pnl"]; b[2] += r["N"] * r.get("c_his", 0)
    dcmp["by_trader"] = {k: {"n": v[0], "ours": round(v[1], 2), "his_at_our_size": round(v[2], 2)} for k, v in sorted(by_trader.items(), key=lambda kv: kv[1][1])}
    res["decomp"] = dcmp
    # decisions of followed traders we did NOT copy
    copied = {r["dec_id"] for r in rows if r.get("dec_id")}
    start = min(r["opened"] for r in rows if r["strategy"] == "whales")      # the real account starts copying whales here
    ev_by = collections.defaultdict(list)
    for e in events:
        ev_by[(e.get("whale"), e.get("coin"), e.get("side"))].append(e)
    desk_by = collections.defaultdict(list)
    for c in desk:
        desk_by[(c["whale"], c["coin"], c["side"])].append(c)
    miss = []
    for a, (t0, t1) in fw.items():
        for x in by_tr.get(a, []):
            if not (max(t0, start) <= x["open_t"] <= t1) or (x["trader"], x["coin"], x["side"], x["open_t"]) in copied:
                continue
            k = (a, x["coin"], x["side"])
            near = lambda t: -600_000 <= t - x["open_t"] <= x["close_t"] - x["open_t"] + 600_000  # noqa: E731
            dk = [c for c in desk_by[k] if near(c["opened_t"])]
            ev = [e for e in ev_by[k] if near(e["t"]) and e["kind"] in ("skip", "join_wait")]
            if x["coin"] in TRADFI:
                why = "TradFi — на OKX нет"
            elif dk:
                why = "стол открыл, реальный счёт — нет"
            elif ev:
                e = ev[0]
                why = "стол: бюджет/загрузка" if "загрузк" in e["why"] or "доля" in e["why"] else "стол: ждали лучшей цены (вдогонку)"
            elif x["hold_h"] * 60 < 2:
                why = "сделка короче 2 мин"
            else:
                why = "стол не увидел / не открыл (причина не записана)"
            usd = W.our_usd(x["move"]) * med_n / W.NOTIONAL
            miss.append({"trader": names.get(a, a), "coin": x["coin"], "side": x["side"], "open": x["open_t"], "move": x["move"], "usd": usd, "why": why})
    mb = collections.defaultdict(list)
    for x in miss:
        mb[x["why"]].append(x["usd"])
    res["missed"] = {k: {**W.summ(v), "sum": round(sum(v), 2)} for k, v in mb.items()}
    res["missed_n"] = len(miss)
    res["missed_rows"] = [{**x, "open": f(x["open"]), "move": round(x["move"] * 100, 2), "usd": round(x["usd"], 2)} for x in miss]
    res["missed_by_trader"] = {}
    for x in miss:
        b = res["missed_by_trader"].setdefault(f'{x["trader"]} · {x["why"]}', [0, 0.0])
        b[0] += 1; b[1] = round(b[1] + x["usd"], 2)
    res["follow_windows"] = {names.get(a, a): [f(t0), f(t1)] for a, (t0, t1) in fw.items()}
    res["rows"] = [{k: (round(v, 5) if isinstance(v, float) else v) for k, v in r.items() if k != "dec_id"} for r in rows]
    if out_path:
        json.dump(res, open(out_path, "w"), ensure_ascii=False, indent=1)
    return res


if __name__ == "__main__":
    r = main()
    print(json.dumps({k: v for k, v in r.items() if k not in ("rows",)}, ensure_ascii=False, indent=1))
    for x in r["rows"]:
        print(f(x["opened"]), x["coin"], x["side"][0], (x["name"] or "")[:12], "his", f(x.get("his_open")), x.get("delay_s"), x.get("gap_pct"),
              "| exit", f(x.get("closed_t")), "his", f(x.get("his_close")), x["why"], "| his%", round((x.get("his_move") or 0) * 100, 2),
              "our%", round(x["move"] * 100, 2), "$", x["pnl"], "N", round(x["N"]), "lev", x["lev"])
