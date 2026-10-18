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


def test_portfolio_mode_scales_the_coins_to_the_portfolio_spot_share():
    pf = {"portfolios": {"ideas": {"positions": [{"symbol": "BTC", "weight": 0.5}, {"symbol": "ETH", "weight": 0.4}]}}}
    cap = {"sleeves": [{"key": "spot", "weight": 0.2}, {"key": "yield", "weight": 0.3}]}
    assert SM.spot_share(cap) == 0.2 and SM.spot_share({}) is None
    tg = SM.targets(pf, SM.spot_share(cap))
    assert abs(tg["BTC"] - 0.10) < 1e-9 and abs(tg["ETH"] - 0.08) < 1e-9     # BTC below its 200-day average → 20 % of the sum in coins
    acct = FakeAcct(10_000.0, {}, {"BTC", "ETH"}, {"BTC": 100_000.0, "ETH": 4_000.0})
    r = SM.step(acct, pf, run=False, scale=0.2)
    assert [(o["coin"], o["usd"]) for o in r["orders"]] == [("BTC", 1000.0), ("ETH", 800.0)] and acct.sent == []


def test_keep_aside_coins_are_never_sold_or_counted():
    pf = {"portfolios": {"ideas": {"positions": [{"symbol": "BTC", "weight": 0.5}, {"symbol": "SOL", "weight": 0.2}]}}}
    acct = FakeAcct(5_000.0, {"SOL": {"qty": 30.0, "usd": 3_000.0}, "BONK": {"qty": 1e9, "usd": 2_000.0}}, {"BTC", "SOL", "BONK"},
                    {"BTC": 100_000.0, "SOL": 100.0, "BONK": 0.000002})
    r = SM.step(acct, pf, run=False, keep={"bonk", "sol"})
    assert r["equity"] == 5_000.0 and [(o["coin"], o["side"]) for o in r["orders"]] == [("BTC", "Buy")] and r["kept_aside"] == ["BONK", "SOL"]


def test_okx_account_signs_and_maps_the_interface():
    import base64, hashlib, hmac, io, json as _j
    from cnq.execution.okx_spot import OkxSpot
    sent = []

    class R(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def opener(req):
        sent.append(req)
        u = req.full_url
        if "/account/balance" in u:
            d = [{"details": [{"ccy": "USDT", "cashBal": "1000", "eqUsd": "1000"}, {"ccy": "SOL", "cashBal": "2", "eqUsd": "200"},
                              {"ccy": "DUST", "cashBal": "5", "eqUsd": "0.2"}]}]
        elif "/public/instruments" in u:
            d = [{"instId": "SOL-USDT", "state": "live", "lotSz": "0.0001"}]
        elif "/market/ticker" in u:
            d = [{"last": "100.5"}]
        else:
            d = [{"ordId": "777", "sCode": "0"}]
        return R(_j.dumps({"code": "0", "data": d}).encode())

    a = OkxSpot("k", "s", "p", live=False, opener=opener)
    usdt, held = a.holdings()
    assert usdt == 1000 and held == {"SOL": {"qty": 2.0, "usd": 200.0}}                  # dust under $1 ignored
    assert a.instrument("SOL")["lotSizeFilter"]["basePrecision"] == "0.0001" and a.price("SOL") == 100.5
    assert a.market("SOL", "Buy", 50, True) == {"orderId": "777"}
    h = dict(sent[-1].header_items())
    body = sent[-1].data.decode()
    assert _j.loads(body) == {"instId": "SOL-USDT", "tdMode": "cash", "side": "buy", "ordType": "market", "sz": "50", "tgtCcy": "quote_ccy"}
    ts = h["Ok-access-timestamp"]
    want = base64.b64encode(hmac.new(b"s", (ts + "POST" + "/api/v5/trade/order" + body).encode(), hashlib.sha256).digest()).decode()
    assert h["Ok-access-sign"] == want and h["X-simulated-trading"] == "1" and h["Ok-access-passphrase"] == "p"
