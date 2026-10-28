"""22.10 (owner, build 44): the app's PUBLIC portfolio = the header of the closed system. These run the panel's REAL functions
(web/app.html: pxNow, futEq, spotEq, ddmm, sSince, FUT_D0, baseD0, sinceD0, novaRes, hdrExtras, hdrVals) in node on fixture data
and check that the server (cnq/agents/panel_header.py → app_home "public") gives the same numbers: futures, spot, «Заработано
всего», the start dates — without live prices (what the server uses) and with the panel's live prices for some coins."""
import json
import os
import shutil
import sqlite3
import subprocess

from cnq.agents import app_home, panel_header as ph

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cut(s: str, start: str, end: str) -> str:
    i = s.index(start)
    j = s.index(end, i)
    return s[i:j]


def _panel_js() -> str:
    """the panel's own code, cut out of web/app.html by the functions' first words (the test fails if one moves or changes name)"""
    s = open(os.path.join(ROOT, "web", "app.html"), encoding="utf-8").read()
    line = lambda head: s[s.index(head):s.index("\n", s.index(head))]
    return "\n".join([
        line("function ddmm(t){"),
        line("const pSince=P=>"),
        line("const PXT={}, PX_FRESH_MS"), line("const pxNow=sym=>"), line("const futEq=P=>"), line("const spotEq=S=>"),
        line("function hdrExtras(){"),
        _cut(s, "function hdrVals(){", "const hdrFLabel="),
        _cut(s, "const FUT_D0=", "// 21.10 (owner: «фьючерсы CRYPTO NOVA"),
    ])


HARNESS = r"""
const STRATS={cons:{name:'Консервативная'},bal:{name:'Сбалансированная'},aggr:{name:'Агрессивная'},imp:{name:'Импульс BTC'},own:{name:'Сигналы CRYPTO NOVA'}};
const HDR_MAN={f:false,s:false}; const PROF='bal', SPROF='ideas'; let cur=null, lastPx=null; const K={};
let PAPER=null, PF=null; const PP=()=>PAPER.profiles[PROF], SP=()=>PF.portfolios[SPROF];
__PANEL__
const cases=JSON.parse(process.argv[1]), out=[];
for(const c of cases){ PAPER=c.paper; PF={available:true,portfolios:{ideas:c.ideas}}; DSEL=c.sel;   // 28.10: web/desk_select.json (null = none)
  for(const k of Object.keys(PXT)) delete PXT[k];
  for(const [s,p] of Object.entries(c.px||{})) pxMark(s,p);                     // a fresh live price for that symbol (22.10: PXT)
  const H=hdrVals(); out.push({f:H.f,f0:H.f0,fr:H.fr,sp:H.sp,s0:H.s0,sr:H.sr,tot:H.tot,fsince:H.fsince,ssince:sSince(H.S),
    keys:H.N?H.N.comps.map(x=>x.k):null, fo:H.fo, so:H.so}); }
console.log(JSON.stringify(out));
"""

T0 = ph.FUT_D0


def _desk(start_eq, hist, equity, open_=(), stats=None):
    return {"start_equity": start_eq, "equity": equity, "equity_history": [{"time": t, "equity": e} for t, e in hist],
            "open": list(open_), "closed": [], "stats": stats or {}}


def _cases() -> list[dict]:
    """28.10: the main futures desk is whatever web/desk_select.json chose — the fixtures put the main strategy's account under the
    chosen key (none → the default main desk) and the other two sizes of it under the rest of the family, which never count"""
    h = 3_600_000
    main = _desk(10000.0, [(T0 - 5 * h, 10120.0), (T0 - h, 10150.0), (T0 + 2 * h, 10190.0), (T0 + 30 * h, 10331.5)], 10331.5,
                [{"symbol": "APT", "side": "LONG", "entry": 0.7915, "price": 0.7811, "qty_left": 3217.71, "pnl_usd": 0.0},
                 {"symbol": "ETH", "side": "SHORT", "entry": 2400.0, "price": 2376.0, "qty_left": 0.8, "pnl_usd": 12.5}])
    imp = _desk(10000.0, [(T0 + 20 * h, 10000.0), (T0 + 40 * h, 9962.4)], 9962.4,
                [{"symbol": "SUI", "side": "LONG", "entry": 3.1, "price": 3.25, "qty_left": 640.0, "pnl_usd": 0.0}])
    own = _desk(10000.0, [(T0 + 50 * h, 10000.0), (T0 + 60 * h, 10104.05)], 10104.05,
                [{"symbol": "AVAX", "side": "LONG", "entry": 9.772, "price": 9.348, "qty_left": 34.11, "pnl_usd": 0.0}],
                {"trades": 9, "avg_trade_pct": 3.51})
    ideas = {"start_usd": 10000, "index": 102.1, "equity_usd": 10209.79, "started": T0 + 22 * h,
             "positions": [{"symbol": "BTC", "weight": 0.2476, "price": 108000.0, "status": "OPEN"},
                           {"symbol": "ZEC", "weight": 0.0835, "price": 1264.88, "status": "OPEN"},
                           {"symbol": "PENGU", "weight": 0.0255, "price": 0.0301, "status": "OPEN"}]}
    side = _desk(10000.0, [(T0, 10000.0)], 10050.0)

    def paper_for(key):
        return {"profiles": {key: main, "imp": imp, "own": own, **{k: side for k in ph.FAMILY if k != key}}}
    paper = paper_for(ph.NOVA_BASE[0])
    own_in = json.loads(json.dumps(paper))
    own_in["profiles"]["own"]["stats"] = {"trades": 31, "avg_trade_pct": 0.42}         # the own signals passed the gate
    return [{"paper": paper, "ideas": ideas, "px": {}, "sel": None},                   # the server: no live prices; no desk_select.json
            {"paper": paper, "ideas": ideas, "px": {"APTUSDT": 0.80, "ETHUSDT": 2410.0, "BTCUSDT": 109500.0, "ZECUSDT": 1200.0},
             "sel": {"current": "lab"}},                                               # a key that is not a main desk → the default
            {"paper": own_in, "ideas": ideas, "px": {"AVAXUSDT": 9.9, "SUIUSDT": 3.0}, "sel": None},
            {"paper": paper_for("bal"), "ideas": ideas, "px": {}, "sel": {"current": "bal"}},
            {"paper": paper_for("cons"), "ideas": ideas, "px": {"APTUSDT": 0.80, "ETHUSDT": 2410.0}, "sel": {"current": "cons", "by": "auto"}}]


def _kyiv_ddmm(ms):
    import datetime as dt
    return dt.datetime.fromtimestamp(ms / 1000, app_home._kyiv()).strftime("%d.%m")


def test_server_header_equals_the_panel_formula():
    node = shutil.which("node")
    if not node:
        return
    cases = _cases()
    r = subprocess.run([node, "-e", HARNESS.replace("__PANEL__", _panel_js()), json.dumps(cases)], capture_output=True, text=True,
                       timeout=60, env={**os.environ, "TZ": "Europe/Kyiv"})
    js = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else None
    assert js, r.stderr
    for c, J in zip(cases, js):
        H = ph.header(c["paper"]["profiles"], c["ideas"], c["px"], c["sel"])
        F, S = H["fut"], H["spot"]
        assert J["fo"] and J["so"] and J["keys"] == F["keys"]
        for a, b in ((F["f"], J["f"]), (F["f0"], J["f0"]), (F["fr"], J["fr"]), (S["sp"], J["sp"]), (S["s0"], J["s0"]),
                     (S["sr"], J["sr"]), (H["tot"], J["tot"])):
            assert abs(a - b) < 1e-6, (a, b)
        assert _kyiv_ddmm(F["since"]) == J["fsince"] and _kyiv_ddmm(S["since"]) == J["ssince"]
    m = ph.NOVA_BASE[0]
    assert js[0]["keys"] == js[1]["keys"] == [m, "imp"] and js[2]["keys"] == [m, "imp", "own"]
    assert js[3]["keys"] == ["bal", "imp"] and js[4]["keys"] == ["cons", "imp"]
    assert abs(js[3]["f"] - js[0]["f"]) < 1e-9 and abs(js[4]["f"] - js[1]["f"]) < 1e-9       # the chosen desk counts, nothing else
    assert abs(js[1]["f"] - js[0]["f"]) > 1                                               # the live prices really moved the futures part
    assert js[0]["sp"] == js[1]["sp"] == 10209.79                                           # 22.10: spot = the server's measurement, one number


def test_app_home_public_block_is_the_panel_header(tmp_path):
    """app_home "public" on the same data: the header's numbers, the spot coins with their «почему», the calculator split"""
    d = str(tmp_path)
    c = _cases()[0]
    paper = {**c["paper"], "profile_list": [{"key": "bal", "risk": 0.01, "max_open": 4}]}
    ideas = json.loads(json.dumps(c["ideas"]))
    ideas["positions"][0].update({"entry": 100000.0, "bucket": "core", "kind_ru": "ядро", "target_weight": 0.3,
                                  "table_status": "buy", "table_why_ru": "Ядро, анкета 55/100, BTC бычий — можно брать частями"})
    ideas["positions"][1].update({"entry": 1150.92, "bucket": "mid", "kind_ru": "крупный альт", "target_weight": 0.075,
                                  "table_status": "buy", "levels": {"stop": 920.7},
                                  "table_why_ru": "Анкета 51, консультант 99, тренд вверх, BTC бычий — можно брать"})
    ideas["positions"][2].update({"entry": 0.029, "bucket": "meme", "kind_ru": "мем", "target_weight": 0.025, "table_status": "wait",
                                  "table_why_ru": "−26% от максимума за 30 дней — ждём просадку от −30%"})
    for name, obj in (("paper.json", paper), ("portfolio.json", {"portfolios": {"ideas": ideas}}),
                      ("app_portfolio.json", {"index": 102.1, "history": [{"t": T0 + 22 * 3_600_000, "v": 100.0},
                                                                          {"t": T0 + 40 * 3_600_000, "v": 101.5}]})):
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            json.dump(obj, f)
    now = T0 + 70 * 3_600_000
    doc = app_home.run(sqlite3.connect(":memory:"), d, now=now)
    p, H = doc["public"], ph.header(paper["profiles"], ideas)
    assert p["total_usd"] == round(H["tot"], 2) == round(p["futures"]["usd"] + p["spot"]["usd"], 2)
    assert p["futures"]["usd"] == round(H["fut"]["fr"], 2) == doc["futures"]["usd"]       # Home and the Futures tab agree
    assert p["spot"]["value_usd"] == 10209.79 and p["spot"]["usd"] == 209.79 and p["base_usd"] == 20000.0   # equity_usd, as the panel
    assert p["since"] == H["fut"]["since"] and _kyiv_ddmm(p["since"]) == "17.10" and p["spot"]["since"] == ideas["started"]
    assert p["curve"][-1] == [now, p["total_usd"]] and all(x[0] >= p["since"] for x in p["curve"])
    coins = {x["sym"]: x for x in p["spot"]["coins"]}
    assert [x["sym"] for x in p["spot"]["coins"]] == ["BTC", "ZEC", "PENGU"] and coins["BTC"]["weight"] == 24.76
    assert abs(sum(x["usd"] for x in coins.values()) + p["spot"]["cash_usd"] - p["spot"]["value_usd"]) < 0.05
    assert "ядро" in coins["BTC"]["why"] and "30%" in coins["BTC"]["why"]
    assert "анкета 51 из 100, консультант 99 из 100" in coins["ZEC"]["why"] and "7,5%" in coins["ZEC"]["why"]
    assert "26%" in coins["PENGU"]["why"] and "«ждём»" in coins["PENGU"]["why"]
    calc = p["calc"]
    assert (calc["spot"], calc["futures"], calc["active"]) == (65, 25, 10) and "1% счёта" in calc["futures_why"]
    en = json.load(open(os.path.join(d, "app_home.en.json"), encoding="utf-8"))["public"]
    assert en["total_usd"] == p["total_usd"] and "questionnaire 51 of 100" in en["spot"]["coins"][1]["why"]
    assert "7.5%" in en["spot"]["coins"][1]["why"] and "Futures are" in en["calc"]["futures_why"]
    # no ideas portfolio → no public block (the app then shows its older view), never a made-up one
    os.remove(os.path.join(d, "portfolio.json"))
    assert app_home.run(sqlite3.connect(":memory:"), d, now=now)["public"] is None
