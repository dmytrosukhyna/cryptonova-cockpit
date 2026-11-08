"""06.11 (owner 17:30): the owner's Futures picture — web/fut_owner.json joins the real OKX futures with the whale each copy came from."""
import json
import os
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
    json.dumps(d)


def test_the_owner_card_is_owner_only_in_both_panels():
    app = open("web/app.html", encoding="utf-8").read()
    assert 'id="fOwnCard"' in app and "#fOwnCard{display:none!important}body.tab-futures:not(.guest) #fOwnCard" in app
    assert "if(I.guest||document.body.classList.contains('guest')){ el.hidden=true; el.innerHTML=''; return; }" in app
    fut = open("web/vip/futures.js", encoding="utf-8").read()
    assert "if (V.auth.owner() && window.CNFutOwner)" in fut
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
