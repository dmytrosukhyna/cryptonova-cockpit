"""«Автопилот OKX · фьючерсы» (22.10.2026) — the futures sleeve of the owner's OKX sub-account follows FUTURES · CRYPTO NOVA
(«отобранные сигналы»): the desks the closed system's header adds up — the MAIN desk and «Импульс BTC» (imp), plus
«Сигналы CRYPTO NOVA» (own) once its live record passes the gate (≥ 30 trades, ≥ +0.3 % a trade) — the same open / close / stop /
target events the Bybit demo mirror follows (web/paper.json; cnq/execution/mirror.py fetch_desk / step). The header shows them as
ONE $10 000 account (their average), so each desk gets an equal part of the budget.
The main desk (28.10, owner: «система должна автоматически определять самую эффективную систему и переключаться на нее»): «current» of
web/desk_select.json (cnq/agents/desk_select.py — slow, evidence-based, at most once in 7 days, never with an open position of the
current desk on this account), read through panel_header.main_desk like the panel header; missing / broken / unreachable →
«Агрессивная» (the owner's 28.10 choice). OKX_FUT_PROFILES overrides everything. Guard: while this account still holds a position of
the previous main desk (state «main»), that desk stays the main one until the position is closed — a position never changes desk.

What it does, once a minute inside the autopilot window (okx_autopilot --loop) and once on --run:
  • a desk trade → a USDT-margined perpetual swap (instType SWAP,
    <COIN>-USDT-SWAP), isolated margin, a limit IOC entry at most OKX_SLIP (0.4 %) worse than the best price, with a stop-loss
    (and the desk's final target) ATTACHED to the order (attachAlgoOrds, trigger on the last price — what a lead trader needs);
    the stop is then checked on OKX: missing or wrong → amended or placed again for the whole position; if that fails too, the
    position is closed at once — never a position without a stop;
  • a moved desk stop / target → the OKX stop / target amended; a partial desk exit (qty_left) → a reduce-only partial close;
  • a trade the desk closed → a reduce-only market close of the whole position and its stop orders cancelled;
  • a position closed by OKX itself (stop or target hit) → forgotten.
Late-join guard (22.10 review): a desk trade is mirrored only if it was opened after the autopilot's first live start (and is ≤ 45
min old), OR the price now is at most OKX_FUT_JOIN_GAP (0.3 %) worse than the desk's entry; otherwise it is skipped («пропущено:
сделка открыта раньше, цена ушла на X% от входа системы») and looked at again a minute later. The size always comes from the worst
price the entry may fill at vs the desk's stop, so the risk stays within the cap whatever the fill.

Size: the desk's own risk (risk_usd ÷ desk equity) applied to the desk's part of the FUTURES BUDGET = OKX_FUT_SHARE (0.25) of the
account (okx_alloc.shares). Hard caps (env can only make them stricter):
  leverage ≤ 3 (OKX_FUT_MAX_LEV, default 2; isolated margin, set per instrument) and all positions' notional ≤ budget × leverage;
  risk per trade = the desk's own risk share × this desk's part of the budget (owner 22.10: «риск как у системы»), never more than
  1 % of the whole budget (distance to the stop × size);   at most 5 open positions;
  daily loss stop: realised P/L of the UTC day + open losses ≤ −3 % of the budget → no new entries until the next UTC day;
  the kill switch ~/.cryptonova_okx_stop (shared with the spot autopilot): nothing is placed at all — positions keep their stops on
  OKX; `--fut-close` closes them now.
Money: real only with CNQ_LIVE_TRADING=yes given in the autopilot window («--live» + «ДА»); otherwise OKX demo trading (header
x-simulated-trading: 1, a demo key). A key with «Withdraw» is refused; the account must be in the «Фьючерсы» mode (acctLv 2, the
API's «Spot and futures mode»); in the «Спот» mode (acctLv 1) OKX has no swaps and this part does nothing. A copy trader account
(roleType 2) is refused: a lead must not copy anyone.
Lead trader («Копировать CRYPTO NOVA»): detected from the account config (roleType 1) or OKX_IS_LEAD=1. 22.10 (owner: everything
the account trades must be copyable): an entry on a swap that is not yet a lead instrument (traderInsts) first adds it —
POST /api/v5/copytrading/set-instruments, instType SWAP, the full list (current + new) — and enters in the same pass; when OKX
refuses, the entry goes as an ordinary order («не копируется»; the refusal is remembered for a day). Stops are set on the lead
sub-positions (/api/v5/copytrading/algo-order) and exits close the lead sub-positions (/api/v5/copytrading/close-subposition); a
position without lead sub-positions (opened before, or refused) keeps the ordinary stop, partial exits and reduce-only close. Every stop triggers on the last price. One position per instrument and
reduce-only exits: an order in the opposite direction is never used to exit.
Through USDC (22.10, owner: «если по USDT ничего нету, то надо через USDC»): a coin with no <COIN>-USDT-SWAP but a live
<COIN>-USDC-SWAP — OKX: settleCcy «Settlement and margin currency» = USDC, a linear contract whose ctVal is counted in ctValCcy =
the coin — is traded there with the same isolated sizing, risk, stop and exits. The margin (+ fee room) is bought as USDC on the
spot pair USDC-USDT just before the entry; after a close the freed USDC goes back to USDT (okx_autopilot.sweep, at the end of every
pass). Not for a lead trader (its instruments are USDT swaps). Off with OKX_FUT_USDC=off.
On unless OKX_FUTURES=off. Log: data/okx_futures.jsonl. State: ~/.cryptonova_okx_futures.json.
Not financial advice: futures with leverage can lose money fast. Stdlib only."""
from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request

from ..agents import panel_header as ph
from . import myspot_watch as ms
from . import okx_alloc as al
from . import okx_autopilot as ap
from . import okx_lead as ld

LOG = "data/okx_futures.jsonl"
FRESH_MS = 45 * 60_000
JOIN_GAP = 0.003            # a trade opened before the start is joined only while the price is ≤ 0.3 % worse than the desk's entry
MAX_LEV, DEF_LEV = 3, 2
RISK_CAP = 0.01             # hard ceiling: 1 % of the futures budget per trade (the desk's own risk, e.g. 1.5 % of a half budget = 0.75 %, fits)
MAX_POS = 5
DAY_LOSS = 0.03             # 3 % of the futures budget per UTC day
OWN_GATE = (30, 0.3)        # «Сигналы CRYPTO NOVA» join the futures result after 30 closed trades at ≥ +0.3 % a trade (as the header)
MIN_STOP = 0.002            # a stop closer than 0.2 % to the price is not a trade
BUDGET_TTL = 15 * 60_000
VERIFY_MS = 10 * 60_000     # every open position's stop is re-checked on OKX at least this often
PX_TOL = 0.15               # OKX price vs the desk's entry (catches a wrong ticker mapping)
MAX_TRIES = 3
SRC = "https://raw.githubusercontent.com/dmytrosukhyna/cryptonova-cockpit/main/web/paper.json"
SEL_SRC = "https://raw.githubusercontent.com/dmytrosukhyna/cryptonova-cockpit/main/web/desk_select.json"
DAY = 86_400_000


class StopFailed(RuntimeError):
    pass


# ── env / state ──────────────────────────────────────────────────────────────────────────────────────────────────────
def _env(env=None) -> dict:
    return os.environ if env is None else env


def enabled(env=None) -> bool:
    return (_env(env).get("OKX_FUTURES") or "on").strip().lower() not in ("off", "no", "0", "false")


def _num(e, k, d):
    try:
        v = float(e.get(k) or d)
        return v if v > 0 else d
    except ValueError:
        return d


def caps(env=None) -> dict:
    """The hard caps. Env may only make them stricter."""
    e = _env(env)
    return {"lev": max(1, min(MAX_LEV, int(_num(e, "OKX_FUT_MAX_LEV", DEF_LEV)))),
            "risk": min(RISK_CAP, _num(e, "OKX_FUT_RISK", RISK_CAP)),
            "max_pos": max(1, min(MAX_POS, int(_num(e, "OKX_FUT_MAX_POS", MAX_POS)))),
            "day_loss": min(DAY_LOSS, _num(e, "OKX_FUT_DAY_LOSS", DAY_LOSS))}


def join_gap(env=None) -> float:
    try:
        v = float((_env(env).get("OKX_FUT_JOIN_GAP") or JOIN_GAP))
        return v if 0 <= v <= 0.02 else JOIN_GAP
    except ValueError:
        return JOIN_GAP


def start_key(cl: ap.Okx, env=None) -> str:
    """The first LIVE start counts for the real account; the first start for the demo one."""
    return "first_live" if ap.live(env) and not cl.demo else "started"


def usdc_on(env=None) -> bool:
    return (_env(env).get("OKX_FUT_USDC") or "on").strip().lower() not in ("off", "no", "0", "false")


def lead_forced(env=None) -> bool:
    return (_env(env).get("OKX_IS_LEAD") or "").strip().lower() in ("1", "yes", "on", "true")


def state_path(env=None) -> str:
    return os.path.expanduser(_env(env).get("OKX_FUT_STATE") or "~/.cryptonova_okx_futures.json")


def load_state(env=None) -> dict:
    try:
        with open(state_path(env), encoding="utf-8") as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def save_state(st: dict, env=None) -> None:
    p = state_path(env)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, separators=(",", ":"))
    try:
        os.chmod(p + ".tmp", 0o600)
    except OSError:
        pass
    os.replace(p + ".tmp", p)


def log(ev: dict, path: str | None = None) -> None:
    ap.log(ev, path or LOG)


def desk_src(env=None) -> tuple[str, list | None]:
    """(paper.json url, forced desk keys or None = the header's own set)."""
    e = _env(env)
    forced = [x.strip() for x in (e.get("OKX_FUT_PROFILES") or "").split(",") if x.strip()]
    return (e.get("OKX_FUT_SRC") or e.get("CNQ_MIRROR_SRC") or SRC), (forced or None)


def sel_src(env=None) -> str:
    """where web/desk_select.json is read: OKX_FUT_SEL_SRC, else next to the desks' paper.json, else the repository's file"""
    e = _env(env)
    if e.get("OKX_FUT_SEL_SRC"):
        return e["OKX_FUT_SEL_SRC"]
    src = desk_src(env)[0]
    return src[: -len("paper.json")] + ph.SEL_FILE if src.endswith("/paper.json") else SEL_SRC


def pick_desks(paper: dict, forced: list | None = None, sel: dict | None = None, prev: str | None = None, held=()) -> list:
    """[(key, desk)] — the desks of FUTURES · CRYPTO NOVA, as the header counts them: the main desk (desk_select.json «current» via
    panel_header.main_desk; none → «Агрессивная») + imp, + own after its gate. `prev` = the main desk of the last pass, `held` = the
    desks of this account's open positions: a previous main desk that still holds a position stays the main one until it closes."""
    pr = paper.get("profiles") or {}
    if not pr and paper.get("open") is not None:          # a single desk file
        return [(paper.get("profile") or "bal", paper)]
    if forced:
        keys = list(forced)
    else:
        keys = list(ph.nova_base(sel))
        s = (pr.get("own") or {}).get("stats") or {}
        if "own" not in keys and (s.get("trades") or 0) >= OWN_GATE[0] and s.get("avg_trade_pct") is not None \
                and s["avg_trade_pct"] >= OWN_GATE[1]:
            keys = keys + ["own"]
        if prev and prev not in keys and prev in held and prev in ph.MAIN_KEYS and isinstance(pr.get(prev), dict):
            keys[0] = prev                                  # never move an open position between desks: wait until it is closed
    return [(k, pr[k]) for k in keys if isinstance(pr.get(k), dict)]


def _get_json(url: str):
    req = urllib.request.Request(url + f"?t={int(time.time())}", headers={"Cache-Control": "no-cache", "User-Agent": "cnq-okx-futures"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def fetch_sel(env=None) -> dict | None:
    """web/desk_select.json — the main desk the selector chose; None when missing, broken or unreachable (→ the default desk)"""
    try:
        d = _get_json(sel_src(env))
        return d if isinstance(d, dict) else None
    except Exception:  # noqa: BLE001
        return None


def held_desks(st: dict | None) -> set:
    """the desks whose trades this account holds now (state «trades»: «<desk>:<id>»)"""
    return {str((m or {}).get("desk") or tid.split(":")[0]) for tid, m in ((st or {}).get("trades") or {}).items()}


def fetch_desks(env=None, st: dict | None = None) -> list:
    src, forced = desk_src(env)
    paper = _get_json(src)
    return pick_desks(paper, forced, None if forced else fetch_sel(env), (st or {}).get("main"), held_desks(st))


# ── OKX: the swap calls (through the autopilot's signed client — no transfer, no withdrawal exists here) ──────────────────
class Ctx:
    """One futures pass over one account: config, mode, cached instruments / positions / stop orders."""

    def __init__(self, cl: ap.Okx, cfg: dict, env=None):
        self.cl, self.cfg, self.env = cl, cfg, env
        self.net = str(cfg.get("posMode") or "net_mode") != "long_short_mode"
        self.lead = str(cfg.get("roleType") or "0") == "1" or lead_forced(env)
        ti = cfg.get("traderInsts") or []
        self.lead_insts = set(ti if isinstance(ti, list) else [x for x in str(ti).split(",") if x])
        self._inst, self._pos, self._algos = {}, None, {}
        if self.lead and not self.lead_insts:          # no traderInsts in the config → the lead's own list
            try:
                self.lead_insts = {str(x.get("instId")) for x in self.get(ld.INSTS, {"instType": "SWAP"}) or []
                                   if str(x.get("enabled")).lower() == "true"}
            except ms.OkxError:
                pass

    def get(self, path, params=None, signed=True):
        return self.cl.call("GET", path, params, signed=signed)

    def post(self, path, body):
        d = self.cl.call("POST", path, body=body)
        for o in d if isinstance(d, list) else []:
            if isinstance(o, dict) and str(o.get("sCode", "0")) != "0":
                raise ap.OkxError(o.get("sCode"), o.get("sMsg") or "", path)
        return d

    def inst(self, inst_id: str) -> dict | None:
        if inst_id not in self._inst:
            try:
                d = self.get("/api/v5/public/instruments", {"instType": "SWAP", "instId": inst_id}, signed=False)
            except ms.OkxError:
                d = []
            x = d[0] if d else None
            ok = x and x.get("state") == "live" and (x.get("ctType") or "linear") == "linear"
            if inst_id.endswith("-USDC-SWAP"):        # margin and P/L in USDC; the contract counted in the coin itself
                ok = ok and x.get("settleCcy") == "USDC" and x.get("ctValCcy") == inst_id.split("-")[0] and ms._f(x.get("ctVal"))
            else:
                ok = ok and (x.get("settleCcy") or "USDT") == "USDT"
            self._inst[inst_id] = x if ok else None
        return self._inst[inst_id]

    def ticker(self, inst_id: str) -> dict | None:
        try:
            d = self.get("/api/v5/market/ticker", {"instId": inst_id}, signed=False)
        except ms.OkxError:
            return None
        return d[0] if d else None

    def positions(self, fresh: bool = False) -> dict:
        """{instId: {"long": bool, "sz": contracts, "upl", "notional", "avgPx"}} — open SWAP positions only."""
        if self._pos is None or fresh:
            out = {}
            for p in self.get("/api/v5/account/positions", {"instType": "SWAP"}):
                q = ms._f(p.get("pos")) or 0.0
                if not q:
                    continue
                side = p.get("posSide") or "net"
                out[p["instId"]] = {"long": (q > 0) if side == "net" else side == "long", "sz": abs(q), "upl": ms._f(p.get("upl")) or 0.0,
                                    "notional": abs(ms._f(p.get("notionalUsd")) or 0.0), "avgPx": ms._f(p.get("avgPx"))}
            self._pos = out
        return self._pos

    def algos(self, inst_id: str) -> list:
        """The pending stop / target orders on this instrument (attached ones appear here once the entry is filled)."""
        if inst_id not in self._algos:
            out = []
            for typ in ("conditional", "oco"):
                try:
                    out += [a for a in self.get("/api/v5/trade/orders-algo-pending", {"ordType": typ, "instType": "SWAP", "instId": inst_id})
                            if a.get("instId") == inst_id]
                except ms.OkxError:
                    pass
            self._algos[inst_id] = out
        return self._algos[inst_id]

    def forget(self, inst_id: str) -> None:
        self._algos.pop(inst_id, None)
        self._pos = None

    def subs(self, inst_id: str) -> list:
        try:
            return self.get("/api/v5/copytrading/current-subpositions", {"instType": "SWAP", "instId": inst_id})
        except ms.OkxError:
            return []

    def avail_usdt(self) -> float:
        return self.avail_ccy("USDT")

    def avail_ccy(self, ccy: str) -> float:
        try:
            return self.cl.avail(ccy)
        except ms.OkxError:
            return 0.0


def refuse(cfg: dict, cl: ap.Okx, env=None) -> str | None:
    """None when futures orders may go out on this account; otherwise why not (plain Russian)."""
    perm = ap.perm_of(cfg)
    if "withdraw" in perm:
        return ("У ключа OKX есть право ВЫВОДА — фьючерсы с таким ключом не работают. Удали ключ и создай новый: «Чтение» + «Торговля», "
                "без «Вывода».")
    if "trade" not in perm:
        return "У ключа OKX нет права «Торговля» — фьючерсы не могут ставить ордера."
    bad = ap.world_ok(cl, env)
    if bad:
        return bad
    lv = str(cfg.get("acctLv") or "")
    if lv == "1":
        return ("Субаккаунт в режиме «Спот» — на OKX в нём нет свопов. Переключи субаккаунт в режим «Фьючерсы» "
                "(Настройки → Режим счёта; без открытых ордеров и позиций).")
    if str(cfg.get("roleType") or "0") == "2":
        return ("На этом счёте включено копирование чужих сделок (copy trader) — автопилот фьючерсов здесь не работает: "
                "ведущий трейдер не должен ничего копировать. Останови копирование в OKX.")
    return None


def mode_text(cfg: dict) -> str:
    lv = str(cfg.get("acctLv") or "?")
    return {"1": "«Спот» (acctLv 1) — свопов нет", "2": "«Фьючерсы» (acctLv 2, Spot and futures) — подходит",
            "3": "«Мультивалютная маржа» (acctLv 3) — свопы есть, но для ведущего трейдера на споте нужен режим «Фьючерсы»",
            "4": "«Портфельная маржа» (acctLv 4) — свопы есть, но нужен режим «Фьючерсы»"}.get(lv, f"не прочитался (acctLv {lv})")


# ── helpers ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def target(t: dict) -> float | None:
    """The desk's final target: the last target with a non-zero part (one target with 100 % → that one)."""
    tps = [x for x in (t.get("tps") or []) if x]
    if not tps:
        return None
    parts = t.get("parts") or [1.0]
    nz = [i for i, p in enumerate(parts[:len(tps)]) if (p or 0) > 0]
    return float(tps[nz[-1] if nz else 0])


def _round_px(x: float, tick: float, down: bool) -> float:
    return ap.floor_to(x, tick) if down else ap.ceil_to(x, tick)


def _same(a, b, tick: float) -> bool:
    a, b = ms._f(a), ms._f(b)
    return a is not None and b is not None and abs(a - b) <= tick * 0.51 + 1e-12


def inst_of(sym: str) -> tuple[str, float]:
    base, mult = al.okx_base(sym)
    return f"{base}-USDT-SWAP", mult


def day_start(now: int) -> int:
    return now - now % DAY


def day_pnl(ctx: Ctx, now: int) -> float:
    """Realised P/L of the UTC day (fees and funding included, OKX positions history) + the open losses. The sub-account is the
    autopilot's own: every SWAP position on it counts."""
    start = day_start(now)
    try:
        hist = ctx.get("/api/v5/account/positions-history", {"instType": "SWAP"})
    except ms.OkxError:
        hist = []
    real = sum(ms._f(h.get("realizedPnl")) or 0.0 for h in hist if int(h.get("uTime") or 0) >= start)
    upl = sum(p["upl"] for p in ctx.positions().values())
    return round(real + min(0.0, upl), 2)


def account_total(cl: ap.Okx) -> float:
    """The whole account in dollars, as the spot plan counts it (trading + funding + Earn)."""
    return round(sum(r["usd"] for r in ms.assets(ms.read_raw(cl.fetch, cl.host))), 2)


def budget(cl: ap.Okx, st: dict, env=None, now: int | None = None, fresh: bool = False) -> dict:
    """{"usd", "share", "total"}: OKX_FUT_SHARE (0.25) of the whole account, refreshed every 15 min."""
    now = int(now or time.time() * 1000)
    b = st.get("budget") or {}
    if not fresh and b.get("usd") and now - int(b.get("t") or 0) < BUDGET_TTL:
        return b
    share = al.shares(env)["futures"]
    total = account_total(cl)
    b = {"t": now, "usd": round(total * share, 2), "share": share, "total": total}
    st["budget"] = b
    return b


def size(t: dict, desk_eq: float, part: float, bud: float, cap: dict, inst: dict, px: float, mult: float, open_notional: float,
         avail: float, slip: float = 0.004) -> dict:
    """Contracts for one desk trade, or {"skip": why}. part = this desk's share of the budget (budget ÷ number of desks).
    Risk = the desk's risk share × part (as the system), never more than RISK_CAP of the whole budget, counted from the WORST price the IOC entry may
    fill at (price ± OKX_SLIP) to the rounded stop; then the leverage room (all positions' notional ≤ budget × leverage) and the
    free USDT can only make it smaller."""
    long = t.get("side") == "LONG"
    tick = float(inst["tickSz"])
    stop = _round_px(float(t.get("stop_now") or t["stop"]) / mult, tick, long)
    tp = target(t)
    tp = _round_px(tp / mult, tick, long) if tp else None
    if (long and px <= stop) or (not long and px >= stop):
        return {"skip": "цена уже за стопом сигнала"}
    if abs(px - stop) / px < MIN_STOP:
        return {"skip": "стоп ближе 0,2% к цене — сделку не повторяю"}
    if tp and ((long and px >= tp) or (not long and px <= tp)):
        return {"skip": "цена уже дошла до цели сигнала"}
    worst = ap.ceil_to(px * (1 + slip), tick) if long else ap.floor_to(px * (1 - slip), tick)
    d_risk = ms._f(t.get("risk_usd")) or (float(t.get("qty") or 0) * abs(float(t["entry"]) - float(t["stop"])))
    frac = d_risk / desk_eq if desk_eq and d_risk else cap["risk"]
    risk_usd = min(frac * part, cap["risk"] * bud)
    lev = max(1, min(cap["lev"], int(float(inst.get("lever") or cap["lev"]))))
    coins = risk_usd / abs(worst - stop)
    capped = f"лимит риска {cap['risk'] * 100:g}% бюджета".replace(".", ",") if frac * part > cap["risk"] * bud else None
    room = bud * lev - open_notional
    if coins * worst > room:
        coins, capped = max(0.0, room) / worst, f"предел плеча {lev} на весь бюджет"
    if coins * worst > avail * 0.95 * lev:
        coins, capped = max(0.0, avail * 0.95 * lev) / worst, "мало свободных USDT"
    ct, lot, mn = float(inst["ctVal"]), float(inst["lotSz"]), float(inst["minSz"])
    sz = ap.floor_to(coins / ct, lot)
    if sz < mn or sz <= 0:
        return {"skip": f"меньше минимума OKX ({inst['minSz']} контр. ≈ {ap.usd(mn * ct * px)})" + (f" · {capped}" if capped else "")}
    coins = sz * ct
    r = coins * abs(worst - stop)
    return {"sz": ap.fmt(sz, inst["lotSz"]), "contracts": sz, "coins": coins, "notional": round(coins * px, 2), "lev": lev,
            "margin": round(coins * worst / lev, 2), "risk_usd": round(r, 2), "risk_pct": round(r / bud * 100, 3) if bud else 0.0,
            "stop": stop, "tp": tp, "px": px, "worst": worst, "capped": capped, "desk_risk_pct": round(frac * 100, 2)}


# ── orders ───────────────────────────────────────────────────────────────────────────────────────────────────────────
def _cid(prefix: str, now: int, seq: int) -> str:
    return f"{prefix}{now // 1000}{seq:03d}"[:32]


def set_leverage(ctx: Ctx, inst_id: str, lev: int, long: bool) -> None:
    body = {"instId": inst_id, "lever": str(lev), "mgnMode": "isolated"}
    if not ctx.net:
        body["posSide"] = "long" if long else "short"
    ctx.post("/api/v5/account/set-leverage", body)


def entry_body(ctx: Ctx, inst_id: str, inst: dict, long: bool, s: dict, slip: float, cl_id: str, algo_id: str, bid: float, ask: float) -> dict:
    tick = float(inst["tickSz"])
    px = ap.ceil_to(ask * (1 + slip), tick) if long else ap.floor_to(bid * (1 - slip), tick)
    b = {"instId": inst_id, "tdMode": "isolated", "side": "buy" if long else "sell", "ordType": "ioc", "px": ap.fmt(px, inst["tickSz"]),
         "sz": s["sz"], "clOrdId": cl_id}
    if not ctx.net:
        b["posSide"] = "long" if long else "short"
    att = {"attachAlgoClOrdId": algo_id, "slTriggerPx": ap.fmt(s["stop"], inst["tickSz"]), "slOrdPx": "-1", "slTriggerPxType": "last"}
    if s.get("tp"):
        att.update({"tpTriggerPx": ap.fmt(s["tp"], inst["tickSz"]), "tpOrdPx": "-1", "tpTriggerPxType": "last"})
    b["attachAlgoOrds"] = [att]
    return b


def fill(ctx: Ctx, body: dict, sleep) -> dict:
    """Send one order and read its final state. A network error after sending is resolved by the client order id."""
    inst_id, cl_id = body["instId"], body["clOrdId"]
    try:
        oid = (ctx.post("/api/v5/trade/order", body) or [{}])[0].get("ordId")
    except (urllib.error.URLError, TimeoutError, OSError):
        sleep(1.0)
        d = ctx.get("/api/v5/trade/order", {"instId": inst_id, "clOrdId": cl_id})
        if not d:
            raise
        oid = d[0].get("ordId")
    s = {}
    for _ in range(5):
        try:
            d = ctx.get("/api/v5/trade/order", {"instId": inst_id, "ordId": oid})
            s = d[0] if d else {}
        except ms.OkxError:
            s = {}
        if s and s.get("state") not in ("live", "partially_filled"):
            break
        sleep(0.4)
    return {"ordId": oid, "state": s.get("state") or "unknown", "filled": ms._f(s.get("accFillSz")) or 0.0, "avg_px": ms._f(s.get("avgPx")) or 0.0}


def exit_body(ctx: Ctx, inst_id: str, long: bool, sz: str, cl_id: str) -> dict:
    """A reduce-only market close (one-way mode) / a close of the same side (hedge mode) — never an opening in the other direction."""
    b = {"instId": inst_id, "tdMode": "isolated", "side": "sell" if long else "buy", "ordType": "market", "sz": sz, "clOrdId": cl_id}
    if ctx.net:
        b["reduceOnly"] = True
    else:
        b["posSide"] = "long" if long else "short"
    return b


def cancel_algos(ctx: Ctx, inst_id: str) -> int:
    al_ = [{"instId": inst_id, "algoId": a["algoId"]} for a in ctx.algos(inst_id) if a.get("algoId")]
    if al_:
        try:
            ctx.post("/api/v5/trade/cancel-algos", al_)
        except ms.OkxError:
            return 0
    ctx.forget(inst_id)
    return len(al_)


def close(ctx: Ctx, m: dict, sz: float | None, now: int, seq: int, sleep, why: str, log_path=None) -> bool:
    """Close the whole position (sz None) or part of it. Lead: the lead sub-positions are closed (whole only)."""
    inst_id, long = m["inst"], m["side"] == "LONG"
    p = ctx.positions().get(inst_id)
    if not p or p["long"] != long:
        return False
    if ctx.lead and sz is None:
        n = 0
        for s in ctx.subs(inst_id):
            ctx.post("/api/v5/copytrading/close-subposition", {"instType": "SWAP", "subPosId": s["subPosId"], "ordType": "market"})
            n += 1
        if n:
            log({"ev": "close", "inst": inst_id, "lead": True, "subs": n, "why": why}, log_path)
            cancel_algos(ctx, inst_id)
            return True
    q = p["sz"] if sz is None else min(sz, p["sz"])
    f = fill(ctx, exit_body(ctx, inst_id, long, ap.fmt(q, m["lot"]), _cid("cnqfx", now, seq)), sleep)
    log({"ev": "close" if sz is None else "reduce", "inst": inst_id, "sz": q, "why": why, **f}, log_path)
    if sz is None or f["filled"] >= p["sz"] * 0.999:
        cancel_algos(ctx, inst_id)
    else:
        ctx.forget(inst_id)
    return f["filled"] > 0


def ensure_stop(ctx: Ctx, m: dict, size_now: float, log_path=None) -> str:
    """The position's stop on OKX equals the desk stop and covers the whole position: «ok», «amended» or «placed». Raises StopFailed."""
    inst_id, long, tick = m["inst"], m["side"] == "LONG", float(m["tick"])
    stop, tp = m["stop"], m.get("tp")
    if ctx.lead:
        subs = ctx.subs(inst_id)
        if subs:
            n = 0
            for s in subs:
                if not _same(s.get("slTriggerPx"), stop, tick) or (tp and not _same(s.get("tpTriggerPx"), tp, tick)):
                    body = {"instType": "SWAP", "subPosId": s["subPosId"], "slTriggerPx": ap.fmt(stop, m["tick"]), "slTriggerPxType": "last"}
                    if tp:
                        body.update({"tpTriggerPx": ap.fmt(tp, m["tick"]), "tpTriggerPxType": "last"})
                    try:
                        ctx.post("/api/v5/copytrading/algo-order", body)
                    except ms.OkxError as e:
                        raise StopFailed(f"lead stop: {e}") from e
                    n += 1
            return "amended" if n else "ok"
    mine = [a for a in ctx.algos(inst_id) if a.get("slTriggerPx")]

    def covers(a):          # the whole position, exactly: a bigger stop could open the other side if OKX did not treat it as reduce-only
        return str(a.get("closeFraction") or "") == "1" or abs((ms._f(a.get("sz")) or 0) - size_now) < float(m["lot"]) * 0.5

    if any(_same(a.get("slTriggerPx"), stop, tick) and covers(a) for a in mine):
        return "ok"
    if mine:
        a = mine[0]
        body = {"instId": inst_id, "algoId": a["algoId"], "newSlTriggerPx": ap.fmt(stop, m["tick"]), "newSlOrdPx": "-1",
                "newSlTriggerPxType": "last"}
        if tp and a.get("tpTriggerPx") and not _same(a.get("tpTriggerPx"), tp, tick):
            body.update({"newTpTriggerPx": ap.fmt(tp, m["tick"]), "newTpOrdPx": "-1", "newTpTriggerPxType": "last"})
        if not covers(a):
            body["newSz"] = ap.fmt(size_now, m["lot"])
        try:
            ctx.post("/api/v5/trade/amend-algos", body)
            ctx.forget(inst_id)
            log({"ev": "stop_amended", "inst": inst_id, "stop": stop, "sz": size_now}, log_path)
            return "amended"
        except ms.OkxError as e:
            log({"ev": "stop_amend_failed", "inst": inst_id, "error": str(e)[:160]}, log_path)
            cancel_algos(ctx, inst_id)
    body = {"instId": inst_id, "tdMode": "isolated", "side": "sell" if long else "buy", "ordType": "conditional", "closeFraction": "1",
            "slTriggerPx": ap.fmt(stop, m["tick"]), "slOrdPx": "-1", "slTriggerPxType": "last",
            "algoClOrdId": f"cnqfs{int(time.time())}"[:32]}
    if tp:
        body.update({"tpTriggerPx": ap.fmt(tp, m["tick"]), "tpOrdPx": "-1", "tpTriggerPxType": "last"})
    if ctx.net:
        body["reduceOnly"] = True
    else:
        body["posSide"] = "long" if long else "short"
    try:
        ctx.post("/api/v5/trade/order-algo", body)
    except ms.OkxError as e:
        raise StopFailed(str(e)) from e
    ctx.forget(inst_id)
    log({"ev": "stop_placed", "inst": inst_id, "stop": stop}, log_path)
    return "placed"


def fund_usdc(ctx: Ctx, s: dict, cl_id: str, sleep, log_path=None) -> str | None:
    """The isolated margin of a USDC swap entry (+ fee room) as free USDC: what is missing is bought on USDC-USDT (limit IOC within
    OKX_SLIP). None = enough USDC now; otherwise why not (the entry waits for the next cycle; unused USDC goes back by the sweep)."""
    need = s["margin"] * 1.01 + s["notional"] * 0.002
    have = ctx.avail_ccy("USDC")
    if have < need:
        fee = ap.taker_fee(ctx.cl, ctx.env)
        c = ap.convert(ctx.cl, "buy", (need - have) / (1 - fee), cl_id, sleep, ap.limits(ctx.env)["slip"])
        log({"ev": "usdc_in", "need": round(need - have, 2), **c, "note": "купил USDC на USDC-USDT под маржу свопа в USDC"}, log_path)
        have = ctx.avail_ccy("USDC")
    if have < need * 0.999:
        return f"USDC под маржу не докупился ({ap.usd(have)} из {ap.usd(need)}) — вход на следующем цикле"
    return None


# ── one pass ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def connect(env=None, opener=None, sleep=time.sleep) -> ap.Okx:
    return ap.connect(env, opener, ap.load_state(env), sleep)


def want_map(desks: list) -> dict:
    """{"<desk>:<trade id>": (trade, desk equity, desk key)} for every open trade of the CRYPTO NOVA futures desks."""
    out = {}
    for k, d in desks:
        eq = float(d.get("equity") or d.get("start_equity") or 10_000)
        for t in d.get("open") or []:
            if t.get("id") and t.get("symbol") and t.get("side") in ("LONG", "SHORT") and t.get("stop") and t.get("entry"):
                out[f"{k}:{t['id']}"] = (t, eq, k)
    return out


def late(t: dict, px: float, mult: float, start: int, now: int, gap: float) -> str | None:
    """None when the trade may be joined now; otherwise why not. New = opened after the start and ≤ 45 min ago; an older one only
    while the price is at most `gap` worse than the desk's entry (higher for a long, lower for a short)."""
    o = int(t.get("opened") or 0)
    if o >= start and now - o <= FRESH_MS:
        return None
    entry = float(t["entry"]) / mult
    away = (px / entry - 1) if t.get("side") == "LONG" else (1 - px / entry)
    if away <= gap:
        return None
    x = f"{away * 100:.2f}".replace(".", ",")
    if o < start:
        return f"пропущено: сделка открыта раньше, цена ушла на {x}% от входа системы (порог {gap * 100:g}%)".replace(".", ",")
    return f"пропущено: сигнал открыт {(now - o) / 60_000:.0f} мин назад, цена ушла на {x}% от входа системы".replace(".", ",")


def decide(ctx: Ctx, t: dict, desk_eq: float, part: float, bud: float, cap: dict, pos: dict, avail: float, slip: float,
           start: int = 0, now: int = 0, gap: float = JOIN_GAP) -> dict:
    """Everything about one new desk trade: the order it gets or why it gets none ({"skip", "final"}). Used by step() AND by the
    plan, so the plan shows exactly what the window would place."""
    inst_id, mult = inst_of(str(t["symbol"]))
    long = t.get("side") == "LONG"
    base = {"inst": inst_id, "mult": mult, "long": long}
    if len(pos) >= cap["max_pos"]:
        return {**base, "skip": f"уже {len(pos)} открытых позиций — предел {cap['max_pos']}", "final": False}
    if inst_id in pos:
        return {**base, "skip": "по этому инструменту уже есть позиция — вторую не открываю", "final": True}
    if ctx.lead:
        base["copy"] = inst_id in ctx.lead_insts      # not yet a lead instrument → step() adds it before the entry (add_lead)
    inst = ctx.inst(inst_id)
    alt = inst_id[:-len("-USDT-SWAP")] + "-USDC-SWAP"
    if not inst and not ctx.lead and usdc_on(ctx.env) and ap.usdt_account(ctx.cl, ctx.env):
        ainst = ctx.inst(alt)                       # no USDT swap of this coin → its USDC-margined swap (22.10)
        if ainst:
            if alt in pos:
                return {**base, "inst": alt, "skip": "по этому инструменту уже есть позиция — вторую не открываю", "final": True}
            inst_id, inst = alt, ainst
            base = {**base, "inst": alt, "settle": "USDC"}
            avail = avail + ctx.avail_ccy("USDC")   # USDT turns into USDC margin right before the entry
    if not inst:
        return {**base, "skip": f"на OKX нет свопа {inst_id}" + (f" (и {alt})" if not ctx.lead and usdc_on(ctx.env) else ""), "final": True}
    tk = ctx.ticker(inst_id) or {}
    bid, ask = ms._f(tk.get("bidPx")) or ms._f(tk.get("last")), ms._f(tk.get("askPx")) or ms._f(tk.get("last"))
    px = ask if long else bid
    if not px:
        return {**base, "skip": "нет цены на OKX", "final": False}
    if abs(px * mult / float(t["entry"]) - 1) > PX_TOL:
        return {**base, "skip": f"цена {inst_id} отличается от входа сигнала больше чем на 15% — возможно, другой токен", "final": True}
    why = late(t, px, mult, start, now or int(time.time() * 1000), gap)
    if why:
        return {**base, "skip": why, "final": False, "late": True}
    sz = size(t, desk_eq, part, bud, cap, inst, px, mult, sum(p["notional"] for p in pos.values()), avail, slip)
    if sz.get("skip"):
        return {**base, "skip": sz["skip"], "final": True}
    return {**base, "inst_info": inst, "bid": bid or px, "ask": ask or px, "s": sz}


def step(env=None, opener=None, desks: list | None = None, now: int | None = None, sleep=time.sleep, cl: ap.Okx | None = None,
         log_path: str | None = None, save: bool = True, force: bool = False) -> dict:
    """One futures pass: exits, stop / target / partial updates, then new entries. desks = [(key, desk)] (default: fetched)."""
    now = int(now or time.time() * 1000)
    res = {"opened": 0, "closed": 0, "reduced": 0, "stops": 0, "gone": 0, "errors": 0, "skipped": [], "why": None, "t": now}
    if not (enabled(env) or force):
        res["why"] = "фьючерсы выключены (OKX_FUTURES=off)"
        return res
    if ap.stopped(env):
        res["why"] = "включён стоп (--stop) — фьючерсы ничего не делают; позиции остаются со своими стопами на OKX"
        return res
    g = ap.lease.gate(env, now)                 # 22.10: never two autopilots on one account (okx_lease.py)
    if not g["ok"]:
        res["why"] = g["why"] + " · позиции остаются со своими стопами на OKX"
        return res
    st = load_state(env)
    st.setdefault("started", now)
    for k in ("trades", "skip", "tries", "done"):
        st.setdefault(k, {})
    cl = cl or connect(env, opener, sleep)
    cl.lease_deadline = ap.lease.deadline(g, now)
    cfg = cl.config()
    bad = refuse(cfg, cl, env)
    if bad:
        res["why"] = bad
        if st.get("last_refusal") != bad:
            log({"ev": "refused", "why": bad}, log_path)
        st["last_refusal"] = bad
        if save:
            save_state(st, env)
        return res
    st.pop("last_refusal", None)
    st.setdefault(start_key(cl, env), now)
    start = int(st[start_key(cl, env)])
    ctx = Ctx(cl, cfg, env)
    ds = desks if desks is not None else fetch_desks(env, st)
    if desks is None and ds and not desk_src(env)[1] and ds[0][0] in ph.MAIN_KEYS and st.get("main") != ds[0][0]:   # 28.10: main desk
        if st.get("main"):
            log({"ev": "desk_main", "from": st["main"], "to": ds[0][0], "note": "основной стол фьючерсов сменился (web/desk_select.json)"},
                log_path)
        st["main"] = ds[0][0]
    want = want_map(ds)
    slip = ap.limits(env)["slip"]
    seq = 0
    via_used = any(m.get("settle") == "USDC" for m in st["trades"].values())     # a USDC swap now or this pass → sweep at its end
    ctx.positions()

    # 1. the desk closed it → close here (reduce-only); or OKX already closed it (stop / target)
    for tid, m in list(st["trades"].items()):
        if tid in want:
            continue
        seq += 1
        try:
            if close(ctx, m, None, now, seq, sleep, "сигнал закрыт", log_path):
                res["closed"] += 1
            else:
                cancel_algos(ctx, m["inst"])
                log({"ev": "gone", "id": tid, "inst": m["inst"], "note": "закрыта на OKX (стоп или цель)"}, log_path)
                res["gone"] += 1
            st["done"][tid] = st["trades"].pop(tid).get("inst")
        except Exception as e:  # noqa: BLE001
            res["errors"] += 1
            log({"ev": "close_error", "id": tid, "inst": m["inst"], "error": str(e)[:200]}, log_path)
    pos = ctx.positions(fresh=True)

    # 2. still open on the desk: partial exits, a moved stop / target, the stop really is on OKX
    for tid, m in list(st["trades"].items()):
        if tid not in want:
            continue
        t = want[tid][0]
        p = pos.get(m["inst"])
        if not p or p["long"] != (m["side"] == "LONG"):
            cancel_algos(ctx, m["inst"])
            log({"ev": "gone", "id": tid, "inst": m["inst"], "note": "закрыта на OKX (стоп или цель)"}, log_path)
            res["gone"] += 1
            st["done"][tid] = st["trades"].pop(tid).get("inst")
            continue
        try:
            size_now = p["sz"]
            q = float(t.get("qty") or 0)
            ql = float(t["qty_left"]) if t.get("qty_left") is not None else q
            ratio = ql / q if q > 0 else 1.0
            if ratio < 0.999 and not (ctx.lead and ctx.subs(m["inst"])):    # a lead position closes whole; an ordinary one in parts
                tgt = ap.floor_to(m["c0"] * ratio, float(m["lot"]))
                cut = size_now if tgt < float(m["min"]) else size_now - tgt
                if cut >= float(m["min"]) - 1e-12:
                    seq += 1
                    if close(ctx, m, None if cut >= size_now else cut, now, seq, sleep, "частичный выход по сигналу", log_path):
                        res["reduced"] += 1
                    pos = ctx.positions(fresh=True)
                    p = pos.get(m["inst"])
                    if not p:
                        st["done"][tid] = st["trades"].pop(tid).get("inst")
                        continue
                    size_now, m["chk"] = p["sz"], 0
            tick, long = float(m["tick"]), m["side"] == "LONG"
            ns = _round_px(float(t.get("stop_now") or t["stop"]) / m["mult"], tick, long)
            ntp = target(t)
            ntp = _round_px(ntp / m["mult"], tick, long) if ntp else None
            changed = not _same(ns, m["stop"], tick) or (ntp is not None and m.get("tp") is not None and not _same(ntp, m["tp"], tick))
            if changed:
                log({"ev": "desk_moved", "id": tid, "inst": m["inst"], "stop": ns, "tp": ntp, "old_stop": m["stop"]}, log_path)
                m["stop"], m["tp"] = ns, (ntp if ntp is not None else m.get("tp"))
            if changed or now - int(m.get("chk") or 0) >= VERIFY_MS:
                try:
                    r = ensure_stop(ctx, m, size_now, log_path)
                except StopFailed as e:
                    seq += 1
                    close(ctx, m, None, now, seq, sleep, "стоп-лосс не ставится — позиция закрыта", log_path)
                    log({"ev": "stop_failed_closed", "id": tid, "inst": m["inst"], "error": str(e)[:200]}, log_path)
                    res["errors"] += 1
                    st["done"][tid] = st["trades"].pop(tid).get("inst")
                    continue
                m["chk"] = now
                if r != "ok":
                    res["stops"] += 1
        except Exception as e:  # noqa: BLE001
            res["errors"] += 1
            log({"ev": "update_error", "id": tid, "inst": m["inst"], "error": str(e)[:200]}, log_path)

    # 3. new desk trades
    for k in ("skip", "tries", "done"):
        st[k] = {x: v for x, v in st[k].items() if x in want}
    fresh = [(tid, w) for tid, w in want.items() if tid not in st["trades"] and tid not in st["skip"] and tid not in st["done"]]
    if fresh:
        cap = caps(env)
        day = time.strftime("%Y-%m-%d", time.gmtime(now / 1000))
        bud = budget(cl, st, env, now)
        if st.get("day_stop") != day:
            dp = day_pnl(ctx, now)
            st["day_pnl"], st["day"] = dp, day
            if bud["usd"] > 0 and dp <= -cap["day_loss"] * bud["usd"]:
                st["day_stop"] = day
                log({"ev": "day_stop", "day_pnl": dp, "budget": bud["usd"]}, log_path)
        if st.get("day_stop") == day:
            res["skipped"] += [{"id": tid, "why": "дневной стоп: убыток дня ≥ 3% бюджета фьючерсов — новых входов до завтра (UTC)"} for tid, _ in fresh]
            fresh = []
        pos = ctx.positions(fresh=True)
        avail = ctx.avail_usdt() if fresh else 0.0
        part = bud["usd"] / max(1, len(ds))
        for tid, (t, deq, dk) in sorted(fresh, key=lambda x: int(x[1][0].get("opened") or 0)):
            dcs = decide(ctx, t, deq, part, bud["usd"], cap, pos, avail, slip, start, now, join_gap(env))
            if dcs.get("skip"):
                res["skipped"].append({"id": tid, "inst": dcs["inst"], "why": dcs["skip"]})
                if dcs["final"]:
                    st["skip"][tid] = dcs["skip"]
                    log({"ev": "skip", "id": tid, "inst": dcs["inst"], "why": dcs["skip"]}, log_path)
                continue
            inst_id, inst, s, long = dcs["inst"], dcs["inst_info"], dcs["s"], dcs["long"]
            seq += 1
            if dcs.get("settle") == "USDC":
                via_used = True
                why = fund_usdc(ctx, s, _cid("cnqfu", now, seq), sleep, log_path)
                if why:
                    st["tries"][tid] = st["tries"].get(tid, 0) + 1
                    res["skipped"].append({"id": tid, "inst": inst_id, "why": why})
                    log({"ev": "usdc_margin_short", "id": tid, "inst": inst_id, "note": why}, log_path)
                    if st["tries"][tid] >= MAX_TRIES:
                        st["skip"][tid] = why
                    continue
            if ctx.lead and not dcs.get("copy"):
                dcs["copy"] = add_lead(ctx, st, inst_id, now, log_path)   # 22.10: make it copyable first; refused → ordinary
            try:
                set_leverage(ctx, inst_id, s["lev"], long)
                body = entry_body(ctx, inst_id, inst, long, s, slip, _cid("cnqf", now, seq), _cid("cnqfs", now, seq), dcs["bid"], dcs["ask"])
                f = fill(ctx, body, sleep)
            except Exception as e:  # noqa: BLE001
                res["errors"] += 1
                st["tries"][tid] = st["tries"].get(tid, 0) + 1
                log({"ev": "open_error", "id": tid, "inst": inst_id, "error": str(e)[:200]}, log_path)
                if st["tries"][tid] >= MAX_TRIES:
                    st["skip"][tid] = f"OKX не принял ордер {MAX_TRIES} раза: {str(e)[:80]}"
                continue
            if f["filled"] <= 0:
                st["tries"][tid] = st["tries"].get(tid, 0) + 1
                log({"ev": "not_filled", "id": tid, "inst": inst_id, "px": body["px"]}, log_path)
                if st["tries"][tid] >= MAX_TRIES:
                    st["skip"][tid] = f"цена ушла: IOC-ордер не исполнился {MAX_TRIES} раза"
                continue
            real_risk = f["filled"] * float(inst["ctVal"]) * abs((f["avg_px"] or s["worst"]) - s["stop"])
            m = {"inst": inst_id, "sym": str(t["symbol"]), "desk": dk, "side": "LONG" if long else "SHORT", "c0": f["filled"],
                 **({"copy": bool(dcs.get("copy"))} if ctx.lead else {}),
                 **({"settle": "USDC"} if dcs.get("settle") == "USDC" else {}),
                 "stop": s["stop"], "tp": s.get("tp"), "mult": dcs["mult"], "lot": inst["lotSz"], "min": inst["minSz"],
                 "tick": inst["tickSz"], "ct": inst["ctVal"], "opened": now, "lev": s["lev"], "entry": f["avg_px"] or s["px"]}
            st["trades"][tid] = m
            ctx.forget(inst_id)
            log({"ev": "open", "id": tid, "inst": inst_id, "side": m["side"], "sz": f["filled"], "avg_px": f["avg_px"], "stop": s["stop"],
                 **({"copy": m["copy"]} if ctx.lead else {}),
                 "tp": s.get("tp"), "lev": s["lev"], "risk_usd": round(real_risk, 2), "risk_cap": round(bud["usd"] * cap["risk"], 2),
                 "budget": bud["usd"]}, log_path)
            pos = ctx.positions(fresh=True)
            try:
                ensure_stop(ctx, m, (pos.get(inst_id) or {}).get("sz") or f["filled"], log_path)
                m["chk"] = now
            except StopFailed as e:
                seq += 1
                close(ctx, m, None, now, seq, sleep, "стоп-лосс не поставился — позиция закрыта сразу", log_path)
                log({"ev": "stop_failed_closed", "id": tid, "inst": inst_id, "error": str(e)[:200]}, log_path)
                st["done"][tid] = (st["trades"].pop(tid, None) or {}).get("inst")
                res["errors"] += 1
                pos = ctx.positions(fresh=True)
                continue
            res["opened"] += 1
            pos = ctx.positions(fresh=True)
            avail = ctx.avail_usdt()
    if via_used:
        ap.sweep(cl, env, _cid("cnqfb", now, seq + 1), sleep, log_path)      # freed / unused USDC back to USDT at the end of the pass
    st["last"] = {k: res[k] for k in ("t", "opened", "closed", "reduced", "stops", "gone", "errors")}
    st["last"]["skipped"] = len(res["skipped"])
    if save:
        save_state(st, env)
    return res


def add_lead(ctx: Ctx, st: dict, inst_id: str, now: int, log_path=None) -> bool:
    """Add one swap to the lead instruments (set-instruments, instType SWAP, the full list: current + this one) right before its
    entry. True = now a lead instrument (the entry is copied). A refusal is remembered for a day — the entry goes ordinary."""
    ref = st.setdefault("lead_refused", {})
    if now - int(ref.get(inst_id) or 0) < ld.DAY_MS:
        return False
    try:                                        # the list OKX has right now — the POST replaces it, so nothing may be missing
        cur = {str(x.get("instId")) for x in ctx.get(ld.INSTS, {"instType": "SWAP"}) or [] if str(x.get("enabled")).lower() == "true"}
    except ms.OkxError:
        cur = set()
    cur = cur or set(ctx.lead_insts)
    if not cur:                                 # the lead list is unreadable: a POST could replace it with one swap — never
        log({"ev": "lead_add", "inst": inst_id, "ok": False, "note": "список ведущего не прочитался — вход обычный, не копируется"}, log_path)
        return False
    if inst_id in cur:
        ctx.lead_insts = cur
        return True
    en, fails = ld.add_insts(ctx.cl, "SWAP", cur, {inst_id})
    ctx.lead_insts = en or ctx.lead_insts
    ok = inst_id in en and inst_id not in fails
    if not ok:
        ref[inst_id] = now
    log({"ev": "lead_add", "inst": inst_id, "ok": ok, **({"why": fails.get(inst_id)} if not ok else {}),
         "note": "инструмент добавлен в список ведущего — вход копируется" if ok else "OKX не добавил инструмент — вход обычный, не копируется"},
        log_path)
    return ok


def close_all(env=None, opener=None, now: int | None = None, sleep=time.sleep, cl=None, log_path=None) -> str:
    """--fut-close: every position the futures autopilot opened is closed now (reduce-only market), its stops cancelled. Works with
    the kill switch on (it only reduces risk); the key's world must still match the window's mode."""
    now = int(now or time.time() * 1000)
    st = load_state(env)
    trades = st.get("trades") or {}
    if not trades:
        return "Фьючерсы: открытых позиций автопилота нет."
    cl = cl or connect(env, opener, sleep)
    cfg = cl.config()
    bad = ap.world_ok(cl, env)
    if bad:
        return "Фьючерсы: " + bad
    ctx = Ctx(cl, cfg, env)
    n = 0
    for seq, (tid, m) in enumerate(list(trades.items()), 1):
        try:
            if close(ctx, m, None, now, seq, sleep, "закрыть всё (--fut-close)", log_path):
                n += 1
            else:
                cancel_algos(ctx, m["inst"])
            st.setdefault("done", {})[tid] = trades.pop(tid).get("inst")      # never re-opened, even if the desk still holds it
        except Exception as e:  # noqa: BLE001
            log({"ev": "close_error", "id": tid, "error": str(e)[:200]}, log_path)
    st["trades"] = trades
    save_state(st, env)
    left = f" Не удалось закрыть: {len(trades)} — проверь в приложении OKX." if trades else ""
    return f"Фьючерсы: закрыто позиций {n}.{left}"


# ── words for the owner ──────────────────────────────────────────────────────────────────────────────────────────────
def check_lines(cl: ap.Okx, cfg: dict, env=None) -> list[str]:
    """`--check`: is this account ready for the futures part (mode, permission, budget, leverage cap, lead status)."""
    cap = caps(env)
    out = [f"Фьючерсы: {'включены' if enabled(env) else 'ВЫКЛЮЧЕНЫ строкой OKX_FUTURES=off'}"]
    out.append("  режим счёта: " + mode_text(cfg) + " · позиции: " +
               ("в одну сторону (one-way), выходы только reduce-only" if str(cfg.get("posMode") or "net_mode") != "long_short_mode"
                else "лонг и шорт отдельно (hedge)"))
    if str(cfg.get("acctLv") or "") != "2":
        out.append("  !!! Нужен режим «Фьючерсы»: OKX → войти в субаккаунт → Настройки → Режим счёта → «Фьючерсы». Переключается, "
                   "только когда нет открытых ордеров и позиций; первый раз OKX может попросить пройти короткий тест.")
    role = str(cfg.get("roleType") or "0")
    out.append("  ведущий трейдер: " + {"1": "да (roleType 1) — стопы и выходы через позиции ведущего; своп не из списка ведущего добавляется "
               "в список перед входом (OKX откажет — обычный вход, не копируется)", "2": "НЕТ — на счёте включено "
               "копирование чужих сделок; автопилот фьючерсов здесь работать не будет"}.get(role, "нет")
               + (" · OKX_IS_LEAD=1 — режим ведущего включён вручную" if lead_forced(env) and role != "1" else ""))
    bad = refuse(cfg, cl, env)
    if bad:
        out.append("  ордера не пойдут: " + bad)
    try:
        b = budget(cl, {}, env, fresh=True)
        out.append(f"  бюджет фьючерсов: {ap.usd(b['usd'])} = {al._p(b['share'])} счёта {ap.usd(b['total'])} · плечо не выше {cap['lev']}"
                   f" · риск на сделку не больше {ap.usd(b['usd'] * cap['risk'])} ({cap['risk'] * 100:g}%; сама сделка — с риском стола, как у системы) · дневной стоп {ap.usd(b['usd'] * cap['day_loss'])} (3%)"
                   f" · не больше {cap['max_pos']} позиций")
    except Exception as e:  # noqa: BLE001
        out.append(f"  бюджет фьючерсов не посчитан: {str(e)[:120]}")
    ctx = Ctx(cl, cfg, env)
    out.append("  свопы OKX: " + ("BTC-USDT-SWAP отвечает" if ctx.inst("BTC-USDT-SWAP") else "BTC-USDT-SWAP не ответил"))
    st = load_state(env)
    try:
        names = ", ".join(k for k, _ in fetch_desks(env, st))
    except Exception:  # noqa: BLE001
        names = "не загрузились"
    out.append(f"  сигналы: CRYPTO NOVA (столы {names}) · открыто автопилотом: {len(st.get('trades') or {})}"
               + (" · сегодня дневной стоп" if st.get("day_stop") == time.strftime("%Y-%m-%d", time.gmtime()) else ""))
    return out


def plan_lines(cl: ap.Okx, cfg: dict, env=None, desks: list | None = None, now: int | None = None) -> list[str]:
    """`--plan`: every desk trade, and the exact order the window would place for it now (the same decide() as step). Places nothing."""
    now = int(now or time.time() * 1000)
    st = load_state(env)
    cap = caps(env)
    L = ["ФЬЮЧЕРСЫ · CRYPTO NOVA (отобранные сигналы)" + ("" if enabled(env) else " — ВЫКЛЮЧЕНЫ (OKX_FUTURES=off), ниже только расчёт")]
    bad = refuse(cfg, cl, env)
    if bad:
        L.append("  ОРДЕРОВ НЕ БУДЕТ: " + bad)
    ctx = Ctx(cl, cfg, env)
    try:
        b = budget(cl, {}, env, now, fresh=True)
    except Exception as e:  # noqa: BLE001
        return L + [f"  бюджет не посчитан: {str(e)[:120]}"]
    try:
        ds = desks if desks is not None else fetch_desks(env, st)
    except Exception as e:  # noqa: BLE001
        return L + [f"  сигналы не загрузились: {str(e)[:120]} — новых позиций не будет"]
    part = b["usd"] / max(1, len(ds))
    L.append(f"  бюджет {ap.usd(b['usd'])} ({al._p(b['share'])} счёта) · столы: {', '.join(k for k, _ in ds) or 'нет'} — по {ap.usd(part)} на стол"
             f" · плечо ≤ {cap['lev']} (изолированная маржа) · риск ≤ {ap.usd(b['usd'] * cap['risk'])} на сделку · дневной стоп "
             f"{ap.usd(b['usd'] * cap['day_loss'])} · до {cap['max_pos']} позиций · стоп-лосс у каждой позиции на OKX")
    want = want_map(ds)
    if not want:
        L.append("  У столов сейчас нет открытых сделок — позиций нет. Новые сигналы окно повторит в течение минуты.")
        return L
    pos = ctx.positions()
    start = int(st.get(start_key(cl, env)) or now)          # before the first live run: every open desk trade is «раньше»
    avail = ctx.avail_usdt()
    slip = ap.limits(env)["slip"]
    for tid, (t, deq, dk) in sorted(want.items(), key=lambda x: int(x[1][0].get("opened") or 0)):
        long = t.get("side") == "LONG"
        age = (now - int(t.get("opened") or 0)) / 60_000
        head = f"  [{dk}] {t['symbol']} {'ЛОНГ' if long else 'ШОРТ'} · сигнал открыт {age:.0f} мин назад по {float(t['entry']):.6g}"
        if tid in (st.get("trades") or {}):
            m = st["trades"][tid]
            L.append(f"{head} → уже ведём: {m['c0']:g} контр. {m['inst']}, стоп {m['stop']:g}" + (f", цель {m['tp']:g}" if m.get("tp") else "")
                     + ((" · копируется" if ctx.subs(m["inst"]) else " · не копируется (обычная позиция)") if ctx.lead else ""))
            continue
        if tid in (st.get("skip") or {}):
            L.append(f"{head} → пропущен: {st['skip'][tid]}")
            continue
        if tid in (st.get("done") or {}):
            L.append(f"{head} → уже закрыт на OKX (стоп или цель) — повторно не открываем")
            continue
        dcs = decide(ctx, t, deq, part, b["usd"], cap, pos, avail, slip, start, now, join_gap(env))
        if dcs.get("skip"):
            L.append(f"{head} → {dcs['skip'] if dcs.get('late') else 'не открываем: ' + dcs['skip']}")
            continue
        s = dcs["s"]
        L.append(f"{head} → ОРДЕР: {'купить' if long else 'продать'} {s['sz']} контр. {dcs['inst']} ≈ {ap.usd(s['notional'])}, "
                 f"лимит IOC не хуже {s['worst']:.6g}, плечо {s['lev']}, маржа {ap.usd(s['margin'])}, стоп-лосс {s['stop']:.6g}"
                 + (f", цель {s['tp']:.6g}" if s.get("tp") else "") + f" · риск {ap.usd(s['risk_usd'])} ({s['risk_pct']:g}% бюджета; "
                 f"у стола {s['desk_risk_pct']:g}%)" + (f" · урезано: {s['capped']}" if s.get("capped") else "")
                 + (" · через USDC: маржа USDT → USDC перед входом, после закрытия USDC → USDT" if dcs.get("settle") == "USDC" else "")
                 + ((" · копируется" if dcs.get("copy") else " · копируется после добавления инструмента в список ведущего "
                     "(если OKX откажет — обычный вход, не копируется)") if ctx.lead else ""))
    return L


def status(env=None) -> dict:
    """For the encrypted owner card: no network, no keys."""
    st = load_state(env)
    cap = caps(env)
    b = st.get("budget") or {}
    return {"enabled": enabled(env), "stopped": ap.stopped(env), "live": ap.live(env), "budget_usd": b.get("usd"), "share": b.get("share"),
            "lev": cap["lev"], "risk_pct": cap["risk"] * 100, "max_pos": cap["max_pos"], "day_loss_pct": cap["day_loss"] * 100,
            "day_pnl": st.get("day_pnl"), "day_stop": st.get("day_stop") == time.strftime("%Y-%m-%d", time.gmtime()),
            "open": [{"sym": m.get("sym"), "side": m.get("side"), "sz": m.get("c0"), "stop": m.get("stop"), "tp": m.get("tp"),
                      "lev": m.get("lev")} for m in (st.get("trades") or {}).values()],
            "last": st.get("last")}


def summary(r: dict) -> str | None:
    """One Russian line for the window when something happened."""
    if r.get("why"):
        return None
    parts = [f"{k} {r[v]}" for k, v in (("открыто", "opened"), ("закрыто", "closed"), ("частично", "reduced"),
                                          ("стопов обновлено", "stops"), ("закрыто биржей", "gone"), ("ошибок", "errors")) if r.get(v)]
    return ("Фьючерсы OKX: " + ", ".join(parts)) if parts else None
