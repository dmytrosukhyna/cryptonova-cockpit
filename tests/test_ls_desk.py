import json
import sqlite3

from cnq.agents import ls_desk as L

DAY = 86_400_000
T0 = 1_790_000_000_000          # some day in 2026


def _quality(n=40):
    return {"rows": [{"symbol": f"C{k}", "rank": k + 1, "score": 1 - k / n} for k in range(n)], "basket": []}


def _daily_with_sigma(sig_by_sym):
    def daily(sym):
        s = sig_by_sym.get(sym, 0.03)
        c, out = 100.0, []
        for k in range(40):
            c *= 1 + (s if k % 2 else -s)
            out.append([k * DAY, c, c, c, c, 1.0])
        return out
    return daily


def _run(conn, tmp_path, now, px, rates=None, daily=None, quality=None):
    (tmp_path / "quality.json").write_text(json.dumps(quality or _quality()))
    if not (tmp_path / "paper.json").exists():
        (tmp_path / "paper.json").write_text(json.dumps({"profiles": {}, "profile_list": []}))
    return L.run(conn, str(tmp_path), now=now, prices=lambda: px, funding=lambda: rates or {}, daily=daily or _daily_with_sigma({}))


def test_weights_are_inverse_volatility_and_capped():
    w = L.weights(["A", "B", "C", "D"], {"A": 0.01, "B": 0.02, "C": 0.04, "D": None})
    assert abs(sum(w.values()) - 1) < 1e-9
    assert w["A"] > w["B"] > w["C"]
    assert max(w.values()) <= L.CAP_MULT / 4 + 1e-9
    w2 = L.weights(["A"] + [f"X{k}" for k in range(9)], {"A": 0.0001, **{f"X{k}": 0.05 for k in range(9)}})
    assert abs(w2["A"] - 0.25) < 1e-9                    # 2.5 equal shares of 10


def test_first_run_buys_the_top_ten_and_shorts_the_bottom_ten_at_gross_075(tmp_path):
    conn = sqlite3.connect(":memory:")
    px = {f"C{k}": 10.0 for k in range(40)}
    d = _run(conn, tmp_path, T0, px)
    longs = sorted(o["symbol"] for o in d["open"] if o["side"] == "LONG")
    shorts = sorted(o["symbol"] for o in d["open"] if o["side"] == "SHORT")
    assert longs == sorted(f"C{k}" for k in range(10))
    assert shorts == sorted(f"C{k}" for k in range(30, 40))
    gross = sum(o["qty"] * o["price"] for o in d["open"])
    assert abs(gross - L.START * L.GROSS) < 1.0          # equal-σ coins → equal weights; fees come off the cash
    assert d["equity_mtm"] < L.START and d["equity_mtm"] > L.START - 10
    paper = json.loads((tmp_path / "paper.json").read_text())
    assert paper["profiles"]["lsn"]["name"] == L.NAME and any(x["key"] == "lsn" for x in paper["profile_list"])


def test_the_market_moving_together_leaves_the_book_flat_and_the_gap_pays(tmp_path):
    conn = sqlite3.connect(":memory:")
    px = {f"C{k}": 10.0 for k in range(40)}
    d0 = _run(conn, tmp_path, T0, px)
    up = {s: p * 1.2 for s, p in px.items()}                     # everything +20 %
    d1 = _run(conn, tmp_path, T0 + DAY, up)
    assert abs(d1["equity_mtm"] - d0["equity_mtm"]) < 1.0
    gap = {s: (12.0 if int(s[1:]) < 10 else 10.0) for s in px}     # the longs +20 %, the shorts flat
    d2 = _run(conn, tmp_path, T0 + 2 * DAY, gap)
    assert d2["equity_mtm"] - d0["equity_mtm"] > 0.2 * L.START * L.GROSS * 0.5 * 0.99


def test_rebalance_only_once_a_month_and_keeps_coins_that_stay(tmp_path):
    conn = sqlite3.connect(":memory:")
    px = {f"C{k}": 10.0 for k in range(40)}
    _run(conn, tmp_path, T0, px)
    opened = {o["symbol"]: o["opened"] for o in _run(conn, tmp_path, T0 + DAY, px)["open"]}
    assert set(opened.values()) == {T0}                             # same month → no rebalance
    q = _quality()
    q["rows"] = q["rows"][1:] + q["rows"][:1]                      # C0 drops to the bottom, C10 enters the top
    d = _run(conn, tmp_path, T0 + 40 * DAY, px, quality=q)
    sides = {o["symbol"]: o["side"] for o in d["open"]}
    assert sides["C0"] == "SHORT" and sides["C10"] == "LONG" and "C30" not in sides
    assert {o["symbol"]: o["opened"] for o in d["open"]}["C1"] == T0        # stayed in the top: the same position
    assert any(t["symbol"] == "C30" for t in d["closed"]) and any(t["symbol"] == "C0" and t["side"] == "LONG" for t in d["closed"])


def test_funding_longs_pay_shorts_receive(tmp_path):
    conn = sqlite3.connect(":memory:")
    px = {f"C{k}": 10.0 for k in range(40)}
    _run(conn, tmp_path, T0, px)
    e0 = L.equity(conn, px)
    rates = {f"C{k}": 0.001 for k in range(10)}                    # only the longs have a positive rate
    _run(conn, tmp_path, T0 + 8 * 3_600_000, px, rates=rates)
    assert L.equity(conn, px) < e0
    conn2 = sqlite3.connect(":memory:")
    _run(conn2, tmp_path, T0, px)
    e1 = L.equity(conn2, px)
    _run(conn2, tmp_path, T0 + 8 * 3_600_000, px, rates={f"C{k}": 0.001 for k in range(30, 40)})
    assert L.equity(conn2, px) > e1


def test_waits_without_a_quality_card(tmp_path):
    conn = sqlite3.connect(":memory:")
    d = _run(conn, tmp_path, T0, {"C1": 1.0}, quality={"rows": []})
    assert d["open"] == [] and any("ждёт" in e for e in d["events"])
    d = _run(conn, tmp_path, T0 + 3_600_000, {f"C{k}": 10.0 for k in range(40)})
    assert len(d["open"]) == 20                                     # the card arrived → same month, it still builds the book
