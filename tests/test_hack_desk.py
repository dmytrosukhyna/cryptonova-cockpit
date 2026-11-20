"""20.11 (owner): «Взломы → шорт (тест)» — PAPER, owner-only (cnq/agents/hack_desk.py, cnq/api/hacks.py). Made-up data only."""
import os
import tempfile

from cnq.agents import hack_desk as HD
from cnq.api import hacks as HA
from cnq.execution import okx_futures as fu

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = 1_795_170_000_000
TG_PAGE = '''<div class="tgme_widget_message" data-post="PeckShieldAlert/901"><div class="tgme_widget_message_text js-message_text">
#PeckShieldAlert Foobar Finance has been exploited for ~$12M, $FOO dropped 40%</div><time datetime="2026-11-20T10:00:00+00:00">10:00</time></div>
<div class="tgme_widget_message" data-post="PeckShieldAlert/902"><div class="tgme_widget_message_text">Weekly recap of funding rates</div>
<time datetime="2026-11-20T11:00:00+00:00">11:00</time></div>'''
RSS = '''<rss><channel><item><title>Barbaz Protocol drained in bridge attack</title><link>https://rekt.news/barbaz</link>
<pubDate>Thu, 20 Nov 2026 09:30:00 +0000</pubDate><description>attacker took $30M</description></item></channel></rss>'''
IDX = HD.protocol_index([{"id": "1", "name": "Barbaz Protocol", "symbol": "BAZ"}, {"id": "2", "name": "Foobar Finance", "symbol": "FOO"},
                         {"id": "3", "name": "Lido", "symbol": "LDO"}, {"id": "4", "name": "Ether Stable", "symbol": "USDT"}])
SWAPS = {"FOO-USDT-SWAP", "BAZ-USDT-SWAP"}


def test_sources_parse_and_tokens_map_without_majors():
    posts = HD.parse_tg(TG_PAGE, "PeckShieldAlert")
    assert [p["key"] for p in posts] == ["tg|PeckShieldAlert/901", "tg|PeckShieldAlert/902"]
    assert posts[0]["hack"] is True and posts[1]["hack"] is False and posts[0]["t_src"] == 1795168800000
    assert HD.token_of(posts[0], IDX) == "FOO"
    r = HD.parse_rss(RSS, "rekt")
    assert r[0]["hack"] and r[0]["link"] == "https://rekt.news/barbaz" and HD.token_of(r[0], IDX) == "BAZ"
    assert HD.token_of({"text": "hacker stole 500 $ETH and $USDC"}, IDX) is None
    assert HD.token_of({"defillama_id": 4, "text": ""}, IDX) is None                 # a stablecoin symbol is never shorted
    assert HD.from_defillama([{"name": "Lido", "date": 1, "defillamaId": 3}])[0]["defillama_id"] == 3


def test_first_run_is_a_baseline_then_a_new_alert_opens_two_paper_shorts_and_they_close():
    px = {"v": 1.0}
    hi = {"v": 1.0}
    kw = dict(px=lambda inst: px["v"], high=lambda inst, t0: hi["v"])
    src = {"tg:PeckShieldAlert": HD.parse_tg(TG_PAGE, "PeckShieldAlert")[1:], "rekt": "URLError"}
    st = HD.step(None, NOW, src, IDX, SWAPS, **kw)
    assert st["open"] == [] and st["incidents"] == [] and st["status"]["rekt"] == {"ok": False, "err": "URLError"}
    st = HD.step(st, NOW + 300_000, {"tg:PeckShieldAlert": HD.parse_tg(TG_PAGE, "PeckShieldAlert")}, IDX, SWAPS, **kw)
    inc = st["incidents"][-1]
    assert inc["token"] == "FOO" and inc["okx"] and inc["traded"] and inc["delay_s"] == (NOW + 300_000 - 1795168800000) // 1000
    assert sorted(o["hold"] for o in st["open"]) == ["24h", "7d"] and all(o["stop"] == 1.15 and o["size"] == 1000.0 for o in st["open"])
    st = HD.step(st, NOW + 600_000, {"tg:PeckShieldAlert": HD.parse_tg(TG_PAGE, "PeckShieldAlert")}, IDX, SWAPS, **kw)
    assert len(st["open"]) == 2                                                     # the same post again → no new trades
    px["v"] = 0.8
    st = HD.step(st, NOW + HD.DAY + 600_000, {}, IDX, SWAPS, **kw)                   # 24 h passed → the «24h» short closes at +19.9 %
    c = st["closed"][-1]
    assert c["hold"] == "24h" and c["why"] == "время" and abs(c["pct"] - 19.9) < 1e-6 and len(st["open"]) == 1
    hi["v"] = 1.2
    st = HD.step(st, NOW + 2 * HD.DAY, {}, IDX, SWAPS, **kw)                         # a +20 % spike → the «7d» short is stopped at +15 %
    c = st["closed"][-1]
    assert c["hold"] == "7d" and c["why"] == "стоп +15%" and abs(c["pct"] + 15.1) < 1e-6 and st["open"] == []
    doc = HD.public(st, NOW)
    assert doc["test"] and doc["owner_only"] and doc["by_hold"]["24h"]["wins"] == 1 and "ТЕСТ" in doc["rule_ru"]


def test_owner_only_server_side_and_never_the_real_account():
    d = tempfile.mkdtemp()
    h = HA.Hacks(d)
    doc = h.tick(now=NOW, sources={"rekt": HD.parse_rss(RSS, "rekt")}, idx=IDX, swap_set=SWAPS, px=lambda i: 1.0, high=lambda i, t: 1.0)
    assert doc and h.desk()[0] == 200 and os.path.exists(os.path.join(d, "state.json"))
    assert not fu.copy_sig("hacks:FOO") and not fu.copy_sig("hacks:FOO", {})
    srv = open(os.path.join(ROOT, "cnq", "api", "server.py"), encoding="utf-8").read()
    i = srv.index("def _owner(")
    assert '"/v2/owner/hacks"' in srv[i:] and srv.index("self.owner_auth.require(headers)", i) < srv.index('"/v2/owner/hacks"', i)
    j = srv.index('if path == "/v2/admin/hacks"')
    assert srv.index("deny = self.console.check(headers)", j) < srv.index("self.hacks.desk()", j)
    assert not os.path.exists(os.path.join(ROOT, "web", "hacks_desk.json"))
