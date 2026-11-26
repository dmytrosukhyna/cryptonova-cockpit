"""Experiment program on the server (owner 22.11.2026 16:18). PAPER ONLY, owner-only.
Every 5 minutes: the experiment desks (cnq/agents/exp_desks.py) follow the whale copy stream (web/whales_live.json) in every
variant; every hour: the strategy board (cnq/agents/strategy_board.py) → <run>/data/strategy_board.json; once a day after
18:30 UTC the board is published as docs/research/strategy_board.md (GitHub contents API, the same token as web/ files) for the
21:07 Athens evening report. Owner routes: GET /v2/owner/exp_desks, GET /v2/owner/strategy_board. Nothing here places an order;
the variants have no copy-signal id, so the real account can never open their trades."""
from __future__ import annotations

import base64
import json
import os
import threading
import time

EVERY_S = 300
BOARD_EVERY_MS = 3_600_000


class Experiments:
    def __init__(self, data_dir: str, web_dir: str, env=None):
        self.root, self.web = data_dir, web_dir           # data_dir = <run>/data
        self.dir = os.path.join(data_dir, "exp_desks")
        os.makedirs(self.dir, exist_ok=True)
        self.env = os.environ if env is None else env
        self.lock = threading.Lock()
        self.last_err: str | None = None
        self.c4: tuple[int, list] = (0, [])

    def _load(self, p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _save(self, p, doc) -> None:
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
        os.replace(p + ".tmp", p)

    def tick(self, now: int | None = None, get=None) -> dict | None:
        from cnq.agents import exp_desks as X, strategy_board as SB
        now = int(now or time.time() * 1000)
        with self.lock:
            try:
                desk = (self._load(os.path.join(self.web, "whales_live.json")) or {}).get("desk") or {}
                if now - self.c4[0] > 15 * 60_000:
                    try:
                        self.c4 = (now, X.candles("BTC-USDT-SWAP", "4H", 60, get))
                    except Exception:  # noqa: BLE001 — no regime this pass: the filter lets trades through
                        pass
                ctx = {"regime": X.regime(self.c4[1]), "smart": X.smart_flags(self._load(os.path.join(self.web, "smart_money.json")))}

                def atr_of(coin):
                    return X.atr(X.candles(f"{coin}-USDT-SWAP", "1H", 30, get))

                def lvl_of(coin):                        # 26.11: whale copies re-confirmed by our levels / Quasimodo (paper)
                    from cnq.agents import levels_desk as LD
                    return X.level_ctx(LD.hourly(coin))
                if get is None:                          # tests pass a fake `get`: no level data there, the f_* variants skip
                    ctx["lvl_of"] = lvl_of
                sp = os.path.join(self.dir, "state.json")
                st = self._load(sp) or {}
                X.step(st, desk, now, ctx, atr_of)
                self._save(sp, st)
                try:                                     # 22.11 16:48: the experiment factory (50+ specs, same marks)
                    self.factory_tick(now, get, desk)
                except Exception as e:  # noqa: BLE001
                    self.last_err = f"factory {type(e).__name__}"
                b = self._load(os.path.join(self.root, "strategy_board.json"))
                if not b or now - int(b.get("asof") or 0) >= BOARD_EVERY_MS:
                    b = SB.write(self.web, self.root, now)
                    self._publish(b, now)
                self.last_err = None
                return st
            except Exception as e:  # noqa: BLE001
                self.last_err = type(e).__name__
                return None

    def factory_tick(self, now: int, get=None, desk: dict | None = None) -> dict:
        from cnq.agents import exp_desks as X, exp_factory as F
        g = get or F._get
        d = os.path.join(self.root, "exp_factory")
        os.makedirs(d, exist_ok=True)
        cache = getattr(self, "_fc", {})
        tk = g(f"{F.OKX}/market/tickers?instType=SWAP").get("data") or []
        marks = {x["instId"][:-len("-USDT-SWAP")]: float(x["last"]) for x in tk if str(x.get("instId", "")).endswith("-USDT-SWAP") and x.get("last")}

        def cached(key, ttl, fn):
            t, v = cache.get(key, (0, None))
            if now - t >= ttl:
                try:
                    v = fn()
                except Exception:  # noqa: BLE001
                    pass
                cache[key] = (now, v)
            return v
        c4 = cached("c4", 15 * 60_000, lambda: F.candles("BTC-USDT-SWAP", "4H", 60, g)) or []
        c1 = cached("c1", 15 * 60_000, lambda: F.candles("BTC-USDT-SWAP", "1H", 80, g)) or []
        cd = cached("cd", 3 * 3_600_000, lambda: F.candles("BTC-USDT-SWAP", "1D", 300, g)) or []
        fund = cached("fund", 3_600_000, lambda: {c: float((g(f"{F.OKX}/public/funding-rate?instId={c}-USDT-SWAP").get("data") or [{}])[0]
                                                         .get("fundingRate") or 0) for c in ("BTC", "ETH", "SOL")})
        lsr = cached("lsr", 3_600_000, lambda: float((g(f"{F.OKX}/rubik/stat/contracts/long-short-account-ratio?ccy=BTC&period=1H")
                                                      .get("data") or [[0, None]])[0][1]))
        self._fc = cache
        mk = self._load(os.path.join(self.web, "markets.json")) or {}
        pp = (self._load(os.path.join(self.web, "paper.json")) or {}).get("profiles") or {}
        ctx = {"c4": c4, "reg4h20": F.trend_state(c4, 20), "reg1h50": F.trend_state(c1, 50), "reg1d200": F.trend_state(cd, 200),
               "fng": (mk.get("fng") or {}).get("value"), "funding": fund or {}, "lsr": lsr,
               "smart": X.smart_flags(self._load(os.path.join(self.web, "smart_money.json")))}
        sources = {"whales": desk or {}, "own": pp.get("own") or {}, "aggr": pp.get("aggr") or {}}
        sp = os.path.join(d, "state.json")
        st = self._load(sp) or {}
        try:                                   # 23.11: macro bias state for the «макро-перекос» spec
            evs = cached("macro_ev", 6 * 3_600_000, lambda: self._macro_events()) or []
            st["macro_bias"] = F.macro_bias(evs, c4, now)
        except Exception:  # noqa: BLE001
            st["macro_bias"] = {"active": False}
        # 22.11 17:19: watched Hyperliquid wallets (config/exp_watch_traders.json, from scripts/research/global_traders.py) — paper
        cfg = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config")
        wl = self._load(os.path.join(cfg, "exp_watch_traders.json")) or {}
        hf = self._load(os.path.join(cfg, "hedge_flags.json")) or {}       # 26.11: «хедж» traders (scripts/research/hedge_pool.py)
        ctx["hedge"] = {str(x).lower() for x in hf.get("flagged") or []}
        wallets = [str(w["addr"]).lower() for w in wl.get("hl") or [] if isinstance(w, dict) and str(w.get("addr", "")).startswith("0x")][:30]
        if wallets:
            hp = getattr(self, "_hl_post", None) or self._hl

            def pos_of(a):
                ch = hp({"type": "clearinghouseState", "user": a}) or {}
                out = []
                for p_ in ch.get("assetPositions") or []:
                    q = p_.get("position") or {}
                    sz = float(q.get("szi") or 0)
                    if sz and q.get("coin") and q.get("entryPx"):
                        out.append({"coin": str(q["coin"]).upper(), "side": "LONG" if sz > 0 else "SHORT", "entry": float(q["entryPx"])})
                return out
            if st.get("specs"):
                F.ensure_watch_specs(st, wallets, now)
            st["hl_book"], sources["hlw"] = F.hl_book(st.get("hl_book"), wallets, pos_of, now, marks)
        # 26.11: Binance / OKX lead traders of the relaxed bar (paper only) — their public positions, one read per 5-min pass
        ws = [str(w["addr"]) for w in wl.get("ws") or [] if isinstance(w, dict) and str(w.get("addr", "")).split(":")[0] in ("bn", "okx")][:10]
        if ws:
            wp = getattr(self, "_ws_pos", None) or self._ws_positions
            if st.get("specs"):
                F.ensure_watch_specs(st, ws, now, src="wsw", family="широкий пул")
            st["ws_book"], sources["wsw"] = F.hl_book(st.get("ws_book"), ws, lambda a: wp(a, g), now, marks)
        if st.get("specs"):
            F.ensure_specs(st, F.POSTMORTEM_SPECS, now, "postmortem")
            F.ensure_specs(st, F.HEDGE_SPECS, now, "hedge")
        # 22.11 22:36: a trader's public Telegram calls — the handle lives ONLY in <run>/data/influencers/channels.json (owner-only)
        allch = self.channels()
        ch = [c for c in allch if c.get("kind", "spot") != "fut"]
        fch = [c for c in allch if c.get("kind") == "fut"][:5]
        if fch and st.get("specs"):                    # 26.11: public futures-signal channels (sealed handles) — paper desks
            F.ensure_specs(st, F.fut_specs([c["key"] for c in fch]), now, "telegram·фьючерсы")
            if not st.get("swap_coins") or now - int(st.get("tgf_t") or 0) >= 30 * 60_000:
                st["swap_coins"] = sorted(marks)
            if now - int(st.get("tgf_t") or 0) >= 30 * 60_000:
                st["tgf_t"] = now
                for c in fch:
                    try:
                        book = st.setdefault("tgf_posts", {}).setdefault(c["key"], {})
                        from cnq.agents import influencer as I
                        for p_ in I.parse_page(self._tg_get(f"https://t.me/s/{c['handle']}")):
                            book[str(p_["id"])] = p_
                        if len(book) > 400:
                            for k_ in sorted(book, key=int)[:-400]:
                                book.pop(k_, None)
                    except Exception:  # noqa: BLE001
                        pass
            fe = {"open": [], "closed": []}
            for c in fch:
                fe["open"] += F.fut_events(list((st.get("tgf_posts") or {}).get(c["key"], {}).values()), set(st.get("swap_coins") or []),
                                           c["key"], int(st["born"]))["open"]
            sources["tgf"] = fe
        if ch and st.get("specs"):
            from cnq.agents import influencer as I
            F.ensure_specs(st, F.TG_SPECS, now, "telegram")
            if now - int(st.get("tg_t") or 0) >= 30 * 60_000:
                st["tg_t"] = now
                spot = cached("spot", 6 * 3_600_000, lambda: [x["instId"].split("-")[0] for x in
                                                              (g(f"{F.OKX}/public/instruments?instType=SPOT").get("data") or [])
                                                              if x.get("instId", "").endswith("-USDT")]) or []
                st["tg_coins"] = sorted(set(spot))
                for c in ch[:3]:
                    try:
                        page = self._tg_get(f"https://t.me/s/{c['handle']}")
                        new = I.parse_page(page)
                        book = st.setdefault("tg_posts", {}).setdefault(c["key"], {})
                        for p_ in new:
                            book[str(p_["id"])] = p_
                        d_ = os.path.join(self.root, "influencers", c["key"])
                        os.makedirs(d_, exist_ok=True)
                        self._save(os.path.join(d_, "posts.json"), sorted(book.values(), key=lambda x: x["id"]))
                    except Exception:  # noqa: BLE001
                        pass
            evs = {"open": [], "closed": []}
            for c in ch[:3]:
                e_ = F.tg_events(list((st.get("tg_posts") or {}).get(c["key"], {}).values()), set(st.get("tg_coins") or []), c["key"], int(st["born"]))
                evs["open"] += e_["open"]
                lines = []                     # calls forwarded by the owner (Instagram): repo file (anonymous key) + the owner-only data dir
                for fp in (os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config",
                                        f"influencer_calls_{c['key']}.jsonl"),
                           os.path.join(self.root, "influencers", c["key"], "manual_calls.jsonl")):
                    try:
                        with open(fp, encoding="utf-8") as f:
                            lines += [json.loads(x) for x in f if x.strip()]
                    except (OSError, ValueError):
                        pass
                evs["open"] += F.manual_events(lines, c["key"], int(st["born"]))
            sources["tgc"] = evs
            stk = cached("spot_tk", 5 * 60_000, lambda: g(f"{F.OKX}/market/tickers?instType=SPOT").get("data") or []) or []
            for x in stk:
                b_ = str(x.get("instId", ""))
                if b_.endswith("-USDT") and x.get("last"):
                    marks.setdefault(b_[:-5], float(x["last"]))
                    ctx.setdefault("vol24", {})[b_[:-5]] = float(x.get("volCcy24h") or 0)
        fo = (self._load(os.path.join(self.web, "fut_owner.json")) or {}).get("kpi") or {}
        if fo.get("budget_usd"):
            st["fut_budget"] = float(fo["budget_usd"])             # the real futures budget for the «real rules» specs
        r = F.step(st, now, ctx, sources, marks,
                   trend_of=lambda c: F.trend_state(F.candles(f"{c}-USDT-SWAP", "1H", 60, g), 50),
                   atr_of=lambda c: F.atr_pct(F.candles(f"{c}-USDT-SWAP", "1H", 30, g)))
        self._save(sp, st)
        return r

    @staticmethod
    def _macro_events() -> list:
        from datetime import datetime, timezone
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        out = []
        for fn in ("macro_events.json", "macro_events_ext.json"):
            try:
                for e in json.load(open(os.path.join(root, "scripts", fn), encoding="utf-8"))["events"]:
                    if e.get("type") in ("CPI", "PCE", "FOMC") and e.get("utc"):
                        out.append((int(datetime.strptime(e["utc"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).timestamp() * 1000), e["type"]))
            except Exception:  # noqa: BLE001
                pass
        return out

    @staticmethod
    def _tg_get(url: str) -> str:
        import urllib.request
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (cnq-factory)"}), timeout=15) as r:
            return r.read().decode("utf-8", "replace")

    def channels(self) -> list:
        """watched channels: the sealed web/sealed/influencers.json (encrypted to this server's key — the handle is never in plain text in
        the public repo), else <run>/data/influencers/channels.json. Both shapes: {"channels": [{key, handle}]} or {key: {handle}}"""
        d = None
        try:
            from cnq import sealed
            d = sealed.load(self.web, self.root, "influencers")
        except Exception:  # noqa: BLE001
            d = None
        if not d:
            d = self._load(os.path.join(self.root, "influencers", "channels.json")) or {}
        if isinstance(d.get("channels"), list):
            out = d["channels"]
        else:
            out = [{"key": k, "handle": v.get("handle")} for k, v in d.items() if isinstance(v, dict)]
        return [c for c in out if isinstance(c, dict) and c.get("key") and c.get("handle")]

    def set_channel(self, key: str, handle: str) -> tuple[int, dict]:
        """owner-only: add / replace a watched Telegram channel (kept in <run>/data/influencers/channels.json, never in git)"""
        import re as _re
        if not _re.fullmatch(r"[A-Za-z0-9_]{3,40}", str(handle or "")) or not _re.fullmatch(r"[A-Za-z0-9_]{1,12}", str(key or "")):
            return 400, {"error": "bad_input"}
        p = os.path.join(self.root, "influencers", "channels.json")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        d = self._load(p) or {"channels": []}
        d["channels"] = [c for c in d.get("channels") or [] if c.get("key") != key] + [{"key": key, "handle": handle}]
        self._save(p, d)
        return 200, {"ok": True, "channels": [c["key"] for c in d["channels"]]}

    @staticmethod
    def _ws_positions(addr: str, g) -> list:
        """26.11: current positions of a Binance / OKX lead trader → [{coin, side, entry}] (public copy-trading pages)"""
        src, code = addr.split(":", 1)
        if src == "bn":
            from cnq.agents import whale_sources as WS
            return [{"coin": p["coin"], "side": p["side"], "entry": p["entry"]}
                    for p in WS.bn_positions(WS.bn_current(WS.bn_get, {"code": code})) if p.get("coin") and p.get("entry")]
        from cnq.agents import whale_study as W
        rows = g(f"https://www.okx.com/api/v5/copytrading/public-current-subpositions?instType=SWAP&uniqueCode={code}&limit=100").get("data") or []
        return [{"coin": p["coin"], "side": p["side"], "entry": p["entry"]} for p in W.okx_positions({"okx": rows}) if p.get("coin") and p.get("entry")]

    @staticmethod
    def _hl(body: dict):
        import urllib.request
        req = urllib.request.Request("https://api.hyperliquid.xyz/info", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": "cnq-factory"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def factory(self) -> tuple[int, dict]:
        from cnq.agents import exp_factory as F
        st = self._load(os.path.join(self.root, "exp_factory", "state.json"))
        if st is None:
            return 503, {"error": "no_data", "last_error": self.last_err}
        b = self._load(os.path.join(self.root, "strategy_board.json")) or {}
        src = next((r["d30"] for r in b.get("rows") or [] if r.get("key") == "whales"), {})
        return 200, {"asof": int(time.time() * 1000), **F.summary(st, {"pf": src.get("pf"), "max_dd": src.get("max_dd_pct")}),
                     "open": st.get("open") or []}

    def _publish(self, b: dict, now: int) -> None:
        """docs/research/strategy_board.md once a UTC day after 18:30 — the evening report reads it"""
        from cnq.agents import strategy_board as SB
        from cnq.execution import ivan_watch
        if time.gmtime(now / 1000).tm_hour * 60 + time.gmtime(now / 1000).tm_min < 18 * 60 + 30:
            return
        mark = os.path.join(self.root, "strategy_board.published")
        day = time.strftime("%Y-%m-%d", time.gmtime(now / 1000))
        try:
            if open(mark, encoding="utf-8").read().strip() == day:
                return
        except OSError:
            pass
        tok, repo = self.env.get("CNQ_GH_TOKEN", ""), self.env.get("CNQ_GH_REPO") or ivan_watch.DEFAULT_REPO
        if not tok:
            return
        url = f"https://api.github.com/repos/{repo}/contents/docs/research/strategy_board.md"
        hdr = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json", "User-Agent": "cnq-strategy-board"}
        if ivan_watch.put_contents(url, hdr, base64.b64encode(SB.markdown(b).encode()).decode(), f"strategy board {day}"):
            with open(mark, "w", encoding="utf-8") as f:
                f.write(day)

    def desks(self) -> tuple[int, dict]:
        from cnq.agents import exp_desks as X
        st = self._load(os.path.join(self.dir, "state.json"))
        if st is None:
            return 503, {"error": "no_data", "last_error": self.last_err}
        b = self._load(os.path.join(self.root, "strategy_board.json")) or {}
        src = next((r["d30"] for r in b.get("rows") or [] if r.get("key") == "whales"), {})
        return 200, {"asof": int(time.time() * 1000), "summary": X.summary(st, {"pf": src.get("pf"), "max_dd_pct": src.get("max_dd_pct")}),
                     "open": st.get("open") or [], "closed": (st.get("closed") or [])[-200:]}

    def board(self) -> tuple[int, dict]:
        b = self._load(os.path.join(self.root, "strategy_board.json"))
        return (200, b) if b else (503, {"error": "no_data", "last_error": self.last_err})

    def start(self) -> None:
        def loop():
            while True:
                self.tick()
                time.sleep(EVERY_S)
        threading.Thread(target=loop, daemon=True, name="experiments").start()
