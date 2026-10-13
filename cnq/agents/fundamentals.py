"""Fundamentals department — CoinGecko public API (no key, rate-limited ~10-30 req/min).

Gives: market cap, FDV, circulating/total supply (→ unlock overhang = (total−circ)/circ),
30d/7d/24h change, volume/market-cap ratio, developer & community scores when present.
Deterministic red flags; the LLM is not involved here."""
from __future__ import annotations

import json
import time
import urllib.request

CG = "https://api.coingecko.com/api/v3"
IDS = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "BNB": "binancecoin", "XRP": "ripple", "DOGE": "dogecoin",
       "ADA": "cardano", "AVAX": "avalanche-2", "LINK": "chainlink", "LTC": "litecoin", "APT": "aptos", "ARB": "arbitrum",
       "BCH": "bitcoin-cash", "DOT": "polkadot", "NEAR": "near", "OP": "optimism", "SUI": "sui", "TRX": "tron"}


def _get(path: str, timeout: float = 20) -> dict | list | None:
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{CG}{path}", headers={"User-Agent": "cnq/1.0"}), timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:  # noqa: BLE001
        return None


def snapshot(symbols: list[str], sleep_s: float = 2.5) -> dict[str, dict]:
    """One call per coin (markets endpoint is cheaper but lacks dev/community data)."""
    out = {}
    for s in symbols:
        cid = IDS.get(s)
        if not cid:
            continue
        d = _get(f"/coins/{cid}?localization=false&tickers=false&community_data=true&developer_data=true&sparkline=false")
        if not d:
            out[s] = {"available": False}
            continue
        md = d.get("market_data", {})
        circ, total, maxs = md.get("circulating_supply"), md.get("total_supply"), md.get("max_supply")
        mcap, fdv = md.get("market_cap", {}).get("usd"), md.get("fully_diluted_valuation", {}).get("usd")
        vol = md.get("total_volume", {}).get("usd")
        out[s] = assess({
            "available": True, "name": d.get("name"), "mcap_usd": mcap, "fdv_usd": fdv, "volume_24h_usd": vol,
            "circulating": circ, "total_supply": total, "max_supply": maxs,
            "chg_24h": md.get("price_change_percentage_24h"), "chg_7d": md.get("price_change_percentage_7d"),
            "chg_30d": md.get("price_change_percentage_30d"), "ath_change_pct": md.get("ath_change_percentage", {}).get("usd"),
            "dev_commits_4w": (d.get("developer_data") or {}).get("commit_count_4_weeks"),
            "dev_stars": (d.get("developer_data") or {}).get("stars"),
            "twitter_followers": (d.get("community_data") or {}).get("twitter_followers"),
            "categories": (d.get("categories") or [])[:4], "fetched": int(time.time() * 1000)})
        time.sleep(sleep_s)
    return out


def assess(f: dict) -> dict:
    """Deterministic red/green flags from the numbers."""
    flags = []
    circ, total, mcap, fdv, vol = f.get("circulating"), f.get("total_supply"), f.get("mcap_usd"), f.get("fdv_usd"), f.get("volume_24h_usd")
    if circ and total and total > circ:
        overhang = (total - circ) / circ
        f["unlock_overhang"] = round(overhang, 3)
        if overhang > 1.0:
            flags.append(("red", f"нераспределённый саплай {overhang*100:.0f}% от обращения — давление будущих разлоков"))
        elif overhang > 0.3:
            flags.append(("amber", f"саплай в обращении {circ/total*100:.0f}% от общего"))
    if mcap and fdv and fdv > mcap * 1.5:
        flags.append(("amber", f"FDV/MCap {fdv/mcap:.1f}×"))
    if mcap and vol:
        f["vol_mcap"] = round(vol / mcap, 3)
        if vol / mcap < 0.02:
            flags.append(("amber", "низкая ликвидность: объём < 2% капитализации"))
        elif vol / mcap > 0.5:
            flags.append(("amber", "объём > 50% капитализации — спекулятивный всплеск"))
    if f.get("dev_commits_4w") is not None and f["dev_commits_4w"] == 0:
        flags.append(("amber", "0 коммитов за 4 недели"))
    if f.get("ath_change_pct") is not None and f["ath_change_pct"] < -90:
        flags.append(("red", f"{f['ath_change_pct']:.0f}% от исторического максимума"))
    f["flags"] = flags
    f["grade"] = "red" if any(c == "red" for c, _ in flags) else ("amber" if flags else "green")
    return f
