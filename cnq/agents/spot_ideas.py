"""Spot ideas — ONE table «что брать / что не брать» for the Spot tab (owner 17.10.2026: «топ-20 или топ-30 по перспективам»,
one list, not split into memes / non-memes, a status per coin and an «i» with the reasons). Output: web/ideas.json.

No new data and no forecasts: the module only re-reads what the other departments already wrote in web/ —
  • research.json  `cards`      — the coin «анкета»: risk class cons/mod/high, rating 0–100, 7/30/200-day change, distance from
                                   the all-time high, FDV/market cap, tags (мем …), CoinGecko id;
  • portfolio.json `btc_bull`   — BTC regime (the same flag every portfolio uses); `picks` short/mid/long — the consultant's
                                   0–100 scores; `portfolios.*.positions` — what our demo portfolios hold (+ server `reco`;
                                   the follower «По рекомендациям» only adds coins to the universe, see below);
  • memes.json     `spot_memes` — the meme desk's checked spot memes: status small/wait/avoid, why_ru, quality score,
                                   `from_high30_pct` (distance from the 30-day high, a lower-bound estimate);
  • tokenomics.json `tokens`    — 18.10: holders revenue from DefiLlama (buybacks / burns / payouts): a fact line in the «i» and a
                                   score bonus (≤ +10, 2 points per 1 % of market cap a year) — never a pass / fail check;
  • tokenomics.json `unlocks_next` — 18.10: the next unlock within 30 days (our calendar of public sources). Our study
                                   (scripts/unlocks_study.py, 287 unlocks 2024–2026): ≥ 20 % of the circulating supply → the coin
                                   lagged BTC by ≈15 % over the 30 days before (3 of 4 cases) → «ждём» for non-core coins until the
                                   unlock; 5–20 % → a fact line only (the effect faded in 2026);
  • markets.json   `top`        — 24 h volume (liquidity);  decision.json `coins` — support zones and the 20-day high for the
                                   18 futures coins of the terminal (used for «цена у поддержки» and the 30-day-high estimate).
Missing files are skipped; a coin missing a value simply does not pass the check that needs it (never a guess).

Universe: every research card + every consultant pick + every position of the demo portfolios + every spot meme, deduplicated
by symbol. Left out: stablecoins / wrapped / tokenized funds (research `stable` flag, a known stablecoin ticker or «USD» in the
ticker, or a peg-like price: |7 d| < 1.5 %, |30 d| < 3 % and within 12 % of the all-time high).

Kind (`kind_ru`): BTC/ETH/SOL «ядро»; a meme (meme desk row, «мем» tag, or held as a meme) «мем»; research class mod
«крупный альт»; everything else (research class high, or no card) «высокий риск».

Score 0–100 (`score`, used for ordering only): weighted mean of the scores that exist for the coin — research rating × 0.5,
consultant score × 0.3 (max of its mid «тренд 1–6 мес» and long «фундамент 6–24 мес» scores, from picks or positions),
meme-desk quality × 0.2; weights are re-normalised over the parts that exist.

Status — checked in this order, the first rule that fires wins:
  avoid «не брать»  — meme desk says avoid (red flag / low turnover / not listed / weak quality);
                      one of our portfolios holds it at its stop (server reco «sell», or P&L ≤ −12 % in «Умеренный»,
                      ≤ −25 % in «Высокий риск», the panel's own thresholds);
                      24 h turnover < $5M (MIN_VOL); not core and ≥ 90 % below the all-time high (DEAD_ATH, the same «мёртвая
                      монета» rule as the «Сбалансированный» portfolio).
  add   «можно добрать» — already held by one of our portfolios, not at its stop and not at a take-profit point, and either the
                      server reco says «add» or the price is inside / approaching a support zone (decision.json levels state
                      IN_ZONE / APPROACHING); not overheated; core — in any regime; others — only in a BTC bull regime, with
                      consultant mid ≥ 60 or long ≥ 70 (the panel's own «Можно добрать» rule), not a centralized-exchange
                      token, FDV/market cap < 2, and a meme only when the meme desk says small.
  buy   «можно брать» — core: research rating ≥ 40 and not overheated (allowed in a bear regime too).
                      large alt: BTC bull regime AND rating ≥ 40 AND consultant ≥ 60 AND higher than 200 days ago AND not
                      overheated AND FDV/market cap < 2 AND turnover ≥ $20M AND not a centralized-exchange token.
                      High risk and memes are NEVER «buy». In a BTC bear regime nothing except core is «buy».
  small «можно на небольшую сумму» — meme: the meme desk says small (checks passed and ≥ 30 % below the 30-day high:
                      in docs/research/memes.md only buying after a flush made money; regime-independent, the flush edge was
                      found when BTC was weak); high risk: BTC bull regime AND rating ≥ 35 AND FDV/market cap < 2 AND ≥ 30 %
                      below the 30-day high AND not overheated AND not a centralized-exchange token.
  wait  «ждём»      — everything else; why_ru names the first check that failed.
«Overheated» = +40 % or more in 7 days or +120 % or more in 30 days (we do not chase a spike; in the meme research every
momentum / breakout entry lost money). The 30-day high is a LOWER-BOUND estimate (so a dip is never overstated): for memes the
meme desk's value; otherwise the max of today's price, the prices 24 h / 7 d / 30 d ago implied by CoinGecko's % changes and the
20-day high of the terminal's candles (decision.json, only when its price is within 10 % of CoinGecko's).

Followers (17.10): the 6th spot portfolio «По рекомендациям» (`ideas`, spot_profiles.IdeasPortfolio) BUYS from this table, so its
positions only keep a coin in the universe — they never count as «held», never trigger «у стопа» / «добрать» and give no consultant
score (no feedback loop: its own reco «sell» must not turn a coin into «не брать»). `statuses` = {symbol: status, status_ru,
why_ru, kind, price} for EVERY evaluated coin, so the follower can sell by status even when a coin is not among the 30 rows.

Rows: sorted buy, add, small, wait, avoid, then by score; 30 rows. Up to 6 of the 30 places are kept for «не брать» (those we hold
or the consultant picks first, then by score) so the table also shows what NOT to buy. Each row has a ≤ 14-word `why_ru` and a
2–5-sentence `info_ru` for the «i» popup: the checks with their real numbers, the research behind the rule, our portfolios
holding the coin and an honest line that this is not a price forecast. Stdlib only.
"""
from __future__ import annotations

import json
import os
import time

STATUS_ORDER = ("buy", "add", "small", "wait", "avoid")
STATUS_RU = {"buy": "можно брать", "add": "можно добрать", "small": "можно на небольшую сумму", "wait": "ждём", "avoid": "не брать"}
KIND_RU = {"core": "ядро", "mid": "крупный альт", "high": "высокий риск", "meme": "мем"}
CORE = ("BTC", "ETH", "SOL")
CEX_TOKENS = {"BNB", "LEO", "OKB", "BGB", "GT", "KCS", "CRO", "HTX", "WBT", "MX"}     # same list as portfolio.CEX_TOKENS
STABLES = {"USDT", "USDC", "DAI", "USDE", "FDUSD", "USDS", "TUSD", "PYUSD", "USD1", "BUSD", "USDD", "FRAX", "EURC", "USDG", "RLUSD",
           "LUSD", "GUSD", "USDP", "USDL"}

RATING_BUY = 40          # research rating for «можно брать» (core and large alts); top ~15 % of the 133 cards on 17.10.2026
RATING_SMALL = 35        # research rating for «на небольшую сумму» (high risk)
CONSULTANT_BUY = 60      # consultant max(mid, long) for a large alt; the mod portfolio buys mid ideas from 60
ADD_MID, ADD_LONG = 60, 70   # panel's «Можно добрать»: consultant mid ≥ 60 or long ≥ 70
OVERHEAT_7D, OVERHEAT_30D = 40.0, 120.0
UNLOCK_BIG, UNLOCK_WARN = 20.0, 5.0   # unlock in ≤ 30 days, % of circulating: ≥ 20 → «ждём» (non-core), ≥ 5 → a fact line
DIP_30D = -30.0          # ≥ 30 % below the 30-day high (meme desk and «Сбалансированный» meme rule)
DEAD_ATH = -90.0         # ≥ 90 % below the all-time high = «мёртвая монета» (portfolio.BAL["dead_ath"])
DILUTION_FDV = 2.0       # FDV / market cap ≥ 2 → half or more of the tokens are still to come
MIN_VOL = 5e6            # 24 h turnover below this → «не брать» (meme desk STATUS_MIN_VOL)
MIN_VOL_BUY = 2e7        # turnover for «можно брать» of a large alt (BAL lead_min_vol)
STOP_NEAR = {"mod": -12.0, "high": -25.0}   # P&L at which the panel already shows «у стопа» for portfolios without server reco
MAX_ROWS, AVOID_SLOTS = 30, 6
STALE_MS = 6 * 3_600_000
FOLLOWERS = ("ideas",)   # portfolios that buy FROM this table («По рекомендациям») — their holdings never feed back into a status
MAX_WHY_WORDS = 14

RULES_RU = [
    "«Можно брать» — только ядро (BTC, ETH, SOL) и крупные альты. Ядро: анкета от 40/100 и без рывка вверх; можно и при "
    "медвежьем BTC. Крупный альт: BTC в бычьем режиме, анкета от 40, оценка консультанта от 60, монета выше, чем 200 дней назад, "
    "без рывка, в обращении больше половины токенов (FDV/капитализация меньше 2), оборот от $20M в сутки, не токен биржи.",
    "«Можно добрать» — монета уже есть в одном из наших портфелей, не у стопа и не у точки фиксации, и либо портфель сам "
    "рекомендует добор, либо цена у сильной поддержки. Для всего, кроме ядра, — только при бычьем BTC и оценке консультанта "
    "от 60 (тренд) или 70 (фундамент).",
    "«Можно на небольшую сумму» — мемы и высокий риск, только после просадки на 30% и больше от максимума за 30 дней. Мемы — "
    "по проверкам мем-деска; в нашем исследовании мемов (docs/research/memes.md) плюс давала только покупка после обвала, "
    "пробои и рост — минус. Высокий риск — при бычьем BTC, анкете от 35 и FDV/капитализации меньше 2. Только сумма, которую "
    "не жалко потерять.",
    "«Не брать» — красный флаг мем-деска, оборот меньше $5M в сутки, монета на 90% и больше ниже исторического максимума "
    "(кроме ядра) или наш портфель держит её у стопа.",
    "«Ждём» — всё остальное: в строке написано, какая проверка не пройдена (медвежий BTC, рывок вверх — +40% за 7 дней или "
    "+120% за 30, слабая анкета, нет подтверждения консультанта, нет просадки, много токенов впереди).",
    "Балл 0–100 — только для порядка в таблице: анкета × 0.5 + консультант × 0.3 + качество мема × 0.2 (если какой-то оценки "
    "нет, веса делятся между остальными). Максимум за 30 дней — оценка снизу, просадка не завышается.",
    "Это правила отбора по сегодняшним данным, а не прогноз цены. Высокий риск и мемы никогда не «можно брать», при медвежьем "
    "BTC «можно брать» только ядро.",
]


# ----------------------------------------------------------------------------- formatting
def _pct(x: float | None, nd: int = 0, sign: bool = True) -> str:
    if x is None:
        return "—"
    if round(x, nd) == 0:
        return f"{0:.{nd}f}%"
    s = f"{x:+.{nd}f}%" if sign else f"{x:.{nd}f}%"
    return s.replace("-", "−")


def _usd(x: float | None) -> str:
    if x is None:
        return "—"
    if x >= 1e9:
        return f"${x/1e9:.1f}B"
    if x >= 1e6:
        return f"${x/1e6:.0f}M"
    if x >= 1e3:
        return f"${x/1e3:.0f}K"
    return f"${x:.0f}"


def _words(s: str) -> int:
    return len([w for w in s.split() if any(ch.isalnum() for ch in w)])


def _clip(s: str, n: int = MAX_WHY_WORDS) -> str:
    if _words(s) <= n:
        return s
    out, k = [], 0
    for w in s.split():
        if any(ch.isalnum() for ch in w):
            k += 1
        if k > n:
            break
        out.append(w)
    return " ".join(out).rstrip(",;:—") + "…"


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


# ----------------------------------------------------------------------------- inputs
def load_inputs(web_dir: str = "web") -> dict:
    out = {}
    for key, fn in (("research", "research.json"), ("portfolio", "portfolio.json"), ("memes", "memes.json"),
                    ("markets", "markets.json"), ("decision", "decision.json"), ("tokenomics", "tokenomics.json"),
                    ("quality", "quality.json")):
        try:
            with open(os.path.join(web_dir, fn), encoding="utf-8") as f:
                out[key] = json.load(f)
        except Exception:  # noqa: BLE001 — a missing / broken file is just skipped
            out[key] = None
    return out


def _peg_like(c: dict) -> bool:
    ch7, ch30, ath = _num(c.get("chg7")), _num(c.get("chg30")), _num(c.get("ath_chg"))
    if ch7 is None or ath is None:
        return False
    return abs(ch7) < 1.5 and abs(ch30 or 0.0) < 3.0 and ath > -12.0


def high30_lower_bound(price: float | None, chg24=None, chg7=None, chg30=None, extra: list[float] | None = None):
    """(pct below the 30-day high, basis list). The high is the max of today's price, the prices 24 h / 7 d / 30 d ago implied
    by the % changes, and any extra highs that lie inside the last 30 days — a lower bound, so the dip is never overstated."""
    if not price or price <= 0:
        return None, []
    pts, basis = [price], []
    for ch, lab in ((chg24, "24 ч"), (chg7, "7 дн"), (chg30, "30 дн")):
        ch = _num(ch)
        if ch is not None and ch > -100:
            pts.append(price / (1 + ch / 100)); basis.append(lab)
    for h in extra or []:
        if _num(h) and h > 0:
            pts.append(h); basis.append("максимум 20 дней (свечи терминала)")
    hi = max(pts)
    return round((price / hi - 1) * 100, 1), basis


# ----------------------------------------------------------------------------- core
def _gather(inp: dict, now: int) -> tuple[dict, dict]:
    """symbol → merged sources; also returns context (regime, names, staleness)."""
    research = inp.get("research") or {}
    pf = inp.get("portfolio") or {}
    memes = inp.get("memes") or {}
    markets = inp.get("markets") or {}
    decision = inp.get("decision") or {}
    coins: dict[str, dict] = {}

    def slot(sym: str) -> dict:
        return coins.setdefault(sym, {"symbol": sym, "card": None, "meme": None, "picks": {}, "positions": [], "vol": None, "dec": None})

    for c in research.get("cards") or []:
        sym = (c.get("symbol") or "").upper()
        if not sym or c.get("stable") or sym in STABLES or "USD" in sym or _peg_like(c):
            continue
        slot(sym)["card"] = c
    for h, lst in (pf.get("picks") or {}).items():
        for x in lst or []:
            sym = (x.get("symbol") or "").upper()
            if sym and sym not in STABLES:
                s = slot(sym)
                if h not in s["picks"] or (x.get("score") or 0) > (s["picks"][h].get("score") or 0):
                    s["picks"][h] = x
    names = {}
    for key in pf.get("portfolio_order") or list((pf.get("portfolios") or {}).keys()):
        p = (pf.get("portfolios") or {}).get(key) or {}
        names[key] = (p.get("name") or key).split(" · ")[0]
        for pos in p.get("positions") or []:
            sym = (pos.get("symbol") or "").upper()
            if sym and sym not in STABLES:
                if key in FOLLOWERS:      # only keeps the coin evaluated; never a holding / stop / reco / consultant input
                    slot(sym)
                else:
                    slot(sym)["positions"].append((key, pos))
    for m in memes.get("spot_memes") or []:
        sym = (m.get("symbol") or "").upper()
        if sym:
            slot(sym)["meme"] = m
    for t in markets.get("top") or []:
        sym = (t.get("symbol") or "").upper()
        if sym in coins:
            coins[sym]["vol"] = _num(t.get("volume"))
    dec_ok = bool(decision.get("asof")) and now - int(decision.get("asof") or 0) <= STALE_MS
    for d in (decision.get("coins") or []) if dec_ok else []:
        sym = (d.get("symbol") or "").split("-")[0].upper()
        if sym in coins:
            coins[sym]["dec"] = d
    # stablecoins that only came in through picks / positions (should not happen) are dropped by the STABLES checks above
    btc_bull = pf.get("btc_bull")
    if btc_bull is None:
        btc_bull = markets.get("btc_bull")
    if btc_bull is None:
        btc_bull = ((decision.get("portfolio") or {}).get("btc_bull")) if dec_ok else None
    bt = (((pf.get("portfolios") or {}).get("bal") or {}).get("backtest") or {}).get("result") or {}
    ctx = {"btc_bull": btc_bull, "names": names, "core_bt": bt.get("cons_50_25_15"),
           "tok": ((inp.get("tokenomics") or {}).get("tokens")) or {},
           "unl": ((inp.get("tokenomics") or {}).get("unlocks_next")) or {},
           # 19.10: the spot quality card (spot_quality.py): rank among the top-100 alts by revenue, age, calm volume, no 3-month spike
           "qual": {str(r.get("symbol") or "").upper(): r for r in ((inp.get("quality") or {}).get("rows") or [])},
           "qual_basket": set((inp.get("quality") or {}).get("basket") or []),
           "sources": {k: (v or {}).get("asof") for k, v in inp.items() if isinstance(v, dict)}}
    return coins, ctx


def _kind(sym: str, s: dict) -> str:
    if sym in CORE:
        return "core"
    card = s["card"] or {}
    if s["meme"] or "мем" in (card.get("tags") or []) or any(p.get("role") == "meme" or p.get("bucket") == "meme" for _, p in s["positions"]):
        return "meme"
    if card.get("risk") == "mod":
        return "mid"
    if card.get("risk") in ("high", "cons"):
        return "high"
    roles = {p.get("role") or p.get("bucket") for _, p in s["positions"]}
    return "mid" if "mid" in roles else "high"


def _consultant(s: dict) -> dict:
    """Best known consultant score per horizon (picks and positions both carry the same scorer's numbers)."""
    out: dict[str, int] = {}
    for h, x in s["picks"].items():
        if _num(x.get("score")) is not None:
            out[h] = max(out.get(h, 0), int(x["score"]))
    for _, p in s["positions"]:
        for h, v in (p.get("score") or {}).items():
            if _num(v) is not None:
                out[h] = max(out.get(h, 0), int(v))
    return out


def _pos_state(s: dict) -> dict:
    """At stop / at take-profit / reco says add — across all our portfolios holding the coin."""
    st = {"at_stop": [], "trim": [], "add": [], "recos": []}
    for key, p in s["positions"]:
        r = p.get("reco") or {}
        pnl = _num(p.get("pnl_pct"))
        if r.get("k") == "sell" or (not r and key in STOP_NEAR and pnl is not None and pnl <= STOP_NEAR[key]):
            st["at_stop"].append((key, pnl, r.get("t")))
        elif r.get("k") == "trim":
            st["trim"].append((key, r.get("t")))
        elif r.get("k") == "add":
            st["add"].append((key, r.get("t")))
        if r.get("t"):
            st["recos"].append((key, r.get("t")))
    return st


def _near_support(dec: dict | None) -> dict | None:
    if not dec:
        return None
    ok = [L for L in dec.get("levels") or [] if L.get("kind") == "support" and L.get("status", "ACTIVE") == "ACTIVE"
          and L.get("state") in ("IN_ZONE", "APPROACHING")]
    return max(ok, key=lambda L: L.get("score") or 0) if ok else None


def evaluate(sym: str, s: dict, ctx: dict) -> dict:
    card, meme = s["card"] or {}, s["meme"] or {}
    kind = _kind(sym, s)
    bull = ctx["btc_bull"] is True
    cons = _consultant(s)
    C = max([cons[h] for h in ("mid", "long") if h in cons], default=None)
    R = _num(card.get("rating")) if card else None
    M = _num(meme.get("score")) if meme else None
    pos0 = s["positions"][0][1] if s["positions"] else {}
    pick0 = next(iter(s["picks"].values()), {})
    price = _num(card.get("price")) or _num(meme.get("price")) or _num(pos0.get("price")) or _num(pick0.get("price"))
    chg7 = _num(card.get("chg7")) if card else None
    if chg7 is None:
        chg7 = _num(meme.get("chg7d")) if meme else _num(pick0.get("chg7"))
    chg30 = _num(card.get("chg30")) if card else None
    if chg30 is None:
        chg30 = _num(meme.get("chg30d")) if meme else _num(pick0.get("chg30"))
    chg200, ath = _num(card.get("chg200")), _num(card.get("ath_chg"))
    if ath is None and meme:
        ath = _num(meme.get("from_ath_pct"))
    fdv = _num(card.get("fdv_mcap"))
    vol = s["vol"] if s["vol"] is not None else (_num(meme.get("vol24")) if meme else None)
    dec = s["dec"]
    if meme and _num(meme.get("from_high30_pct")) is not None:
        hi30, hi30_basis = meme["from_high30_pct"], ["оценка мем-деска"]
    else:
        extra = []
        if dec and price and _num(dec.get("price")) and abs(dec["price"] / price - 1) <= 0.10:
            extra = [((dec.get("regime") or {}).get("hh20"))]
        hi30, hi30_basis = high30_lower_bound(price, card.get("chg24"), chg7, chg30, extra)
    overheated = (chg7 is not None and chg7 >= OVERHEAT_7D) or (chg30 is not None and chg30 >= OVERHEAT_30D)
    ps = _pos_state(s)
    held = [k for k, _ in s["positions"]]
    held = list(dict.fromkeys(held))
    sup = _near_support(dec)
    cex = sym in CEX_TOKENS
    dead = kind != "core" and ath is not None and ath <= DEAD_ATH
    diluted = fdv is not None and fdv >= DILUTION_FDV
    circ = round(100 / fdv) if fdv else None

    preferred = any(x.get("preferred") for x in s["picks"].values())
    checks: list[dict] = []

    def chk(key: str, t: str, ok):
        checks.append({"key": key, "t": t, "ok": ok})

    # ---- checks with their real numbers (only the ones that matter for this kind); ok=None — a fact, not a gate ----
    if kind == "meme":
        if not meme:
            chk("desk", "мем-деск эту монету не проверял", False)
        elif meme.get("status") == "avoid":
            chk("desk", f"мем-деск: {meme.get('why_ru') or 'не прошла проверки'}", False)
        else:
            chk("desk", "мем-деск: красных флагов нет, оборот и листинг в порядке", True)
        if M is not None:
            chk("quality", f"качество мема {int(M)}/100", None)
    if R is not None:
        need = RATING_BUY if kind in ("core", "mid") else (RATING_SMALL if kind == "high" else None)
        chk("rating", f"анкета {int(R)}/100" + (f" (нужно от {need})" if need else ""), (R >= need) if need else None)
    elif kind != "meme":
        chk("rating", "анкеты пока нет", False)
    if kind != "meme":
        if C is not None:
            parts = [f"{lab} {cons[h]}/100" for h, lab in (("mid", "тренд"), ("long", "фундамент")) if h in cons]
            chk("consultant", "консультант: " + ", ".join(parts) + (f" (нужно от {CONSULTANT_BUY})" if kind == "mid" else ""),
                (C >= CONSULTANT_BUY) if kind == "mid" else None)
        elif kind == "mid":
            chk("consultant", "консультант монету не выделил", False)
    if preferred:
        chk("preferred", "в списке доверия владельца", None)
    if chg7 is not None or chg30 is not None:
        chk("overheat", f"за 7 дней {_pct(chg7, 1)}, за 30 дней {_pct(chg30, 1)}", not overheated)
    if kind == "mid" and chg200 is not None:
        chk("trend200", f"за 200 дней {_pct(chg200)}", chg200 > 0)
    if ath is not None and kind != "core":
        chk("ath", f"от исторического максимума {_pct(ath)} (предел −90%)", not dead)
    if kind in ("high", "meme"):
        chk("dip", (f"от максимума за 30 дней {_pct(hi30)}, нужно от −30% (оценка снизу)" if hi30 is not None
                    else "максимум за 30 дней неизвестен"), hi30 is not None and hi30 <= DIP_30D)
    if fdv is not None and kind != "core" and (diluted or kind in ("mid", "high")):
        chk("dilution", f"в обращении ~{min(100, circ)}% токенов", not diluted)
    if vol is not None:
        chk("vol", f"оборот {_usd(vol)} в сутки", vol >= MIN_VOL if kind != "mid" else vol >= MIN_VOL_BUY)
    if cex:
        chk("cex", "токен централизованной биржи — наши портфели такие не берут", False)
    if kind != "core":
        chk("regime", "BTC в бычьем режиме" if bull else "BTC в медвежьем режиме", bull)
    if sup and held:
        where = ("цена в зоне поддержки" if sup.get("state") == "IN_ZONE"
                 else f"до поддержки {_pct(abs(sup.get('dist_pct') or 0), 1, sign=False)}")
        chk("support", f"{where} {sup.get('lo')}–{sup.get('hi')}", True)
    for key, pnl, t in ps["at_stop"]:
        chk("stop", f"«{ctx['names'].get(key, key)}» держит её у стопа ({_pct(pnl, 1)})", False)
    for key, t in ps["trim"]:
        chk("trim", f"«{ctx['names'].get(key, key)}» у точки фиксации ({t})", False)

    # ---- status: the first rule that fires wins; dkey = the check that decided it ----
    status, why, dkey = None, None, None
    if kind == "meme" and meme.get("status") == "avoid":
        status, why, dkey = "avoid", f"Мем-деск: {meme.get('why_ru') or 'не прошла проверки'}", "desk"
    elif ps["at_stop"]:
        key, pnl, _ = ps["at_stop"][0]
        status, why, dkey = "avoid", f"Наш портфель «{ctx['names'].get(key, key)}» у стопа ({_pct(pnl, 0)}) — не добираем", "stop"
    elif vol is not None and vol < MIN_VOL:
        status, why, dkey = "avoid", f"Оборот {_usd(vol)} в сутки — цену легко сдвинуть", "vol"
    elif dead:
        status, why, dkey = "avoid", f"{_pct(ath)} от исторического максимума — монета «мёртвая», не берём", "ath"
    if status is None and held and not ps["trim"] and (ps["add"] or sup) and not overheated:
        trend_ok = kind == "core" or (bull and (cons.get("mid", 0) >= ADD_MID or cons.get("long", 0) >= ADD_LONG)
                                      and not cex and not diluted and not (kind == "meme" and meme.get("status") != "small"))
        if trend_ok:
            status, dkey = "add", "support"
            why = ("Уже в портфеле, цена " + ("в зоне поддержки" if sup.get("state") == "IN_ZONE" else "у поддержки")
                   + " — можно добрать" if sup
                   else f"Портфель «{ctx['names'].get(ps['add'][0][0], ps['add'][0][0])}» сам добирает до плановой доли")
    if status is None:
        reason = None
        if kind == "core":
            if overheated:
                reason = (f"Рывок {_pct(chg30 if (chg30 or 0) >= OVERHEAT_30D else chg7)} — ядро тоже не догоняем, ждём", "overheat")
            elif R is None or R < RATING_BUY:
                reason = (f"Анкета {int(R) if R is not None else '—'}/100 ниже порога {RATING_BUY} — ждём", "rating")
            elif ps["trim"]:
                reason = ("У точки фиксации в нашем портфеле — не добираем", "trim")
            else:
                status, dkey = "buy", "rating"
                why = (f"Ядро, анкета {int(R)}/100, BTC бычий — можно брать частями" if bull
                       else f"Ядро, анкета {int(R)}/100; BTC медвежий — брать только частями")
        elif kind == "meme":
            if not meme:
                reason = ("Мем без проверки мем-деска — не берём вслепую", "desk")
            elif meme.get("status") == "small":
                status, dkey = "small", "dip"
                why = meme.get("why_ru") or f"просадка {_pct(hi30)} от максимума за 30 дней"
            else:
                reason = (meme.get("why_ru") or "проверки пройдены, просадки нет — ждём", "dip")
        else:
            if cex:
                reason = ("Токен биржи — наши портфели такие не берут", "cex")
            elif not bull:
                reason = ("BTC в медвежьем режиме — покупаем только ядро", "regime")
            elif overheated:
                d7 = (chg7 or 0) >= OVERHEAT_7D
                reason = (f"Уже {_pct(chg7 if d7 else chg30)} за {7 if d7 else 30} дней — не догоняем, ждём отката", "overheat")
            elif R is None:
                reason = ("Анкеты пока нет — ждём данных", "rating")
            elif kind == "mid":
                if R < RATING_BUY:
                    reason = (f"Анкета {int(R)}/100 ниже порога {RATING_BUY} — ждём", "rating")
                elif C is None:
                    reason = ("Консультант не выделил — тренд не подтверждён", "consultant")
                elif C < CONSULTANT_BUY:
                    reason = (f"Консультант {C}/100 ниже порога {CONSULTANT_BUY} — ждём", "consultant")
                elif chg200 is not None and chg200 <= 0:
                    reason = (f"Ниже, чем 200 дней назад ({_pct(chg200)}) — тренда нет", "trend200")
                elif diluted:
                    reason = (f"В обращении ~{circ}% токенов — впереди разлоки, ждём", "dilution")
                elif vol is not None and vol < MIN_VOL_BUY:
                    reason = (f"Оборот {_usd(vol)} в сутки — для покупки мало", "vol")
                elif ps["trim"]:
                    reason = ("У точки фиксации в нашем портфеле — не добираем", "trim")
            else:  # high risk
                if R < RATING_SMALL:
                    reason = (f"Анкета {int(R)}/100 ниже порога {RATING_SMALL} — ждём", "rating")
                elif diluted:
                    reason = (f"В обращении ~{circ}% токенов — впереди разлоки, ждём", "dilution")
                elif hi30 is None:
                    reason = ("Нет данных о максимуме за 30 дней — ждём", "dip")
                elif hi30 > DIP_30D:
                    reason = (f"Высокий риск без просадки: {_pct(hi30)} от максимума 30 дней, ждём −30%", "dip")
                elif ps["trim"]:
                    reason = ("У точки фиксации в нашем портфеле — не добираем", "trim")
            if status is None and reason is None:
                if kind == "mid":
                    status, dkey = "buy", "consultant"
                    why = f"Анкета {int(R)}, консультант {C}, тренд вверх, BTC бычий — можно брать"
                else:
                    status, dkey = "small", "dip"
                    why = f"Высокий риск, просадка {_pct(hi30)} от максимума 30 дней — небольшой суммой"
        if status is None:
            status, (why, dkey) = "wait", reason

    # ---- upcoming unlock (18.10, tokenomics.json `unlocks_next`; study scripts/unlocks_study.py, 287 unlocks 2024–2026) ----
    un = (ctx.get("unl") or {}).get(sym)
    upct = _num((un or {}).get("pct_circ"))
    if un and upct is not None and upct >= UNLOCK_WARN and kind != "core":
        when = "сегодня" if un["days"] == 0 else f"через {un['days']} дн."
        big = upct >= UNLOCK_BIG
        chk("unlock", f"{when} ({un['date']}) разлок {upct:g}% монет в обращении", False if big else None)
        if big and status in ("buy", "add", "small"):
            status, dkey = "wait", "unlock"
            why = (f"{when[:1].upper() + when[1:]} разлок {upct:g}% монет в обращении — до разлока не берём: в 2024–2026 такие "
                   "монеты за 30 дней до разлока отставали от BTC в среднем на 15%")

    # ---- holders revenue (18.10, tokenomics.json): the protocol pays its token holders (buybacks / burns / payouts) ----
    tk = (ctx.get("tok") or {}).get(sym) or {}
    h30 = _num(tk.get("holders_rev_30d_usd"))
    mcap = _num(card.get("mcap")) if card else None
    hy = h30 * 12 / mcap if (h30 and mcap) else None
    if kind != "meme":
        if h30 and h30 >= 100_000:
            chk("holders", f"держатели получают от протокола {_usd(h30)} за 30 дней" + (f" (≈{hy*100:.1f}% капитализации в год)" if hy else "")
                + " — выкуп, сжигание или выплаты", None)
        elif kind == "mid" and ctx.get("tok"):
            chk("holders", "держатели дохода протокола не получают (по DefiLlama) — рост держится только на спросе", None)
    sg = _num(tk.get("supply_30d_pct"))
    if sg is not None and sg >= 2 and kind != "core":
        chk("supply", f"за 30 дней монет в обращении стало больше на {sg:.1f}% — идут разлоки; по исследованию Keyrock около 90% "
                      "крупных разлоков давили на цену", None)

    # ---- quality card (19.10, spot_quality.py; study: revenue +2 %/month vs other alts, older coins +3 %, hype lost) ----
    q = (ctx.get("qual") or {}).get(sym) or (ctx.get("qual") or {}).get("1000" + sym)
    q_adj = 0
    if q and kind not in ("core", "meme"):
        n_q = max(1, len(ctx.get("qual") or {}))
        tags = ["есть выручка" if q.get("rev") else "без выручки", f"{int(q['age'] // 365)} г. торгов" if q.get("age") else None,
                ("объём раздут" if (q.get("vt") or 0) > 1.3 else "объём спокойный") if q.get("vt") is not None else None]
        chk("quality", f"анкета качества: место {q.get('rank')} из {n_q} ({', '.join(t for t in tags if t)})"
            + (" — в альт-части модели «ядро + альтсезон»" if sym in (ctx.get("qual_basket") or set()) else ""), None)
        q_adj = 8 if q.get("rank", n_q) <= n_q / 3 else -8 if q.get("rank", 0) > 2 * n_q / 3 else 0

    # ---- score ----
    parts = [(v, w) for v, w in ((R, 0.5), (C, 0.3), (M, 0.2)) if v is not None]
    score = round(sum(v * w for v, w in parts) / sum(w for _, w in parts)) if parts else 0
    if hy is not None and kind != "meme":         # a real yield to holders lifts the coin in the order (≤ +10 points)
        score = min(100, score + round(min(10.0, hy * 100 * 2)))
    if sg is not None and sg >= 2 and kind != "core":   # realised dilution (unlocks) pushes it down (≤ −10 points)
        score = max(0, score - round(min(10.0, sg * 2)))
    score = max(0, min(100, score + q_adj))             # quality card: top third +8, bottom third −8 (never a pass / fail check)

    # ---- info for the «i» popup: 2–5 short sentences, the deciding check first ----
    first = lambda xs: sorted(xs, key=lambda c: c["key"] != dkey)   # noqa: E731
    ok = [c["t"] for c in first([c for c in checks if c["ok"] is True])][:4 if status in ("buy", "add", "small") else 3]
    bad = [c["t"] for c in first([c for c in checks if c["ok"] is False])][:3]
    facts = [c["t"] for c in checks if c["ok"] is None][:2]
    s_ok = ("Пройдено: " + "; ".join(ok + facts) + ".") if ok or facts else None
    s_bad = ("Не пройдено: " + "; ".join(bad) + ".") if bad else None
    sent = [x for x in ((s_bad, s_ok) if status in ("wait", "avoid") else (s_ok, s_bad)) if x]
    if kind == "core":
        bt = ctx.get("core_bt") or {}
        if bt.get("final_x") and bt.get("max_dd_pct") is not None:
            sent.append(f"Ядро держим через циклы без стопа: в проверке 2021–2026 доли BTC/ETH/SOL 50/25/15 дали ×{bt['final_x']}, "
                        f"худшая просадка {_pct(bt['max_dd_pct'], 0, sign=False)} (scripts/balanced_backtest.py).")
        else:
            sent.append("Ядро держим через циклы без стопа — правило «Консервативного» и «Сбалансированного» портфелей.")
    elif kind == "mid":
        sent.append("Консультант сравнивает рост за 30/200 дней и оборот с другими монетами; фильтры — как у крупных альтов "
                    "«Сбалансированного».")
    elif kind == "meme":
        sent.append("В исследовании мемов (docs/research/memes.md) плюс давала только покупка после обвала, вход после роста — минус.")
    else:
        sent.append("Для альтов высокого риска доказанного правила нет: в честной проверке откупы просадок дали ноль или минус "
                    "(docs/research/alt100.md).")
    if held:
        recos = list(dict.fromkeys(t for _, t in ps["recos"]))
        sent.append("Уже в портфелях: " + ", ".join(f"«{ctx['names'].get(k, k)}»" for k in held)
                    + (f" (рекомендация: {', '.join(recos[:2])})" if recos else "") + ".")
    tail = "Это не прогноз цены, а правило отбора по сегодняшним данным"
    sent.append(tail + ("; только сумма, которую не жалко потерять." if status == "small" else "."))
    if len(sent) > 5:
        sent = sent[:4] + [sent[-1]]

    links = {}
    if card.get("id"):
        links["coingecko"] = f"https://www.coingecko.com/en/coins/{card['id']}"
    elif meme.get("link"):
        links["coingecko"] = meme["link"]
    if meme.get("link") and meme["link"] != links.get("coingecko"):
        links["meme"] = meme["link"]

    name = card.get("name") or meme.get("name") or pos0.get("name") or pick0.get("name") or sym
    row = {"symbol": sym, "name": name, "kind": kind, "kind_ru": KIND_RU[kind], "status": status, "status_ru": STATUS_RU[status],
           "score": int(score), "why_ru": _clip(why[:1].upper() + why[1:]), "info_ru": " ".join(sent), "in_portfolios": held,
           "price": price, "chg7": round(chg7, 2) if chg7 is not None else None,
           "from_high30_pct": hi30, "high30_basis": hi30_basis, "rating": int(R) if R is not None else None,
           "consultant": cons, "meme_quality": int(M) if M is not None else None, "checks": checks,
           "decided": dkey}            # 28.10: the check that decided the status («stop» = another portfolio at its stop)
    if card.get("image"):
        row["image"] = card["image"]
    if links:
        row["links"] = links
    row["_notable"] = bool(held or s["picks"])
    return row


def _select(rows: list[dict]) -> list[dict]:
    key = lambda r: (STATUS_ORDER.index(r["status"]), -r["score"], r["symbol"])   # noqa: E731
    rows = sorted(rows, key=key)
    keep = [r for r in rows if r["status"] != "avoid"]
    avoid = sorted([r for r in rows if r["status"] == "avoid"], key=lambda r: (not r["_notable"], -r["score"], r["symbol"]))
    n_keep = min(len(keep), MAX_ROWS - min(AVOID_SLOTS, len(avoid)))
    n_avoid = min(len(avoid), MAX_ROWS - n_keep)
    return sorted(keep[:n_keep] + avoid[:n_avoid], key=key)


def _names(rows: list[dict], st: str, n: int = 8) -> str:
    xs = [r["symbol"] for r in rows if r["status"] == st]
    return ", ".join(xs[:n]) + (f" и ещё {len(xs) - n}" if len(xs) > n else "")


def build(inp: dict, now: int | None = None) -> dict:
    now = now or int(time.time() * 1000)
    coins, ctx = _gather(inp, now)
    allrows = [evaluate(sym, s, ctx) for sym, s in coins.items()]
    rows = _select(allrows)
    counts = {k: sum(1 for r in rows if r["status"] == k) for k in STATUS_ORDER}
    counts_all = {k: sum(1 for r in allrows if r["status"] == k) for k in STATUS_ORDER}
    parts = []
    if counts["buy"]:
        parts.append("можно брать: " + _names(rows, "buy"))
    if counts["add"]:
        parts.append("можно добрать: " + _names(rows, "add"))
    if counts["small"]:
        parts.append("на небольшую сумму: " + _names(rows, "small"))
    head = ("Сейчас " + "; ".join(parts)) if parts else "Сейчас брать нечего"
    summary = head + "; остальные ждём" + (f" (не брать: {_names(rows, 'avoid', 5)})" if counts["avoid"] else "") + "."
    bull = ctx["btc_bull"]
    for r in rows:
        r.pop("_notable", None)
    return {"asof": now, "available": bool(allrows), "btc_bull": bull,
            "regime_ru": ("BTC в бычьем режиме" if bull is True else "BTC в медвежьем режиме — можно брать только ядро" if bull is False
                          else "режим BTC неизвестен — считаем медвежьим, можно брать только ядро"),
            "summary_ru": summary, "rules_ru": RULES_RU,
            "score_formula_ru": "балл 0–100 = анкета × 0.5 + консультант (max тренд/фундамент) × 0.3 + качество мема × 0.2; "
                                "веса делятся заново, если какой-то оценки нет; анкета качества альтов: верхняя треть +8, нижняя −8. "
                                "Балл — для порядка, не прогноз роста.",
            "counts": counts, "counts_all": counts_all, "evaluated": len(allrows), "shown": len(rows),
            "portfolio_names": ctx["names"], "sources": ctx["sources"],
            "disclaimer_ru": "Правила отбора по данным панели, а не прогноз цены и не инвестиционный совет.",
            "rows": rows,
            # every evaluated coin, not only the 30 shown rows — «По рекомендациям» sells by status even when a coin left the table
            "statuses": {r["symbol"]: {"status": r["status"], "status_ru": r["status_ru"], "why_ru": r["why_ru"], "kind": r["kind"],
                                       "price": r["price"], "decided": r.get("decided")} for r in sorted(allrows, key=lambda r: r["symbol"])}}


def run(web_dir: str = "web", now: int | None = None) -> dict:
    return build(load_inputs(web_dir), now)


def write(out_dir: str = "web", web_dir: str | None = None) -> dict:
    d = run(web_dir or out_dir)
    # 22.10: «Поддержка проекта» (cnq/agents/support.py) — information only: adds rows[].support / support, changes no status
    try:
        from . import support
        support.attach(d, web_dir or out_dir)
    except Exception as e:  # noqa: BLE001 — the table is written even if the support block fails
        d["support_error"] = repr(e)[:200]
    os.makedirs(out_dir, exist_ok=True)
    tmp = os.path.join(out_dir, "ideas.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(out_dir, "ideas.json"))
    return d
