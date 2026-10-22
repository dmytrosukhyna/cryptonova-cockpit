"""«Замок» автопилота OKX (22.10.2026) — two autopilots must never trade the same OKX account at once: the owner's Mac window and,
later, the server service (scripts/okx_autopilot_server.sh). Every order cycle (spot rebalance, futures pass, active-spot pass)
asks gate() first; no «да» → no orders in that cycle. Fail safe: no token, the lease unreadable (and our own lease not
provably valid), a write conflict, another live holder, the wrong machine or the global stop → no orders.

The lease is ONE small JSON file, okx_lease.json, on its own branch «okx-lease» of the repository (never main: no workflow, no
auto-deploy, no bot commit sees it), read and written through the GitHub contents API. A write names the version (sha) it
replaces, so GitHub accepts only one of two simultaneous writers — a compare-and-swap. Both machines already reach GitHub
(the autopilot reads the public portfolio and the desks there) and already hold a repository token (CNQ_GH_TOKEN).

  {"v": 1, "node": "mac" | "server",    where the autopilot is meant to run: the owner's choice, changed only by the server
                                         script's install (→ server) and uninstall (→ mac); a process on the other machine never
                                         trades, whatever else happens
   "holder": "mac-1a2b3c", "until": ms,  which process on that machine is trading now: a 15-minute lease, renewed every ~5 minutes
                                         by the cycles themselves, released on a clean exit (Ctrl+C, closed window, systemctl stop)
   "stop": false}                         the global kill switch: `stop` on any machine stops every autopilot, `resume` lifts it

Timing: a cycle starts only with ≥ 3 minutes of its lease left (a cycle takes seconds to a minute); another process may take a
lease only 1 minute after it expired (clock difference; OKX itself refuses clocks more than 30 s off). A process that froze
mid-cycle (a sleeping Mac) is caught too: the autopilot's client refuses to send an order once its lease ran out
(okx_autopilot.Okx.call). The window id (OKX_LEASE_ID) stays the same across restarts inside one window, so a restart resumes
at once; a second window gets another id and waits.

  python3 -m cnq.execution.okx_lease --status       one line: where the autopilot runs, who trades now, the stop
  python3 -m cnq.execution.okx_lease --node         mac | server | unknown (for scripts)
  python3 -m cnq.execution.okx_lease --switch server|mac   the server script's install / uninstall; refused while a live
                                                    process of the other machine holds the lease
Env: CNQ_GH_TOKEN, CNQ_GH_REPO, OKX_NODE (mac by default; the server unit sets server), OKX_LEASE_ID, OKX_LEASE_BRANCH.
OKX_LEASE_FILE=<path> keeps the lease in a local file instead (tests only — it can not see another machine).
No keys, balances or anything personal are written: only the machine name, a random window id and times. Stdlib only."""
from __future__ import annotations

import base64
import fcntl
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

TTL_MS = 15 * 60_000          # a lease lasts 15 minutes
RENEW_MS = 10 * 60_000        # renewed when less than 10 minutes are left (≈ every 5 minutes)
MARGIN_MS = 3 * 60_000        # a cycle starts only with at least 3 minutes of its lease left
SKEW_MS = 60_000              # clock difference allowed between the machines
FRESH_MS = 50_000             # a lease read this recently is trusted without asking GitHub again
NODES = ("mac", "server")
NODE_RU = {"mac": "Маке", "server": "сервере"}
BRANCH = "okx-lease"
PATH = "okx_lease.json"
DEFAULT_REPO = "dmytrosukhyna/cryptonova-cockpit"
_CACHE: dict = {}
_ME: dict = {}


class LeaseLost(RuntimeError):
    """Raised by the autopilot's client when an order would go out after this process's lease ran out."""


class LeaseError(RuntimeError):
    pass


def _env(env=None) -> dict:
    return os.environ if env is None else env


def now_ms() -> int:
    return int(time.time() * 1000)


def node_of(env=None) -> str:
    n = (_env(env).get("OKX_NODE") or "mac").strip().lower()
    return n if n in NODES else "mac"


def holder_id(env=None) -> str:
    """This window's id: OKX_LEASE_ID (the window scripts set it once per window), else one random id per process."""
    e = _env(env)
    x = (e.get("OKX_LEASE_ID") or "").strip()
    if x:
        return x[:40]
    n = node_of(env)
    if n not in _ME:
        _ME[n] = f"{n}-{secrets.token_hex(3)}"
    return _ME[n]


def hhmm(ms: int) -> str:
    return time.strftime("%H:%M", time.localtime(ms / 1000)) if ms else "—"


# ── stores ───────────────────────────────────────────────────────────────────────────────────────────────────────────
def _http(method: str, url: str, headers: dict, data: bytes | None = None, timeout: int = 15):
    """(status, json) for every answer, errors included."""
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode()
            return r.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode()
            return e.code, (json.loads(body) if body else None)
        except Exception:  # noqa: BLE001
            return e.code, None


class GitHubStore:
    """okx_lease.json on the branch okx-lease, through the contents API. read() → (doc | None, sha | None);
    write(doc, sha) → True, or False when another writer was first (the sha changed)."""

    def __init__(self, token: str, repo: str = DEFAULT_REPO, branch: str = BRANCH, http=None):
        self.repo, self.branch, self.http = repo, branch, http or _http
        self.base = f"https://api.github.com/repos/{repo}"
        self.hdr = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": "cnq-okx-lease",
                    "X-GitHub-Api-Version": "2022-11-28"}
        self.key = f"gh:{repo}:{branch}"

    def read(self):
        st, js = self.http("GET", f"{self.base}/contents/{PATH}?ref={urllib.parse.quote(self.branch)}", self.hdr, None)
        if st == 404:
            return None, None
        if st != 200 or not isinstance(js, dict):
            raise LeaseError(f"GitHub ответил {st}")
        try:
            doc = json.loads(base64.b64decode(js.get("content") or "").decode() or "{}")
        except Exception as e:  # noqa: BLE001
            raise LeaseError(f"файл замка не читается: {e}") from e
        return (doc if isinstance(doc, dict) else {}), js.get("sha")

    def _branch(self) -> None:
        """Create the branch okx-lease from main's head (once)."""
        st, js = self.http("GET", f"{self.base}/git/ref/heads/main", self.hdr, None)
        sha = ((js or {}).get("object") or {}).get("sha") if st == 200 else None
        if not sha:
            raise LeaseError(f"ветка main не прочиталась ({st})")
        st, _ = self.http("POST", f"{self.base}/git/refs", {**self.hdr, "Content-Type": "application/json"},
                          json.dumps({"ref": f"refs/heads/{self.branch}", "sha": sha}).encode())
        if st not in (201, 422):                       # 422: already there (another writer made it first)
            raise LeaseError(f"ветка {self.branch} не создалась ({st})")

    def write(self, doc: dict, sha: str | None, what: str = "update") -> bool:
        body = {"message": f"okx-lease: {what}", "branch": self.branch,
                "content": base64.b64encode(json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode()).decode()}
        if sha:
            body["sha"] = sha
        for attempt in range(2):
            st, js = self.http("PUT", f"{self.base}/contents/{PATH}", {**self.hdr, "Content-Type": "application/json"},
                               json.dumps(body).encode())
            if st in (200, 201):
                return True
            if st in (409, 422):                       # the version changed under us (or the file appeared): someone else wrote
                return False
            if st == 404 and not sha and attempt == 0:  # no branch yet
                self._branch()
                continue
            raise LeaseError(f"GitHub не принял замок ({st})")
        return False


class FileStore:
    """The same lease in a local file (tests). The «sha» is a counter; flock makes read-compare-write atomic."""

    def __init__(self, path: str):
        self.path, self.key = path, f"file:{path}"

    def _rw(self, fn):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path + ".lock", "a+") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            try:
                try:
                    with open(self.path, encoding="utf-8") as f:
                        cur = json.load(f)
                except FileNotFoundError:
                    cur = None
                return fn(cur)
            finally:
                fcntl.flock(lk, fcntl.LOCK_UN)

    def read(self):
        def f(cur):
            return (None, None) if cur is None else (cur.get("doc") or {}, str(cur.get("n")))
        return self._rw(f)

    def write(self, doc: dict, sha: str | None, what: str = "update") -> bool:
        def f(cur):
            if (str(cur.get("n")) if cur else None) != sha:
                return False
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"n": (int(cur["n"]) + 1) if cur else 1, "doc": doc}, fh)
            os.replace(tmp, self.path)
            return True
        return self._rw(f)


def store(env=None, http=None):
    e = _env(env)
    if (e.get("OKX_LEASE_FILE") or "").strip():
        return FileStore(os.path.expanduser(e["OKX_LEASE_FILE"].strip()))
    tok = (e.get("CNQ_GH_TOKEN") or "").strip()
    if not tok:
        return None
    return GitHubStore(tok, (e.get("CNQ_GH_REPO") or DEFAULT_REPO).strip(), (e.get("OKX_LEASE_BRANCH") or BRANCH).strip(), http)


# ── the gate ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def _ok(until: int) -> dict:
    return {"ok": True, "why": None, "until": until}


def _no(why: str, key=None) -> dict:
    if key is not None:
        _CACHE.pop(key, None)
    return {"ok": False, "why": why, "until": None}


def gate(env=None, now: int | None = None, st=None) -> dict:
    """{"ok", "why", "until"} — may THIS process place orders for the next cycle? Takes or renews the lease when it may.
    until = when the lease ends (the client refuses orders after until − 1 minute)."""
    now = int(now or now_ms())
    s = st if st is not None else store(env)
    if s is None:
        return _no("нет строки CNQ_GH_TOKEN — без «замка» (защиты от второго автопилота) ордера не ставлю")
    me, node = holder_id(env), node_of(env)
    key = (s.key, me)
    c = _CACHE.get(key)
    if c and c["until"] - now >= RENEW_MS and 0 <= now - c["read"] < FRESH_MS:
        return _ok(c["until"])
    try:
        doc, sha = s.read()
    except Exception as e:  # noqa: BLE001
        if c and c["until"] - SKEW_MS - now >= MARGIN_MS:        # nobody can take our lease before it ends
            return _ok(c["until"])
        return _no(f"«замок» не читается ({str(e)[:80]}) — ордера не ставлю, проверю через минуту", key)
    doc = dict(doc or {})
    if doc.get("stop"):
        return _no("включён общий стоп автопилота — ордера не ставлю. Снять: bash scripts/okx_autopilot_mac.sh resume", key)
    where = doc.get("node") or "mac"
    if where not in NODES:
        return _no(f"в «замке» непонятная машина «{where}» — ордера не ставлю", key)
    holder, until = doc.get("holder"), int(doc.get("until") or 0)
    if where != node:
        if holder == me:
            try:
                s.write({**doc, "holder": None, "until": 0, "t": now}, sha, "release (moved)")
            except Exception:  # noqa: BLE001
                pass
        return _no(f"автопилот переведён на {'сервер' if where == 'server' else 'Мак'} — здесь ({NODE_RU[node]}) ордера не ставлю", key)
    if holder and holder != me and until + SKEW_MS > now:
        return _no(f"работает другое окно автопилота ({holder}, его замок до {hhmm(until)}) — это окно ордера не ставит, ждёт", key)
    if holder == me and until - now >= RENEW_MS:
        _CACHE[key] = {"until": until, "read": now}
        return _ok(until)
    new = {**doc, "v": 1, "node": node, "holder": me, "until": now + TTL_MS, "t": now, "stop": False}
    try:
        won = s.write(new, sha, "renew" if holder == me else "take")
    except Exception:  # noqa: BLE001
        won = False
    if won:
        _CACHE[key] = {"until": new["until"], "read": now}
        return _ok(new["until"])
    if holder == me and until - SKEW_MS - now >= MARGIN_MS:      # the renewal did not land, but our lease still runs
        return _ok(until)
    return _no("«замок» занят или не записался — в этом цикле ордера не ставлю", key)


def deadline(g: dict, now: int | None = None) -> float | None:
    """Wall-clock ms after which the client must refuse to send orders (the lease end − 1 minute)."""
    if not g or not g.get("ok") or not g.get("until"):
        return None
    return time.time() * 1000 + (int(g["until"]) - int(now or now_ms())) - SKEW_MS


def _cas(env, fn, what: str, tries: int = 4, st=None) -> bool:
    """read → fn(doc) → write with the same version; a conflict is read again (a few times)."""
    s = st if st is not None else store(env)
    if s is None:
        return False
    for _ in range(tries):
        doc, sha = s.read()
        new = fn(dict(doc or {}))
        if new is None:
            return True
        if s.write(new, sha, what):
            return True
        time.sleep(0.3)
    return False


def release(env=None, st=None) -> bool:
    """A clean exit gives the lease back at once (only our own)."""
    me = holder_id(env)
    s = st if st is not None else store(env)
    if s is None:
        return False
    for k in [k for k in _CACHE if k[1] == me]:
        _CACHE.pop(k, None)
    try:
        return _cas(env, lambda d: {**d, "holder": None, "until": 0, "t": now_ms()} if d.get("holder") == me else None, "release", st=s)
    except Exception:  # noqa: BLE001
        return False


def set_stop(env=None, on: bool = True, st=None) -> bool:
    """The global kill switch (every machine stops placing orders) — set by `stop`, lifted by `resume`."""
    _CACHE.clear()
    try:
        return _cas(env, lambda d: {**d, "v": 1, "node": d.get("node") or "mac", "stop": bool(on), "stop_t": now_ms(),
                                    **({"holder": None, "until": 0} if on else {})}, "stop" if on else "resume", st=st)
    except Exception:  # noqa: BLE001
        return False


def switch(env=None, to: str = "server", now: int | None = None, st=None) -> tuple[bool, str]:
    """Move the autopilot to `to` (server install / uninstall). Refused while a live process of the other machine trades."""
    if to not in NODES:
        return False, f"нет такой машины: {to}"
    now = int(now or now_ms())
    s = st if st is not None else store(env)
    if s is None:
        return False, "нет строки CNQ_GH_TOKEN — «замок» недоступен"
    out = {"why": ""}

    def fn(d):
        holder, until = d.get("holder"), int(d.get("until") or 0)
        cur = d.get("node") or "mac"
        if holder and until + SKEW_MS > now and cur != to:
            out["why"] = (f"на {NODE_RU[cur]} ещё работает окно автопилота ({holder}, замок до {hhmm(until)}). Закройте его (Ctrl+C) "
                          "и повторите — или подождите, пока замок закончится.")
            return None
        if cur == to and d:
            out["why"] = f"автопилот уже на {NODE_RU[to]}"
            return None
        return {**d, "v": 1, "node": to, "holder": None if cur != to else holder, "until": 0 if cur != to else until, "t": now,
                "stop": bool(d.get("stop"))}
    try:
        done = _cas(env, fn, f"node {to}", st=s)
    except Exception as e:  # noqa: BLE001
        return False, f"«замок» недоступен: {str(e)[:100]}"
    if out["why"] and "уже на" not in out["why"]:
        return False, out["why"]
    return (done, out["why"] or (f"автопилот переведён на {'сервер' if to == 'server' else 'Мак'}" if done else "«замок» не записался — повторите"))


def status(env=None, now: int | None = None, st=None) -> dict | None:
    """For --check, the card and the scripts. None without a store; {"error"} when unreadable."""
    s = st if st is not None else store(env)
    if s is None:
        return None
    now = int(now or now_ms())
    try:
        doc, _ = s.read()
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:120]}
    doc = doc or {}
    until = int(doc.get("until") or 0)
    live = bool(doc.get("holder")) and until + SKEW_MS > now
    return {"node": doc.get("node") or "mac", "holder": doc.get("holder") if live else None, "until": until if live else None,
            "stop": bool(doc.get("stop")), "exists": bool(doc)}


def status_text(env=None) -> str:
    s = status(env)
    if s is None:
        return "Замок автопилота: нет строки CNQ_GH_TOKEN — без замка автопилот ордера не ставит"
    if s.get("error"):
        return f"Замок автопилота: не читается ({s['error']}) — ордера не пойдут, пока не прочитается"
    who = f"сейчас торгует {s['holder']} (замок до {hhmm(s['until'])})" if s.get("holder") else "сейчас никто не торгует"
    return (f"Замок автопилота: автопилот на {NODE_RU.get(s['node'], s['node'])} · {who}"
            + (" · ВКЛЮЧЁН ОБЩИЙ СТОП (resume — снять)" if s.get("stop") else ""))


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--node" in argv:
        s = status()
        print((s or {}).get("node") if s and not s.get("error") else "unknown")
        return 0
    if "--switch" in argv:
        i = argv.index("--switch")
        ok, msg = switch(to=argv[i + 1] if i + 1 < len(argv) else "")
        print(("Замок: " if ok else "Замок: НЕ переключён — ") + msg)
        return 0 if ok else 2
    if "--release" in argv:
        print("Замок отпущен." if release() else "Замок не отпущен (он не у этого окна или GitHub не ответил).")
        return 0
    print(status_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
