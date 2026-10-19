"""Спот: анкета качества — revenue mapping, the composite and the basket written for the altseason model; no network."""
import json
import sqlite3
import tempfile

from cnq.agents import spot_quality as sq

DAY = sq.DAY
NOW = 1_800_000_000_000


def test_revenue_slugs_map_protocols_parents_and_chains():
    ov = {"protocols": [{"defillamaId": "1", "slug": "aave-v3", "total30d": 5.0}, {"defillamaId": "2", "slug": "uni-v3", "total30d": 3.0,
                         "parentProtocol": "parent#uniswap"}, {"slug": "tron", "name": "Tron", "protocolType": "chain", "total30d": 9.0},
                        {"defillamaId": "4", "slug": "dead", "total30d": 0}]}
    prot = [{"defillamaId": "1", "symbol": "AAVE"}, {"defillamaId": "9", "symbol": "UNI", "parentProtocol": "parent#uniswap"},
            {"defillamaId": "4", "symbol": "DEAD"}]
    m = sq.revenue_slugs(ov, prot, [{"name": "Tron", "tokenSymbol": "TRX"}])
    assert m == {"AAVE": ["aave-v3"], "UNI": ["uni-v3"], "TRX": ["tron"]}


def test_composite_prefers_revenue_age_calm_volume_and_not_the_recent_winner():
    rows = {"OLD": {"age": 2000, "m3": -0.1, "vt": 0.8, "rg": 0.2, "rev": True},
            "HYPED": {"age": 400, "m3": 1.5, "vt": 3.0, "rg": None, "rev": False},
            "MID": {"age": 900, "m3": 0.1, "vt": 1.1, "rg": None, "rev": False}}
    sc = sq.composite(rows)
    assert sc["OLD"] > sc["MID"] > sc["HYPED"]


def test_run_writes_the_basket_and_quality_json():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE own_state(k TEXT PRIMARY KEY, v TEXT)")
    feats = {"BTC": {"dv30": 9e9, "dv180": 9e9, "m3": 0, "n": 400}}
    for k in range(14):
        feats[f"C{k}"] = {"dv30": 1e8 * (k + 1), "dv180": 1e8 * (k + 1) * (1 + k / 10), "m3": (k - 7) / 10, "n": 400}
    conn.execute("INSERT INTO own_state VALUES('pool_feats', ?)", (json.dumps(feats),))
    day = NOW // DAY * DAY

    def fetch(url):
        if "overview/fees" in url:
            return {"protocols": [{"defillamaId": "3", "slug": "c3-dex", "total30d": 1e6}]}
        if url.endswith("/protocols"):
            return [{"defillamaId": "3", "symbol": "C3"}]
        if "v2/chains" in url:
            return []
        if "summary/fees/c3-dex" in url:
            return {"totalDataChart": [[(day - k * DAY) // 1000, 1000.0 * (2 if k <= 90 else 1)] for k in range(1, 200)]}
        raise AssertionError(url)
    tmp = tempfile.mkdtemp()
    d = sq.run(conn, tmp, now=NOW, fetch=fetch, first_day=lambda s: NOW - 800 * DAY)
    assert d["universe"] == 14 and d["basket"][0] == "C3" and len(d["basket"]) == 10            # the revenue token first
    row = next(r for r in d["rows"] if r["symbol"] == "C3")
    assert row["rev"] and row["rev30"] == 60000 and row["rg"] == 1.0
    assert json.loads(conn.execute("SELECT v FROM own_state WHERE k='alt_basket'").fetchone()[0]) == d["basket"]
    assert json.load(open(tmp + "/quality.json"))["basket"] == d["basket"]
    assert sq.run(conn, tmp, now=NOW + 3_600_000, fetch=fetch)["skipped"]                        # once a day
