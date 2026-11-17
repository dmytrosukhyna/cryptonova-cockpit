"""06.11 (owner 17:30): the owner's Futures picture — web/fut_owner.json joins the real OKX futures with the whale each copy came from."""
import json
import os
import re
import tempfile

from cnq.agents import fut_owner as F

H = 3600_000
T0 = F.SWITCH_T + 10 * H


def _dir():
    d = tempfile.mkdtemp()
    real = {"since": F.SWITCH_T - 13 * 24 * H, "start_usd": 10_000.0, "asof": T0 + 5 * H, "fut_pnl_usd": -20.0,
            "alloc": {"fut_budget_usd": 2500.0, "fut_pct": 25.0, "fut_margin_usd": 100.0, "fut_reserve_usd": 2400.0},
            "fut_curve": [[F.SWITCH_T - 2 * H, 5.0], [T0, -10.0], [T0 + 4 * H, -20.0]],
            "closed": [
                {"inst": "ETH-USDT-SWAP", "side": "long", "open_px": 2000.0, "close_px": 2100.0, "move_pct": 5.0, "pnl_usd": 12.0,
                 "opened": T0, "closed": T0 + H, "partial": False},                                      # sig in real_why → whale A
                {"inst": "BTC-USDT-SWAP", "side": "short", "open_px": 80_000.0, "close_px": 81_000.0, "move_pct": -1.2, "pnl_usd": -8.0,
                 "opened": T0 + 30 * 60_000, "closed": T0 + 2 * H, "partial": False},                     # no sig → by time → whale B
                {"inst": "SOL-USDT-SWAP", "side": "long", "open_px": 100.0, "close_px": 99.0, "move_pct": -1.0, "pnl_usd": -1.0,
                 "opened": F.SWITCH_T - 5 * H, "closed": F.SWITCH_T - 4 * H, "partial": False},            # before 04.11 → v1
                {"inst": "XRP-USDT-SWAP", "side": "long", "pnl_usd": 3.0, "opened": T0, "closed": T0 + H, "partial": True}],
            "futures": [{"inst": "ZEC-USDT-SWAP", "side": "short", "entry": 1300.0, "upnl_usd": -4.0, "sig": f"whales:W-ZEC-S-{T0 + H}"}],
            "actions": []}
    rw = {"closed": [{"inst": "ETH-USDT-SWAP", "opened": T0 + 60_000, "sig": f"whales:W-ETH-L-{T0 - 60_000}"}]}
    wl = {"names": {"bn:A": "Alpha", "bn:B": "Beta"},
          "desk": {"closed": [{"whale": "bn:A", "coin": "ETH", "side": "LONG", "opened_t": T0 - 60_000, "whale_entry": 1990.0, "pnl": 40.0, "closed_t": T0 + H},
                              {"whale": "bn:B", "coin": "BTC", "side": "SHORT", "opened_t": T0 + 10 * 60_000, "whale_entry": 80_100.0, "pnl": -20.0, "closed_t": T0 + 2 * H},
                              {"whale": "bn:B", "coin": "SOL", "side": "LONG", "opened_t": F.SWITCH_T - 30 * H, "pnl": 100.0, "closed_t": F.SWITCH_T - 20 * H}],
                   "open": [{"whale": "bn:A", "coin": "ZEC", "side": "SHORT", "opened_t": T0 + H, "whale_entry": 1310.0}]}}
    wt = {"following": [{"addr": "bn:A", "name": "Alpha", "src": "bn", "win": 0.9, "pf": 12.0, "decisions": 40, "hold_med_h": 10, "pnl_60d": 5000, "lev_med": 10}],
          "follow_dropped": [{"addr": "bn:B", "name": "Beta", "src": "bn", "why_ru": ["ниже других по PF"]}],
          "bench": [{"addr": "bn:C", "name": "Gamma", "win": 0.8, "pf": 3.0}],
          "follow_lock": {"next_t": T0 + 72 * H, "joined": {"bn:A": F.SWITCH_T}, "ru": "Список закреплён"},
          "desk": {"start": 10_000.0, "equity": 10_200.0, "pnl_usd": 200.0, "pnl_pct": 2.0, "trades": 3, "wins": 2, "started": F.SWITCH_T - 20 * 24 * H}}
    for n, v in (("public_real.json", real), ("real_why.json", rw), ("whales_live.json", wl), ("whales_top.json", wt),
                 ("ivan_stats.json", {"closed_n": 1, "wins": 1, "net_closed_usd": 122.8, "open_n": 0})):
        json.dump(v, open(os.path.join(d, n), "w"))
    return d


def test_every_real_copy_finds_its_trader_and_the_totals_add_up():
    d = F.run(_dir(), now=T0 + 6 * H)
    T = {(t["coin"], t["closed"]): t for t in d["trades"]}
    assert len(d["trades"]) == 4                                              # the partial close is not a trade
    assert T[("ETH", True)]["whale"] == "bn:A" and T[("ETH", True)]["match"] == "sig" and T[("ETH", True)]["entry_gap_pct"] == 0.5
    assert T[("BTC", True)]["whale"] == "bn:B" and T[("BTC", True)]["match"] == "time"
    assert T[("SOL", True)]["strategy"] == "unknown" or T[("SOL", True)]["strategy"] == "v1"
    assert T[("ZEC", False)]["whale"] == "bn:A" and T[("ZEC", False)]["match"] == "sig"
    a = d["following"][0]
    assert a["name"] == "Alpha" and a["status"] == "following" and a["joined"] == F.SWITCH_T
    assert a["totals"]["n"] == 1 and a["totals"]["open_n"] == 1 and a["totals"]["total_usd"] == 8.0      # +12 closed, −4 open
    b = next(x for x in d["dropped"] if x["addr"] == "bn:B")
    assert b["totals"]["sum_usd"] == -8.0 and "ниже других по PF" in b["why_ru"]
    k = d["kpi"]
    assert k["budget_pct"] == 25.0 and k["budget_start_pct"] == 15.0 and k["used_pct"] == 4.0 and k["open_n"] == 1
    assert k["pnl_all_usd"] == -20.0 and k["pnl_switch_usd"] == -25.0                                    # −20 now, +5 at the switch
    assert k["closed"]["n"] == 3 and k["closed"]["best"]["coin"] == "ETH" and k["closed"]["worst"]["coin"] == "BTC"
    assert [m["t"] for m in d["markers"]] == [F.SWITCH_T, F.BUDGET_T, F.FIX_T]
    assert d["curve"][0] == [d["since"], 0.0]
    assert d["desk"]["switch_pct"] == round((10_200 / 10_100 - 1) * 100, 2)                             # only closes before 04.11 count
    names = [x["name_ru"] for x in d["tests"][:2]]
    assert names == ["CRYPTO NOVA v1 (правила, до 04.11)", "CRYPTO NOVA v2 — копирование китов, с 04.11"]
    assert d["ivan"]["copied_real"] is False and d["ivan"]["stats"]["closed_n"] == 1
    # 17.11 20:10 (owner): the copy line comes from the current state; the source line does not claim a demo account it cannot see
    assert d["ivan"]["copy_ru"] == "На реальный счёт сейчас не копируется: «Иван» не в списке копирования и копий нет."
    assert "демо" not in d["ivan"]["source_ru"] and "нет источника" not in open(F.__file__, encoding="utf-8").read()
    json.dumps(d)


def test_the_owner_card_is_owner_only_in_both_panels():
    app = open("web/app.html", encoding="utf-8").read()
    assert 'id="fOwnCard"' in app and "#fOwnCard{display:none!important}body.tab-futures:not(.guest) #fOwnCard" in app
    assert "if(I.guest||document.body.classList.contains('guest')){ el.hidden=true; el.innerHTML=''; return; }" in app
    fut = open("web/vip/futures.js", encoding="utf-8").read()
    assert "if (!V.auth.owner()) {" in fut and fut.index("if (!V.auth.owner()) {") < fut.index("window.CNFutOwner.html(")
    js = open("web/fut_owner.js", encoding="utf-8").read()
    assert "window.CNFutOwner = { html, mount }" in js


def test_whale_copies_are_never_labelled_as_the_news_rule():
    """08.11 (owner): real positions with «whales:W-…» ids read «сделка по новости» (KIND_RU.W) — owner: «копия: <трейдер>», guests:
    «сигнал системы CRYPTO NOVA AI»; other desks keep their rule names"""
    import re
    import shutil
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    html = open(os.path.join(root, "web", "app.html"), encoding="utf-8").read()
    assert "function realDesk(sig,x){ return realDeskOf(" in html and "realDesk(x.sig)" not in html
    assert "futRule(k,o.grade)" not in html and "(k==='whales'||k==='ivan'||(o&&isWhaleCopy(o.sig,o.strategy)))?whaleLabel(" in html
    node = shutil.which("node")
    if not node:
        return
    a, b = html.index("// WHALE_LABEL_START"), html.index("// WHALE_LABEL_END")
    kind = re.search(r"const KIND_RU=(\{[^}]*\});", html).group(1)
    js = html[a:b] + f"""
const KIND_RU={kind}, kindRu=g=>KIND_RU[g]||g, names={{bal:{{name:'Сбалансированная'}},whales:{{name:'Киты'}}}};
const trades=[{{sig:'whales:W-XRP-S-1794084285861', whale_name:'法兰西基金'}}];
const r=(sig,st,guest)=>realDeskOf(sig,st,guest,guest?[]:trades,names,kindRu);
process.stdout.write(JSON.stringify([r('whales:W-XRP-S-1794084285861',null,false), r('whales:W-BTC-L-1794084285861',null,false),
  r('ivan:W-ETH-L-1',null,false), r('whales:W-XRP-S-1794084285861',null,true), r('x:W-ZEC-S-1','whales',true), r('bal:B-ETH-1',null,true)]));"""
    p = os.path.join(tempfile.mkdtemp(), "w.js")
    open(p, "w", encoding="utf-8").write(js)
    out = json.loads(subprocess.run([node, p], capture_output=True, text=True, timeout=30).stdout)
    assert out == ["копия: 法兰西基金", "копия трейдера", "копия: Иван", "сигнал системы CRYPTO NOVA AI", "сигнал системы CRYPTO NOVA AI",
                   "Сбалансированная · откуп каскада"]
    assert not any("новост" in x for x in out)


def test_the_whales_desk_comes_from_the_live_file_when_newer():
    """10.11 (owner): the real ETH long of 13:50 was missing from whales_top.json's desk at 14:16 — the pipeline snapshot; the
    server's whales_live.json is the desk the real account mirrors"""
    import shutil
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    html = open(os.path.join(root, "web", "app.html"), encoding="utf-8").read()
    assert "WTOP=liveDesk(WTOP,r.ok?await r.json():null)" in html
    node = shutil.which("node")
    if not node:
        return
    a, b = html.index("// LIVE_DESK_START"), html.index("// LIVE_DESK_END")
    T = {"asof": 100, "desk": {"start": 10000, "equity": 11000, "open": [{"coin": "BTC", "whale": "a"}], "closed": [], "trades": 1, "wins": 1}}
    L = {"asof": 200, "names": {"b": "Trader B"}, "desk": {"start": 10000, "equity": 11500, "open": [{"coin": "BTC", "whale": "a"}, {"coin": "ETH", "whale": "b"}],
                                                          "closed": [{"coin": "SOL", "pnl": 5, "closed_t": 1}, {"coin": "XRP", "pnl": -2, "closed_t": 2}]}}
    p = os.path.join(tempfile.mkdtemp(), "l.js")
    open(p, "w", encoding="utf-8").write(html[a:b] + f"\nconst R=liveDesk({json.dumps(T)},{json.dumps(L)}).desk, S=liveDesk({json.dumps(T)},{json.dumps(dict(L, asof=50))}).desk;"
                                         "process.stdout.write(JSON.stringify([R.open.map(o=>o.coin+':'+o.whale_ru),R.pnl_usd,R.trades,R.wins,R.closed[0].coin,S.open.length]));")
    out = json.loads(subprocess.run([node, p], capture_output=True, text=True, timeout=30).stdout)
    assert out == [["BTC:a", "ETH:Trader B"], 1500, 2, 1, "XRP", 1]


def test_research_factors_without_data_read_no_data_not_weak():
    """11.11: research.json cards have factors_nodata (missing factors are removed from factors) — the panel shows «нет данных»,
    never a zero bar or «слабая сторона», and does not break when team / community are absent"""
    import shutil
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    html = open(os.path.join(root, "web", "app.html"), encoding="utf-8").read()
    assert "${factorBars(c)}" in html and "${factorLine(c)}" in html and "const f=c.factors; const best=Object.entries(f)" not in html
    node = shutil.which("node")
    if not node:
        return
    a, b = html.index("// FACTORS_START"), html.index("// FACTORS_END")
    cards = [{"factors": {"economics": 91, "team": 100, "market": 41, "narrative": 80}, "factors_nodata": ["community"]},
             {"factors": {"market": 55}, "factors_nodata": ["team", "community", "economics", "narrative"]},
             {"factors_nodata": ["team"]}, {}, {"factors": {"economics": 70, "team": None, "market": 30}}]
    p = os.path.join(tempfile.mkdtemp(), "f.js")
    open(p, "w", encoding="utf-8").write(html[a:b] + f"\nconst C={json.dumps(cards)};process.stdout.write(JSON.stringify(C.map(c=>[factorLine(c),factorBars(c)])));")
    out = json.loads(subprocess.run([node, p], capture_output=True, text=True, timeout=30).stdout)
    assert out[0][0] == "Сильная сторона — команда (100), слабая — рынок (41). Нет данных: сообщество."
    assert out[0][1].count("нет данных") == 1 and "<small>сообщество</small>" in out[0][1] and "undefined" not in out[0][1]
    assert out[1][0].startswith("Оценка есть только по одному фактору: рынок (55). Нет данных: команда, сообщество")
    assert out[2][0] == "Нет данных: команда." and out[3] == ["", ""]
    assert out[4][0] == "Сильная сторона — экономика (70), слабая — рынок (30). Нет данных: команда." and "NaN" not in out[4][1]


def test_owner_futures_is_one_compact_list_with_ivan_apart_and_the_connect_box():
    """17.11 (owner 18:31: «куча табличек, неудобно»; «не могу найти, куда вставить ключи Ивана»): the owner's «Фьючерсы» is four
    folded sections — the real account (open copies as one list), the traders (one row each, a trader with no trades greyed with a
    one-line reason), Ivan (HIS account apart from OUR copies of him), the demo desk. Under it, owner-only: the connect form of
    Ivan's test accounts and links to the test desks. The VIP phone menu folds the test desks into «Тесты» when they do not fit,
    and vip.html asks for every script with the hour in its address (no stale panel after a release)."""
    import shutil
    import subprocess
    import tempfile
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rd = lambda *p: open(os.path.join(root, *p), encoding="utf-8").read()                                       # noqa: E731
    app, vip, core, vfut, it = rd("web", "app.html"), rd("web", "vip.html"), rd("web", "vip", "core.js"), rd("web", "vip", "futures.js"), rd("web", "ivan_tests.js")
    # the connect box: owner-only, on vip.html ONLY (17.11 20:01 owner: management lives on cryptonova.vip; app.html shows results)
    assert "ownerToolsCard" not in app and "connectHtml" not in app and "/v2/owner/ivan_tests" not in app
    for href in ("#/peter", "#/reversal", "#/smart_alts", "#/ivan_tests"):
        assert f"'{href}'" in vfut, href
    assert vfut.index("if (!V.auth.owner()) {") < vfut.index("connectHtml(")
    assert "function connectHtml(R, opts)" in it and "form(true)" in it and "Подключить ключи Ивана (Тест 2/3/4)" in it
    # the phone menu and the cache
    assert "group: 'tests'" in core and "V.bottomNav = function (routes)" in core and "V.BNAV_MAX = 8" in core
    assert "Math.floor(Date.now() / 3600000)" in vip and "'?h=' + h" in vip
    assert '<script src="vip/' not in vip and '"vip/futures.js"' in vip and '"vip/ivan_tests.js"' in vip
    node = shutil.which("node")
    if not node:
        return
    doc = {"asof": 1794932533707, "kpi": {"pnl_all_usd": -253.35, "today_usd": -42.4, "margin_usd": 393, "budget_usd": 1250, "open_n": 2,
                                         "open_usd": -28.4, "closed": {"n": 50, "win_pct": 60}},
           "curve": [], "markers": [],
           "trades": [{"coin": "AAVE", "side": "SHORT", "closed": False, "pnl_usd": -10.65, "whale_name": "0xRubi", "strategy": "whales"},
                      {"coin": "ETH", "side": "LONG", "closed": False, "pnl_usd": -17.75, "whale_name": "Иван", "strategy": "ivan"},
                      {"coin": "SOL", "side": "LONG", "closed": True, "pnl_usd": 5.0, "whale_name": "0xRubi"}],
           "following": [{"addr": "bn:1", "name": "0xRubi", "status": "following", "copies": [], "totals": {"n": 13, "wins": 10, "open_n": 2, "total_usd": 11.82}},
                         {"addr": "okx:2", "name": "ТИХИЙ", "status": "following", "copies": [], "totals": {"n": 0, "open_n": 0, "total_usd": 0}},
                         {"addr": "ivan:bybit", "name": "Иван", "status": "following", "copies": [],
                          "totals": {"n": 4, "wins": 3, "open_n": 6, "sum_usd": -14.61, "open_usd": -24.72, "total_usd": -39.33}}],
           "dropped": [{"addr": "bn:3", "name": "БЕЗСДЕЛОК", "status": "dropped", "why_ru": "ниже других по PF", "copies": [], "totals": {"n": 0}}],
           "bench": [{"name": "ЗАПАС", "win_pct": 70, "why_ru": "только наблюдение"}],
           "desk": {"pnl_pct": 11.04, "switch_pct": 2.56, "real_switch_pct": -14.23}, "tests": [],
           "ivan": {"stats": {"net_closed_usd": -685.0, "open_upnl_usd": -931.0, "closed_n": 7, "open_n": 9, "wins": 5, "max_dd_pct": 20.8}}}
    script = f"""global.window={{}};global.document={{getElementById:()=>1}};require({json.dumps(os.path.join(root, "web", "fut_owner.js"))});
process.stdout.write(window.CNFutOwner.html({json.dumps(doc, ensure_ascii=False)},{{}}));"""
    p = os.path.join(tempfile.mkdtemp(), "f.js")
    open(p, "w", encoding="utf-8").write(script)
    h = subprocess.run([node, p], capture_output=True, text=True, timeout=30).stdout
    secs = re.findall(r'<details class="fo-sec" data-fo-sec="([a-z]+)"( open)?>', h)
    assert [k for k, _ in secs] == ["real", "traders", "ivan", "desk"] and not any(o for _, o in secs)          # all folded
    real = h[h.index('data-fo-sec="real"'):h.index('data-fo-sec="traders"')]
    assert real.count('class="fo-li"') == 2 and "AAVE" in real and "0xRubi" in real and "SOL" not in real.split("fo-sub")[1].split("<details")[0]
    tr = h[h.index('data-fo-sec="traders"'):h.index('data-fo-sec="ivan"')]
    assert "0xRubi" in tr and "Иван" not in tr                                       # Ivan is only in his own section
    assert 'class="fo-tr zero"' in tr and "ТИХИЙ" in tr and "не открыл ни одной новой позиции" in tr
    assert "Выбывшие без наших сделок · 1" in tr and "Скамейка · 1" in tr
    iv = h[h.index('data-fo-sec="ivan"'):h.index('data-fo-sec="desk"')]
    assert "Его счёт · деньги Ивана, не наши" in iv and "Наши копии по нему · реальный счёт OKX, наши деньги" in iv
    assert re.search(r"его счёт <span[^>]*>−\u2060\$1\s616</span> · наши копии <span[^>]*>−\u2060\$39</span>", iv)
    body = iv[iv.index("</summary>"):]
    assert body.index("−\u2060$685") < body.index("Наши копии по нему") < body.index("−\u2060$39")    # his money first, ours apart


def test_strategies_today_on_top_of_the_owners_futures():
    """17.11 20:10 (owner): on vip.html «Фьючерсы» the owner first sees ONE list «Стратегии сегодня» — the real account's futures,
    each followed trader, Ivan (his account, our copies apart), Ivan Test 2/3/4, the demo desk «Киты», Разворот, Альты от китов,
    Golden Donut — each one row: today's $ and % and how many are open, details on tap; every «сегодня» says what it measures and
    nothing is estimated. Below: everything folded; vip.html#/futures/ivan-keys opens the Ivan keys box and scrolls to it. app.html
    has one owner-only link to the panel and no tools."""
    import shutil
    import subprocess
    import tempfile
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rd = lambda *p: open(os.path.join(root, *p), encoding="utf-8").read()                                       # noqa: E731
    vfut, vip, app = rd("web", "vip", "futures.js"), rd("web", "vip.html"), rd("web", "app.html")
    assert '"strat_today.js", "vip/futures.js"' in vip
    assert vfut.index("data-st-top") < vfut.index("fold('fut-owner'") < vfut.index("fold('ivan-keys'") < vfut.index("fold('fut-real'")
    assert "arg === 'ivan-keys'" in vfut and "k.open = true" in vfut and "scrollIntoView" in vfut
    assert "Подключить ключи Ивана (Тест 2/3/4)" in vfut
    # app.html: the owner-only link, nothing else
    assert 'id="ownerPanelLink" href="vip.html#/futures" hidden' in app and "CNAuth.state.isOwner===true" in app
    assert "/v2/owner/ivan_tests" not in app and "connectHtml" not in app
    node = shutil.which("node")
    if not node:
        return
    H, D = 3_600_000, 86_400_000
    now = 1794938000000
    day0 = now - now % D
    src = {
        "home": {"public_portfolio": {"futures": {"periods": {"day": {"available": True, "usd": -36.35, "pct": -2.79}}}}},
        "real": {"futures": [{"inst": "ETH-USDT-SWAP", "side": "long", "upnl_usd": -3.9}]},
        "fo": {"following": [{"addr": "bn:1", "name": "0xRubi", "totals": {"total_usd": 11.8}}, {"addr": "okx:2", "name": "ТИХИЙ", "totals": {}},
                             {"addr": "ivan:bybit", "name": "Иван", "totals": {}}],
               "trades": [{"whale": "bn:1", "coin": "AAVE", "side": "SHORT", "closed": False, "pnl_usd": -10.65},
                          {"whale": "bn:1", "coin": "SOL", "side": "LONG", "closed": True, "closed_t": day0 + H, "pnl_usd": 5.0},
                          {"whale": "bn:1", "coin": "XRP", "side": "LONG", "closed": True, "closed_t": day0 - H, "pnl_usd": 99.0},
                          {"whale": "ivan:bybit", "coin": "BTC", "side": "LONG", "closed": False, "pnl_usd": -0.85}],
               "desk": {"pnl_pct": 11.0, "pnl_usd": 1100, "history": [[day0 + 5 * H, 11265.11], [now, 11148.81]]}},
        "ivanStats": {"open_n": 9, "open_upnl_usd": -1003.67, "net_closed_usd": 246.38},
        "ivanTests": {"accounts": [{"id": "a1", "name": "Иван Тест 2", "copy_real": True, "real": {"open_n": 1, "open_pnl_usd": -2},
                                    "data": {"account": {"equity_hist": [[day0 - H, 10000], [day0 + H, 10100], [now, 9900]], "positions": [{}], "upnl": -40}}}]},
        "reversal": {"data": {"pnl_usd": 12, "open": [{"coin": "ZEC", "side": "SHORT", "upnl": 3}],
                              "closed": [{"closed_t": day0 + 2 * H, "pnl": -4}, {"closed_t": day0 - 2 * H, "pnl": 50}]}},
        "smartAlts": None,
        "peter": {"data": {"pnl_pct": 1.5, "equity_notional": 10150, "curve": [[day0 - D, 10000], [day0 + H, 10200]], "open": []}},
    }
    js = f"""global.window={{}};require({json.dumps(os.path.join(root, "web", "strat_today.js"))});const C=window.CNStratToday;
const R=C.rows({json.dumps(src, ensure_ascii=False)},{now});process.stdout.write(JSON.stringify({{rows:R,html:C.html(R)}}));"""
    p = os.path.join(tempfile.mkdtemp(), "s.js")
    open(p, "w", encoding="utf-8").write(js)
    out = json.loads(subprocess.run([node, p], capture_output=True, text=True, timeout=30).stdout)
    R = {r["key"]: r for r in out["rows"]}
    assert [r["key"] for r in out["rows"]] == ["real", "w:bn:1", "w:okx:2", "ivan", "it:a1", "desk", "reversal", "smart_alts", "peter"]
    assert R["real"]["today"] == {"usd": -36.35, "pct": -2.79, "label": "с 00:00 UTC"} and R["real"]["open"]["n"] == 1
    assert R["w:bn:1"]["today"]["usd"] == 5.0 and R["w:bn:1"]["today"]["pct"] is None and R["w:bn:1"]["open"] == {"n": 1, "usd": -10.65}
    assert R["w:okx:2"]["today"] is None and "наших копий пока нет" in R["w:okx:2"]["note"]
    assert R["w:bn:1"]["today"]["label"] == "закрыто сегодня · 1"
    assert R["ivan"]["today"] is None and "его счёт" in R["ivan"]["note"] and "наши копии: открыто 1" in R["ivan"]["note"]
    assert R["it:a1"]["today"] == {"usd": -100.0, "pct": -1.0, "label": "с 00:00 UTC"} and "ВКЛ" in R["it:a1"]["tag"]
    assert R["desk"]["today"]["label"].startswith("с 05:00") and R["desk"]["today"]["usd"] == -116.3     # the curve starts later: said so
    assert R["reversal"]["today"]["usd"] == -4 and R["reversal"]["today"]["label"] == "закрыто сегодня · 1"
    assert R["smart_alts"]["today"] is None and R["smart_alts"]["note"] == "нет данных с сервера"
    assert R["peter"]["today"]["usd"] == 150.0 and R["peter"]["today"]["label"] == "с 00:00 UTC"
    h = out["html"]
    assert "Стратегии сегодня" in h and h.count('<details class="st-r"') == 9 and " open" not in h.split("Стратегии сегодня")[1].split(">")[0]
    assert "за сегодня нет данных" in h and "сегодня = с 00:00 UTC" in h
