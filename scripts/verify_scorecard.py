#!/usr/bin/env python3
"""Check the public scorecard's ledgers yourself — Python 3 standard library only, no code of ours is imported.

  python3 scripts/verify_scorecard.py                     the two chains, the links between them and web/scorecard.json
  python3 scripts/verify_scorecard.py --against HEAD~50   + nothing that was in the ledger 50 commits ago was changed or removed

Files: each chain (ledger = predictions, outcomes = their results) is its closed segments data/scorecard/<chain>-<first line
number>.jsonl in name order, then the active file data/scorecard_<chain>.jsonl — read together as one text.
Format: one JSON object per line, keys sorted, no spaces, and the last key is "h". To check line k: take the line, cut off the last 72 characters (,"h":"<64 hex>"}) and put back "}" — that is the
exact text that was hashed. h_k = sha256(h_(k-1) + that text), with h_0 = 64 zeros. Each line's "n" is its line number. Every
outcome line names its prediction ("id") and that prediction's hash ("ph").

Prints OK or BROKEN (with the first bad line) and exits 0 / 1."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys

GENESIS = "0" * 64


def check(text: str) -> tuple[bool, list[dict], str, str]:
    """(ok, rows, head hash, message)"""
    prev, rows = GENESIS, []
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    for i, line in enumerate(lines, 1):
        if len(line) < 75 or not line.endswith('"}') or line[-72:-66] != ',"h":"':
            return False, rows, prev, f"line {i}: no hash at the end of the line"
        h = line[-66:-2]
        body = line[:-72] + "}"
        if hashlib.sha256((prev + body).encode("utf-8")).hexdigest() != h:
            return False, rows, prev, f"line {i}: hash does not match — this line or one before it was changed, removed or reordered"
        try:
            d = json.loads(line)
        except ValueError:
            return False, rows, prev, f"line {i}: not JSON"
        if not isinstance(d, dict) or d.get("h") != h or d.get("n") != i:
            return False, rows, prev, f"line {i}: line number or hash field wrong"
        rows.append(d)
        prev = h
    return True, rows, prev, f"{len(rows)} lines, head {prev}"


def read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def chain_text(data: str, name: str) -> tuple[str | None, str]:
    """(the whole chain as one text, description of the files)"""
    sdir = os.path.join(data, "scorecard")
    segs = sorted(f for f in os.listdir(sdir) if f.startswith(name + "-") and f.endswith(".jsonl")) if os.path.isdir(sdir) else []
    texts = [read(os.path.join(sdir, f)) or "" for f in segs]
    act = read(os.path.join(data, f"scorecard_{name}.jsonl"))
    if act is None and not texts:
        return None, ""
    act = act or ""
    if texts and act and act == texts[-1]:          # the engine stopped in the middle of closing a segment; it fixes this itself
        act = ""
    where = f"{os.path.join(data, f'scorecard_{name}.jsonl')}" + (f" + {len(segs)} segment(s) in {sdir}" if segs else "")
    return "".join(texts) + act, where


def git_chain_text(rev: str, data: str, name: str) -> str | None:
    """the same text as it was at a git revision"""
    d = data.replace(os.sep, "/").rstrip("/")
    try:
        ls = subprocess.run(["git", "ls-tree", "--name-only", f"{rev}:{d}/scorecard"], capture_output=True, text=True).stdout.split()
    except Exception:  # noqa: BLE001
        ls = []
    out = []
    for f in sorted(x for x in ls if x.startswith(name + "-") and x.endswith(".jsonl")):
        out.append(subprocess.run(["git", "show", f"{rev}:{d}/scorecard/{f}"], capture_output=True, text=True).stdout)
    act = subprocess.run(["git", "show", f"{rev}:{d}/scorecard_{name}.jsonl"], capture_output=True, text=True)
    if act.returncode != 0 and not out:
        return None
    a = act.stdout if act.returncode == 0 else ""
    if out and a and a == out[-1]:
        a = ""
    return "".join(out) + a


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="verify the CRYPTO NOVA scorecard hash chains")
    ap.add_argument("--data", default="data")
    ap.add_argument("--feed", default=os.path.join("web", "scorecard.json"))
    ap.add_argument("--against", help="a git revision: the ledger at that revision must be an unchanged beginning of today's")
    a = ap.parse_args(argv)
    bad = False
    led_txt, led_path = chain_text(a.data, "ledger")
    oc_txt, oc_path = chain_text(a.data, "outcomes")
    oc_path = oc_path or os.path.join(a.data, "scorecard_outcomes.jsonl")
    if led_txt is None:
        led_path = os.path.join(a.data, "scorecard_ledger.jsonl")
        print(f"BROKEN {led_path}: file not found")
        return 1
    ok, preds, head, msg = check(led_txt)
    print(("OK     " if ok else "BROKEN ") + f"{led_path}: {msg}")
    bad |= not ok
    by_id = {}
    for p in preds:
        if p.get("id") in by_id:
            print(f"BROKEN {led_path}: id {p.get('id')} recorded twice (lines {by_id[p['id']]['n']} and {p['n']})")
            bad = True
        by_id[p.get("id")] = p
    ok2, outs, head2, msg2 = check(oc_txt or "")
    print(("OK     " if ok2 else "BROKEN ") + f"{oc_path}: {msg2}")
    bad |= not ok2
    seen = set()
    for o in outs:
        p = by_id.get(o.get("id"))
        if p is None or p.get("h") != o.get("ph"):
            print(f"BROKEN {oc_path}: line {o.get('n')} checks {o.get('id')}, which is not that line of the ledger")
            bad = True
        if o.get("id") in seen:
            print(f"BROKEN {oc_path}: {o.get('id')} resolved twice")
            bad = True
        seen.add(o.get("id"))
    feed = read(a.feed)
    if feed:
        try:
            ch = json.loads(feed).get("chain") or {}
            for name, rows, h0 in (("ledger", preds, head), ("outcomes", outs, head2)):
                c = ch.get(name) or {}
                n, h = int(c.get("lines") or 0), c.get("head")
                if n == 0 and h == GENESIS:
                    continue
                if n > len(rows) or (rows[n - 1].get("h") if n else GENESIS) != h:
                    print(f"BROKEN {a.feed}: published {name} head {h} (line {n}) is not in the file")
                    bad = True
                else:
                    print(f"OK     {a.feed}: published {name} head = line {n} of the file" + ("" if n == len(rows) else f" (file has {len(rows)} now)"))
        except (ValueError, TypeError, AttributeError):
            print(f"note   {a.feed}: not readable, skipped")
    if a.against:
        old = git_chain_text(a.against, a.data, "ledger")
        if old is None:
            print(f"note   --against {a.against}: no ledger at that revision")
        else:
            if led_txt.startswith(old):
                print(f"OK     the ledger at {a.against} ({len(old.splitlines())} lines) is an unchanged beginning of today's")
            else:
                print(f"BROKEN the ledger at {a.against} is NOT a beginning of today's: history was rewritten")
                bad = True
    print("BROKEN" if bad else "OK")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
