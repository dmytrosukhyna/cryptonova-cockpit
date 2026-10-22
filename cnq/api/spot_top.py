"""«Топ-10 монет на заметку» (build 45, owner 22.10): the Spot tab's short list under the user's own portfolio — what else
the system's table lets one look at. GET /v2/spot/top?lang=… (public, no login). Stdlib only.

  rows   the first 10 coins of web/ideas.json with the status «можно брать», «можно добрать» or «на небольшую сумму», in the
         table's own order (its rows first, then the same statuses it did not show, by score); each with one plain reason in the
         app's language (cnq/api/pf_review.reason), the table's score, the 7-day move, the fall from the 30-day high, the
         project-support score when the table has one ("support", optional), whether «CRYPTO NOVA портфель» holds it (its share,
         entry, stop and the point where it takes half), and whether OKX / BingX list it on spot (null = not known yet)
  prices today's price of every coin the table rates — the app values the coins the user added to the demo portfolio
Nothing is a recommendation to buy: the app ends the list with «Список по правилам CRYPTO NOVA, а не финансовый совет»."""
from __future__ import annotations

import json
import threading
import time
import urllib.request

from cnq.agents.i18n import DEFAULT, norm_lang

from . import pf_review

STATUSES = ("buy", "add", "small")
TOP_N = 10
OKX_SPOT = "https://www.okx.com/api/v5/public/instruments?instType=SPOT"
BINGX_SPOT = "https://open-api.bingx.com/openApi/spot/v1/common/symbols"
LIST_TTL = 12 * 3600


def _get(url: str, timeout: float = 10.0) -> dict:
    req = urllib.request.Request(url, headers={"user-agent": "Mozilla/5.0 cryptonova/1.0", "accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def okx_bases(get=_get) -> set[str]:
    d = (get(OKX_SPOT) or {}).get("data") or []
    out = {str(x.get("baseCcy") or "").upper() for x in d
           if isinstance(x, dict) and x.get("quoteCcy") in ("USDT", "USDC") and x.get("state", "live") == "live"}
    out.discard("")
    if not out:
        raise RuntimeError("okx: empty")
    return out


def bingx_bases(get=_get) -> set[str]:
    d = ((get(BINGX_SPOT) or {}).get("data") or {}).get("symbols") or []
    out = set()
    for x in d:
        sym = str((x or {}).get("symbol") or "").upper()
        base, _, quote = sym.partition("-")
        if base and quote in ("USDT", "USDC") and str(x.get("status", 1)) == "1":
            out.add(base)
    if not out:
        raise RuntimeError("bingx: empty")
    return out


class Listings:
    """the two exchanges' spot coins, fetched in the background at most every 12 h; a failed fetch keeps the last list"""

    def __init__(self, fetchers: dict | None = None, ttl: float = LIST_TTL):
        self.fetchers = fetchers if fetchers is not None else {"okx": okx_bases, "bingx": bingx_bases}
        self.ttl = ttl
        self.lists: dict[str, set[str]] = {}
        self.at: dict[str, float] = {}
        self._busy = False
        self._lock = threading.Lock()

    def refresh(self) -> None:
        for name, fn in self.fetchers.items():
            try:
                got = fn()
                with self._lock:
                    self.lists[name], self.at[name] = set(got), time.time()
            except Exception as e:  # noqa: BLE001
                print(f"spot top: {name} list failed: {type(e).__name__}", flush=True)
                with self._lock:
                    self.at.setdefault(name, 0.0)

    def get(self, background: bool = True) -> dict[str, set[str]]:
        now = time.time()
        stale = any(now - self.at.get(n, 0.0) > self.ttl for n in self.fetchers)
        if stale and not self._busy:
            self._busy = True

            def run():
                try:
                    self.refresh()
                finally:
                    self._busy = False
            if background:
                threading.Thread(target=run, daemon=True).start()
            else:
                run()
        with self._lock:
            return dict(self.lists)


def _num(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _support(src: dict):
    """the table's «Поддержка проекта» when it has one: a number, or {"score", "why_ru"?}"""
    s = src.get("support")
    if s is None:
        return None
    if isinstance(s, dict):
        sc = _num(s.get("score") if s.get("score") is not None else s.get("value"))
        return {"score": sc} if sc is not None else None
    sc = _num(s)
    return {"score": sc} if sc is not None else None


def top(data: pf_review.Data, lang: str = DEFAULT, listings: dict[str, set[str]] | None = None) -> dict:
    lang = norm_lang(lang)
    ideas = data.get("ideas")
    statuses = ideas.get("statuses") or {}
    rows = [r for r in ideas.get("rows") or [] if isinstance(r, dict) and r.get("status") in STATUSES and r.get("symbol")]
    seen = {r["symbol"] for r in rows}
    extra = sorted(({"symbol": s, **v} for s, v in statuses.items() if isinstance(v, dict) and v.get("status") in STATUSES and s not in seen),
                   key=lambda r: (STATUSES.index(r["status"]), -(_num(r.get("score")) or 0)))
    pf = ((data.get("portfolio").get("portfolios") or {}).get("ideas") or {})
    held = {}
    for p in pf.get("positions") or []:
        sym = str(p.get("symbol") or "").upper()
        lv = p.get("levels") or {}
        if sym:
            held[sym] = {"pct": round((_num(p.get("weight")) or 0) * 100, 1), "entry": _num(p.get("entry")),
                         "stop": _num(lv.get("stop")), "target": _num(lv.get("take_half"))}
    lists = listings or {}
    out = []
    for r in (rows + extra)[:TOP_N]:
        sym = r["symbol"]
        st = {**(statuses.get(sym) or {}), **r}
        why, key, _flag = pf_review.reason(st.get("why_ru") or "", st["status"], lang)
        out.append({
            "sym": sym, "name": st.get("name"), "kind": st.get("kind"), "status": st["status"],
            "status_label": pf_review._t(pf_review.STATUS_LABEL[st["status"]], lang), "reason": why, "score": _num(st.get("score")),
            "price": _num(st.get("price")), "chg7": _num(st.get("chg7")), "from_high30_pct": _num(st.get("from_high30_pct")),
            "support": _support(st), "held": held.get(sym),
            "okx": (sym in lists["okx"]) if lists.get("okx") else None,
            "bingx": (sym in lists["bingx"]) if lists.get("bingx") else None,
        })
    prices = {s: _num(v.get("price")) for s, v in statuses.items() if isinstance(v, dict) and _num(v.get("price"))}
    return {"asof": ideas.get("asof"), "lang": lang, "rows": out, "prices": prices, "bull": ideas.get("btc_bull")}
