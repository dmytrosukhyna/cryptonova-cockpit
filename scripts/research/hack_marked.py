"""Hand-marked hacks (owner 20.11.2026, part 2 of «Взломы → шорт»): the largest 2022–2026 incidents whose token had a USDT perp, with
the EARLIEST PUBLIC TIME we could find for each (a news article's own published_time, or an FXStreet URL time stamp — later than the
first X alert, which is not reachable without the paid X API; the exploit's on-chain start when an article states it). RESEARCH ONLY,
small n — indicative, no pass rule. Trade: short at t + 5 / 15 / 60 min (5-minute closes), exit after 1 h / 4 h / 24 h / 72 h / 7 d;
costs 0.10 % + funding 0.01 % per 8 h (assumed). Also the move from t − 6 h to t (already gone before the report).
Prices: as hack_short_study.py (Binance USDT-M incl. the archive of delisted symbols, else OKX). Usage: hack_marked.py OUT.json"""
import json, os, statistics as st, sys
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hack_short_study as S

# name, token, earliest public time we found (UTC), its source, on-chain start if an article states it
CASES = [
    ("Curve (Vyper)", "CRV", "2023-07-30T17:09:00", "https://www.theblock.co/news/ecosystems/2023-07-30-curve-finance-factory-pools-targeted-due-to-reentrancy-vulnerability-242066", None),
    ("KyberSwap Elastic", "KNC", "2023-11-22T23:19:00", "https://fxstreet.com/amp/cryptocurrencies/news/breaking-kyber-network-exploited-across-multiple-chains-for-over-20-million-202311222319", None),
    ("Radiant", "RDNT", "2024-10-16T18:38:49", "https://theblock.co/post/321606/radiant-capital-appears-to-suffer-51-million-exploit-on-its-bnb-chain-and-arbitrum-instances", "2024-10-16T17:09:00"),
    ("Cetus", "CETUS", "2025-05-22T11:58:23", "https://www.theblock.co/news/regulation/2025-05-22-sui-based-token-prices-cetus-protocol-355372", None),
    ("Balancer V2", "BAL", "2025-11-03T08:35:56", "https://www.theblock.co/news/defi/2025-11-03-defi-protocol-balancer-potentially-exploited-as-onchain-data-shows-millions-in-outflows-377223", None),
    ("GMX V1", "GMX", "2025-07-09T14:24:08", "https://www.theblock.co/post/361806/hacker-drains-42-million-from-decentralized-perpetual-exchange-gmx", None),
    ("Kelp rsETH", "KERNEL", "2026-04-18T19:17:12", "https://www.theblock.co/post/397988/kelp-dao-293-million-defi-hack-lazarus-layerzero", "2026-04-18T17:35:00"),
    ("Humanity", "H", "2026-06-09T01:15:05", "https://www.theblock.co/post/404053/humanity-protocol-exploit", None),
    ("Resolv USR", "RESOLV", "2026-03-22T15:38:32", "https://www.theblock.co/post/394582", "2026-03-22T02:21:00"),
    ("Drift", "DRIFT", "2026-04-01T18:34:27", "https://www.theblock.co/post/396107/solana-trading-platform-drift-exploited-at-least-200-million", None),
    ("UXLINK", "UXLINK", "2025-09-23T05:59:30", "https://www.theblock.co/post/371783/uxlink-multisig-hack", None),
    ("WOO X", "WOO", "2025-07-24T17:22:57", "https://www.theblock.co/post/364188/centralized-exchange-woo-x-investigates-contained-incident-resulting-in-estimated-14-million-loss", None),
    ("Abracadabra (GM cauldrons)", "SPELL", "2025-03-25T15:59:57", "https://www.theblock.co/post/348059/hacker-steals-13-million-in-abracadabras-magic-internet-money-seemingly-using-a-flash-loan-attack", None),
    ("Yearn yETH", "YFI", "2025-11-30T22:49:36", "https://www.theblock.co/post/380822/3-million-in-eth-sent-to-tornado-cash-following-apparent-attack-on-yearns-yeth", None),
    ("THORChain", "RUNE", "2026-05-15T10:20:56", "https://theblock.co/post/401462/thorchain-pauses-trading-as-security-researchers-flag-suspected-10m-multi-chain-exploit", None),
    ("Trust Wallet extension", "TWT", "2025-12-26T02:01:03", "https://www.theblock.co/post/383735/trust-wallet-extension-incident", None),
    ("WOOFi", "WOO", "2024-03-06T08:06:25", "https://www.theblock.co/news/ecosystems/2024-03-06-woofi-offers-bounty-after-8-75-million-exploit-on-arbitrum-280759", "2024-03-05T15:49:00"),
    ("Bittensor", "TAO", "2024-07-03T02:46:36", "https://www.theblock.co/post/303235/bittensor-halts-network-after-reported-security-attack-on-wallets-zachxbt", None),
    ("Yearn yUSDT", "YFI", "2023-04-13T08:12:00", "https://www.fxstreet.com/cryptocurrencies/news/yearn-finance-victim-of-a-flash-loan-exploit-blockchain-security-firm-alerts-defi-protocol-202304130812", None),
    ("SagaEVM", "SAGA", "2026-01-22T04:40:54", "https://www.theblock.co/post/386638/sagaevm-suffers-exploit", None),
    ("Taiko bridge", "TAIKO", "2026-06-22T05:01:25", "https://www.theblock.co/post/405486", None),
    ("Abracadabra (MIM)", "SPELL", "2024-01-30T12:35:00", "https://fxstreet.com/amp/cryptocurrencies/news/abracadabra-finance-loses-nearly-65-million-to-an-exploit-202401301235", None),
    ("dYdX v3 (YFI)", "DYDX", "2023-11-18T19:12:32", "https://www.theblock.co/post/263632/dydxs-insurance-fund-lost-9-million-as-a-result-of-targeted-attack", None),
]
DELAYS = (5, 15, 60)


def ms(s):
    return int(datetime.fromisoformat(s + "+00:00").timestamp() * 1000)


def main():
    rows = []
    for name, tok, t_pub, link, t_start in CASES:
        t = ms(t_pub)
        mk = S.find_market(tok, t)
        if not mk:
            rows.append({"name": name, "token": tok, "t": t, "market": None})
            continue
        venue, sym = mk
        K5 = S.candles(venue, sym, "5m", t - 7 * S.H, t + 2 * S.DAY)
        K1 = S.candles(venue, sym, "1h", t - 2 * S.DAY, t + 8 * S.DAY)
        r = {"name": name, "token": tok, "symbol": sym, "venue": venue, "t": t, "link": link, "t_start": ms(t_start) if t_start else None,
             "pre6h_pct": None, "short": {}}
        p6, p0 = S.px_at(K5, t - 6 * S.H), S.px_at(K5, t)
        if p6 and p0:
            r["pre6h_pct"] = round((p0 / p6 - 1) * 100, 2)
        for d in DELAYS:
            r["short"][str(d)] = {hn: (round(v, 2) if v is not None else None) for hn, hv in S.HORIZ.items()
                                  for v in [S.short_ret(K5 if hv <= S.DAY else K1, t + d * 60_000, hv)]}
        if t_start:
            K5s = S.candles(venue, sym, "5m", r["t_start"] - S.H, r["t_start"] + 2 * S.DAY)
            r["from_start_15m"] = {hn: (round(v, 2) if v is not None else None) for hn, hv in S.HORIZ.items() if hv <= S.DAY
                                   for v in [S.short_ret(K5s, r["t_start"] + 15 * 60_000, hv)]}
        rows.append(r)
        print(name, sym, r["pre6h_pct"], r["short"]["15"], flush=True)
    summ = {}
    ok = [r for r in rows if r.get("short")]
    for d in DELAYS:
        summ[str(d)] = {}
        for hn in S.HORIZ:
            xs = [r["short"][str(d)][hn] for r in ok if r["short"][str(d)].get(hn) is not None]
            summ[str(d)][hn] = {"n": len(xs), "mean": round(st.mean(xs), 2) if xs else None, "median": round(st.median(xs), 2) if xs else None,
                                "win": round(sum(x > 0 for x in xs) / len(xs), 2) if xs else None, "worst": min(xs) if xs else None,
                                "t": S.tstat(xs)}
    pre = [r["pre6h_pct"] for r in ok if r.get("pre6h_pct") is not None]
    O = {"cases": rows, "summary": summ, "pre6h_median": round(st.median(pre), 2) if pre else None, "priced": len(ok), "listed": len(CASES)}
    json.dump(O, open(sys.argv[1], "w"))


if __name__ == "__main__":
    main()
