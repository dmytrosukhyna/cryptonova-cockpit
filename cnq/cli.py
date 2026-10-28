"""cnq — CRYPTO NOVΛ quant CLI.

  python -m cnq.cli backfill [--days 120]     fetch candles + funding into SQLite
  python -m cnq.cli stream                    record trades/order book/OI 24/7
  python -m cnq.cli status                    data coverage + quality log
  python -m cnq.cli levels [--interval 15m]   current levels for the symbol
  python -m cnq.cli replay [--interval 15m] [--mode STRICT_CRYPTO_NOVA] [--risk 1.0]
  python -m cnq.cli ev                        arithmetic of the TP ladder
  python -m cnq.cli binance-sync [--days 90] [--aggtrades]   Binance research datasets → SQLite
  python -m cnq.cli binance-peek              print first rows of each Binance dataset (format check)
  python -m cnq.cli terminal [--out web]      build web/terminal.json from stored candles (BingX universe)
  python -m cnq.cli brief                     print the daily brief (and send it: --send)
  python -m cnq.cli alerts                    send not-yet-sent events to Telegram
  python -m cnq.cli fx-gaps [--send]          Monday weekend-gap signals for FX (Twelve Data key) → Telegram
  python -m cnq.cli agents [--send]           run departments (news/fundamentals/risk/editor) → web/decision.json (+post to Telegram)
  python -m cnq.cli funding [--out web]       paper funding-carry account (spot long + perp short) → web/funding.json
  python -m cnq.cli arb [--out web]           cross-exchange spot spreads net of fees, sampled each run → web/arb.json
  python -m cnq.cli tri [--out web]           triangles inside one exchange (all USDT/USDC/FDUSD/BTC/ETH/BNB pairs) → web/tri.json
  python -m cnq.cli memes [--out web]         HL meme perps + Solana memecoin watchlist + paper «Мем-портфель» → web/memes.json
  python -m cnq.cli macro [--out web]         DXY, US 10Y/2Y, USDJPY, oil, gold, index futures, VIX, Nikkei: value + 1d/5d change → web/macro.json
  python -m cnq.cli ideas [--out web]         one spot table «можно брать / добрать / на небольшую сумму / ждём / не брать» from
                                              research.json + portfolio.json + memes.json (+ markets/decision) → web/ideas.json
  python -m cnq.cli outlook [--out web]       BTC/ETH/SOL on day / week / month: band of 2 outcomes out of 3 (90-day volatility, history
                                              2021–2026), share of periods that ended higher in the current BTC regime, A/B forecast score,
                                              calendar/macro/autopilot drivers → web/outlook.json
  python -m cnq.cli broadcast [--out web] [--force]   Friday live-show deck (slides: what to say / what to show in the panel);
                                              rebuilt on Fridays (Kyiv) or when missing/older than 7 days, else only `live` → web/broadcast.json
  python -m cnq.cli conveyor [run|status] [--force]   strategy conveyor LAB → PAPER → READY → RETIRED → data/conveyor_state.json + web/conveyor.json
  python -m cnq.cli conveyor reinstate <desk> --reason «…»   the owner brings a retired desk back to PAPER (demo only)
  python -m cnq.cli deskselect      the main futures desk (slow, evidence-based) → web/desk_select.json (+ data/desk_select.jsonl on a switch)
  python -m cnq.cli vipfeeds        the closed panel's VIP feeds (read-only): web/spot_pipeline.json, web/strategies.json,
                                    web/opportunities.json, web/system_status.json (cnq/agents/vip_feeds.py)
  python -m cnq.cli scorecard [--out web]     public prediction scorecard: every published forecast / signal → hash-chained
                                              data/scorecard_ledger.jsonl, checked on the engine's candles → web/scorecard.json
  python -m cnq.cli appsignals [--out web]    CRYPTO NOVΛ app signals (public /api/signals/active + /history) → agents.sqlite history,
                                              outcomes checked on 15m candles under 5 exit policies, strategy profile, rules → web/app_signals.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time

from .config import Config
from .store.db import Store


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("kill", "risk"):    # 20.10 risk layer: kill on|off|status, risk [status|reset] (cnq/risk/cli.py)
        from .risk import cli as risk_cli
        return risk_cli.main(argv)
    ap = argparse.ArgumentParser(prog="cnq")
    ap.add_argument("cmd", choices=["backfill", "stream", "status", "levels", "replay", "ev", "binance-sync", "binance-peek", "terminal", "brief", "alerts", "agents", "fx-gaps", "portfolio", "paper", "weekly", "calibrate", "research", "whales", "channel", "botmail", "voice", "funding", "arb", "tri", "memes", "macro", "ideas", "outlook", "broadcast", "appsignals", "whalestudy", "signalfeed", "capital", "yield", "basis", "listings", "tokenomics", "chwatch", "turbo", "own", "altport", "quality", "lsn", "appportfolio", "apphome", "apppush", "conveyor", "scorecard", "buynow", "okxsignal", "deskselect", "vipfeeds"])
    ap.add_argument("sub", nargs="?", help="conveyor: run (default) | status | reinstate")
    ap.add_argument("target", nargs="?", help="conveyor reinstate: the desk key (e.g. bal)")
    ap.add_argument("--reason", help="conveyor reinstate: why the owner brings the desk back (stays private on the server)")
    ap.add_argument("--symbol")
    ap.add_argument("--db")
    ap.add_argument("--days", type=int)
    ap.add_argument("--interval", default="15m")
    ap.add_argument("--mode", default="STRICT_CRYPTO_NOVA")
    ap.add_argument("--risk", type=float, default=1.0)
    ap.add_argument("--balance", type=float, default=10_000)
    ap.add_argument("--min-score", type=float, default=60.0)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--aggtrades", action="store_true")
    ap.add_argument("--binance-symbol", default="BTCUSDT")
    ap.add_argument("--cache", default="data/binance")
    ap.add_argument("--out", default="web")
    ap.add_argument("--force", action="store_true", help="weekly / broadcast: rebuild (and send) even if it is not Friday")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--slot", help="channel: morning|midday|evening|intro (default: by Kyiv time)")
    ap.add_argument("--print", action="store_true", help="channel: print the post instead of sending")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    cfg = Config()
    if args.symbol:
        cfg = Config(symbol=args.symbol)
    store = Store(args.db or cfg.db_path)

    if args.cmd == "backfill":
        from .collector.backfill import backfill
        print(json.dumps(backfill(cfg, store, days=args.days), indent=2))
    elif args.cmd == "stream":
        from .collector.stream import run_stream
        asyncio.run(run_stream(cfg, store))
    elif args.cmd == "status":
        from .collector.quality import coverage
        out = {}
        for iv in cfg.intervals:
            out[iv] = coverage(store.candles(cfg.symbol, iv), iv)
        q = store.conn.execute("SELECT kind, COUNT(*) FROM quality_log GROUP BY kind").fetchall()
        out["quality_log"] = dict(q)
        for tbl in ("trades", "depth_snapshots", "open_interest", "funding", "paper_trades"):
            out[tbl] = store.conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        print(json.dumps(out, indent=2))
    elif args.cmd == "levels":
        from .features.levels import build_levels
        candles = store.candles(cfg.symbol, args.interval, limit=600)
        if not candles:
            print("no candles — run backfill first"); return 1
        for L in build_levels(candles)[:15]:
            print(f"{L.status:9} {L.kind:10} {L.lo:>12.2f} – {L.hi:<12.2f} touches={L.touch_count} score={L.score}")
        print(f"last close {candles[-1].close:.2f}")
    elif args.cmd == "replay":
        from .collector.quality import coverage
        from .paper.replay import run_replay
        candles = store.candles(cfg.symbol, args.interval)
        if not candles:
            print("no candles — run backfill first"); return 1
        cov = coverage(candles, args.interval)["coverage"]
        funding = [{"time": t, "rate": r} for t, r in store.conn.execute(
            "SELECT time, rate FROM funding WHERE symbol=? ORDER BY time", (cfg.symbol,))]
        t0 = time.time()
        res = run_replay(cfg.symbol, candles, args.interval, args.balance, args.risk, args.mode,
                         store=store, min_score=args.min_score, coverage=cov, funding=funding)
        res["summary"]["seconds"] = round(time.time() - t0, 1)
        print(json.dumps(res["summary"], indent=2))
        if not args.json:
            for t in res["trades"][-10:]:
                print(f"{t['direction']:5} entry={t['entry']:.2f} stop={t['stop']:.2f} "
                      f"tps={t['tps_hit']} exit={t['exit_kind']:6} {t['result_r']:+.2f}R")
    elif args.cmd == "ev":
        from .risk.ev import breakeven_p_tp1, ev_table, outcomes
        for m in ("STRICT_CRYPTO_NOVA", "CRYPTO_NOVA_RUNNER", "LEVEL_3R"):
            print(m, "outcomes by TPs hit:", {o["tps_hit"]: o["result_r"] for o in outcomes(m)})
            for pn in (0.6, 0.75, 0.9):
                print(f"  p_next={pn}: break-even P(TP1) = {breakeven_p_tp1(m, pn):.0%}",
                      {r['p_tp1']: r['ev_r'] for r in ev_table(m, p_next=pn)})
    elif args.cmd == "binance-sync":
        from .binance.load import sync_all
        print(json.dumps(sync_all(store, args.binance_symbol, args.days or 90, args.cache, args.aggtrades), indent=2))
    elif args.cmd == "binance-peek":
        from datetime import date, timedelta
        from .binance import public_data as pd_
        d = date.today() - timedelta(days=2)
        for kind, iv in (("klines", "1m"), ("aggTrades", None), ("bookDepth", None), ("metrics", None)):
            p = pd_.download(pd_.file_url(kind, args.binance_symbol, d, iv), f"{args.cache}/peek")
            print(f"== {kind} {d}: {p}")
            if p:
                for i, r in enumerate(pd_.read_rows(p, kind)):
                    print("  ", r)
                    if i >= 2:
                        break
    elif args.cmd == "portfolio":
        import os
        import sqlite3
        from .agents import portfolio as pf
        from .agents.llm import LLM
        from .terminal.report import write_report
        os.makedirs("data", exist_ok=True)
        conn = sqlite3.connect("data/agents.sqlite")
        btc_bull = None
        try:
            from .terminal.report import build_report
            cs = store.candles("BTC-USDT", "15m")
            if len(cs) >= 96 * 60:
                btc_bull = build_report({"BTC-USDT": cs})["coins"][0]["regime"]["long"] == "BULL"
        except Exception:  # noqa: BLE001
            pass
        llm = LLM()
        res = pf.run(conn, btc_bull=btc_bull, llm=llm if llm.available else None)
        os.makedirs(args.out, exist_ok=True)
        write_report(res, os.path.join(args.out, "portfolio.json"))
        print(f"portfolio.json: available={res.get('available')} universe={res.get('universe')} hl={res.get('hyperliquid')} "
              f"positions={len(res.get('portfolio', {}).get('positions', []))} actions={len(res.get('portfolio', {}).get('actions', []))} note={res.get('note_source')}")
    elif args.cmd == "research":
        import os, sqlite3
        from .agents import research
        from .terminal.report import write_report
        conn = sqlite3.connect("data/agents.sqlite")
        os.makedirs(args.out, exist_ok=True)
        mk, rs = research.run(conn)
        write_report(mk, os.path.join(args.out, "markets.json"))
        write_report(rs, os.path.join(args.out, "research.json"))
        print(f"markets.json: top={len(mk['top'])} fng={mk['fng'].get('value')} mcap={mk['global'].get('total_mcap')} | research.json: cards={rs['total']} covered={rs['covered']} fetched_now={rs['fetched_now']}")
    elif args.cmd == "whales":
        import os, sqlite3
        from .agents import whales
        from .terminal.report import write_report
        conn = sqlite3.connect("data/agents.sqlite")
        os.makedirs(args.out, exist_ok=True)
        d = whales.run(conn)
        write_report(d, os.path.join(args.out, "whales.json"))
        print(f"whales.json: available={d.get('available')} whales={len(d.get('whales', []))} coins={len(d.get('coins', []))} changes={len(d.get('changes', []))} bias={(d.get('summary') or {}).get('bias')}")
    elif args.cmd == "whalestudy":
        import os, sqlite3
        from .agents import whale_study
        from .terminal.report import write_report
        conn = sqlite3.connect("data/agents.sqlite")
        os.makedirs(args.out, exist_ok=True)
        d = whale_study.run(conn)
        write_report(d, os.path.join(args.out, "whales_top.json"))
        fn = d.get("funnel") or {}
        dk = d.get("desk") or {}
        print(f"whales_top.json: complete={d.get('complete')} studied={fn.get('studied')} eligible={fn.get('eligible')} top={fn.get('top')} "
              f"waiting={fn.get('waiting')} candles_waiting={fn.get('candles_waiting')} desk={dk.get('equity')} open={len(dk.get('open') or [])} notes={len(d.get('notes_ru') or [])}")
        for n in (d.get("notes_ru") or [])[:5]:
            print("  note:", n)
    elif args.cmd == "funding":
        import os, sqlite3
        from .agents import funding_desk
        from .terminal.report import write_report
        os.makedirs("data", exist_ok=True)
        conn = sqlite3.connect("data/agents.sqlite")
        os.makedirs(args.out, exist_ok=True)
        d = funding_desk.run(conn)
        write_report(d, os.path.join(args.out, "funding.json"))
        print(f"funding.json: venue={d['venue']} ok={d['venues_ok']} equity={d['equity']} positions={[p['symbol'] for p in d['positions']]} events={[e['text'] for e in d['events'][:3]]}")
    elif args.cmd == "arb":
        import os, sqlite3
        from .agents import arb_watch
        from .terminal.report import write_report
        os.makedirs("data", exist_ok=True)
        conn = sqlite3.connect("data/agents.sqlite")
        os.makedirs(args.out, exist_ok=True)
        d = arb_watch.run(conn)
        write_report(d, os.path.join(args.out, "arb.json"))
        print(f"arb.json: venues={d['venues_ok']} day={d['day']} best_now={d['now'][:1]}")
    elif args.cmd == "tri":
        import os, sqlite3
        from .agents import tri_watch
        from .terminal.report import write_report
        os.makedirs("data", exist_ok=True)
        conn = sqlite3.connect("data/agents.sqlite")
        os.makedirs(args.out, exist_ok=True)
        d = tri_watch.run(conn)
        write_report(d, os.path.join(args.out, "tri.json"))
        print(f"tri.json: venues={d['venues_ok']} scanned={d['scanned_total']} day={d['day']} best_now={d['now'][:1]}")
    elif args.cmd == "memes":
        import os, sqlite3
        from .agents import memes
        from .terminal.report import write_report
        os.makedirs("data", exist_ok=True)
        conn = sqlite3.connect("data/agents.sqlite")
        os.makedirs(args.out, exist_ok=True)
        d = memes.run(conn)
        write_report(d, os.path.join(args.out, "memes.json"))
        a = d["account"]
        print(f"memes.json: {d.get('seconds')}s sources={d['sources']} equity={a['equity']} positions={[p['coin'] for p in a['positions']]} "
              f"hl={[(r['coin'], r['score']) for r in d['hl_memes'][:5]]} sol={[(r['symbol'], r['score'], r['passed']) for r in d['solana'][:5]]} "
              f"events={[e['text'] for e in a['events'][:2]]}")
    elif args.cmd == "ideas":
        from .agents import spot_ideas
        d = spot_ideas.write(args.out)
        print(f"ideas.json: btc_bull={d['btc_bull']} evaluated={d['evaluated']} shown={d['shown']} counts={d['counts']} | {d['summary_ru']}")
    elif args.cmd == "outlook":
        import os, sqlite3
        from .agents import outlook
        os.makedirs("data", exist_ok=True)
        conn = sqlite3.connect("data/agents.sqlite")
        d = outlook.write(args.out, conn)
        h = d.get("horizons") or {}
        print(f"outlook.json: btc={d.get('price')} regime_bull={(d.get('regime') or {}).get('btc_bull')} history={d.get('history')} | "
              + " | ".join(f"{k}: ±{(v.get('range') or {}).get('pm_pct')}% up={(v.get('base_rate_up') or {}).get('p')} n={(v.get('base_rate_up') or {}).get('n')} "
                           f"drivers={len(v.get('drivers') or [])}" for k, v in h.items()))
    elif args.cmd == "broadcast":
        import os, sqlite3
        from .agents import broadcast
        conn = sqlite3.connect("data/agents.sqlite") if os.path.exists("data/agents.sqlite") else None
        doc, why = broadcast.run(args.out, conn, force=args.force)
        print(f"broadcast.json: {'rebuilt (' + why + ')' if why else 'live refreshed'} week_of={doc.get('week_of')} slides={len(doc.get('slides') or [])} "
              f"~{doc.get('duration_min_est')} min ids={[s['id'] for s in doc.get('slides') or []]} errors={doc.get('errors')}")
    elif args.cmd == "yield":
        import os, sqlite3
        from .agents import yield_desk
        os.makedirs("data", exist_ok=True)
        d = yield_desk.run(sqlite3.connect("data/agents.sqlite"), args.out)
        print(f"yield.json: equity={d['equity']} apy={d['apy_now']} rates={d['rates']} stale={d['stale']} error={d['error']} table={len(d['table'])}")
    elif args.cmd == "chwatch":
        import os, sqlite3
        from .agents import channel_watch
        os.makedirs("data", exist_ok=True)
        d = channel_watch.run(sqlite3.connect("data/agents.sqlite"), args.out)
        print("channel_watch.json: " + "; ".join(f"{k}: done={c['forecasts']} open={c['open']} hit={c['hit_rate_pct']} up={c['always_up_pct']} err={c['error']}"
                                                  for k, c in d["channels"].items()))
    elif args.cmd == "tokenomics":
        import os, sqlite3
        from .agents import tokenomics
        os.makedirs("data", exist_ok=True)
        d = tokenomics.run(args.out, conn=sqlite3.connect("data/agents.sqlite"))
        print(f"tokenomics.json: {len(d['tokens'])} tokens with holders revenue; top {[t['symbol'] for t in d['top'][:8]]} error={d['error']}")
    elif args.cmd == "own":
        import os, sqlite3
        from .agents import own_desk
        os.makedirs("data", exist_ok=True)
        d = own_desk.run(sqlite3.connect("data/agents.sqlite"), args.out)
        print(f"own signals: equity={d['equity']} open={[(t['symbol'], t['grade'], t['side']) for t in d['open']]} regime={d['regime_ru']} events={d['events'][:8]}")
    elif args.cmd == "altport":
        import os, sqlite3
        from .agents import alt_portfolio
        os.makedirs("data", exist_ok=True)
        d = alt_portfolio.run(sqlite3.connect("data/agents.sqlite"), args.out)
        print("altseason portfolio:", {k: d.get(k) for k in ("gauge", "posted", "error")})
    elif args.cmd == "appportfolio":
        import os, sqlite3
        from .agents import app_portfolio
        os.makedirs("data", exist_ok=True)
        d = app_portfolio.run(sqlite3.connect("data/agents.sqlite"), args.out)
        print("app_portfolio.json:", {"positions": len(d["positions"]), "index": d["index"], "history": len(d["history"])})
    elif args.cmd == "apphome":
        import os, sqlite3
        from .agents import app_home
        os.makedirs("data", exist_ok=True)
        d = app_home.run(sqlite3.connect("data/agents.sqlite"), args.out)
        print("app_home.json:", {"total": d["total"], "open": len(d["futures"]["open"]), "spot_coins": d["spot"]["n_coins"]})
    elif args.cmd == "buynow":
        from .agents import buy_now
        d = buy_now.run(args.out)
        print("buy_now.json:", {"checked": d["checked"], "items": [x["sym"] for x in d["items"]]})
    elif args.cmd == "apppush":
        import os, sqlite3
        from .agents import app_push
        os.makedirs("data", exist_ok=True)
        d = app_push.run(sqlite3.connect("data/agents.sqlite"), os.path.join("data", "api.sqlite"), args.out)
        print("app push:", d)
    elif args.cmd == "conveyor":
        # 20.10: strategy conveyor LAB → PAPER → READY → RETIRED (cnq/agents/conveyor.py) — the engine runs it hourly; demo only
        import os, sqlite3
        from .agents import conveyor
        os.makedirs("data", exist_ok=True)
        conn = sqlite3.connect("data/agents.sqlite", timeout=30)
        sub = args.sub or "run"
        try:
            if sub == "reinstate":
                if not args.target:
                    print("usage: python -m cnq.cli conveyor reinstate <desk> --reason «…»"); return 2
                d = conveyor.reinstate(conn, args.target, args.reason or "", args.out)
                print(f"conveyor: {args.target} reinstated → paper; counts={d.get('counts')}")
            elif sub == "status":
                for line in conveyor.status_table(conn):
                    print(line)
            elif sub == "run":
                d = conveyor.run(conn, args.out, force=args.force)
                if d.get("skipped"):
                    print(f"conveyor: skipped — last run {d['minutes_since_last']} min ago (hourly at most; --force to run now)")
                else:
                    print(f"conveyor: counts={d['counts']} recommend_paper={d['recommend_paper']} errors={d['status']['errors']} | "
                          + " ".join(f"{r['key']}={r['stage']}" for r in d["desks"]))
            else:
                print("usage: python -m cnq.cli conveyor [run|status|reinstate <desk> --reason …] [--force]"); return 2
        except ValueError as e:
            print("conveyor:", e); return 1
        except Exception as e:  # noqa: BLE001 — never a silent failure: the public feed shows it, the engine sees rc 1
            conveyor.write_error(args.out, f"{type(e).__name__}: {e}")
            print("conveyor failed:", repr(e)[:300]); return 1
    elif args.cmd == "lsn":
        import os, sqlite3
        from .agents import ls_desk
        os.makedirs("data", exist_ok=True)
        d = ls_desk.run(sqlite3.connect("data/agents.sqlite"), args.out)
        print("neutral basket:", {"equity": d["equity_mtm"], "open": [(o["symbol"], o["side"]) for o in d["open"]], "events": d["events"][:6]})
    elif args.cmd == "quality":
        import os, sqlite3
        from .agents import spot_quality
        os.makedirs("data", exist_ok=True)
        d = spot_quality.run(sqlite3.connect("data/agents.sqlite"), args.out)
        print("spot quality:", {k: d.get(k) for k in ("universe", "basket", "error", "skipped")})
    elif args.cmd == "turbo":
        import os, sqlite3
        from .agents import turbo_desk
        d = turbo_desk.run(sqlite3.connect("data/agents.sqlite"), args.out, candles_db=os.environ.get("CNQ_DB", "data/cnq.sqlite"))
        print("turbo:", {k: (v["equity"], v.get("month_pct")) for k, v in d.items()})
        try:   # 22.10: «Ложный пробой · лонг» — a paper-only test desk (cnq/agents/sweep_desk.py, docs/research/sweep.md)
            from .agents import sweep_desk
            s = sweep_desk.run(sqlite3.connect("data/agents.sqlite"), args.out, candles_db=os.environ.get("CNQ_DB", "data/cnq.sqlite"))
            print("sweep:", s["equity"], len(s["open"]), s["status_ru"][:160])
        except Exception as e:  # noqa: BLE001 — the test desk never breaks the turbo step
            print("sweep: error", type(e).__name__, str(e)[:200])
    elif args.cmd == "listings":
        import os, sqlite3
        from .agents import listing_desk
        os.makedirs("data", exist_ok=True)
        d = listing_desk.run(sqlite3.connect("data/agents.sqlite"), args.out)
        print(f"listings: watching={d['watching']} equity={d['equity']} open={[t['symbol'] for t in d['open']]} events={d['events'][:5]} error={d['error']}")
    elif args.cmd == "basis":
        import os, sqlite3
        from .agents import basis_desk
        os.makedirs("data", exist_ok=True)
        d = basis_desk.run(sqlite3.connect("data/agents.sqlite"), args.out)
        print(f"basis.json: equity={d['equity']} rate_now={d['rate_now']} position={d['position']} rates={ {k: v['apy'] for k, v in d['rates'].items()} } error={d['error']}")
    elif args.cmd == "capital":
        import os, sqlite3
        from .agents import capital
        os.makedirs("data", exist_ok=True)
        d = capital.run(sqlite3.connect("data/agents.sqlite"), args.out)
        print(f"capital.json: ${d['value_usd']} ({d['pnl_pct']}%) sleeves={[(s['key'], s['weight']) for s in d['sleeves']]} month={d['month']}")
    elif args.cmd == "scorecard":
        from .agents import scorecard
        d = scorecard.run(args.out, "data", args.db or cfg.db_path)
        h = d.get("headline") or {}
        ch = (d.get("chain") or {}).get("ledger") or {}
        print(f"scorecard.json: this run={d.get('this_run')} recorded={h.get('recorded')} checked={h.get('checked')} pending={h.get('pending')} "
              f"lines={ch.get('lines')} head={(ch.get('head') or '')[:12]} ok={ch.get('ok')} written={d.get('written')} "
              f"errors={(d.get('errors') or [])[:3]} {d.get('error', '')}")
    elif args.cmd == "signalfeed":
        import os, sqlite3
        from .agents import signal_feed
        os.makedirs("data", exist_ok=True)
        d = signal_feed.run(sqlite3.connect("data/agents.sqlite"), args.out)
        print(f"signals_feed.json: {d.get('stats')} this run={d.get('this_run')} channel_on={d.get('channel_on')} {d.get('error', '')}")
    elif args.cmd == "deskselect":
        # 28.10 (owner): the main futures desk — cnq/agents/desk_select.py; the engine runs it hourly after the conveyor
        from .agents import desk_select
        try:
            d = desk_select.run(args.out)
        except Exception as e:  # noqa: BLE001 — the last web/desk_select.json stays; readers fall back to «Агрессивная» if it breaks
            print("desk_select failed:", repr(e)[:300]); return 1
        print(f"desk_select.json: current={d['current']} ({d['by']}) decision={d['decision']} — {d['short_ru']}")
    elif args.cmd == "vipfeeds":
        # 28.10 (owner's VIP spec): spot pipeline, strategies, other opportunities, system status — read-only, from the recorded data
        import os, sqlite3
        from .agents import vip_feeds
        conn = sqlite3.connect("data/agents.sqlite") if os.path.exists("data/agents.sqlite") else None
        d = vip_feeds.run(args.out, conn)
        sp, ss = d["spot_pipeline.json"], d["system_status.json"]
        print(f"vip feeds: cash real={sp.get('cash_pct_real')}% portfolio={sp.get('cash_pct_portfolio')}% floor={sp.get('cash_target_pct')}% "
              f"stages={sp.get('counts')} strategies={len(d['strategies.json']['strategies'])} "
              f"opportunities={len(d['opportunities.json']['items'])} status={ss['overall']} "
              + " ".join(f"{c['key']}={c['status']}" for c in ss["components"]))
    elif args.cmd == "okxsignal":
        import os, sqlite3
        from .agents import okx_signal
        os.makedirs("data", exist_ok=True)
        d = okx_signal.run(sqlite3.connect("data/agents.sqlite"), args.out)
        print(f"okx_signal.json: on={d.get('on')} sent={d.get('sent')} pending={d.get('pending')} failed={d.get('failed')} "
              f"skipped={d.get('skipped')} this run={len(d.get('this_run') or [])} {d.get('why_off') or ''} {d.get('error', '')}")
    elif args.cmd == "appsignals":
        import os, sqlite3
        from .agents import app_signals
        os.makedirs("data", exist_ok=True)
        conn = sqlite3.connect("data/agents.sqlite")
        d = app_signals.write(args.out, conn)
        c, p, ac = d["counts"], d["stats"]["policies"], d["accounts"]
        print(f"app_signals.json: api={d['source']['api_ok']} counts={c} candles={d['source']['candles']} "
              f"main ladder_be={p['ladder_be']['avg_pct']}%/{p['ladder_be']['avg_r']}R (n={p['ladder_be']['n']}) | app={p['app']['avg_pct']}% "
              f"tp1={p['tp1']['avg_pct']}% ladder={p['ladder']['avg_pct']}% last={p['last']['avg_pct']}% | app_flags={d['stats']['app_flags']['agree']}/"
              f"{d['stats']['app_flags']['n']} | accounts backfill as_is={ac['backfill']['as_is']['equity']} improved={ac['backfill']['improved']['equity']} "
              f"live as_is={ac['since_live']['as_is']['equity']} improved={ac['since_live']['improved']['equity']} "
              f"blacklist={[b['coin'] for b in d['coin_filter']['blacklist']]} notes={d['notes_ru'][:3]} | {d['summary_ru']}")
    elif args.cmd == "macro":
        from .agents import macro_watch
        d = macro_watch.write(args.out)
        got = [i for i in d["items"] if i["value"] is not None]
        print(f"macro.json: {len(got)}/{len(d['items'])} items, sources={sorted({i['source'] for i in got})} "
              f"signals={[i['key'] for i in got if i['signal_ru']]} errors={d['errors'][:3]}")
    elif args.cmd == "channel":
        import os, sqlite3
        from .agents.channel import Channel, kyiv_now
        from .agents.llm import LLM
        from .terminal import notify
        conn = sqlite3.connect("data/agents.sqlite")
        ch = Channel(conn, args.out)
        llm = LLM(); llm = llm if llm.available else None
        if args.print:
            print(ch.post(args.slot or "morning")); return 0
        send = notify.send if args.send else (lambda t: (print(t), True)[1])
        send_html = (lambda t: notify.send(t, parse_mode="HTML")) if args.send else send
        photo = (lambda path, cap: notify.send_photo(path, cap)) if args.send else (lambda path, cap: print('photo:', path))
        res = ch.run(send_html, llm=llm, force_slot=args.slot, photo_fn=photo)
        print(f"channel: slot={res.get('slot')} sent={res.get('sent')} {res.get('why', '')} kyiv={kyiv_now():%H:%M}")
    elif args.cmd == "voice":
        from .agents import voice
        r = voice.run(args.out)
        print("voice:", r.get("audio") or r.get("error"), "|", r["text"][:120])
    elif args.cmd == "botmail":
        import sqlite3
        from .agents import botmail
        from .agents.llm import LLM
        conn = sqlite3.connect("data/agents.sqlite")
        llm = LLM(); llm = llm if llm.available else None
        print("botmail:", botmail.run(conn, llm=llm))
    elif args.cmd == "fx-gaps":
        from .fx.gaps import run as fx_run
        res = fx_run(send=args.send)
        print(f"{len(res)} gap signals")
    elif args.cmd in ("terminal", "brief", "alerts", "agents", "paper", "weekly", "calibrate"):
        import os
        from .terminal.report import build_report, write_report
        from .terminal import notify
        # Universe = BingX symbols with ≥60 days of 15m candles in the store (run `backfill` per symbol first)
        universe = {}
        # 20.10: «paper» runs every minute on the server; loading every 15m candle ever stored (as objects) pushed the step past the
        # service's 2 GB from 08:45 UTC and systemd restarted the engine in a loop. The desk and its report look back ≤ 1 year
        # (daily candles d1[-365:], 90-day ranks, the 50-day BTC EMA) → load 400 days for it; the other commands keep everything.
        import time as _t
        since = int(_t.time() * 1000) - 400 * 86_400_000 if args.cmd == "paper" else None
        for bx, _ in cfg.universe:
            cs = store.candles(bx, "15m", start=since)
            if len(cs) >= 96 * 60:
                universe[bx] = cs
        if not universe:
            print("no 15m candles in the store — run: CNQ_SYMBOL=BTC-USDT python -m cnq.cli backfill (per symbol)"); return 1
        rep = build_report(universe)
        os.makedirs(args.out, exist_ok=True)
        write_report(rep, os.path.join(args.out, "terminal.json"))
        if args.cmd == "terminal":
            print(f"written {args.out}/terminal.json: {len(rep['coins'])} coins, {len(rep['events'])} events")
        elif args.cmd == "paper":
            import sqlite3
            from .agents import paper_desk
            conn = sqlite3.connect("data/agents.sqlite")
            for c in rep["coins"]:   # 4h retest scan needs ~75 closed 4h bars → give the desk 20 days of 15m
                cs = universe.get(c["symbol"])
                if cs:
                    c["candles_long"] = [[k.open_time, k.open, k.high, k.low, k.close, k.volume] for k in cs[-96 * 22:]]
                try:   # 22.10 (audit): settled funding for the demo perpetual desks — 16 days covers the longest hold (14 d)
                    c["funding"] = store.funding(c["symbol"], int(_t.time() * 1000) - 16 * 86_400_000)
                except Exception:  # noqa: BLE001
                    pass
            try:   # class T (lab, 18.10): BTC vs the EMA of its 15m closes over 4 800 bars (≈ 50 days), as in scripts/lab/fam_trend.py
                bcs = next((v for k, v in universe.items() if k.startswith("BTC")), None)
                if bcs and len(bcs) >= 96 * 30:
                    a_, e_ = 2 / 4801, bcs[0].close
                    for k in bcs[1:]:
                        e_ = a_ * k.close + (1 - a_) * e_
                    rep["btc_ema50d"] = {"ema": e_, "close": bcs[-1].close, "bull": bcs[-1].close > e_, "bars": len(bcs)}
            except Exception as e:  # noqa: BLE001
                print("btc ema50d:", e)
            try:   # 22.10: what «Импульс BTC» waits for — BTC +2.5 % in an hour; the last time it happened in the stored 400 days
                bcs = next((v for k, v in universe.items() if k.startswith("BTC")), None)
                if bcs:
                    rep["btc_impulse"] = paper_desk.btc_impulse_watch([[k.open_time, k.open, k.high, k.low, k.close] for k in bcs],
                                                                      int(_t.time() * 1000))
            except Exception as e:  # noqa: BLE001
                print("btc impulse watch:", e)
            for bx in cfg.b_extra:   # lab-only alt coins (docs/research/alt100.md): candles only, excluded from breadth and the main accounts
                cs = store.candles(bx, "15m", start=int(_t.time() * 1000) - 33 * 86_400_000)
                if len(cs) >= 96 * 31:
                    rows = [[k.open_time, k.open, k.high, k.low, k.close, k.volume] for k in cs[-96 * 31:]]
                    rep["coins"].append({"symbol": bx, "candles": rows, "levels": [], "regime": {}, "b_only": True})
            try:   # macro dashboard of the previous run (Nikkei close for lab rule J)
                rep["macro"] = json.load(open(os.path.join(args.out, "macro.json"), encoding="utf-8"))
            except Exception:  # noqa: BLE001
                rep["macro"] = None
            res = paper_desk.run(conn, rep)
            try:   # macro calendar (FOMC / CPI / NFP) → web/calendar.json
                from .agents import macro_calendar
                macro_calendar.write(args.out)
            except Exception as e:  # noqa: BLE001
                print("calendar:", e)
            write_report(res, os.path.join(args.out, "paper.json"))
            print(f"paper.json: equity={res['equity']} open={len(res['open'])} pending={len(res['pending'])} closed={res['stats']['trades']} events={res['events'][:3]}")
        elif args.cmd == "calibrate":
            import sqlite3
            from .agents import calibration
            conn = sqlite3.connect("data/agents.sqlite")
            news = []
            try:
                news = json.load(open(os.path.join(args.out, "decision.json"), encoding="utf-8"))["news"]["impacts"]
            except Exception:
                pass
            doc = calibration.run(conn, rep, news)
            write_report(doc, os.path.join(args.out, "calibration.json"))
            print(f"calibration.json: recorded {doc['recorded']}, resolved {doc['resolved']}, stats n={doc['stats'].get('n')} hit={doc['stats'].get('hit_rate')} brier={doc['stats'].get('brier')}")
        elif args.cmd == "weekly":
            import sqlite3
            from .agents import weekly
            conn = sqlite3.connect("data/agents.sqlite")
            path = os.path.join(args.out, "weekly.json")
            why = weekly.should_run(path) or ("forced" if args.force else None)
            if not why:
                print("weekly: not Friday yet / already written this week"); return 0
            doc = weekly.run(conn, rep, path)
            print(f"weekly.json written ({why}): {doc['week']['from']}–{doc['week']['to']}, paper {doc['paper']['n']} trades {doc['paper']['sum_r']:+.2f}R")
            if args.send and why in ("friday", "forced"):
                print("sent" if notify.send(doc["post"]) else "not sent (CNQ_TG_TOKEN/CNQ_TG_CHAT missing?)")
        elif args.cmd == "agents":
            from .agents.orchestrator import Orchestrator
            orc = Orchestrator(db_path="data/agents.sqlite")
            d = orc.run(rep)
            write_report(d, os.path.join(args.out, "decision.json"))
            print(f"decision.json: llm={d['news']['llm']} fresh={d['news'].get('fresh')} analyzed={d['news'].get('analyzed')} llm_error={d['news'].get('llm_error')} post_error={d.get('post_error')} news={len(d['news']['items'])} impacts={len(d['news']['impacts'])} "
                  f"blackout={d['risk']['blackout_assets']} post={d['post_source']} ({len(d['post'])} chars)")
            if args.send:
                print("sent" if notify.send(d["post"]) else "not sent (CNQ_TG_TOKEN/CNQ_TG_CHAT missing?)")
        elif args.cmd == "brief":
            print(rep["brief"])
            if args.send:
                print("sent" if notify.send(rep["brief"]) else "not sent (CNQ_TG_TOKEN/CNQ_TG_CHAT missing?)")
        else:
            print("alerts sent:", notify.alert_new_events(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
