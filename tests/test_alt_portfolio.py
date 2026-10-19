"""Портфель «Ядро + альтсезон»: the gauge on daily closes, the model shares and the channel posts — no network."""
import json
import sqlite3
import tempfile

from cnq.agents import alt_portfolio as ap
from cnq.agents.own_desk import COINS40, COINS30

DAY = ap.DAY
D0 = 1_780_000_000_000 // DAY * DAY


def _closes(conn, btc_growth, alt_growth, n=260, alts=12):
    conn.execute("CREATE TABLE IF NOT EXISTS own_closes(symbol TEXT, day INTEGER, close REAL, PRIMARY KEY(symbol, day))")
    rows = []
    for i in range(n):
        d = D0 + i * DAY
        rows.append(("BTC", d, 100 * btc_growth ** i))
        rows.append(("ETH", d, 10 * btc_growth ** i))
        for a in range(alts):
            rows.append((COINS40[2 + a], d, (1 + a) * alt_growth ** i))
    conn.executemany("INSERT INTO own_closes VALUES(?,?,?)", rows)


def test_gauge_on_when_alts_beat_btc_in_a_rising_market_and_off_otherwise():
    c = sqlite3.connect(":memory:")
    _closes(c, 1.002, 1.004)
    g = ap.gauge(c)
    assert g["bull"] is True and g["on"] is True and g["breadth_pct"] == 100 and g["alt_vs_btc_91d_pct"] > 0
    c2 = sqlite3.connect(":memory:")
    _closes(c2, 1.003, 1.001)
    g2 = ap.gauge(c2)
    assert g2["bull"] is True and g2["on"] is False


def test_targets_add_to_100_and_shrink_in_a_falling_market():
    for bull in (True, False):
        for on in (True, False):
            t = ap.targets(bull, on)
            assert abs(sum(t.values()) - 100) < 0.6
    assert ap.targets(True, False)["BTC"] > 55 and "PENDLE" not in ap.targets(True, False)
    t = ap.targets(True, True, ["ZEC", "SOL", "XRP", "HYPE", "DOGE", "BNB", "NEAR", "1000PEPE", "SUI", "WLD"])
    assert abs(sum(t.values()) - 100) < 0.6 and t["ZEC"] == 2.0 and t["SOL"] == 6.7 and t["BTC"] == 39.2   # alts 20 %, BTC 39.2 %
    assert ap.targets(False, False)["USDT (Earn)"] > 50


def test_posts_first_status_then_only_changes():
    c = sqlite3.connect(":memory:")
    _closes(c, 1.002, 1.004)
    sent = []
    tmp = tempfile.mkdtemp()
    now = D0 + 260 * DAY + 2 * 3_600_000 + 3 * DAY       # not a Monday-dependent check: first post always
    d = ap.run(c, tmp, now=now, send=lambda m: sent.append(m) or 1, daily=False)
    assert d["posted"] and "подключён" in sent[0] and "Доли портфеля" in sent[0]
    ap.run(c, tmp, now=now + 3_600_000, send=lambda m: sent.append(m) or 1, daily=False)
    assert len(sent) == 1 or "понедельник" in sent[-1].lower()
    assert json.load(open(tmp + "/altseason.json"))["gauge"]["on"] is True


def test_gauge_stays_on_the_40_coins_it_was_checked_on():
    """The desk trades 70 coins since 19.10; the 30 added ones must not change the altseason gauge."""
    c = sqlite3.connect(":memory:")
    _closes(c, 1.002, 1.004)
    before = ap.gauge(c)
    c.executemany("INSERT INTO own_closes VALUES(?,?,?)", [(s, D0 + i * DAY, 50 * 0.99 ** i) for s in COINS30[:20] for i in range(260)])
    assert ap.gauge(c) == before


def test_the_basket_comes_from_the_desk_and_a_model_change_posts_once():
    c = sqlite3.connect(":memory:")
    _closes(c, 1.002, 1.004)
    c.execute("CREATE TABLE own_state(k TEXT PRIMARY KEY, v TEXT)")
    c.execute("INSERT INTO own_state VALUES('alt_basket', ?)", (json.dumps(["ZEC", "XRP", "HYPE", "DOGE", "BNB", "SOL", "NEAR", "SUI", "WLD", "ENA"]),))
    c.execute("CREATE TABLE alt_state(k TEXT PRIMARY KEY, v TEXT)")
    c.execute("INSERT INTO alt_state VALUES('state', ?)", (json.dumps({"bull": True, "on": True}),))     # the v1 state
    sent, tmp = [], tempfile.mkdtemp()
    now = D0 + 260 * DAY + 2 * 3_600_000 + 3 * DAY
    d = ap.run(c, tmp, now=now, send=lambda m: sent.append(m) or 1, daily=False)
    assert d["targets_pct"]["ZEC"] == 2.0 and "Модель обновлена" in sent[0] and "ZEC — 2%" in sent[0]
    ap.run(c, tmp, now=now + 3_600_000, send=lambda m: sent.append(m) or 1, daily=False)
    assert len(sent) == 1
