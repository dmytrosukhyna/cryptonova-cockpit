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


def test_stage4_app_home_has_neutral_trade_ids_and_the_app_logic_still_resolves():
    """11.11 stage 4 (app_home part): no internal trade id («whales:», «ivan:», «aggr:») in app_home*.json / /v2/home; the shipped
    app (builds 54–57, FuturesScreen.copyWhy) still finds every system trade by copy_whale / why_full.copy"""
    import re
    import shutil
    import sqlite3
    from cnq.agents import app_home as AH

    def app_is_copy(row):                       # FuturesScreen.kt: p.copyWhale == true || p.sig?.startsWith("whales:") == true || w?.copy == true
        return row.get("copy_whale") is True or str(row.get("sig") or "").startswith("whales:") or (row.get("why_full") or {}).get("copy") is True

    def rows(doc, acc=None):
        acc = [] if acc is None else acc
        if isinstance(doc, dict):
            if "sig" in doc:
                acc.append(doc)
            for v in doc.values():
                rows(v, acc)
        elif isinstance(doc, list):
            for v in doc:
                rows(v, acc)
        return acc
    inner = {"asof": 1, "public_portfolio": {"open": [
        {"sym": "ETH", "sig": "whales:W-ETH-L-1794311443596", "copy_whale": True, "why_full": {"title": "Сделка системы", "copy": True}},
        {"sym": "BTC", "sig": "aggr:E-BTC-1794174300000", "why_full": {"title": "Откуп паники"}}],
        "closed": [{"sym": "ZEC", "sig": "whales:W-ZEC-S-1793915173877", "copy_whale": True, "why_full": {"copy": True}}]},
        "public": {"real": {"futures": [{"inst": "ETH-USDT-SWAP", "sig": "whales:W-ETH-L-1794311443596"}, {"inst": "X", "sig": "ivan:W-X-L-1"}],
                            "copy_skips": [{"sig": "aggr:E-BCH-1794174300000", "why": "…"}], "actions": [{"kind": "spot_buy", "sig": None}]}}}
    before = json.dumps(inner, ensure_ascii=False)
    out = AH.public_edge(inner)
    assert json.dumps(inner, ensure_ascii=False) == before                               # the internal document is untouched
    txt = json.dumps(out, ensure_ascii=False)
    assert not re.search(r"whales:|ivan:|aggr:|W-ETH|E-BTC", txt) and txt.count('"cn:') == 6
    a, b = rows(inner), rows(out)
    assert [app_is_copy(x) for x in a] == [app_is_copy(x) for x in b] == [True, False, True, True, False, False, False]
    assert out["public_portfolio"]["open"][0]["why_full"]["title"] == "Сделка системы" and "copy_whale" not in out["public_portfolio"]["open"][1]
    assert out["public"]["real"]["futures"][0]["sig"] == out["public_portfolio"]["open"][0]["sig"]      # one trade → one neutral id
    assert AH.public_sig("whales:W-ETH-L-1") != AH.public_sig("whales:W-ETH-L-2") and len(AH.public_sig("x:1")) == 17
    # the real builder on the repo's data: the files it writes carry no internal id, and the builder itself still sees them
    tmp = tempfile.mkdtemp(); shutil.rmtree(tmp); shutil.copytree(os.path.join(ROOT, "web"), tmp)
    ru = AH.run(sqlite3.connect(":memory:"), tmp)
    for f in os.listdir(tmp):
        if re.fullmatch(r"app_home(\.[a-z]{2})?\.json", f):
            s = open(os.path.join(tmp, f), encoding="utf-8").read()
            assert not re.search(r'"sig":"(?!cn:)[^"]', s), f
            d = json.loads(s)
            for r_ in rows(d.get("public_portfolio") or {}):
                if r_.get("copy_whale"):
                    assert (r_.get("why_full") or {}).get("copy") is True, (f, r_.get("sym"))
    inner_sigs = [x["sig"] for x in rows(ru) if isinstance(x.get("sig"), str)]
    assert all(not s.startswith("cn:") for s in inner_sigs)                              # run() returns the internal document
