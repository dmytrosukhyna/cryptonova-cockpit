"""Portfolio risk layer (cnq/risk): every limit triggers and releases, the kill switch, stale data, the conveyor contract, the desks
record a refused entry instead of opening it, and the step never raises on missing or broken inputs. No network; files in temp dirs."""
import contextlib
import json
import os
import random
import sqlite3
import tempfile
import time

from cnq.risk import gate as G
from cnq.risk import limits as L
from cnq.risk import portfolio as P

DAY = 86_400_000
MIN = 60_000


def _wall():
    return int(time.time() * 1000)


@contextlib.contextmanager
def risk_dir(d):
    """the desks call the gate without a base dir → point CNQ_RISK_DIR at a temp dir for the test"""
    old = os.environ.get("CNQ_RISK_DIR")
    os.environ["CNQ_RISK_DIR"] = str(d)
    G.reset_ledger()
    try:
        yield str(d)
    finally:
        if old is None:
            os.environ.pop("CNQ_RISK_DIR", None)
        else:
            os.environ["CNQ_RISK_DIR"] = old
        G.reset_ledger()


def candles_db(path, now, fresh=True):
    """a candle store whose newest BTC 15m bar and last write are `now` (fresh) or 30–40 minutes old (stale)"""
    if os.path.exists(path):
        os.remove(path)
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE candles(symbol TEXT, interval TEXT, open_time INTEGER, close REAL)")
    bar = now // 900_000 * 900_000 if fresh else now // 900_000 * 900_000 - 40 * MIN
    c.execute("INSERT INTO candles VALUES('BTC-USDT', '15m', ?, 100.0)", (bar,))
    c.commit()
    c.close()
    t = (now - 30_000 if fresh else now - 30 * MIN) / 1000
    os.utime(path, (t, t))
    return path


def paper_doc(accounts):
    """accounts: {key: (equity, [(sym, side, notional)], since)} → web/paper.json"""
    prof = {}
    for k, (eq, pos, since) in accounts.items():
        prof[k] = {"name": k.upper(), "equity": eq, "equity_mtm": eq, "since": since, "asof": 0,
                   "open": [{"symbol": s, "side": sd, "qty": n / 100.0, "qty_left": n / 100.0, "price": 100.0} for s, sd, n in pos]}
    return {"profiles": prof}


class Env:
    def __init__(self, tmp=None):
        self.root = str(tmp or tempfile.mkdtemp())
        self.data = os.path.join(self.root, "data")
        self.web = os.path.join(self.root, "web")
        os.makedirs(self.data, exist_ok=True)
        os.makedirs(self.web, exist_ok=True)
        self.cdb = os.path.join(self.data, "cnq.sqlite")
        self.adb = os.path.join(self.data, "agents.sqlite")

    def step(self, accounts, now, fresh=True):
        candles_db(self.cdb, now, fresh)
        with open(os.path.join(self.web, "paper.json"), "w") as f:
            json.dump(paper_doc(accounts), f)
        st, err = P.run(self.web, now, base=self.data, agents_db=self.adb, candles_db=self.cdb)
        assert err is None, err
        return st

    def allow(self, desk, sym, side, notional, now):
        return G.allow_entry(desk, sym, side, notional, now, base=self.data, wall=now)

    def public(self):
        return json.load(open(os.path.join(self.web, "risk.json"), encoding="utf-8"))


def _lim(st, key):
    return next(r for r in st["limits"] if r["key"] == key)


# ───────────────────────────────────────────────────────────────────────────── daily loss / drawdown
def test_daily_loss_halts_until_the_next_utc_day(tmp_path=None):
    e, now = Env(tmp_path), _wall()
    G.reset_ledger()
    st = e.step({"a": (10_000.0, [], 1), "b": (10_000.0, [], 1)}, now)
    assert st["state"] == "OK" and e.allow("a", "BTC", "LONG", 100, now) == (True, "")
    st = e.step({"a": (9_800.0, [], 1), "b": (9_800.0, [], 1)}, now + MIN)            # −2 % = 67 % of the limit → still OK
    assert _lim(st, "daily_loss")["status"] == "ok" and st["state"] == "OK"
    st = e.step({"a": (9_600.0, [], 1), "b": (9_900.0, [], 1)}, now + 90_000)         # −2.5 % of the two together → WARN (≥ 70 %)
    assert _lim(st, "daily_loss")["status"] == "warn" and st["state"] == "WARN" and not st["latch"].get("daily")
    st = e.step({"a": (9_500.0, [], 1), "b": (9_850.0, [], 1)}, now + 2 * MIN)        # −3.25 % → HALT
    assert st["state"] == "HALT" and st["latch"]["daily"]["day"] == G.utc_day(now + 2 * MIN)
    ok, why = e.allow("b", "ETH", "LONG", 100, now + 2 * MIN)
    assert not ok and "дневной стоп" in why
    st = e.step({"a": (9_800.0, [], 1), "b": (10_000.0, [], 1)}, now + 3 * MIN)       # recovers the same day → still closed
    assert st["latch"]["daily"] and not e.allow("b", "ETH", "LONG", 100, now + 3 * MIN)[0]
    nxt = (now // DAY + 1) * DAY + 5 * MIN                                            # the next UTC day → released
    st = e.step({"a": (9_800.0, [], 1), "b": (10_000.0, [], 1)}, nxt)
    assert not st["latch"].get("daily") and st["state"] == "OK" and e.allow("b", "ETH", "LONG", 100, nxt) == (True, "")
    pub = e.public()
    assert pub["state"] == "OK" and any("стоп" in ev["ru"] for ev in pub["events"])


def test_drawdown_halts_until_the_owner_resets(tmp_path=None):
    e, now = Env(tmp_path), _wall()
    G.reset_ledger()
    eq = 10_000.0
    e.step({"a": (eq, [], 1)}, now)
    st = None
    for k in range(1, 7):                                  # −2.9 % a day: never a daily stop, but −16 % from the peak after 6 days
        eq *= 0.971
        st = e.step({"a": (eq, [], 1)}, now + k * DAY)
        assert not st["latch"].get("daily")
        if k == 5:
            assert _lim(st, "drawdown")["status"] == "warn" and not st["latch"].get("drawdown")
    assert st["latch"]["drawdown"] and st["state"] == "HALT"
    t = now + 6 * DAY
    ok, why = e.allow("a", "BTC", "LONG", 100, t)
    assert not ok and "просадк" in why
    st = e.step({"a": (eq * 1.05, [], 1)}, now + 7 * DAY)  # a recovery does not release it
    assert st["latch"]["drawdown"] and not e.allow("a", "BTC", "LONG", 100, now + 7 * DAY)[0]
    G.write_json(os.path.join(e.data, L.RESET_FILE), {"t": now + 7 * DAY + MIN, "reason": "проверено", "by": "test"})
    st = e.step({"a": (eq * 1.05, [], 1)}, now + 7 * DAY + 2 * MIN)
    assert not st["latch"].get("drawdown") and st["index"]["drawdown"] == 0.0 and st["state"] == "OK"
    assert e.allow("a", "BTC", "LONG", 100, now + 7 * DAY + 2 * MIN) == (True, "")
    log = open(os.path.join(e.data, L.LOG_FILE), encoding="utf-8").read()
    assert "drawdown_halt" in log and "drawdown_reset" in log


def test_new_or_reset_accounts_and_data_jumps_do_not_move_the_index(tmp_path=None):
    e, now = Env(tmp_path), _wall()
    e.step({"a": (10_000.0, [], 1)}, now)
    st = e.step({"a": (10_000.0, [], 1), "new": (10_000.0, [], 5)}, now + MIN)      # a new desk is not a profit
    assert st["index"]["value"] == 1.0
    st = e.step({"a": (10_000.0, [], 1), "new": (12_000.0, [], 9)}, now + 2 * MIN)  # the desk was reset (since changed)
    assert st["index"]["value"] == 1.0
    st = e.step({"a": (3_000.0, [], 1), "new": (12_000.0, [], 9)}, now + 3 * MIN)   # −70 % in a minute = a data error, not a loss
    assert st["index"]["value"] == 1.0 and any("скачок" in w for w in st["warnings"])


# ───────────────────────────────────────────────────────────────────────────── leverage / coin / cluster
def test_leverage_blocks_entries_above_3x_and_releases(tmp_path=None):
    e, now = Env(tmp_path), _wall()
    G.reset_ledger()
    st = e.step({"a": (10_000.0, [("BTC", "LONG", 22_000.0)], 1), "b": (10_000.0, [], 1), "c": (100_000.0, [], 1)}, now)
    assert _lim(st, "leverage")["status"] == "warn" and _lim(st, "leverage")["account"] == "a"     # 2.2× ≥ 70 % of 3×
    assert e.allow("a", "ETH", "LONG", 8_000, now) == (True, "")                  # 3.0× exactly is still allowed
    ok, why = e.allow("a", "SOL", "LONG", 500, now)                              # this process already took the 8 000 → 3.05×
    assert not ok and "плечо" in why and "3.05" in why
    assert e.allow("b", "SOL", "LONG", 500, now)[0]                              # another account is not affected
    G.reset_ledger()
    st = e.step({"a": (10_000.0, [("BTC", "LONG", 31_000.0)], 1), "b": (10_000.0, [], 1), "c": (100_000.0, [], 1)}, now + MIN)
    assert any(b["kind"] == "leverage" for b in st["blocks"]) and not e.allow("a", "ETH", "LONG", 10, now + MIN)[0]
    st = e.step({"a": (10_000.0, [("BTC", "LONG", 5_000.0)], 1), "b": (10_000.0, [], 1), "c": (100_000.0, [], 1)}, now + 2 * MIN)
    assert e.allow("a", "ETH", "LONG", 10, now + 2 * MIN) == (True, "")


def test_the_x4_mirror_counts_against_its_base_desk(tmp_path=None):
    from cnq.agents import turbo_desk
    assert dict(L.MIRRORS["lst"])["lstx"] == turbo_desk.LST_K and P.BTC_LEV == turbo_desk.BTC_LEV
    e, now = Env(tmp_path), _wall()
    G.reset_ledger()
    e.step({"lst": (10_000.0, [("AAA", "SHORT", 7_000.0)], 1), "lstx": (10_000.0, [("AAA", "SHORT", 28_000.0)], 1),
            "z": (200_000.0, [], 1)}, now)
    ok, why = e.allow("lst", "NEW", "SHORT", 500, now)                           # lst 0.75×, but lstx 2.8 + 0.2 = 3.0× → allowed
    assert ok
    ok, why = e.allow("lst", "NEW2", "SHORT", 500, now)                          # lstx would be 3.2×
    assert not ok and "зеркало" in why


def test_one_coin_above_25_percent_of_gross_is_blocked(tmp_path=None):
    e, now = Env(tmp_path), _wall()
    G.reset_ledger()
    # equity 40 000, gross 30 000 → the denominator is max(gross, equity) = 40 000: BTC 9 000 = 22.5 % (WARN)
    st = e.step({"a": (20_000.0, [("BTC", "LONG", 9_000.0), ("ETH", "LONG", 4_000.0)], 1),
                 "b": (20_000.0, [("SOL", "LONG", 8_500.0), ("XRP", "SHORT", 8_500.0)], 1)}, now)
    assert _lim(st, "coin")["coin"] == "BTC" and _lim(st, "coin")["status"] == "warn"
    ok, why = e.allow("b", "BTC", "LONG", 2_000, now)                            # 11 000 / 40 000 = 27.5 %
    assert not ok and "BTC" in why and "25%" in why
    assert e.allow("b", "ETH", "LONG", 2_000, now)[0]                            # another coin is fine
    st = e.step({"a": (20_000.0, [("BTC", "LONG", 40_000.0)], 1), "b": (20_000.0, [], 1)}, now + MIN)
    assert any(b["kind"] == "coin" and b["coin"] == "BTC" for b in st["blocks"]) and st["state"] == "WARN"
    st = e.step({"a": (20_000.0, [("BTC", "LONG", 4_000.0)], 1), "b": (20_000.0, [], 1)}, now + 2 * MIN)
    assert not st["blocks"] and e.allow("b", "BTC", "LONG", 2_000, now + 2 * MIN)[0]


def _closes(adb, now, series):
    c = sqlite3.connect(adb)
    c.execute("CREATE TABLE IF NOT EXISTS own_closes(symbol TEXT, day INTEGER, close REAL, PRIMARY KEY(symbol, day))")
    today = now // DAY * DAY
    for sym, xs in series.items():
        for i, x in enumerate(xs):
            c.execute("INSERT OR REPLACE INTO own_closes VALUES(?,?,?)", (sym, today - (len(xs) - i) * DAY, x))
    c.commit()
    c.close()


def test_correlated_cluster_above_50_percent_blocks_same_side_entries(tmp_path=None):
    e, now = Env(tmp_path), _wall()
    G.reset_ledger()
    rnd = random.Random(7)
    base, other = [100.0], [100.0]
    for _ in range(35):
        base.append(base[-1] * (1 + rnd.gauss(0, 0.03)))
        other.append(other[-1] * (1 + rnd.gauss(0, 0.03)))
    noise = random.Random(8)
    _closes(e.adb, now, {"AAA": base, "BBB": [x * (1 + noise.gauss(0, 0.002)) for x in base],
                         "DDD": [x * 2 * (1 + noise.gauss(0, 0.002)) for x in base], "CCC": other})
    st = e.step({"p1": (10_000.0, [("AAA", "LONG", 4_000.0), ("DDD", "LONG", 1_500.0)], 1),
                 "p2": (10_000.0, [("BBB", "LONG", 4_000.0), ("CCC", "LONG", 1_000.0)], 1)}, now)
    cl = next(c for c in st["clusters"] if "AAA" in c["members"])
    assert sorted(cl["members"]) == ["AAA", "BBB", "DDD"] and "CCC" not in st["cluster_of"]
    assert _lim(st, "cluster")["status"] == "warn"                                    # 9 500 / 20 000 = 47.5 %
    ok, why = e.allow("p2", "BBB", "LONG", 800, now)                                  # → 51.5 %, the coin itself only 24 %
    assert not ok and "кластер" in why
    assert e.allow("p1", "AAA", "SHORT", 800, now)[0]                                 # a hedge lowers the cluster's net
    assert e.allow("p2", "CCC", "LONG", 800, now)[0]                                  # an unrelated coin
    pub = e.public()
    assert pub["clusters"] and sorted(pub["clusters"][0]["members"]) == ["AAA", "BBB", "DDD"]


# ───────────────────────────────────────────────────────────────────────────── stale data / stale state / kill / conveyor
def test_stale_market_data_blocks_all_entries_and_fresh_data_releases(tmp_path=None):
    e, now = Env(tmp_path), _wall()
    G.reset_ledger()
    st = e.step({"a": (10_000.0, [], 1)}, now, fresh=False)
    assert st["state"] == "HALT" and st["data"]["age_min"] > 10 and any(b["kind"] == "stale_data" for b in st["blocks"])
    ok, why = e.allow("a", "BTC", "LONG", 10, now)
    assert not ok and "устарели" in why
    st = e.step({"a": (10_000.0, [], 1)}, now + MIN)
    assert st["data"]["age_min"] < 1 and e.allow("a", "BTC", "LONG", 10, now + MIN)[0]
    assert P.data_freshness(os.path.join(e.root, "missing.sqlite"), now)["last_ts"] is None


def test_a_stale_or_missing_risk_state_and_replays(tmp_path=None):
    e, now = Env(tmp_path), _wall()
    G.reset_ledger()
    assert e.allow("a", "BTC", "LONG", 10, now) == (True, "")                         # no state yet (first start / GitHub)
    e.step({"a": (10_000.0, [], 1)}, now)
    ok, why = e.allow("a", "BTC", "LONG", 10, now + 11 * MIN)                         # the risk step stopped → closed
    assert not ok and "не обновлялся" in why
    with open(os.path.join(e.data, L.STATE_FILE), "w") as f:
        f.write("{broken")
    ok, why = e.allow("a", "BTC", "LONG", 10, now)
    assert not ok and "не читается" in why
    G.set_kill(True, "тест", "test", base=e.data, now=now)
    assert G.allow_entry("a", "BTC", "LONG", 10, now - 2 * DAY, base=e.data, wall=now) == (True, "")   # a replay of history


def test_kill_switch_on_off_status_and_log(tmp_path=None):
    e, now = Env(tmp_path), _wall()
    G.reset_ledger()
    e.step({"a": (10_000.0, [], 1)}, now)
    assert G.kill_status(e.data)["on"] is False
    G.set_kill(True, "личная причина владельца", "test", base=e.data, now=now)
    ok, why = e.allow("a", "BTC", "LONG", 10, now)
    assert not ok and "выключатель" in why and "личная" not in why
    st = e.step({"a": (10_000.0, [], 1)}, now + MIN)
    pub = e.public()
    assert st["state"] == "HALT" and pub["kill_switch"]["on"] is True and "личная" not in json.dumps(pub, ensure_ascii=False)
    G.set_kill(False, "", "test", base=e.data, now=now + 2 * MIN)
    assert e.allow("a", "BTC", "LONG", 10, now + 2 * MIN) == (True, "")
    k = G.kill_status(e.data)
    assert [h["on"] for h in k["history"]] == [True, False]
    lines = [json.loads(x) for x in open(os.path.join(e.data, L.LOG_FILE), encoding="utf-8")]
    assert [x["kind"] for x in lines if x["kind"].startswith("kill")] == ["kill_on", "kill_off"]
    with open(os.path.join(e.data, L.KILL_FILE), "w") as f:                              # unreadable switch = ON (fail-closed)
        f.write("not json")
    assert G.kill_status(e.data)["on"] is True and not e.allow("a", "BTC", "LONG", 10, now + 2 * MIN)[0]


def test_kill_cli(tmp_path=None):
    from cnq import cli
    d = tempfile.mkdtemp()
    with risk_dir(d):
        assert cli.main(["kill", "on"]) == 2                                              # a reason is required
        assert cli.main(["kill", "on", "--reason", "тест"]) == 0 and G.kill_status()["on"] is True
        assert cli.main(["kill", "status"]) == 0
        assert cli.main(["kill", "off"]) == 0 and G.kill_status()["on"] is False
        assert cli.main(["risk", "reset", "--reason", "тест"]) == 0 and os.path.exists(os.path.join(d, L.RESET_FILE))
        assert cli.main(["risk", "status"]) == 0


def test_gate_reads_the_conveyor_contract(tmp_path=None):
    e, now = Env(tmp_path), _wall()
    G.reset_ledger()
    e.step({"a": (10_000.0, [], 1)}, now)
    p = os.path.join(e.data, L.CONVEYOR_FILE)
    G.write_json(p, {"desks": {"bal": {"stage": "paper", "paused": True, "reason_ru": "5 убыточных сделок подряд"},
                               "lab": {"stage": "retired", "paused": False, "reason_ru": "не прошла проверку"},
                               "own": {"stage": "ready", "paused": False}, "brk": {"stage": "lab", "paused": False}}})
    ok, why = e.allow("bal", "BTC", "LONG", 10, now)
    assert not ok and "пауз" in why and "5 убыточных" in why
    ok, why = e.allow("lab", "BTC", "LONG", 10, now)
    assert not ok and "снята" in why
    assert e.allow("own", "BTC", "LONG", 10, now)[0] and e.allow("brk", "BTC", "LONG", 10, now)[0]
    assert e.allow("aggr", "BTC", "LONG", 10, now)[0]                                    # not in the file → allowed
    st = e.step({"a": (10_000.0, [], 1)}, now + MIN)
    assert set(st["conveyor"]) == {"bal", "lab"} and e.public()["conveyor_paused"] == ["bal", "lab"]
    for bad in ("[1, 2]", "{broken", json.dumps({"desks": ["bal"]}), json.dumps({"desks": {"bal": "paused"}})):
        with open(p, "w") as f:
            f.write(bad)
        assert e.allow("bal", "BTC", "LONG", 10, now + MIN)[0], bad                       # a malformed file never blocks
    os.remove(p)
    assert e.allow("bal", "BTC", "LONG", 10, now + MIN)[0]


# ───────────────────────────────────────────────────────────────────────────── the desks record a refused entry
def _coin(symbol, now, n=300, price=100.0):
    t0 = now // 900_000 * 900_000 - (n - 1) * 900_000
    return {"symbol": symbol, "candles": [[t0 + i * 900_000, price, price * 1.004, price * 0.996, price, 100.0] for i in range(n)],
            "levels": [], "regime": {"long": "BULL"}}


def test_paper_desk_records_the_skip_and_never_reopens_that_signal():
    from cnq.agents import paper_desk as pd
    d = tempfile.mkdtemp()
    with risk_dir(d):
        now = _wall()
        conn = sqlite3.connect(":memory:")
        c = _coin("SOL-USDT", now)
        c["basis_1h"] = -0.003                                                           # rule D fires
        G.set_kill(True, "тест", "test", now=now)
        res = pd.PaperDesk(conn, "bal").step([c], True, now)
        assert not res["open"] and any("риск-слой" in ev and "выключатель" in ev for ev in res["events"])
        sid = f"D-SOL-{now // 3_600_000}"
        row = conn.execute("SELECT desk, symbol, side, notional, reason FROM risk_skips WHERE id=?", (sid,)).fetchone()
        assert row[0] == "bal" and row[1] == "SOL" and row[2] == "LONG" and abs(row[3] - 1500) < 5 and "выключатель" in row[4]
        G.set_kill(False, "", "test", now=now)
        res = pd.PaperDesk(conn, "bal").step([c], True, now + 60_000)                    # the same signal: refused once = final
        assert not res["open"] and not [ev for ev in res["events"] if "риск-слой" in ev]
        c2 = _coin("SOL-USDT", now + 3_600_000)                                           # an hour later the candles are an hour newer
        c2["basis_1h"] = -0.003                                                          # (22.10: a market entry needs a fresh bar)
        res = pd.PaperDesk(conn, "bal").step([c2], True, now + 3_600_000)                # the next hour's signal opens
        assert [t["grade"] for t in res["open"]] == ["D"]


def test_paper_desk_limit_fill_is_gated_and_the_order_cancelled():
    from cnq.agents import paper_desk as pd
    d = tempfile.mkdtemp()
    with risk_dir(d):
        now = _wall()
        conn = sqlite3.connect(":memory:")
        desk = pd.PaperDesk(conn, "lab")
        c = _coin("ADA-USDT", now, n=250, price=1.0)
        conn.execute(f"INSERT INTO {desk.T_ORD} VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("L-ADA-1", "ADA", "LONG", "L", "RANGE_LOW_LIMIT", 0.999, 0.9, json.dumps([1.1] * 3), json.dumps([1, 0, 0]),
                      100.0, 10.0, now - 2 * 3_600_000, now + 3_600_000, "PENDING", "test"))
        conn.commit()
        G.set_kill(True, "тест", "test", now=now)
        res = desk.step([c], True, now)
        assert conn.execute(f"SELECT status FROM {desk.T_ORD} WHERE id='L-ADA-1'").fetchone()[0] == "CANCELLED"
        assert not res["open"] and conn.execute("SELECT COUNT(*) FROM risk_skips WHERE id='L-ADA-1'").fetchone()[0] == 1
        assert any("снята при исполнении" in ev for ev in res["events"])


def test_listing_desk_records_the_skip_and_does_not_retry():
    from cnq.agents import listing_desk as LD
    d = tempfile.mkdtemp()
    with risk_dir(d):
        H = 3_600_000
        t0 = _wall() - int(5.5 * H)
        mk = lambda **cs: (lambda: {s: {"launch": None, "price": p, "high24": p, "funding": 0.0, "qvol": 5e6} for s, p in cs.items()})  # noqa: E731
        conn = sqlite3.connect(":memory:")
        LD.step(conn, t0, mk(**{"BTC-USDT": 1.0}))
        LD.step(conn, t0 + H // 10, mk(**{"BTC-USDT": 1.0, "NEW-USDT": 1.0}))
        G.set_kill(True, "тест", "test")
        a = LD.step(conn, t0 + int(4.2 * H), mk(**{"BTC-USDT": 1.0, "NEW-USDT": 1.0}))
        assert a["open"] == [] and any("риск-слой" in ev for ev in a["events"])
        assert conn.execute("SELECT seeded FROM lst_known WHERE symbol='NEW-USDT'").fetchone()[0] == 5
        assert conn.execute("SELECT symbol, side FROM risk_skips WHERE desk='lst'").fetchone() == ("NEW", "SHORT")
        G.set_kill(False, "", "test")
        b = LD.step(conn, t0 + int(4.4 * H), mk(**{"BTC-USDT": 1.0, "NEW-USDT": 1.0}))
        assert b["open"] == []


def test_own_desk_records_the_skip():
    from cnq.agents import own_desk as od
    d = tempfile.mkdtemp()
    with risk_dir(d):
        now = _wall()
        if now % DAY < 6 * MIN:
            now += 6 * MIN
        day = now // DAY * DAY
        up = [100 * 1.004 ** i for i in range(260)]
        coin = up[:199] + [up[198] * 1.06]

        def daily(sym):
            src = up[:200] if sym == "BTC" else coin if sym == "ETH" else None
            if src is None:
                raise RuntimeError("no data")
            rows, prev = [], src[0]
            for i, c in enumerate(src):
                rows.append([day - (200 - i) * DAY, prev, max(prev, c) * 1.01, min(prev, c) * 0.99, c, 3000.0 if i == 199 else 1000.0])
                prev = c
            rows.append([day, src[-1], src[-1], src[-1], src[-1], 10.0])
            return rows
        G.set_kill(True, "тест", "test")
        conn = sqlite3.connect(":memory:")
        r = od.step(conn, now, daily=daily, prices=lambda: {})
        assert conn.execute("SELECT COUNT(*) FROM own_trades").fetchone()[0] == 0
        assert conn.execute("SELECT desk, symbol FROM risk_skips").fetchone() == ("own", "ETH")
        assert any("риск-слой" in ev for ev in r["events"])


def test_neutral_basket_postpones_the_whole_rebalance(tmp_path):
    from cnq.agents import ls_desk as LS
    d = tempfile.mkdtemp()
    with risk_dir(d):
        now = _wall()
        (tmp_path / "quality.json").write_text(json.dumps({"rows": [{"symbol": f"C{k}", "rank": k + 1} for k in range(40)]}))
        (tmp_path / "paper.json").write_text(json.dumps({"profiles": {}, "profile_list": []}))
        px = {f"C{k}": 10.0 for k in range(40)}
        daily = lambda s: [[k * DAY, 10.0, 10.0, 10.0, 10.0 * (1.01 if k % 2 else 0.99), 1.0] for k in range(40)]  # noqa: E731
        conn = sqlite3.connect(":memory:")
        G.set_kill(True, "тест", "test")
        a = LS.run(conn, str(tmp_path), now=now, prices=lambda: px, funding=lambda: {}, daily=daily)
        assert a["open"] == [] and any("ребаланс отложен" in ev for ev in a["events"])
        assert conn.execute("SELECT id FROM risk_skips WHERE desk='lsn'").fetchone()[0].startswith("LSN-")
        G.set_kill(False, "", "test")
        b = LS.run(conn, str(tmp_path), now=now + MIN, prices=lambda: px, funding=lambda: {}, daily=daily)
        assert len(b["open"]) == 20


def test_btc_x2_stays_in_dollars_while_blocked_and_still_exits(tmp_path):
    from cnq.agents import turbo_desk as TD
    d = tempfile.mkdtemp()
    with risk_dir(d):
        now = _wall()
        w = str(tmp_path)

        def files(sma):
            json.dump({"profiles": {}}, open(os.path.join(w, "paper.json"), "w"))
            json.dump({"coins": [{"symbol": "BTC-USDT", "price": 80_000.0, "regime": {"sma200": sma, "r60": 0.1}}]},
                      open(os.path.join(w, "decision.json"), "w"))
        conn = sqlite3.connect(":memory:")
        files(70_000.0)
        G.set_kill(True, "тест", "test")
        a = TD.step(conn, w, now)
        assert a["btcx"]["in_market"] is False and "отложен" in a["btcx"]["events"][0]
        assert conn.execute("SELECT desk, symbol FROM risk_skips").fetchone() == ("btcx", "BTC")
        G.set_kill(False, "", "test")
        b = TD.step(conn, w, now + MIN)
        assert b["btcx"]["in_market"] is True
        G.set_kill(True, "тест", "test")
        files(90_000.0)                                                                  # regime off → the exit is never held back
        c = TD.step(conn, w, now + 86_400_000)                                       # the regime is read at the next daily close
        assert c["btcx"]["in_market"] is False and "доллары" in c["btcx"]["events"][0]


# ───────────────────────────────────────────────────────────────────────────── robustness and wiring
def test_the_step_never_raises_on_missing_or_broken_inputs(tmp_path=None):
    e, now = Env(tmp_path), _wall()
    st, err = P.run(e.web, now, base=e.data, agents_db=e.adb, candles_db=e.cdb)          # no paper.json, no databases
    assert err and st["state"] == "HALT" and os.path.exists(os.path.join(e.web, "risk.json"))
    with open(os.path.join(e.web, "paper.json"), "w") as f:
        f.write("{not json")
    st, err = P.run(e.web, now, base=e.data, agents_db=e.adb, candles_db=e.cdb)
    assert err and json.load(open(os.path.join(e.web, "risk.json")))["error"]
    with open(os.path.join(e.web, "paper.json"), "w") as f:
        json.dump({"profiles": {"x": 5, "y": {"equity": "abc"}, "z": {"equity": 10_000, "open": [{"symbol": None, "qty": "q"}, 7]}}}, f)
    with open(os.path.join(e.data, L.STATE_FILE), "w") as f:
        f.write("[[[")
    for name in (L.KILL_FILE, L.CONVEYOR_FILE, L.RESET_FILE):
        with open(os.path.join(e.data, name), "w") as f:
            f.write("\x00garbage")
    st, err = P.run(e.web, now, base=e.data, agents_db=os.path.join(e.root, "nope.sqlite"), candles_db=e.cdb)
    assert st["state"] == "HALT" and st["kill"]["on"] is True                            # unreadable kill switch = on
    with open(os.path.join(e.data, "agents.sqlite"), "w") as f:
        f.write("not a database")
    st, err = P.run(e.web, now, base=e.data, agents_db=os.path.join(e.data, "agents.sqlite"), candles_db=os.path.join(e.data, "agents.sqlite"))
    assert isinstance(st, dict)
    ok, why = G.allow_entry("a", "BTC", "LONG", 10, now, base=e.data, wall=now)
    assert ok is False and why


def test_gate_internal_error_blocks_and_never_raises(tmp_path=None):
    e, now = Env(tmp_path), _wall()
    G.write_json(os.path.join(e.data, L.STATE_FILE), {"updated_at": now, "latch": {}, "data": {"last_ts": now},
                                                       "accounts": "garbage", "combined": {}})
    ok, why = e.allow("a", "BTC", "LONG", 10, now)
    assert ok is False and "ошибка риск-слоя" in why


def test_engine_runs_risk_first_and_publishes_risk_json():
    from cnq import engine as E
    assert E.FAST[0] == "risk" and E.FAST.index("risk") < E.FAST.index("paper") and "risk" in E.STEP_TIMEOUT
    assert "risk.json" in E.OWNED and {"kill_switch.json", "risk_state.json", "risk_reset.json"} <= E.DB_OWNED


def test_public_feed_is_bilingual_and_has_every_limit(tmp_path=None):
    e, now = Env(tmp_path), _wall()
    G.reset_ledger()
    e.step({"a": (10_000.0, [("BTC", "LONG", 1_000.0)], 1)}, now)
    pub = e.public()
    assert pub["state"] == "OK" and pub["headline"]["ru"] and pub["headline"]["en"]
    assert [x["key"] for x in pub["limits"]] == ["daily_loss", "drawdown", "leverage", "coin", "cluster", "data_age"]
    for x in pub["limits"]:
        assert x["name"]["ru"] and x["name"]["en"] and x["status"] in ("ok", "warn", "breach") and x["limit"] is not None
    assert pub["kill_switch"] == {"on": False, "since": None} and pub["not_covered"]["ru"] and pub["rules"]["en"]
    assert pub["updated_at"] == now and pub["accounts"][0]["key"] == "a"
