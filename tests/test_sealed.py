"""11.11 (owner: the repo stays public — «всё, что можно — закрой»): owner files of the pipeline are published only sealed to the
server's key; the owner endpoint opens them; no trader names in the public watch file; the public status says nothing about copying."""
import json
import os
import stat
import sys
import tempfile

from cnq import sealed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))


def test_seal_roundtrip_key_file_and_wrong_key():
    d, other = tempfile.mkdtemp(), tempfile.mkdtemp()
    pub = sealed.public_key(d)
    assert len(pub) == 44 and sealed.public_key(d) == pub
    assert stat.S_IMODE(os.stat(os.path.join(d, sealed.KEY_FILE)).st_mode) == 0o600
    blob = sealed.seal(pub, "секрет 🟢".encode())
    assert "секрет" not in json.dumps(blob, ensure_ascii=False) and set(blob) == {"v", "alg", "asof", "epk", "nonce", "ct"}
    assert sealed.open_(d, blob).decode() == "секрет 🟢"
    try:
        sealed.open_(other, blob)
        assert False
    except Exception:  # noqa: BLE001
        pass


def test_pipeline_step_seals_and_removes_the_plaintext_or_changes_nothing():
    import ci_seal
    web, data = tempfile.mkdtemp(), tempfile.mkdtemp()
    doc = {"asof": 1, "rows": [{"key": "autopilot", "note": "внутреннее"}]}
    json.dump(doc, open(os.path.join(web, "owner_health.json"), "w"), ensure_ascii=False)
    json.dump({"x": 1}, open(os.path.join(web, "markets.json"), "w"))

    def down():
        raise OSError("api down")
    assert ci_seal.run(web, fetch=down, git=False) == [] and os.path.exists(os.path.join(web, "owner_health.json"))   # no key → as before
    pub = sealed.public_key(data)
    assert ci_seal.run(web, fetch=lambda: {"key": pub}, git=False) == ["owner_health"]
    assert not os.path.exists(os.path.join(web, "owner_health.json")) and os.path.exists(os.path.join(web, "markets.json"))
    raw = open(os.path.join(web, "sealed", "owner_health.json"), encoding="utf-8").read()
    assert "внутреннее" not in raw and "autopilot" not in raw
    assert sealed.load(web, data, "owner_health") == doc and sealed.load(web, tempfile.mkdtemp(), "owner_health") is None
    json.dump(doc, open(os.path.join(web, "gh_runs.json"), "w"))
    assert ci_seal.run(web, fetch=down, git=False) == ["gh_runs"]                 # the API is down → the cached key is used


def test_routes_panel_and_public_wording():
    r = lambda *p: open(os.path.join(ROOT, *p), encoding="utf-8").read()
    srv = r("cnq", "api", "server.py")
    i = srv.index("def _owner(")
    assert 'path == "/v2/pubkey"' in srv[:i] and '/v2/owner/sealed/([a-z_]+)' in srv[i:] and "in sealed.NAMES" in srv[i:]
    yml = r(".github", "workflows", "site.yml")
    assert yml.index("python -m cnq.agents.owner_health") < yml.index("python scripts/ci_seal.py") < yml.index("git add web/sealed")
    assert "V.sealed('owner_health')" in r("web", "vip", "health.js") and "'/v2/owner/sealed/' + name" in r("web", "vip", "core.js")
    from cnq.agents import owner_watch as W, vip_feeds as VF
    d = tempfile.mkdtemp()
    json.dump({"whales": [{"name": "法兰西基金", "addr": "bn:5236187913165742336", "positions": [{"coin": "ADA", "side": "LONG", "notional": 2e6}]}]},
              open(os.path.join(d, "whale_watch.json"), "w"))
    out = json.dumps(W.build(d, {}, 1794250000000), ensure_ascii=False)
    assert "法兰西基金" not in out and "5236187913165742336" not in out and "трейдер #" in out
    st = VF.system_status(d, 1794250000000, None, None, None,
                          (1794250000000, "11.11 18:12 (Афины) — без шока, BTC 73.9k. Копирование: стол сервера 2 = реальный счёт 2 (ETH-лонг)"))
    note = [c["note_ru"] for c in st["components"] if c["key"] == "researcher"][0]
    assert note == "11.11 18:12 (Афины) — без шока, BTC 73.9k." and "Копир" not in json.dumps(st, ensure_ascii=False)


def test_stage2_journal_and_watchlist_are_sealed_and_readers_cope():
    """11.11 stage 2: journal.json and whale_watch.json leave the public site; the panel reads them through the owner endpoint;
    the pipeline's own readers work with the file of the same run and do not misfire when it is absent"""
    assert {"journal", "whale_watch", "owner_health", "gh_runs"} <= set(sealed.NAMES)
    assert not {"fut_owner", "whales_top", "whales_live", "real_why", "owner_watch"} & set(sealed.NAMES)   # read back by the copy chain: stage 3
    r = lambda *p: open(os.path.join(ROOT, *p), encoding="utf-8").read()
    yml = r(".github", "workflows", "site.yml")
    for step in ("python -m cnq.cli whalestudy", "python -m cnq.agents.owner_watch", "python scripts/ci_journal.py", "python -m cnq.agents.owner_health"):
        assert yml.index(step) < yml.index("python scripts/ci_seal.py"), step               # every reader runs before the sealing
    wj, hj, app = r("web", "vip", "whales.js"), r("web", "vip", "health.js"), r("web", "app.html")
    assert "V.sealed('whale_watch')" in wj and "V.feed('whale_watch.json')" not in wj and "feeds(['whale_watch.json'" not in wj
    assert "V.sealed('owner_health')" in hj and "Данные временно недоступны." in hj
    assert "/v2/owner/sealed/journal" in app and "V.sealed = async function (name)" in r("web", "vip", "core.js")
    from cnq.agents import owner_watch as W
    d = tempfile.mkdtemp()
    NOW = 1794250000000
    json.dump({"whales": [{"name": "A", "addr": "bn:1", "positions": [{"coin": "ADA", "side": "LONG", "notional": 2e6}]}]},
              open(os.path.join(d, "whale_watch.json"), "w"))
    one = W.build(d, {}, NOW)
    json.dump(one, open(os.path.join(d, "owner_watch.json"), "w"))
    os.remove(os.path.join(d, "whale_watch.json"))                     # the next run has no watchlist (sealed / the study did not run)
    two = W.build(d, {}, NOW + 1800_000)
    ada = lambda doc: next(c for c in doc["coins"] if c["coin"] == "ADA")
    assert ada(two)["traders"] == ada(one)["traders"] and not [a for a in two["alerts"] if a["kind"] == "whale"]
    json.dump(two, open(os.path.join(d, "owner_watch.json"), "w"))
    json.dump({"whales": [{"name": "A", "addr": "bn:1", "positions": [{"coin": "ADA", "side": "LONG", "notional": 2e6}]}]},
              open(os.path.join(d, "whale_watch.json"), "w"))
    assert not [a for a in W.build(d, {}, NOW + 3600_000)["alerts"] if a["kind"] == "whale"]    # the same trader back → not «new»
