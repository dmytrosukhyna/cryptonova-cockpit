"""20.11 (owner): the card of «Торговля от уровней · CRYPTO NOVA AI (тест)» in «Управление» — owner-only, paper only."""
import json
import os
import shutil
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T0 = 1_795_000_000_000


def test_the_card_sits_under_the_manual_desk_and_uses_the_admin_check():
    adm = open(os.path.join(ROOT, "web", "admin.html"), encoding="utf-8").read()
    a, b, c = adm.index('id="manualBox"'), adm.index('id="levelsBox"'), adm.index('id="myCallsBox"')
    assert a < b < c and '<script src="levels_card.js?v=1"></script>' in adm and "api('/v2/admin/levels')" in adm
    assert "LV_START=r.code===503" in adm and "CNManual.setExtra(CNLevels.lines(D))" in adm and "mdApi.preview(coin,side)" in adm
    assert "mdApi.open" not in adm.split("async function lvLoad")[1].split("async function ivtLoad")[0]       # «Повторить» never opens by itself
    srv = open(os.path.join(ROOT, "cnq", "api", "server.py"), encoding="utf-8").read()
    j = srv.index('if path == "/v2/admin/levels":')
    assert srv.index("self.console.check(headers)", j) < srv.index("return", srv.index("self.console.check(headers)", j) + 10) < j + 600
    for f in ("app.html", "vip.html", "index.html"):
        assert "levels_card" not in open(os.path.join(ROOT, "web", f), encoding="utf-8").read(), f


def test_the_card_renders_variant_verdict_trades_and_chart_lines():
    node = shutil.which("node")
    if not node:
        return
    cfg = json.load(open(os.path.join(ROOT, "config", "levels_desk.json"), encoding="utf-8"))
    D = {"name": "Торговля от уровней · CRYPTO NOVA AI (тест)", "test": True, "owner_only": True, "asof": T0, "variant": "bo_retest|S|asym", "mode": "bo_retest",
         "side": "S", "exit": "asym", "start": 10000, "equity": 10012.5, "pnl_usd": 12.5, "started": T0 - 86_400_000, "errors": [],
         "open": [{"coin": "ADA", "side": "S", "entry": 0.55, "stop": 0.56, "tgt": 0.53, "opened_t": T0, "notional": 5000, "risk_usd": 100, "level": 0.552}],
         "pending": [{"coin": "SOL", "side": "S", "px": 140.2, "t": T0}],
         "closed": [{"coin": "BTC", "side": "S", "entry": 90000, "exit": 89000, "opened_t": T0 - 7200000, "closed_t": T0 - 3600000, "pct": 1.1, "usd": 50, "why": "цель"},
                    {"coin": "ETH", "side": "S", "entry": 3000, "exit": 3030, "opened_t": T0 - 9000000, "closed_t": T0 - 5000000, "pct": -1, "usd": -37.5, "why": "стоп"}],
         "trades": 2, "wins": 1, "backtest": cfg["backtest"], "rule_ru": "ПРАВИЛО"}
    p = os.path.join(tempfile.mkdtemp(), "l.js")
    open(p, "w", encoding="utf-8").write(f"global.window={{}};require({json.dumps(os.path.join(ROOT, 'web', 'levels_card.js'))});const L=window.CNLevels;"
                                        f"const D={json.dumps(D, ensure_ascii=False)};process.stdout.write(JSON.stringify({{h:L.html(D),s:L.html(null,{{starting:true}}),l:L.lines(D)}}))")
    out = json.loads(subprocess.run([node, p], capture_output=True, text=True, timeout=30).stdout)
    h = out["h"]
    for w in ("ТЕСТ", "шорт после пробоя поддержки на ретесте, короткое удержание", "не прошёл — работает как наблюдение", cfg["backtest"]["verdict_ru"].replace("<", "&lt;"),
              "ПРАВИЛО", "режим — пробой", "Повторить вручную", "Ждут ретеста", "SOL", "цель", "стоп", "−$37,50", "Прошлое не гарантирует", "<svg"):
        assert w in h, w
    assert h.count('data-lv="repeat"') == 2 and 'data-side="short"' in h
    assert "Стол запускается" in out["s"]
    assert [x["t"] for x in out["l"]] == ["уровни: вход шорт", "уровни: стоп", "уровни: цель"] and all(x["sym"] == "ADA" for x in out["l"])
