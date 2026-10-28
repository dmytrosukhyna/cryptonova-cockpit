"""Owner 22.10 09:56 — the closed system (web/app.html) and the desks behind it:
1. two spot numbers disagreed (header +$387…+$519, «наша стратегия» +$229): the header re-marked the server value with frozen candles;
2. the futures strategy comparison was gone;
3. «Импульс» showed 0, «умный разворот» and «шорт листингов» were missing — every desk now says what it waits for, in numbers;
4. «Архив и проверка» was squeezed and cut — one wide table, its own full-width row;
5. the meme portfolio «не рабочий» — why it waits, and a daily new-meme filter run on paper only.
Panel code runs in node (like test_panel_ai); the desks run on in-memory sqlite."""
import json
import os
import re
import shutil
import sqlite3
import subprocess

from cnq.agents import listing_desk as L
from cnq.agents import memes as mm
from cnq.agents import new_memes as NM
from cnq.agents import paper_desk as PD
from cnq.agents import spot_profiles as SPP

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
H, DAY = 3_600_000, 86_400_000


def _app() -> str:
    return open(os.path.join(ROOT, "web", "app.html"), encoding="utf-8").read()


def _slice(s: str, start: str, end: str) -> str:
    i = s.index(start)
    j = s.index(end, i) + len(end)
    return s[i:j]


def _node(js: str) -> dict:
    node = shutil.which("node")
    if not node:
        return {}
    r = subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=60)
    assert r.stdout.strip(), r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------- 1. one spot source
SPOT = r"""
let K={}, cur='BTCUSDT', lastPx=82000;
__BLOCK__
const S={start_usd:10000,index:102.1,equity_usd:10209.79,positions:[{symbol:'ETH',weight:0.1,price:2400},{symbol:'ZEC',weight:0.08,price:1264}]};
// the old bug: frozen 15m candles at the page-open price (ETH 2600) and the previous coin's lastPx for a coin without a stream (ZEC)
K.ETHUSDT={'15m':[{t:Date.now()-20*3600e3,c:2600}]}; cur='ZECUSDT';
const R={};
R.eq=spotEq(S); R.noEquity=spotEq({start_usd:10000,index:102.5}); R.none=spotEq(null);
R.frozen=pxNow('ETH'); R.otherSym=pxNow('ZEC');
pxMark('ETHUSDT',2450); R.fresh=pxNow('ETH'); PXT.ETHUSDT.t=Date.now()-11*60000; R.stale=pxNow('ETH');
R.hist=spHist({history:[{v:1}],history_all:[{v:1},{v:2}]}).length; R.histOld=spHist({history:[{v:1}]}).length;
console.log(JSON.stringify(R));
"""


def test_spot_value_is_the_server_measurement_and_live_prices_must_be_fresh():
    s = _app()
    blk = _slice(s, "const PXT={}, PX_FRESH_MS", "const spHist=S=>S?((S.history_all&&S.history_all.length?S.history_all:S.history)||[]):[];")
    R = _node(SPOT.replace("__BLOCK__", blk))
    if not R:
        return
    assert R["eq"] == 10209.79 and R["noEquity"] == 10250 and R["none"] == 10000     # never re-marked in the browser
    assert R["frozen"] is None and R["otherSym"] is None                              # no frozen candle, no other coin's price
    assert R["fresh"] == 2450 and R["stale"] is None
    assert R["hist"] == 2 and R["histOld"] == 1


def test_every_spot_place_reads_spotEq_and_the_whole_history():
    s = _app()
    assert "const eq=spotEq(P), s0=P.start_usd||10000, d=eq-s0" in s                  # «наша стратегия» card
    assert "const spEq=P?spotEq(P):10000" in s                                        # the donut numbers
    assert "spotEq(v),v.start_usd||10000" in s                                         # «Результат всех стратегий»
    assert "'держит портфель',sSince(v)||'—'" in s                                     # one start date (17.10), not the first of 60 points
    assert "spHist(P).map(p=>[p.time" in s and "dayPnl(spHist(S)" in s                # chart and «сегодня» from the whole history
    assert "pxMark(s,bar.c)" in s and s.count("pxMark(s,bar.c)") == 2                  # both streams stamp their own symbol
    assert "cur=s; lastPx=null;" in s                                                  # a new chart coin never inherits a price
    assert "setInterval(reloadPF,5*60000)" in s
    # nothing multiplies the server value by browser prices any more
    assert "k+=p.weight*(px/p.price-1)" not in s


def test_value_history_keeps_the_start_and_thins_evenly():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE v(time INTEGER PRIMARY KEY, index_value REAL)")
    conn.executemany("INSERT INTO v VALUES(?,?)", [(t, 100 + t / 1000) for t in range(1000)])
    h = SPP.value_history(conn, "v", since=100, max_points=240)
    assert len(h) == 240 and h[0]["time"] == 100 and h[-1]["time"] == 999
    assert SPP.value_history(conn, "v", max_points=5000)[0]["time"] == 0


# ---------------------------------------------------------------- 3. desks say what they wait for
def _bars(rets, t0=1_792_000_000_000, px=80_000.0):
    out = []
    for i, r in enumerate(rets):
        px *= 1 + r
        out.append([t0 + i * 900_000, px, px, px, px])
    return out


def test_btc_impulse_watch_numbers():
    flat = [0.0] * 200
    bars = _bars(flat[:100] + [0.007, 0.007, 0.007, 0.007] + flat[:96])     # one hour +2.8 % in the middle
    now = bars[-1][0] + 2 * 900_000
    w = PD.btc_impulse_watch(bars, now)
    assert w["need_pct"] == 2.5 and abs(w["last_fire_pct"] - 2.83) < 0.02 and w["fires_30d"] == 1 and w["now_pct"] == 0.0
    assert w["best_week_pct"] == w["last_fire_pct"] and w["last_fire_t"] == bars[103][0] + 900_000
    quiet = PD.btc_impulse_watch(_bars([0.001] * 120), now=_bars([0.001] * 120)[-1][0] + 900_000)
    assert quiet["last_fire_t"] is None and quiet["best_week_pct"] < 2.5
    assert PD.btc_impulse_watch([], 0) is None


def test_rules_line_no_longer_says_zero_positions():
    assert "до 5 позиций" in PD.rules_text(PD.PROFILES["imp"]) and "до 0 позиций" not in PD.rules_text(PD.PROFILES["brk"])
    imps = PD.rules_text(PD.PROFILES["imps"])
    assert "комиссия 0.1%/сторона" in imps and "без фандинга" in imps
    assert PD.rules_text(PD.PROFILES["bal"]).startswith("риск 1% на сделку") and "до 4 позиций" in PD.rules_text(PD.PROFILES["bal"])


def test_paper_run_attaches_the_watch_to_the_impulse_desks():
    conn = sqlite3.connect(":memory:")
    now = 1_792_000_000_000
    bars = [[now - (300 - i) * 900_000, 80_000, 80_100, 79_900, 80_000, 1.0] for i in range(300)]
    rep = {"coins": [{"symbol": "BTC-USDT", "candles": bars, "levels": [], "regime": {"long": "BULL"}}], "_no_network": True,
           "btc_impulse": {"need_pct": 2.5, "now_pct": 0.1, "last_fire_t": None, "history_days": 400}}
    out = PD.run(conn, rep, now)
    assert out["profiles"]["imp"]["watch"]["history_days"] == 400 and out["profiles"]["imps"]["watch"]["need_pct"] == 2.5
    assert "watch" not in out["profiles"]["bal"]


def test_listing_watch_counts_and_says_why_the_last_one_was_not_shorted():
    T0 = 1_792_000_000_000
    mk = lambda **c: (lambda: {s: {"launch": None, "price": p, "high24": p, "funding": 0.0, "qvol": q} for s, (p, q) in c.items()})
    conn = sqlite3.connect(":memory:")
    L.step(conn, T0, mk(**{"BTC-USDT": (1.0, 5e6)}))
    L.step(conn, T0 + H, mk(**{"BTC-USDT": (1.0, 5e6), "OK-USDT": (1.0, 5e6), "THIN-USDT": (1.0, 1e5)}))
    d = L.step(conn, T0 + 6 * H, mk(**{"BTC-USDT": (1.0, 5e6), "OK-USDT": (1.0, 5e6), "THIN-USDT": (1.0, 1e5)}))
    w = d["watch"]
    assert w["tokens"] == 2 and w["shorted"] == 1 and w["skipped"] == 1 and w["waiting"] == 0
    assert w["last"]["symbol"] in ("OK", "THIN")
    thin = conn.execute("SELECT note FROM lst_known WHERE symbol='THIN-USDT'").fetchone()[0]
    assert "тонкий рынок" in thin
    e = L.step(conn, T0 + 7 * H, mk(**{"BTC-USDT": (1.0, 5e6), "OK-USDT": (1.0, 5e6), "THIN-USDT": (1.0, 1e5), "NEW-USDT": (2.0, 5e6)}))
    assert e["watch"]["last"] == {"symbol": "NEW", "listed": T0 + 7 * H, "outcome_ru": "ждём 4 ч после листинга"}


def test_meme_dip_watch_is_seeded_then_tracks_deeper_drops():
    conn = sqlite3.connect(":memory:")
    mm._ensure(conn)
    rows = [{"coin": "WIF", "vol24": 5e6, "dip_dd_pct": -9.0}, {"coin": "THIN", "vol24": 1e3, "dip_dd_pct": -40.0}]
    w = mm.dip_watch(conn, rows, 1_792_700_000_000)
    assert w["closest"] == {"coin": "WIF", "dd_pct": -9.0} and w["deepest"]["coin"] == "USELESS" and w["deepest"]["dd_pct"] == -15.38
    w = mm.dip_watch(conn, [{"coin": "BONK", "vol24": 5e6, "dip_dd_pct": -21.0}], 1_792_800_000_000)
    assert w["deepest"] == {"coin": "BONK", "dd_pct": -21.0, "t": 1_792_800_000_000} and w["need_pct"] == -25.0


# ---------------------------------------------------------------- 2+3 in the panel: the comparison and the statuses
CMP = r"""
const IS_GUEST=false, TESTS=[['research','Лаборатория'],['own','Сигналы CRYPTO NOVA'],['memes','Мемкоины'],['imp','Импульс BTC'],['imps','Активный спот'],['lst','Шорт листингов'],['lstx','Турбо · листинги'],['btcx','Турбо · BTC ×2'],['chan','Каналы'],['whales','Киты']];
const PUBLIC_TESTS=['imp','memes'], PUBLIC_NAMES={imp:'Импульс',memes:'Мем-радар'}, STRATS={bal:{name:'Сбалансированная'},imp:{name:'Импульс BTC'}};
const ddmm=t=>{ const d=new Date(t); return String(d.getUTCDate()).padStart(2,'0')+'.'+String(d.getUTCMonth()+1).padStart(2,'0'); }, fmtT=t=>ddmm(t)+' 11:02';
const plur=(n,a,b,c)=>c, mypEsc=t=>String(t==null?'':t), otag=k=>`[${k}]`, resFmt=(u,b,s)=>`${Math.round(u)}|${s}`, K={};
const pxNow=()=>null;
const futEq=P=>{ let f=P?P.equity:10000; if(P) for(const o of P.open) f+=o.pnl_usd||0; return f; };
const D0=Date.UTC(2026,9,16,21), T=D0+86400e3;
const desk=(eq,extra)=>Object.assign({equity:eq,start_equity:10000,open:[],pending:[],closed:[],equity_history:[{time:T,equity:10000}],stats:{trades:0}},extra||{});
let PAPER={profiles:{bal:desk(10186,{closed:[{closed:T+5e6}]}),imp:desk(10000,{watch:{need_pct:2.5,now_pct:-0.29,best_week_pct:2.25,best_week_t:T+3*86400e3,week_days:7,last_fire_t:Date.UTC(2026,7,14,9),history_days:400}}),
  imps:desk(10000),own:desk(10104,{closed:[{closed:T+1},{closed:T+2}],open:[{}]}),lst:desk(10000,{watching:997,watch:{since:T,tokens:3,shorted:0,skipped:2,missed:1,refused:0,waiting:0,last:{symbol:'PAPER',listed:T+86400e3,outcome_ru:'пропуск по правилу: выросла на 40%'},rule_ru:'новый фьючерс токена на BingX → шорт через 4 ч'}}),
  lstx:desk(10000),btcx:desk(10179,{in_market:true,position_ru:'в BTC с плечом 2'})}};
let MEMES={account:{equity:10000,start_equity:10000,positions:[],stats:{trades:0},history:[[T,10000]],watch:{closest:{coin:'CASHCAT',dd_pct:-11.55},deepest:{coin:'USELESS',dd_pct:-15.38,t:T+86400e3},since:T}}};
let SIGS={recent:[{t:Date.UTC(2026,9,19,11,2)},{t:Date.UTC(2026,9,18,7)}],active:[1,2,3],accounts:{since_live:{smart:{pnl_usd:-354.52,start:10000,start_t:T,trades:12}}}};
let WTOP=null, IVAN=null, NOVAF={mix:[{key:'bal'},{key:'imp'},{key:'own'},{key:'lst'},{key:'app_smart',in_mix:false}]};
__BLOCK__
DSEL={current:'bal'};   // 28.10: the main futures desk is whatever web/desk_select.json chose — this fixture's is «Сбалансированная»
const R={rows:cmpRows().map(x=>[x.k,Math.round(x.usd),x.trades]), st:{}};
['imp','imps','lst','lstx','memes','app_smart','btcx','own'].forEach(k=>R.st[k]=deskStatus(k));
R.html=cmpHtml();
console.log(JSON.stringify(R));
"""


def test_futures_comparison_ours_first_then_tests_with_honest_statuses():
    s = _app()
    blk = _slice(s, "function tRes(k){", "Прошлый результат не гарантирует будущий.</div></div>`; }")
    R = _node(CMP.replace("__BLOCK__", blk))
    if not R:
        return
    keys = [r[0] for r in R["rows"]]
    assert keys[0] == "nova" and R["rows"][0][1] == 93                     # (186 + 0) / 2 desks → one $10 000 account
    assert "research" not in keys and "chan" not in keys                    # no account → not in the table
    assert keys.index("btcx") < keys.index("own") < keys.index("imp") and keys[-1] == "app_smart"   # tests by result, best first
    assert R["rows"][0][2] == 1 and dict((k, t) for k, _, t in R["rows"])["own"] == 2
    st = R["st"]
    assert st["imp"].startswith("ждёт условия: BTC за час +2,5%") and "сейчас −0.3%" in st["imp"] and "+2.3%" in st["imp"]
    assert "последний раз условие было 14.08.2026" in st["imp"]
    assert "по свечам панели" in st["imps"] or st["imps"] == "ждёт условия: BTC за час +2,5% и больше"   # no server number → said so
    assert "новых токенов 3, зашортили 0, пропустили по правилу 2, окно пропущено 1" in st["lst"] and "PAPER" in st["lst"]
    assert st["lstx"] == st["lst"]                                           # the ×4 mirror waits for the same listings
    assert "ближе всего сейчас CASHCAT −11.6%" in st["memes"] and "USELESS −15.4%" in st["memes"]
    assert st["app_smart"].startswith("выключено 19.10") and "открыто 3" in st["app_smart"]
    assert st["btcx"].startswith("держит позицию: в BTC с плечом 2")
    assert "Сравнение стратегий" in R["html"] and "[our]" in R["html"]


def test_the_comparison_is_in_the_futures_desk_and_parts_show_their_status():
    s = _app()
    i = s.index("function novaPanel(head)")
    body = s[i:s.index("function memesPanel(head)", i)]
    assert "${cmpHtml()}" in body and body.index("${cmpHtml()}") < body.index("futFold('sig'")   # visible, not inside a fold
    assert "stOf(c.k)" in body and "deskStatus('app_smart')" in body
    assert "<small class=\"rks muted\">${mypEsc(deskStatus(x.k))}</small>" in s           # the tests ranking says it too


# ---------------------------------------------------------------- 4. the archive
def test_archive_is_one_wide_table_in_its_own_row():
    s = _app()
    i = s.index("function renderSigs(){")
    body = s[i:s.index("let IDEAS=null", i)]
    assert 'class="sgt"' in body and "ways.filter(([k])=>L0[k]||B0[k])" in body
    assert '<div class="sview">' not in body and '<div class="saccs">' not in body       # no tiny boxes behind a switch
    assert '<details class="edet" open>' not in body                                      # details folded
    assert "function sigWide()" in s and "innerWidth>1150" in s and "try{ sigWide(); }catch(e){}" in s
    assert "body.sig-wide main>#sigCard{grid-column:1/-1;order:50!important}" in s
    assert "#sigCard>h2,#sigCard.collapsed>h2{white-space:normal}" in s and "cnq_fold_v3" in s


# ---------------------------------------------------------------- 5. memes: visible again + the new-meme filter on paper
def test_meme_sector_card_is_visible_in_spot_again():
    s = _app()
    assert "body.tab-spot:not(.sp-meme) #memesCard{display:none!important}" not in s
    assert "${nmHtml(M)}" in s and "function nmHtml(M)" in s


def _row(sym="NEW", **kw):
    r = {"symbol": sym, "mint": "M" + sym, "price": 1.0, "age_h": 48, "liquidity": 300_000, "vol24": 2_000_000, "buyers24": 5000,
         "sells24": 900, "flags": [], "red_flags": 0, "rug": {"lp_locked_pct": 100}, "holders": {"top10_pct": 15}, "fdv": 3_000_000,
         "chg24h": 40, "score": 50, "auth": {"mint": None, "freeze": None, "fee": None}, "link": "https://dexscreener.com/solana/x"}
    r.update(kw)
    return r


def test_new_meme_filter_reasons():
    assert NM.check(_row()) == []
    w = " | ".join(NM.check(_row(age_h=5, liquidity=50_000, buyers24=10, rug={"lp_locked_pct": 10}, chg24h=900)))
    assert "меньше суток" in w and "пул $50k" in w and "покупателей за сутки 10" in w and "заблокировано 10%" in w and "погоня" in w
    assert "уже не новый" in " ".join(NM.check(_row(age_h=200)))
    assert "RugCheck не проверял" in NM.check(_row(rug=None))
    assert any("комиссия за перевод 1%" in x for x in NM.check(_row(auth={"fee": {"newerTransferFee": {"transferFeeBasisPoints": 100}}})))
    assert any("накрутку" in x for x in NM.check(_row(vol24=20_000_000, liquidity=300_000)))
    assert any(x.startswith("красный флаг") for x in NM.check(_row(flags=[{"level": "red", "text": "могут допечатать токены"}], red_flags=1)))
    assert any("топ-10 кошельков 50%" in x for x in NM.check(_row(holders={"top10_pct": 50})))


def test_new_meme_paper_book_picks_three_a_day_and_exits_honestly():
    conn = sqlite3.connect(":memory:")
    now = 1_792_656_000_000 - 1_792_656_000_000 % DAY + 3 * H
    rows = [_row(s, score=90 - i) for i, s in enumerate(["A", "B", "C", "D"])]
    a = NM.step(conn, rows, now, price_fn=lambda m: {})
    assert sorted(p["symbol"] for p in a["today"]["picks"]) == ["A", "B", "C"] and len(a["book"]["open"]) == 3   # best score first, 3 a day
    usd = sorted((p["usd"] for p in a["book"]["open"]), reverse=True)
    assert usd[0] == 300 and 298 < usd[2] < usd[1] < 300                       # 3 % of the book, valued at what a sale would bring
    assert abs(a["book"]["cash"] - (10_000 - sum(usd))) < 0.02 and a["book"]["equity"] < 10_000   # entry and exit costs show at once
    assert abs(a["book"]["open"][0]["entry"] - 1.03) < 1e-9                                                       # DEX costs against us
    b = NM.step(conn, rows, now + H, price_fn=lambda m: {})
    assert len(b["today"]["picks"]) == 3                                                                          # no 4th the same day
    # A doubles (body: half sold), B falls 60 % (stop), C's pool is pulled (rug → 100 % loss)
    moved = [_row("A", price=2.2), _row("B", price=0.4), _row("C", price=0.5, liquidity=5_000), _row("D")]
    c = NM.step(conn, moved, now + 2 * H, price_fn=lambda m: {})
    cl = {p["symbol"]: p for p in c["book"]["closed"]}
    assert set(cl) == {"B", "C"} and "стоп −50%" in cl["B"]["why_exit"] and cl["C"]["pnl_usd"] == -cl["C"]["usd"]
    assert "100%" in cl["C"]["why_exit"]
    op = {p["symbol"]: p for p in c["book"]["open"]}
    assert op["A"]["body"] == 1 and op["A"]["pnl_usd"] > 0
    # the next day D is picked; A, no longer in the rows, gets its price from the injected DexScreener fetch, then trails out
    nxt = NM.step(conn, [_row("D")], now + DAY, price_fn=lambda m: {"MA": {"price": 1.2, "liquidity": 300_000}})
    assert [p["symbol"] for p in nxt["today"]["picks"]] == ["D"]
    assert any(p["symbol"] == "A" and "откат 40%" in (p["why_exit"] or "") for p in nxt["book"]["closed"])
    # no price for 24 h → counted as a total loss, never kept at the last price
    gone = NM.step(conn, [], now + 2 * DAY + 2 * H, price_fn=lambda m: {})
    assert any(p["symbol"] == "D" and p["pnl_usd"] == -p["usd"] for p in gone["book"]["closed"])
    assert gone["book"]["equity"] < 10_000 and gone["warnings"] and "100%" in " ".join(gone["warnings"])


def test_meme_run_carries_the_new_meme_block_and_the_dip_watch():
    conn = sqlite3.connect(":memory:")
    now = 1_790_000_000_000 - 1_790_000_000_000 % (4 * H) + 20 * 60_000
    flat = [[now - (22 * 24 - i) * H, 1.0, 1.0, 1.0, 1.0, 1e5] for i in range(22 * 24)]
    r = mm.run(conn, now, hl_ctx_fn=lambda: {"WIF": {"px": 1.0, "prev": 1.0, "vol24": 5e7, "oi_usd": 1e7, "funding_h": 0.0}},
               hl_candles_fn=lambda c, s, e: flat, sol_fn=lambda: {"rows": [_row("NEW")], "sources": {}}, btc_fn=dict,
               spot_fn=lambda conn, now, hl, deadline: {"rows": [], "sources": {}, "note": ""}, holders_fn=lambda m: None,
               nm_price_fn=lambda m: {})
    assert r["new_memes"]["today"]["candidates"][0]["symbol"] == "NEW" and r["new_memes"]["book"]["open"]
    assert r["account"]["watch"]["deepest"]["coin"] == "USELESS"


def test_turbo_mirror_copies_the_listing_watch(tmp_path):
    from cnq.agents import turbo_desk
    w = {"tokens": 2, "shorted": 1, "last": None}
    json.dump({"profiles": {"lst": {"start_equity": 10000, "equity": 10000, "equity_mtm": 10000, "open": [], "closed": [], "stats": {}, "watch": w}}},
              open(tmp_path / "paper.json", "w"))
    out = turbo_desk.step(sqlite3.connect(":memory:"), str(tmp_path), 1_792_000_000_000, candles_db=str(tmp_path / "none.sqlite"))
    lstx = out.get("lstx") or out.get("profiles", {}).get("lstx") or json.load(open(tmp_path / "paper.json"))["profiles"]["lstx"]
    assert lstx["watch"] == w


def test_connect_guide_is_the_build_45_flow_without_bot_or_uid():
    """22.10 (owner): no Telegram bot or channel in «Подключить», no UID — OKX by our link → identity check → USDT → «Копировать
    CRYPTO NOVA» (lead-trader profile); BingX only where OKX does not work. 28.10 (owner's decision): no text says copying is banned
    or unavailable in the EEA / UK — the same copy step, with one calm «i» note."""
    html = _app()
    g = _slice(html, "// 18.10: «Подключить» opens a short step guide.", "ov.querySelector('#connYes').addEventListener('click',showSteps);")
    for bad in ("t.me", "CONN_BOT", "UID боту", "Открыть бота", "Написать боту", "Оставьте UID", "готовим", "пока недоступно", "VPN"):
        assert bad not in g, bad
    for good in ("UID и API-ключи не нужны", "Копировать CRYPTO NOVA", "часть прибыли копирующего", "Убытки возможны", "okx_copy_url",
                 "j.copy_url", "Доступность копитрейдинга и отдельных инструментов зависит от страны и региона — условия показывает OKX",
                 "откроется позже",
                 "Пройдите проверку личности", "Пополните счёт в USDT", "Все сделки CRYPTO NOVA видны в панели"):
        assert good in g, good
    js = (_slice(html, "const EEA_TZ=", "// the lead-trader profile") +
          "\nconst C={okx_ref:'https://www.okx.com/join/X',exchange_ref:'https://bingx.com/r/Y',okx_kyc_url:'k'};const out={};"
          "for(const r of ['eu','world','nookx']){ STORE.cnq_region=r; const x=connEx(C); out[r]=[x.name,x.copy,x.reg,connRegion()]; }"
          "console.log(JSON.stringify(out));")
    js = "const STORE={};const localStorage={getItem:k=>STORE[k]||null,setItem:(k,v)=>{STORE[k]=v;}};" + js
    out = _node(js)
    if out:
        assert out["eu"] == ["OKX", True, "https://www.okx.com/join/X", "eu"]       # owner 28.10: the same copy step, one calm note
        assert out["world"] == ["OKX", True, "https://www.okx.com/join/X", "world"]
        assert out["nookx"] == ["BingX", False, "https://bingx.com/r/Y", "nookx"]
