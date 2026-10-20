"""LLM client (stdlib only) + a deterministic mock for tests.

Providers (env CNQ_LLM_PROVIDER): groq (default, free tier), gemini (free Flash models), openrouter (free models),
anthropic (paid). Key: CNQ_LLM_KEY (ANTHROPIC_API_KEY also accepted for anthropic). Model: CNQ_LLM_MODEL overrides
the provider default. Every agent asks for JSON and validates it; a bad or missing answer degrades to the
rule-based fallback, never to invented numbers."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

PROVIDERS = {
    "groq": {"url": "https://api.groq.com/openai/v1/chat/completions", "model": "openai/gpt-oss-120b", "style": "openai"},
    "gemini": {"url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "model": "gemini-3.5-flash-lite", "style": "openai"},
    "openrouter": {"url": "https://openrouter.ai/api/v1/chat/completions", "model": "openai/gpt-oss-120b:free", "style": "openai"},
    "anthropic": {"url": "https://api.anthropic.com/v1/messages", "model": "claude-sonnet-5-5", "style": "anthropic"},
}
API = PROVIDERS["anthropic"]["url"]


class LLM:
    def __init__(self, model: str | None = None, api_key: str | None = None, max_tokens: int = 2000, provider: str | None = None,
                 timeout: float = 120.0, max_wait_429: float = 40.0, reasoning_effort: str | None = None):
        """timeout — seconds per HTTP call; max_wait_429 — the longest pause before the one retry after «429 rate limit»;
        reasoning_effort — low / medium / high for reasoning models that take it (gpt-oss on groq / openrouter): their hidden
        thinking counts against max_tokens, so a small budget with the default effort can end with an empty answer."""
        self.provider = (provider or os.environ.get("CNQ_LLM_PROVIDER") or "groq").lower()
        if self.provider not in PROVIDERS:
            self.provider = "groq"
        self.model = model or os.environ.get("CNQ_LLM_MODEL") or PROVIDERS[self.provider]["model"]
        self.key = api_key or os.environ.get("CNQ_LLM_KEY") or (os.environ.get("ANTHROPIC_API_KEY") if self.provider == "anthropic" else None)
        self.max_tokens = max_tokens
        self.timeout, self.max_wait_429, self.reasoning_effort = timeout, max_wait_429, reasoning_effort
        self.calls = 0
        self.last_error = None
        self.last_finish = None          # why the model stopped: "stop" / "length" (cut by max_tokens) / …
        self.last_usage = None

    @property
    def available(self) -> bool:
        return bool(self.key)

    def complete(self, system: str, user: str, temperature: float = 0.2) -> str:
        if not self.key:
            raise RuntimeError("LLM key not set (CNQ_LLM_KEY)")
        P = PROVIDERS[self.provider]
        if P["style"] == "anthropic":
            body = {"model": self.model, "max_tokens": self.max_tokens, "temperature": temperature,
                    "system": system, "messages": [{"role": "user", "content": user}]}
            headers = {"x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        else:
            body = {"model": self.model, "max_tokens": self.max_tokens, "temperature": temperature,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
            if self.reasoning_effort and "gpt-oss" in self.model:
                if self.provider == "groq":
                    body["reasoning_effort"] = self.reasoning_effort
                elif self.provider == "openrouter":
                    body["reasoning"] = {"effort": self.reasoning_effort}
            headers = {"authorization": "Bearer " + self.key, "content-type": "application/json"}
        # Groq sits behind Cloudflare, which rejects the default Python user agent (error 1010) — send a browser-like one
        headers["user-agent"] = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36 cryptonova/1.0"
        headers["accept"] = "application/json"
        req = urllib.request.Request(P["url"], data=json.dumps(body).encode(), headers=headers)
        msg = None
        self.last_finish = self.last_usage = None
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    msg = json.loads(r.read().decode())
                break
            except urllib.error.HTTPError as e:   # surface the provider's message (model name, quota, auth)
                body_txt = ""
                try:
                    body_txt = e.read().decode()[:300]
                except Exception:  # noqa: BLE001
                    pass
                self.last_error = f"HTTP {e.code}: {body_txt}"
                if e.code == 429 and attempt == 0 and self.max_wait_429 > 0:   # free tier: tokens-per-minute — wait what the provider asks (≤ max_wait_429) and retry once
                    m = re.search(r"try again in ([\d.]+)s", body_txt)
                    time.sleep(min(self.max_wait_429, float(m.group(1)) + 1 if m else 20.0))
                    req = urllib.request.Request(P["url"], data=json.dumps(body).encode(), headers=headers)
                    continue
                raise RuntimeError(self.last_error) from None
        self.last_error = None
        self.calls += 1
        self.last_usage = msg.get("usage")
        if P["style"] == "anthropic":
            self.last_finish = {"end_turn": "stop", "max_tokens": "length"}.get(msg.get("stop_reason"), msg.get("stop_reason"))
            return "".join(b.get("text", "") for b in msg.get("content", []) if b.get("type") == "text")
        ch = (msg.get("choices") or [{}])[0]
        self.last_finish = ch.get("finish_reason")
        return ((ch.get("message") or {}).get("content")) or ""

    def json(self, system: str, user: str) -> dict | list | None:
        """Ask for strict JSON; tolerate code fences; return None on failure (reason in last_error)."""
        keep = self.max_tokens
        self.max_tokens = max(keep, 6000)          # JSON lists are long; a truncated answer is a lost answer
        try:
            txt = self.complete(system + "\nОтвечай ТОЛЬКО валидным JSON без пояснений.", user)
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)[:300]
            return None
        finally:
            self.max_tokens = keep
        out = parse_json(txt)
        if out is None:
            self.last_error = "JSON parse failed: " + (txt or "")[:160]
        return out


def parse_json(txt: str):
    txt = txt.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", txt, re.S)
    if m:
        txt = m.group(1)
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        # last resort: first {...} or [...] block
        m = re.search(r"(\{.*\}|\[.*\])", txt, re.S)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                return None
    return None


class MockLLM(LLM):
    """Deterministic stand-in: keyword rules instead of a model. Used in tests and when
    no key is configured, so the pipeline always runs end to end."""

    def __init__(self):
        super().__init__(model="mock", api_key="mock")

    def complete(self, system: str, user: str, temperature: float = 0.2) -> str:
        self.calls += 1
        if "NEWS_ANALYST" in system:
            items = parse_json(user) or []
            out = []
            for it in items if isinstance(items, list) else []:
                h = (it.get("title", "") + " " + it.get("summary", "")).lower()
                cat, d, mag = "other", "neutral", 1
                for k, c, dd, mm in (("etf", "etf_flows", "bull", 3), ("sec", "regulation", "bear", 3), ("ban", "regulation", "bear", 4),
                                     ("hack", "hack", "bear", 4), ("exploit", "hack", "bear", 4), ("fomc", "macro", "neutral", 3),
                                     ("cpi", "macro", "neutral", 3), ("rate cut", "macro", "bull", 3), ("listing", "listing", "bull", 2),
                                     ("unlock", "unlock", "bear", 2), ("upgrade", "tech", "bull", 2), ("partnership", "adoption", "bull", 1),
                                     ("lawsuit", "regulation", "bear", 3), ("approval", "regulation", "bull", 3), ("outage", "tech", "bear", 2)):
                    if k in h:
                        cat, d, mag = c, dd, mm
                        break
                out.append({"id": it.get("id"), "assets": it.get("assets_hint", ["BTC"]), "category": cat, "direction": d,
                            "horizon": "days", "magnitude": mag, "confidence": 0.4 if cat != "other" else 0.2,
                            "reasoning": "mock: keyword rule", "confirm": "цена закрепится за ближайшим уровнем в сторону оценки"})
            return json.dumps(out, ensure_ascii=False)
        if "EDITOR" in system:
            return ""  # empty → orchestrator uses the rule-based brief
        return "{}"
