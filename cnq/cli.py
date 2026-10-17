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
  python -m cnq.cli memes [--out web]         HL meme perps + Solana memecoin watchlist + paper «Мем-портфель» → web/memes.json
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
    ap = argparse.ArgumentParser(prog="cnq")
    ap.add_argument("cmd", choices=["backfill", "stream", "status", "levels", "replay", "ev", "binance-sync", "binance-peek", "terminal", "brief", "alerts", "agents", "fx-gaps", "portfolio", "paper", "weekly", "calibrate", "research", "whales", "channel", "botmail", "voice", "funding", "arb", "memes"])
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
    ap.add_argument("--force", action="store_true", help="weekly: rebuild and send even if it is not Friday")
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
        for bx, _ in cfg.universe:
            cs = store.candles(bx, "15m")
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
            for bx in cfg.b_extra:   # lab-only alt coins (docs/research/alt100.md): candles only, excluded from breadth and the main accounts
                cs = store.candles(bx, "15m")
                if len(cs) >= 96 * 31:
                    rows = [[k.open_time, k.open, k.high, k.low, k.close, k.volume] for k in cs[-96 * 31:]]
                    rep["coins"].append({"symbol": bx, "candles": rows, "levels": [], "regime": {}, "b_only": True})
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
