"""Шлюз входов риск-слоя: каждый демо-стол спрашивает его перед КАЖДЫМ новым входом (20.10.2026).

  allow_entry(desk, sym, side, notional_usd, now) -> (ok, reason_ru)

Порядок проверок (первая сработавшая — причина отказа):
  1. аварийный выключатель data/kill_switch.json (cli: kill on|off|status);
  2. конвейер data/conveyor_state.json: стол на паузе или стратегия снята (stage = retired) → нет; нет файла / стола → можно;
  3. состояние риск-слоя data/risk_state.json (шаг risk каждую минуту): старше 10 мин или не читается → нет (не торгуем вслепую);
     файла ещё нет (первый запуск, GitHub-подстраховка) → можно;
  4. стоп дня (−3 % за день UTC) и стоп по просадке (−15 % от пика, до сброса владельцем);
  5. рыночные данные старше 10 мин;
  6. плечо счёта после входа > 3× (и у зеркальных счетов, см. limits.MIRRORS);
  7. монета после входа > 25 % брутто; 8. кластер похожих монет после входа > 50 % брутто (по чистой позиции).
Выходы (стопы, цели, время) шлюз не трогает — только новые входы. Одобренные входы этого процесса копятся в памяти (_LEDGER),
поэтому корзина из 6 монет в одном шаге видит и свои же первые сделки. Отказ стол записывает в свою таблицу risk_skips
(record_skip) — сигнал помечен как пропущенный с причиной и больше не открывается. Шлюз никогда не бросает исключение."""
from __future__ import annotations

import datetime as dt
import json
import os
import time

from . import limits as L

_CACHE: dict = {}
_LEDGER: list = []          # [(state_version, account, sym, signed_notional)] — входы, одобренные в этом процессе
_LEDGER_VER = [None]


# ───────────────────────────────────────────────────────────────────────────────────────────── files
def read_json(p: str):
    """(doc, error) — doc is None when the file is missing (error None) or unreadable (error text). Cached by inode/mtime/size."""
    try:
        st = os.stat(p)
    except FileNotFoundError:
        return None, None
    except OSError as e:
        return None, f"{type(e).__name__}: {e}"[:200]
    key = (st.st_ino, st.st_mtime_ns, st.st_size)
    hit = _CACHE.get(p)
    if hit and hit[0] == key:
        return hit[1], hit[2]
    try:
        with open(p, encoding="utf-8") as f:
            doc, err = json.load(f), None
    except Exception as e:  # noqa: BLE001
        doc, err = None, f"{type(e).__name__}: {str(e)[:150]}"
    _CACHE[p] = (key, doc, err)
    return doc, err


def write_json(p: str, doc: dict) -> None:
    """atomic: a reader sees the old file or the new one, never half of it"""
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = f"{p}.tmp{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    os.replace(tmp, p)


def log_event(kind: str, data: dict, base: str | None = None, now: int | None = None) -> None:
    """one line in data/risk_log.jsonl (never raises)"""
    try:
        p = L.path(L.LOG_FILE, base)
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        if os.path.exists(p) and os.path.getsize(p) > 5_000_000:      # keep it small: one older file, then start over
            os.replace(p, p + ".1")
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": now or _wall(), "kind": kind, **data}, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _wall() -> int:
    return int(time.time() * 1000)


def hm(ms) -> str:
    try:
        return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime("%d.%m %H:%M UTC")
    except Exception:  # noqa: BLE001
        return "?"


def utc_day(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


# ───────────────────────────────────────────────────────────────────────────────────────────── kill switch
def kill_status(base: str | None = None) -> dict:
    doc, err = read_json(L.path(L.KILL_FILE, base))
    if err:      # a file that exists but cannot be read is treated as ON — someone meant to stop trading
        return {"on": True, "since": None, "reason": f"файл выключателя не читается ({err})", "error": err}
    doc = doc or {}
    return {"on": bool(doc.get("on")), "since": doc.get("since"), "reason": doc.get("reason") or "", "by": doc.get("by"),
            "changed_at": doc.get("changed_at"), "history": doc.get("history") or []}


def set_kill(on: bool, reason: str = "", by: str = "cli", base: str | None = None, now: int | None = None) -> dict:
    """switch on/off; every change goes to the file's history and to data/risk_log.jsonl"""
    now = now or _wall()
    cur = kill_status(base)
    hist = (cur.get("history") or [])[-19:] + [{"t": now, "on": bool(on), "reason": reason, "by": by}]
    doc = {"on": bool(on), "since": (cur.get("since") if cur.get("on") and cur.get("since") else now) if on else None,
           "reason": reason if on else "", "by": by, "changed_at": now, "history": hist}
    write_json(L.path(L.KILL_FILE, base), doc)
    log_event("kill_on" if on else "kill_off", {"reason": reason, "by": by, "was_on": bool(cur.get("on"))}, base, now)
    return doc


# ───────────────────────────────────────────────────────────────────────────────────────────── conveyor
def conveyor_block(desk: str, base: str | None = None) -> str | None:
    """the conveyor's verdict for a desk: None = allowed (no file, no desk, a malformed file), else the reason (Russian)"""
    doc, _err = read_json(L.path(L.CONVEYOR_FILE, base))
    try:
        d = ((doc or {}).get("desks") or {}).get(desk)
    except AttributeError:
        return None
    if not isinstance(d, dict):
        return None
    why = str(d.get("reason_ru") or "").strip()
    if d.get("paused") is True:
        return "конвейер: стол на паузе" + (f" — {why}" if why else "")
    if str(d.get("stage") or "").lower() == "retired":
        return "конвейер: стратегия снята" + (f" — {why}" if why else "")
    return None


# ───────────────────────────────────────────────────────────────────────────────────────────── skips (the desk's record)
def record_skip(conn, desk: str, sig_id: str, sym: str, side: str, notional: float, reason: str, now: int) -> bool:
    """the desk's honest record of a signal the risk layer did not let in; True when it is new (False: already recorded).
    No commit here — the desk commits with its own rows."""
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS risk_skips(desk TEXT, id TEXT, t INTEGER, symbol TEXT, side TEXT, notional REAL, "
                     "reason TEXT, PRIMARY KEY(desk, id))")
        cur = conn.execute("INSERT OR IGNORE INTO risk_skips VALUES(?,?,?,?,?,?,?)",
                           (desk, sig_id, int(now), sym, side, round(float(notional or 0), 2), reason[:300]))
        return cur.rowcount > 0
    except Exception as e:  # noqa: BLE001
        print("risk_skips:", e)
        return False


def skipped_ids(conn, desk: str) -> set:
    try:
        return {r[0] for r in conn.execute("SELECT id FROM risk_skips WHERE desk=?", (desk,))}
    except Exception:  # noqa: BLE001
        return set()


# ───────────────────────────────────────────────────────────────────────────────────────────── the gate
def reset_ledger() -> None:
    _LEDGER.clear()
    _LEDGER_VER[0] = None


def _sign(side: str) -> int:
    s = str(side or "").upper()
    return 1 if s in ("LONG", "BUY") else -1 if s in ("SHORT", "SELL") else 0


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%".replace(".0%", "%")


def allow_entry(desk: str, sym: str, side: str, notional_usd: float, now: int, *, base: str | None = None,
                wall: int | None = None) -> tuple[bool, str]:
    """May desk `desk` open `side` `sym` for `notional_usd` (demo dollars) at `now` (ms)? → (ok, reason_ru).
    sym "*" = a whole basket (only the account-level checks). Never raises: an internal error blocks (fail-closed)."""
    try:
        return _allow(desk, str(sym or "").upper(), side, float(notional_usd or 0), int(now), base, wall)
    except Exception as e:  # noqa: BLE001
        return False, f"ошибка риск-слоя ({type(e).__name__}: {str(e)[:80]}) — вход не открыт"


def _allow(desk, sym, side, notional, now, base, wall):
    wall = wall if wall is not None else _wall()
    if abs(now - wall) > L.REPLAY_H * 3_600_000:          # a test or a replay of history: the present-day limits do not apply
        return True, ""
    k = kill_status(base)
    if k.get("on"):
        return False, "аварийный выключатель включён" + (f" (с {hm(k['since'])})" if k.get("since") else "") + " — новые входы запрещены"
    cb = conveyor_block(desk, base)
    if cb:
        return False, cb
    st, err = read_json(L.path(L.STATE_FILE, base))
    if err:
        return False, f"состояние риск-слоя не читается ({err[:60]}) — вход не открыт"
    if st is None:
        return True, ""                                    # the risk step has not run yet here (first start, GitHub failover)
    upd = int(st.get("updated_at") or 0)
    age = (now - upd) / 60_000
    if age > L.STATE_MAX_AGE_MIN:
        return False, f"риск-слой не обновлялся {age:.0f} мин (лимит {L.STATE_MAX_AGE_MIN:g}) — вход не открыт"
    latch = st.get("latch") or {}
    dl = latch.get("daily")
    if dl and dl.get("day") == utc_day(now):
        return False, (f"дневной стоп: все демо-счета за день {_pct(dl.get('value') or 0)} (лимит {_pct(L.DAILY_LOSS_HALT)}) — "
                       f"новые входы с 00:00 UTC")
    dd = latch.get("drawdown")
    if dd:
        return False, (f"стоп по просадке: {_pct(dd.get('value') or 0)} от пика (лимит {_pct(L.DRAWDOWN_HALT)}) — "
                       f"до сброса владельцем")
    data = st.get("data") or {}
    last = data.get("last_ts")
    if not last:
        return False, "нет свежих рыночных данных (хранилище свечей не читается) — вход не открыт"
    d_age = (now - int(last)) / 60_000
    if d_age > L.DATA_MAX_AGE_MIN:
        return False, f"рыночные данные устарели: {d_age:.0f} мин (лимит {L.DATA_MAX_AGE_MIN:g}) — вход не открыт"

    # ---- the limits of exposure, after this entry (and the entries this process already let in) ----
    ver = upd
    if _LEDGER_VER[0] != ver:
        _LEDGER.clear()
        _LEDGER_VER[0] = ver
    accounts = st.get("accounts") or {}
    comb = st.get("combined") or {}
    adds = [(desk, notional)] + [(m, notional * kx) for m, kx in L.MIRRORS.get(desk, ())]
    add_total = sum(a for _, a in adds)
    led_gross = lambda f: sum(abs(x[3]) for x in _LEDGER if f(x))      # noqa: E731
    for acc, add in adds:
        a = accounts.get(acc)
        if not a:
            continue
        eq = float(a.get("equity") or 0)
        g_after = float(a.get("gross") or 0) + led_gross(lambda x, acc=acc: x[1] == acc) + add
        if eq <= 0:
            return False, f"капитал счёта «{a.get('name') or acc}» ≤ 0 — вход не открыт"
        lev = g_after / eq
        if lev > L.LEVERAGE_MAX + 1e-9:
            mirror = "" if acc == desk else " (зеркало этого стола)"
            return False, f"плечо счёта «{a.get('name') or acc}»{mirror} стало бы {lev:.2f}× (лимит {L.LEVERAGE_MAX:g}×)"
    sg = _sign(side)
    if sym and sym != "*":
        denom = max(float(comb.get("gross") or 0) + led_gross(lambda x: True) + add_total, float(comb.get("equity") or 0))
        if denom > 0:
            coins = st.get("coins") or {}
            c_after = float((coins.get(sym) or {}).get("gross") or 0) + led_gross(lambda x: x[2] == sym) + add_total
            if c_after / denom > L.COIN_MAX + 1e-9:
                return False, f"монета {sym} заняла бы {_pct(c_after / denom)} брутто всех счетов (лимит {_pct(L.COIN_MAX)})"
            cof = st.get("cluster_of") or {}
            cid = cof.get(sym)
            cl = next((c for c in st.get("clusters") or [] if c.get("id") == cid), None) if cid is not None else None
            if cl and len(cl.get("members") or []) > 1 and sg:
                net0 = float(cl.get("net") or 0) + sum(x[3] for x in _LEDGER if cof.get(x[2]) == cid)
                net1 = net0 + sg * add_total
                if abs(net1) > abs(net0) and abs(net1) / denom > L.CLUSTER_MAX + 1e-9:
                    mem = ", ".join((cl.get("members") or [])[:5]) + ("…" if len(cl.get("members") or []) > 5 else "")
                    return False, (f"кластер похожих монет ({mem}) занял бы {_pct(abs(net1) / denom)} брутто "
                                   f"(лимит {_pct(L.CLUSTER_MAX)})")
    for acc, add in adds:
        _LEDGER.append((ver, acc, sym, (sg or 1) * add))
    return True, ""
