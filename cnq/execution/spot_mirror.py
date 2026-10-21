"""«Инвестировать в один клик» — the CRYPTO NOVA spot portfolio placed on a real exchange account (18.10.2026, owner: «человек жмёт
одну кнопку „Инвестировать“, и система сама раскладывает на бирже, покупает монеты»).

First step: the owner's own Bybit account (DEMO by default). Runs on HIS machine like the futures mirror — exchange keys never
leave it. Every run it reads the server's CRYPTO NOVA портфель (web/portfolio.json → portfolios.ideas: coins and their market
weights) and makes the Bybit spot holdings match those weights on the account's equity (USDT + coins):

  python -m cnq.execution.spot_mirror            → shows the plan (what it would buy / sell), places nothing
  python -m cnq.execution.spot_mirror --run      → places the orders (Bybit demo; real account only with CNQ_LIVE_TRADING=yes)
  python -m cnq.execution.spot_mirror --run --loop 900   → every 15 minutes
  python -m cnq.execution.spot_mirror --exchange okx …      → the same on OKX (cnq/execution/okx_spot.py; OKX demo by default)
  python -m cnq.execution.spot_mirror --keep SOL,LINK …     → coins the mirror never touches (kept aside by the owner)
  python -m cnq.execution.spot_mirror --portfolio [--run]  → only the SPOT SHARE of «CRYPTO NOVA Портфолио» goes into coins
                                                            (capital.json → sleeve «spot» weight: 60 % of the sum while BTC is above
                                                            its 200-day average, 20 % below — the tested rule), the rest stays USDT

Rules: sells first, then buys; an order only when the gap is at least max($10, 2 % of equity); coins that Bybit does not list
as <COIN>USDT spot stay in USDT (logged); the portfolio's own cash share stays in USDT; market orders (buy by USDT amount,
sell by coin quantity rounded down to the exchange's step). Log: data/spot_mirror.jsonl. Stdlib only."""
from __future__ import annotations

import json
import math
import os
import sys
import time
import urllib.request

from .bybit_trade import BybitTrader

SRC = os.environ.get("CNQ_SPOT_SRC", "https://raw.githubusercontent.com/dmytrosukhyna/cryptonova-cockpit/main/web/portfolio.json")
CAP_SRC = os.environ.get("CNQ_CAPITAL_SRC", "https://raw.githubusercontent.com/dmytrosukhyna/cryptonova-cockpit/main/web/capital.json")
LOG = "data/spot_mirror.jsonl"
MIN_USD = 10.0
MIN_GAP = 0.02


def log(ev: dict) -> None:
    os.makedirs("data", exist_ok=True)
    ev = {"t": int(time.time() * 1000), **ev}
    print(json.dumps(ev, ensure_ascii=False), flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def targets(portfolio: dict, scale: float = 1.0) -> dict:
    """{COIN: market weight} of the CRYPTO NOVA portfolio's open positions (the rest of the account is its cash), × scale."""
    p = (portfolio.get("portfolios") or {}).get("ideas") or {}
    out = {}
    for x in p.get("positions") or []:
        if (x.get("status") or "OPEN") == "OPEN" and x.get("weight"):
            out[str(x["symbol"]).upper()] = float(x["weight"]) * scale
    return out


def spot_share(capital: dict) -> float | None:
    """The spot sleeve's target weight in «CRYPTO NOVA Портфолио» (None if the file has none)."""
    for s in capital.get("sleeves") or []:
        if s.get("key") == "spot" and s.get("weight") is not None:
            return max(0.0, min(1.0, float(s["weight"])))
    return None


class SpotAccount:
    """The few Bybit v5 spot calls the mirror needs (wraps BybitTrader's signing)."""

    def __init__(self, bx: BybitTrader):
        self.bx = bx
        self._inst: dict = {}

    def holdings(self) -> tuple[float, dict]:
        res = self.bx._get_signed("/v5/account/wallet-balance", {"accountType": "UNIFIED"})
        coins = ((res.get("list") or [{}])[0].get("coin")) or []
        usdt, held = 0.0, {}
        for c in coins:
            q = float(c.get("walletBalance") or 0)
            if c.get("coin") == "USDT":
                usdt = q
            elif q > 0:
                held[c["coin"]] = {"qty": q, "usd": float(c.get("usdValue") or 0)}
        return usdt, held

    def instrument(self, coin: str) -> dict | None:
        sym = f"{coin}USDT"
        if sym not in self._inst:
            try:
                lst = self.bx._get("/v5/market/instruments-info", {"category": "spot", "symbol": sym}).get("list") or []
            except Exception:  # noqa: BLE001
                lst = []
            self._inst[sym] = lst[0] if lst and lst[0].get("status") == "Trading" else None
        return self._inst[sym]

    def price(self, coin: str) -> float | None:
        try:
            lst = self.bx._get("/v5/market/tickers", {"category": "spot", "symbol": f"{coin}USDT"}).get("list") or []
            return float(lst[0]["lastPrice"]) if lst else None
        except Exception:  # noqa: BLE001
            return None

    def market(self, coin: str, side: str, qty: float, by_quote: bool) -> dict:
        body = {"category": "spot", "symbol": f"{coin}USDT", "side": side, "orderType": "Market", "qty": f"{qty:.8f}".rstrip("0").rstrip("."),
                "marketUnit": "quoteCoin" if by_quote else "baseCoin"}
        return self.bx._post("/v5/order/create", body)


def plan(tg: dict, usdt: float, held: dict, acct) -> tuple[list, dict]:
    """Orders that bring the holdings to the target weights. Returns (orders, info)."""
    equity = usdt + sum(h["usd"] for h in held.values())
    orders, skipped = [], []
    gap_min = max(MIN_USD, MIN_GAP * equity)
    coins = set(tg) | set(held)
    for c in sorted(coins):
        inst = acct.instrument(c)
        want = tg.get(c, 0.0) * equity
        have = held.get(c, {}).get("usd", 0.0)
        if inst is None:
            if want > 0:
                skipped.append(c)
            continue
        diff = want - have
        if abs(diff) < gap_min:
            continue
        if diff < 0:
            px = acct.price(c)
            step = float((inst.get("lotSizeFilter") or {}).get("basePrecision") or 0) or 1e-8
            qty = math.floor(min(-diff / px, held.get(c, {}).get("qty", 0)) / step) * step if px else 0
            if qty > 0:
                orders.append({"coin": c, "side": "Sell", "qty": qty, "by_quote": False, "usd": round(qty * px, 2)})
        else:
            orders.append({"coin": c, "side": "Buy", "qty": round(diff, 2), "by_quote": True, "usd": round(diff, 2)})
    orders.sort(key=lambda o: o["side"] != "Sell")          # sells first: they pay for the buys
    return orders, {"equity": round(equity, 2), "usdt": round(usdt, 2), "skipped": skipped, "gap_min": round(gap_min, 2)}


def step(acct, portfolio: dict, run: bool, scale: float = 1.0, keep: set | None = None) -> dict:
    """keep: coins the mirror never touches (not sold, not bought, not counted) — e.g. a long-term bag the owner keeps aside."""
    keep = {c.upper() for c in keep or ()}
    tg = {c: w for c, w in targets(portfolio, scale).items() if c not in keep}
    if not tg:
        log({"ev": "no_targets"})
        return {"orders": [], "done": []}
    usdt, held = acct.holdings()
    held = {c: h for c, h in held.items() if c not in keep}
    orders, info = plan(tg, usdt, held, acct)
    if keep:
        info["kept_aside"] = sorted(keep)
    log({"ev": "plan", **info, "orders": orders})
    done = []
    if run:
        for o in orders:
            try:
                r = acct.market(o["coin"], o["side"], o["qty"], o["by_quote"])
                done.append({**o, "id": r.get("orderId")})
                log({"ev": "order", **o, "id": r.get("orderId")})
            except Exception as e:  # noqa: BLE001
                log({"ev": "order_failed", **o, "error": str(e)[:200]})
    return {"orders": orders, "done": done, **info}


def fetch_portfolio(url: str = SRC) -> dict:
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.loads(r.read().decode())


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    run = "--run" in argv
    by_cap = "--portfolio" in argv
    loop = int(argv[argv.index("--loop") + 1]) if "--loop" in argv else 0
    keep = set(argv[argv.index("--keep") + 1].upper().split(",")) if "--keep" in argv else set()
    exch = argv[argv.index("--exchange") + 1].lower() if "--exchange" in argv else "bybit"
    if exch == "okx":
        from .okx_spot import OkxSpot
        acct = OkxSpot()
        if run and acct.live:
            # 22.10: the owner's real OKX account is run by okx_autopilot (the card's model, limit orders, caps, kill switch, EEA host);
            # this older path (another portfolio, market orders) must never touch real money by mistake.
            print("Для своего реального счёта OKX используй автопилот: python3 -m cnq.execution.okx_autopilot --plan / --run")
            return 2
        mode, venue = acct.mode, "OKX"
    else:
        bx = BybitTrader()
        acct = SpotAccount(bx)
        mode, venue = bx.mode, "Bybit"
    print(f"CRYPTO NOVA портфель → {venue} spot ({mode}). {'Размещаю ордера.' if run else 'Только план, без ордеров (добавь --run).'}"
          + (" Доля спота — из «CRYPTO NOVA Портфолио»." if by_cap else "") + (f" Не трогаем: {', '.join(sorted(keep))}." if keep else ""))
    while True:
        try:
            scale = 1.0
            if by_cap:
                sh = spot_share(fetch_portfolio(CAP_SRC))
                if sh is None:
                    log({"ev": "no_capital_share"})
                    raise RuntimeError("capital.json has no spot share — nothing changed")
                scale = sh
                log({"ev": "spot_share", "share": sh})
            step(acct, fetch_portfolio(), run, scale, keep)
        except Exception as e:  # noqa: BLE001
            log({"ev": "error", "error": str(e)[:200]})
        if not loop:
            return 0
        time.sleep(loop)


if __name__ == "__main__":
    sys.exit(main())
