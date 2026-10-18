import os
import tempfile

from cnq.execution import spot_mirror as SM

SM.LOG = os.path.join(tempfile.mkdtemp(), "spot_mirror.jsonl")      # never write into the repo's data/


class FakeAcct:
    def __init__(self, usdt, held, listed, prices):
        self.usdt, self.held, self.listed, self.prices, self.sent = usdt, held, listed, prices, []

    def holdings(self):
        return self.usdt, self.held

    def instrument(self, c):
        return {"lotSizeFilter": {"basePrecision": "0.001"}} if c in self.listed else None

    def price(self, c):
        return self.prices.get(c)

    def market(self, coin, side, qty, by_quote):
        self.sent.append((coin, side, qty, by_quote)); return {"orderId": f"o{len(self.sent)}"}


def test_plan_sells_first_buys_by_usdt_and_skips_unlisted_coins():
    pf = {"portfolios": {"ideas": {"positions": [{"symbol": "BTC", "weight": 0.15}, {"symbol": "ETH", "weight": 0.15},
                                                 {"symbol": "CASHCAT", "weight": 0.025}, {"symbol": "LINK", "weight": 0.0}]}}}
    # equity $10 000: $8 000 USDT + $2 000 of LINK (not in the portfolio any more) + ETH $1 495 (close enough to $1 500)
    acct = FakeAcct(6_505.0, {"LINK": {"qty": 160.0, "usd": 2_000.0}, "ETH": {"qty": 0.62, "usd": 1_495.0}},
                    listed={"BTC", "ETH", "LINK"}, prices={"LINK": 12.5, "BTC": 80_000.0, "ETH": 2_411.0})
    r = SM.step(acct, pf, run=True)
    assert r["equity"] == 10_000.0 and r["skipped"] == ["CASHCAT"]
    assert acct.sent[0] == ("LINK", "Sell", 160.0, False)                    # sell first, all of it (target 0)
    assert ("BTC", "Buy", 1500.0, True) in acct.sent                       # buy by USDT amount
    assert not any(c == "ETH" for c, *_ in acct.sent)                      # gap $5 < max($10, 2 %) → no order
    assert len(r["done"]) == 2


def test_dry_run_places_nothing():
    pf = {"portfolios": {"ideas": {"positions": [{"symbol": "BTC", "weight": 0.5}]}}}
    acct = FakeAcct(1_000.0, {}, listed={"BTC"}, prices={"BTC": 80_000.0})
    r = SM.step(acct, pf, run=False)
    assert r["orders"] and not acct.sent
