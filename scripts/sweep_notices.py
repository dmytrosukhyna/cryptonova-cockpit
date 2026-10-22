"""Short GitHub notices from sweep.json (scripts/sweep_study.py --md) — the results are readable through the check-run annotations
API, because the job log is not reachable from the research sandbox. At most 10 notices (GitHub's limit per step)."""
import json
import sys

d = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "sweep.json"))


def cell(c, k):
    r = c.get(k) or {}
    return (f"n{r.get('n', 0)} avg{r.get('avg_pct')}% win{r.get('win_pct')}% t{r.get('t')} tw{r.get('t_week')} "
            f"yrs{r.get('years_plus')}" + (f" eq×{r['equity_x']}" if "equity_x" in r else ""))


lines = []
for tf in ("1d.W3", "4h.W24"):
    s = d.get(tf) or {}
    for grp in ("BTC", "ALL"):
        g = s.get(grp) or {}
        for kind in ("sweep", "breakdown"):
            parts = []
            for regn in ("any", "rost", "below"):
                c = g.get(f"{kind}.{regn}") or {}
                parts.append(f"[{regn}] fwd7 {cell(c, 'fwd7')} | rule7 {cell(c, 'rule_h7_struct')}")
            lines.append(f"{tf} {grp} {kind}: " + " || ".join(parts))
for ln in lines[:10]:
    print("::notice title=sweep::" + ln.replace("\n", " ")[:3500])
