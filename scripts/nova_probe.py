"""NOVA probe (20.10.2026): ask the real model a few app questions and print, for each, how long it took, why the model
stopped (finish_reason), the token usage and the start of the answer — as GitHub notices (.github/workflows/nova-probe.yml).

  python scripts/nova_probe.py old   — the prompt as it was before 20.10 (to see why «Сколько я заработал?» failed)
  python scripts/nova_probe.py new   — the live NOVA path: cnq.api.nova (prompt, facts, FAQ, ctx) through Api._agent

Needs CNQ_LLM_KEY (+ CNQ_LLM_PROVIDER / CNQ_LLM_MODEL); prints no keys. Reads web/app_home.json from the checkout."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cnq.agents.llm import LLM, PROVIDERS  # noqa: E402

QUESTIONS = ["Сколько я заработал?", "Объясни плечо", "Что такое копилка?"]
OLD_SYSTEM = (
    "Ты — NOVA, AI-помощник приложения CRYPTO NOVA. Отвечай по-русски, очень простыми словами, как для человека без опыта: "
    "2–5 коротких предложений, цифры цифрами, пример из жизни, если помогает. Используй только числа из КОНТЕКСТА, ничего не "
    "придумывай. Никогда не обещай доход и не советуй купить или продать конкретную монету — объясняй, как это работает и какие "
    "риски. Если вопрос не про крипту, деньги или приложение — вежливо верни разговор к теме. В конце длинного ответа о рисках "
    "можно напомнить: это не финансовый совет.")


def note(title: str, msg: str) -> None:
    msg = msg.replace("\r", " ").replace("\n", " ⏎ ").replace("%", "%25")
    print(f"::notice title={title}::{msg[:900]}", flush=True)


def raw(llm: LLM, system: str, user: str, temperature: float) -> dict:
    """one call exactly like LLM.complete (no retry), but keep finish_reason / usage / timing"""
    P = PROVIDERS[llm.provider]
    if P["style"] == "anthropic":
        body = {"model": llm.model, "max_tokens": llm.max_tokens, "temperature": temperature, "system": system,
                "messages": [{"role": "user", "content": user}]}
        headers = {"x-api-key": llm.key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    else:
        body = {"model": llm.model, "max_tokens": llm.max_tokens, "temperature": temperature,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        headers = {"authorization": "Bearer " + llm.key, "content-type": "application/json"}
    headers["user-agent"] = "Mozilla/5.0 (X11; Linux x86_64) cryptonova-probe/1.0"
    t0 = time.time()
    try:
        with urllib.request.urlopen(urllib.request.Request(P["url"], data=json.dumps(body).encode(), headers=headers), timeout=120) as r:
            msg = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"sec": round(time.time() - t0, 1), "http": e.code, "err": e.read().decode(errors="replace")[:240]}
    except Exception as e:  # noqa: BLE001
        return {"sec": round(time.time() - t0, 1), "err": repr(e)[:240]}
    sec = round(time.time() - t0, 1)
    if P["style"] == "anthropic":
        txt = "".join(b.get("text", "") for b in msg.get("content", []) if b.get("type") == "text")
        return {"sec": sec, "finish": msg.get("stop_reason"), "usage": msg.get("usage"), "content": txt}
    ch = (msg.get("choices") or [{}])[0]
    m = ch.get("message") or {}
    return {"sec": sec, "finish": ch.get("finish_reason"), "usage": msg.get("usage"), "content": m.get("content") or "",
            "reasoning_chars": len(m.get("reasoning") or "")}


def old() -> None:
    llm = LLM(max_tokens=600)
    home = json.load(open("web/app_home.json", encoding="utf-8"))
    ctx = {k: home.get(k) for k in ("total", "market")}
    fut = home.get("futures") or {}
    ctx["futures"] = {k: fut.get(k) for k in ("usd", "pct", "today_usd", "trades", "wins", "worst_dd_pct", "open")}
    ctx["spot"] = {k: (home.get("spot") or {}).get(k) for k in ("usd", "pct", "alloc", "last_action")}
    note("model", f"{llm.provider} · {llm.model} · max_tokens {llm.max_tokens} · key set: {bool(llm.key)}")
    for q in QUESTIONS:
        r = raw(llm, OLD_SYSTEM, "КОНТЕКСТ (демо-счета CRYPTO NOVA):\n" + json.dumps(ctx, ensure_ascii=False) + "\n\nВОПРОС:\n" + q, 0.3)
        c = r.pop("content", "")
        note(f"old · {q}", json.dumps({**r, "content_chars": len(c), "start": c[:160]}, ensure_ascii=False))
        time.sleep(3)


def new() -> None:
    import tempfile
    from cnq.api.server import Api
    from cnq.api.store import Store

    class V:
        def verify(self, t):
            return {"uid": t}
    api = Api(Store(os.path.join(tempfile.mkdtemp(), "api.sqlite")), V(), os.path.abspath("web"), LLM(max_tokens=600))
    qs = [(q, None) for q in QUESTIONS] + [
        ("Сколько я заработал?", "Мой демо-счёт: $1 000 с 20.10, сейчас $1 012 (+1.2%). Копилка: $10 в неделю, цель — учёба детей, 10 лет. Режим: DEMO."),
        ("Это не развод?", None), ("Почему биткоин вообще будет расти?", None)]
    for i, (q, ctx) in enumerate(qs):
        t0 = time.time()
        body = {"q": q, **({"ctx": ctx} if ctx else {})}
        code, d = api.handle("POST", "/v2/agent", {"authorization": f"Bearer probe{i}"}, json.dumps(body, ensure_ascii=False).encode())
        a = d.get("answer") or ""
        note(f"new · {q}{' + ctx' if ctx else ''}",
             json.dumps({"http": code, "sec": round(time.time() - t0, 1), "error": d.get("error"), "words": len(a.split()),
                         "answer": a}, ensure_ascii=False))
        time.sleep(3)


if __name__ == "__main__":
    (old if (sys.argv[1:] or ["new"])[0] == "old" else new)()
