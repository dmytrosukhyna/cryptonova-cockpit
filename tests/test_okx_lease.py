"""«Замок» автопилота OKX (cnq/execution/okx_lease.py): two autopilots never trade one account at once — the right machine only,
one live window, a global stop, fail safe without a token or with an unreadable lease, a frozen process can not send an order
after its lease ended, and the order cycles (spot, futures, active spot) really ask it. A fake GitHub checks the version (sha)
on every write like the contents API does. Every key and amount here is made-up test data; GitHub and OKX are never called."""
import base64
import json
import os
import tempfile

import okx_fake as F
from cnq.execution import okx_active as ac
from cnq.execution import okx_autopilot as ap
from cnq.execution import okx_futures as fu
from cnq.execution import okx_lease as L

ap.LOG = os.path.join(tempfile.mkdtemp(), "okx_autopilot.jsonl")
fu.LOG = os.path.join(tempfile.mkdtemp(), "okx_futures.jsonl")
ac.LOG = os.path.join(tempfile.mkdtemp(), "okx_active.jsonl")
NOW = F.NOW
MIN = 60_000


class FakeGitHub:
    """The contents API for one file on one branch + the refs API: a PUT must name the current sha (409 otherwise)."""

    def __init__(self, branch_exists=False, down=False):
        self.branch, self.file, self.sha, self.n, self.down, self.calls = branch_exists, None, None, 0, down, []

    def __call__(self, method, url, headers, data=None):
        self.calls.append((method, url))
        assert headers["Authorization"] == "Bearer GHTOKEN-test"
        if self.down:
            raise OSError("network down")
        body = json.loads(data) if data else None
        if method == "GET" and "/contents/okx_lease.json?ref=okx-lease" in url:
            if not self.branch or self.file is None:
                return 404, {"message": "Not Found"}
            return 200, {"sha": self.sha, "content": base64.encodebytes(self.file.encode()).decode()}
        if method == "GET" and url.endswith("/git/ref/heads/main"):
            return 200, {"object": {"sha": "mainsha"}}
        if method == "POST" and url.endswith("/git/refs"):
            assert body == {"ref": "refs/heads/okx-lease", "sha": "mainsha"}
            if self.branch:
                return 422, {"message": "Reference already exists"}
            self.branch = True
            return 201, {}
        if method == "PUT" and url.endswith("/contents/okx_lease.json"):
            assert body["branch"] == "okx-lease"                         # never main
            if not self.branch:
                return 404, {"message": "Branch okx-lease not found"}
            if (self.file is None and body.get("sha")) or (self.file is not None and body.get("sha") != self.sha):
                return (422 if self.file is not None and not body.get("sha") else 409), {"message": "does not match"}
            self.n += 1
            self.file, self.sha = base64.b64decode(body["content"]).decode(), f"sha{self.n}"
            return (201 if self.n == 1 else 200), {}
        return 500, {"message": f"unexpected {method} {url}"}

    def doc(self):
        return json.loads(self.file) if self.file else None


def gh_env(**kw):
    e = {"CNQ_GH_TOKEN": "GHTOKEN-test", "CNQ_GH_REPO": "o/r"}
    e.update(kw)
    return e


def fresh():
    L._CACHE.clear()


def test_github_store_creates_its_own_branch_and_compares_versions():
    fresh()
    gh = FakeGitHub()
    s = L.store(gh_env(), http=gh)
    g = L.gate(gh_env(OKX_LEASE_ID="mac-w1"), NOW, st=s)
    assert g["ok"] and g["until"] == NOW + L.TTL_MS
    d = gh.doc()
    assert d["node"] == "mac" and d["holder"] == "mac-w1" and not d["stop"]
    assert ("POST", "https://api.github.com/repos/o/r/git/refs") in gh.calls      # the branch okx-lease, made once
    assert not any("ref=main" in u or u.endswith("/contents/okx_lease.json?ref=main") for _, u in gh.calls)
    doc, sha = s.read()
    assert s.write({**doc, "holder": "x"}, "stale-sha") is False                  # an old version never overwrites a newer one
    assert s.write({**doc, "holder": "x"}, sha) is True
    text = json.dumps(gh.doc())
    for bad in ("GHTOKEN", "APKEY", "SECRET"):
        assert bad not in text


def test_one_window_trades_the_second_waits_then_takes_over_after_the_lease():
    fresh()
    s = L.FileStore(os.path.join(tempfile.mkdtemp(), "lease.json"))
    a, b = gh_env(OKX_LEASE_ID="mac-a"), gh_env(OKX_LEASE_ID="mac-b")
    assert L.gate(a, NOW, st=s)["ok"]
    g = L.gate(b, NOW + MIN, st=s)
    assert not g["ok"] and "другое окно" in g["why"] and "mac-a" in g["why"]
    assert L.gate(a, NOW + 6 * MIN, st=s)["ok"]                                  # renewed (less than 10 min were left)
    assert s.read()[0]["until"] == NOW + 6 * MIN + L.TTL_MS
    assert not L.gate(b, NOW + 6 * MIN + L.TTL_MS, st=s)["ok"]                   # just expired: still the 1-minute clock margin
    fresh()
    assert L.gate(b, NOW + 6 * MIN + L.TTL_MS + L.SKEW_MS + 1, st=s)["ok"]      # a dead window's lease ends, b takes it
    assert s.read()[0]["holder"] == "mac-b"
    fresh()
    assert not L.gate(a, NOW + 23 * MIN, st=s)["ok"]                             # a woke up: it is not the holder any more


def test_restart_in_the_same_window_resumes_at_once_and_a_clean_exit_releases():
    fresh()
    s = L.FileStore(os.path.join(tempfile.mkdtemp(), "lease.json"))
    w = gh_env(OKX_LEASE_ID="mac-w")
    assert L.gate(w, NOW, st=s)["ok"]
    fresh()                                                                       # a new process, same window id
    assert L.gate(w, NOW + MIN, st=s)["ok"]
    assert L.release(w, st=s) and s.read()[0]["holder"] is None
    assert L.gate(gh_env(OKX_LEASE_ID="mac-other"), NOW + 2 * MIN, st=s)["ok"]   # released → another window may start now


def test_the_other_machine_never_trades_and_switching_waits_for_a_live_window():
    fresh()
    s = L.FileStore(os.path.join(tempfile.mkdtemp(), "lease.json"))
    mac, srv = gh_env(OKX_LEASE_ID="mac-w"), gh_env(OKX_LEASE_ID="server", OKX_NODE="server")
    g = L.gate(srv, NOW, st=s)
    assert not g["ok"] and "переведён на Мак" in g["why"]                       # default: the Mac
    assert L.gate(mac, NOW, st=s)["ok"]
    ok, why = L.switch(srv, "server", NOW + MIN, st=s)
    assert not ok and "ещё работает окно" in why and s.read()[0]["node"] == "mac"   # install refuses while the Mac trades
    L.release(mac, st=s)
    ok, why = L.switch(srv, "server", NOW + 2 * MIN, st=s)
    assert ok and s.read()[0]["node"] == "server"
    fresh()
    g = L.gate(mac, NOW + 3 * MIN, st=s)
    assert not g["ok"] and "переведён на сервер" in g["why"]
    assert L.gate(srv, NOW + 3 * MIN, st=s)["ok"]
    ok, _ = L.switch(mac, "mac", NOW + 4 * MIN, st=s)                             # uninstall: back only after the server let go
    assert not ok
    L.release(srv, st=s)
    assert L.switch(mac, "mac", NOW + 5 * MIN, st=s)[0] and L.status(mac, NOW, st=s)["node"] == "mac"


def test_a_window_that_finds_the_autopilot_moved_lets_its_lease_go():
    fresh()
    s = L.FileStore(os.path.join(tempfile.mkdtemp(), "lease.json"))
    mac = gh_env(OKX_LEASE_ID="mac-w")
    assert L.gate(mac, NOW, st=s)["ok"]
    doc, sha = s.read()
    assert s.write({**doc, "node": "server"}, sha)                                 # moved by hand while the window held it
    fresh()
    assert not L.gate(mac, NOW + MIN, st=s)["ok"] and s.read()[0]["holder"] is None


def test_global_stop_stops_every_machine_until_resume():
    fresh()
    s = L.FileStore(os.path.join(tempfile.mkdtemp(), "lease.json"))
    w = gh_env(OKX_LEASE_ID="mac-w")
    assert L.gate(w, NOW, st=s)["ok"]
    assert L.set_stop(w, True, st=s)
    g = L.gate(w, NOW + MIN, st=s)
    assert not g["ok"] and "общий стоп" in g["why"]
    assert not L.gate(gh_env(OKX_NODE="server"), NOW + MIN, st=s)["ok"]
    assert L.set_stop(w, False, st=s) and L.gate(w, NOW + 2 * MIN, st=s)["ok"]


def test_fail_safe_without_token_and_when_the_lease_can_not_be_read():
    fresh()
    g = L.gate({"OKX_LEASE_ID": "mac-w"}, NOW)
    assert not g["ok"] and "CNQ_GH_TOKEN" in g["why"]
    gh = FakeGitHub(down=True)
    g = L.gate(gh_env(OKX_LEASE_ID="mac-w"), NOW, st=L.store(gh_env(), http=gh))
    assert not g["ok"] and "не читается" in g["why"]
    gh = FakeGitHub()
    s = L.store(gh_env(), http=gh)
    w = gh_env(OKX_LEASE_ID="mac-w")
    assert L.gate(w, NOW, st=s)["ok"]
    gh.down = True
    assert L.gate(w, NOW + 6 * MIN, st=s)["ok"]                                   # our lease still runs ≥ 3 min: nobody can have it
    g = L.gate(w, NOW + 12 * MIN, st=s)
    assert not g["ok"] and "не читается" in g["why"]                             # too close to its end without a renewal → no orders


def test_a_frozen_process_can_not_send_an_order_after_its_lease_ended():
    fk = F.FakeOkx()
    cl = ap.Okx("k", "s", "p", demo=True, opener=fk)
    cl.lease_deadline = 1.0                                                       # long gone
    try:
        cl.call("POST", "/api/v5/trade/order", body={"instId": "BTC-USDT"})
        raise AssertionError("an order went out after the lease")
    except L.LeaseLost:
        pass
    assert not fk.orders and not fk.calls
    assert cl.call("GET", "/api/v5/account/config")                               # reading is always allowed
    g = {"ok": True, "until": NOW + L.TTL_MS}
    assert abs(L.deadline(g, NOW) - (__import__("time").time() * 1000 + L.TTL_MS - L.SKEW_MS)) < 5000


def test_every_order_cycle_asks_the_lease_and_a_second_window_places_nothing():
    fresh()
    tmp = tempfile.mkdtemp()
    e = F.env(tmp, CNQ_LIVE_TRADING="yes", OKX_AUTOPILOT="on", OKX_LEASE_ID="mac-other")
    other = L.FileStore(e["OKX_LEASE_FILE"])
    assert L.gate({"OKX_LEASE_ID": "mac-first"}, NOW, st=other)["ok"]              # another window holds the account
    fk = F.FakeOkx(demo_key=False, balances={"USDT": 9000.0, "DOGE": 5000.0})
    r = ap.run_once(e, fk, F.portfolio(), NOW + MIN, F.nosleep)
    assert not r["ok"] and "другое окно" in r["why"]
    f = fu.step(e, fk, desks=F.desks(F.trade()), now=NOW + MIN, sleep=F.nosleep)
    assert f["why"] and "другое окно" in f["why"] and "стопами на OKX" in f["why"]
    a = ac.step(e, fk, desk={"equity": 10000.0, "open": [F.trade("A1", "XRP", entry=2.0, stop=1.8, tp=2.4, opened=NOW)]},
                now=NOW + MIN, sleep=F.nosleep)
    assert a["why"] and "другое окно" in a["why"]
    assert not fk.orders and not fk.levers and not fk.algo_posts and not any(c[0] == "POST" for c in fk.calls)
    fresh()
    r = ap.run_once(e, fk, F.portfolio(), NOW + 17 * MIN, F.nosleep)               # the first window's lease ended → this one trades
    assert r["ok"] and fk.orders


def test_stop_writes_the_global_stop_and_resume_lifts_it():
    fresh()
    tmp = tempfile.mkdtemp()
    e = F.env(tmp, CNQ_LIVE_TRADING="yes", OKX_AUTOPILOT="on")
    msg = ap.stop(e, F.FakeOkx(demo_key=False))
    assert "Общий стоп записан" in msg and L.status(e)["stop"]
    assert "Стоп снят" in ap.resume(e) and not L.status(e)["stop"]
    e2 = F.env(tmp)
    e2.pop("OKX_LEASE_FILE")
    assert "НЕ записан" in ap.stop(e2)                                            # no token: the local stop still holds, it says so


def test_status_text_names_the_machine_and_the_window():
    fresh()
    s = L.FileStore(os.path.join(tempfile.mkdtemp(), "lease.json"))
    w = gh_env(OKX_LEASE_ID="mac-w")
    assert L.gate(w, L.now_ms(), st=s)["ok"]
    st = L.status(w, st=s)
    assert st["node"] == "mac" and st["holder"] == "mac-w" and not st["stop"]
    assert "Замок автопилота: автопилот на Маке · сейчас торгует mac-w" in L.status_text({"OKX_LEASE_FILE": s.path})
    assert "нет строки CNQ_GH_TOKEN" in L.status_text({})
