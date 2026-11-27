"""The daily desk improver on the server (owner 27.11.2026 09:38; logic in cnq/agents/desk_improver.py). PAPER / RESEARCH ONLY.
A thread starts `python -m cnq.api.improver run` once a UTC day (and 10 minutes after the API starts, when today's run is missing) as
a separate low-priority process, so the history tests never slow the API. The run reads the paper desks' states in <run>/data, writes
<run>/data/improver/{status.json, log.md}, the children files (levels_lab/children.json, exp_factory/children.json) and publishes
docs/research/improver_log.md (counts and percentages only, like the strategy board). Nothing here can place an order."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT_REPO = os.path.dirname(os.path.dirname(HERE))


def _load(p, d=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return d


def _save(p, doc):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    os.replace(p + ".tmp", p)


def run(root: str, now: int | None = None, candles=None, hist=None, publish=None, env=None) -> dict:
    """one improver pass. candles(coin) → recent OKX hourly; hist(coin) → long OKX hourly history (for the fix tests)"""
    from cnq.agents import desk_improver as DI, levels_desk as LD, levels_lab_desk as LL
    from cnq.agents import exp_factory as F
    now = int(now or time.time() * 1000)
    day = time.strftime("%Y-%m-%d", time.gmtime(now / 1000))
    cache, hcache, pcache, hist_info = {}, {}, {}, {}

    def cd(c):
        if c not in cache:
            cache[c] = (candles or (lambda x: LD.hourly(x, pages=12)))(c)
        return cache[c]
    g4 = {}
    for k in cd("BTC") or []:
        g4[k[0] - k[0] % (4 * DI.H)] = k[4]
    btc4 = sorted(g4.items())
    D = DI.desks(root)
    diag, tests, fixes_by = {}, {}, {}
    for d in D:
        dg = DI.diagnose(d, cd, btc4)
        diag[d["key"]] = dg
        if d["kind"] == "lab" and d.get("variant"):
            fixes_by[d["key"]] = ("lab", DI.lab_fixes(d["variant"], dg["symptoms"], dg["concentration"]), d)
        elif d["kind"] == "factory":
            fixes_by[d["key"]] = ("factory", DI.factory_fixes(d["spec"], dg["symptoms"], dg["concentration"]), d)
        elif d["kind"] == "whale":
            base = {"src": "whales", "side": "follow", "filt": [], "entry": "now", "stop": 16, "tp": None, "hold": None, "size": "fixed",
                    "family": "улучшатель"}
            fixes_by[d["key"]] = ("factory", DI.factory_fixes(base, dg["symptoms"], dg["concentration"]), d)
    m_total = sum(len(f) for k_, (kind, f, _d) in fixes_by.items() if kind == "lab")
    # history (only when some lab fix needs it): the coins of those desks, ~125 days of OKX hourly candles
    lab_need = {k: v for k, v in fixes_by.items() if v[0] == "lab" and v[1]}
    if lab_need:
        coins = sorted({c for _k, (_x, _f, d) in lab_need.items() for c in (d.get("coins") or LD.COINS)})
        for c in coins:
            try:
                K = (hist or (lambda x: LD.hourly(x, pages=30)))(c)
                hist_info[c] = len(K)
                if len(K) >= 600:
                    pcache[c] = LL.prep(K)
                if not hist:
                    time.sleep(0.5)                    # OKX public rate limit
            except Exception as e:  # noqa: BLE001
                hist_info[c] = type(e).__name__

        def regime_of(t):
            return DI.regime_at(btc4, t)
        for k, (_x, fx, d) in lab_need.items():
            Ps = {c: pcache[c] for c in (d.get("coins") or LD.COINS) if c in pcache}
            tests[k] = DI.test_fixes(d["variant"], fx, Ps, m_total, regime_of)
    # children: spawn (≤ 2 a day, ≤ 10 alive), kill (≥ 20 closes, worse than the parent)
    lab_ch_p, fac_ch_p = os.path.join(root, "levels_lab", "children.json"), os.path.join(root, "exp_factory", "children.json")
    lab_ch, fac_ch = _load(lab_ch_p, []) or [], _load(fac_ch_p, []) or []
    alive = [c for c in lab_ch + fac_ch if c.get("alive", True)]
    spawned = []
    cand = []
    for k, rs in tests.items():
        for r in rs:
            if r["beats"]:
                cand.append((0 if r["passed"] else 1, -(r["score"] or 0), k, r))
    cand.sort(key=lambda x: x[:2])
    have = {(c.get("parent"), c.get("fix")) for c in lab_ch + fac_ch}
    for _p, _s, k, r in cand:
        if len(spawned) >= DI.NEW_A_DAY or len(alive) + len(spawned) >= DI.MAX_ALIVE or (k, r["fix"]) in have:
            continue
        par = next(d for d in D if d["key"] == k)
        key = f"i{day.replace('-', '')[2:]}{len(lab_ch) + len(spawned) + 1}"
        c = {"key": key, "parent": k, "fix": r["fix"], "variant": r["variant"], "coins": par.get("coins"), "risk": 0.01, "born": now,
             "alive": True, "label_ru": ("прошёл тест улучшателя" if r["passed"] else "наблюдение — не доказано (лучше родителя на истории)"),
             "name_ru": f"{k} → {r['fix']}", "test": {"h1": r["h1"], "h2": r["h2"], "parent_h1": r["parent_h1"], "parent_h2": r["parent_h2"], "z": r["z"]},
             "test_ru": ("прошёл" if r["passed"] else "лучше родителя в обеих половинах истории, не доказано"), **r["extra"]}
        spawned.append(c)
        lab_ch.append(c)
    for k, (kind, fx, d) in fixes_by.items():
        if kind != "factory":
            continue
        for name, spec in fx[:1]:
            if len(spawned) >= DI.NEW_A_DAY or len(alive) + len(spawned) >= DI.MAX_ALIVE or (k, name) in have:
                continue
            c = {"key": f"f{day.replace('-', '')[2:]}{len(fac_ch) + 1}", "parent": k, "fix": name, "spec": {**spec, "family": "улучшатель"},
                 "born": now, "alive": True, "test_ru": "не проверено на истории (истории трейдеров на сервере нет) — проверка вперёд"}
            spawned.append(c)
            fac_ch.append(c)
    by_key = {d["key"]: d for d in D}

    def perf(c):
        if "spec" in c:
            fs = _load(os.path.join(root, "exp_factory", "state.json"), {}) or {}
            sid_ = F.sid({**{"side": "follow", "filt": [], "entry": "now", "stop": 16, "tp": None, "hold": None, "size": "fixed"}, **c["spec"]})
            mine = [x["pct"] for x in fs.get("closed") or [] if x.get("spec") == sid_]
        else:
            s_ = _load(os.path.join(root, "levels_lab", f"desk_{c['key']}.json"), {}) or {}
            mine = [x["pct"] for x in (s_.get("closed") or {}).values()]
        par = [t["pct"] for t in (by_key.get(c["parent"]) or {}).get("trades", []) if t["t"] >= int(c["born"])]
        return mine, par
    killed = DI.kill_check(lab_ch + fac_ch, perf)
    _save(lab_ch_p, lab_ch)
    _save(fac_ch_p, fac_ch)
    prev = _load(os.path.join(root, "improver", "status.json"), {}) or {}
    status = {"t": now, "day": day, "runs_today": (int(prev.get("runs_today") or 0) + 1) if prev.get("day") == day else 1,
              "retry": bool(lab_need and not any(tests.get(k) for k in lab_need)),
              "history": {"coins": len(pcache), "bars": hist_info, "parent_trades": {k: len(DI.history_trades(
                  {c: pcache[c] for c in (d.get("coins") or LD.COINS) if c in pcache}, d["variant"], {})) for k, (_x, _f, d) in lab_need.items()}}, "desks_seen": len(D), "diagnosed": list(diag), "symptoms": {k: v["symptoms"] for k, v in diag.items()},
              "fixes_tested": m_total, "spawned": [c["key"] for c in spawned], "killed": [c["key"] for c in killed],
              "children_alive": sum(1 for c in lab_ch + fac_ch if c.get("alive", True))}
    d_ = os.path.join(root, "improver")
    _save(os.path.join(d_, "status.json"), status)
    _save(os.path.join(d_, "diagnosis.json"), {"t": now, "diag": diag, "tests": tests})
    try:
        old = open(os.path.join(d_, "log.md"), encoding="utf-8").read()
    except OSError:
        old = ""
    md = DI.log_md(day, diag, tests, spawned, killed, old)
    with open(os.path.join(d_, "log.md"), "w", encoding="utf-8") as f:
        f.write(md)
    (publish or _publish)(md, day, env)
    fw = scout_forward(root, now)                      # 27.11 (owner 09:39): the trader scout's promotion / demotion input
    (publish or _publish)(json.dumps(fw, ensure_ascii=False, indent=1), day, env, "docs/research/scout_forward.json")
    status["scout_forward"] = len(fw["traders"])
    _save(os.path.join(d_, "status.json"), status)
    return status


def scout_forward(root: str, now: int) -> dict:
    """{traders: {addr: {n, pf, mean, gain, max_dd}}} from the factory's solo paper specs of the watched traders (src hlw / wsw)"""
    fs = _load(os.path.join(root, "exp_factory", "state.json"), {}) or {}
    solo = {s_["id"]: s_.get("sub") for s_ in fs.get("specs") or [] if s_.get("src") in ("hlw", "wsw") and s_.get("sub") and not s_.get("filt")
            and s_.get("entry", "now") == "now"}
    by: dict = {}
    for c in sorted(fs.get("closed") or [], key=lambda x: x.get("closed_t") or 0):
        a = solo.get(c.get("spec"))
        if a:
            by.setdefault(a, []).append(float(c.get("pct") or 0))
    out = {}
    for a, xs in by.items():
        cum = peak = dd = 0.0
        for x in xs:
            cum += x
            peak = max(peak, cum)
            dd = max(dd, peak - cum)
        gp, gl = sum(x for x in xs if x > 0), -sum(x for x in xs if x < 0)
        out[a] = {"n": len(xs), "pf": round(gp / gl, 2) if gl else (99.0 if gp else None), "mean": round(sum(xs) / len(xs), 3),
                  "gain": round(cum, 2), "max_dd": round(dd, 2)}
    return {"t": now, "about_ru": "бумажные копии наблюдаемых трейдеров (фабрика, одиночные спецификации); вход для разведчика", "traders": out}


def _publish(md: str, day: str, env=None, path: str = "docs/research/improver_log.md") -> bool:
    from cnq.execution import ivan_watch
    e = env if env is not None else os.environ
    tok, repo = e.get("CNQ_GH_TOKEN", ""), e.get("CNQ_GH_REPO") or ivan_watch.DEFAULT_REPO
    if not tok:
        return False
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    hdr = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json", "User-Agent": "cnq-improver"}
    try:
        return bool(ivan_watch.put_contents(url, hdr, base64.b64encode(md.encode()).decode(), f"{os.path.basename(path)} {day}"))
    except Exception:  # noqa: BLE001
        return False


class Improver:
    def __init__(self, data_dir: str):
        self.root = data_dir                        # <run>/data
        self.last_err: str | None = None

    def due(self, now: int) -> bool:
        st = _load(os.path.join(self.root, "improver", "status.json"), {}) or {}
        today = time.strftime("%Y-%m-%d", time.gmtime(now / 1000))
        if st.get("day") == today:                  # a run whose history tests came back empty is retried (≤ 3 runs a day)
            return ("runs_today" not in st) or (bool(st.get("retry")) and int(st.get("runs_today") or 0) < 3)
        return time.gmtime(now / 1000).tm_hour >= 5

    def start(self) -> None:
        def loop():
            time.sleep(600)
            while True:
                try:
                    if self.due(int(time.time() * 1000)):
                        subprocess.run(["nice", "-n", "15", sys.executable, "-m", "cnq.api.improver", "run", self.root], cwd=ROOT_REPO,
                                       timeout=3 * 3600, check=False)
                    self.last_err = None
                except Exception as e:  # noqa: BLE001
                    self.last_err = type(e).__name__
                time.sleep(1800)
        threading.Thread(target=loop, daemon=True, name="desk-improver").start()


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "run":
        print(json.dumps(run(sys.argv[2]), ensure_ascii=False))
