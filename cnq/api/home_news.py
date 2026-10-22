"""«Главные новости рынка» — the calm news block at the bottom of the app's Home (build 44; owner 22.10: «чтобы люди понимали,
что происходит на рынке»).

GET /v2/home?lang=xx gets "news": up to 5 stories from the closed system's own news desk — web/decision.json news.impacts, the same
data as the panel's «Новости» card (cnq/agents/orchestrator.py: the last 48 h of the desk's scored headlines, one row per asset).
  window   the last 48 h (by the desk's time of the story)
  merged   one item per story: the rows of one story for several assets become one (a coin wins over MARKET), and the same
           story from two sites (most words of the original headline shared) is shown once
  ranked   by strength and freshness: score = magnitude − hours / 24 (a strong story from yesterday ranks with a weaker fresh one)
  skipped  magnitude < 2, «other» below 3, personal feeds (Truth Social posts), retweets and all-caps posts, the mock analyser
Item: {"id", "time" (ms), "asset" (a coin; absent = the whole market), "direction" up / down / neutral, "magnitude" 1–5,
       "category", "title" (in the request's language), "meaning" (one plain line «что это значит»; "" when there is none yet),
       "link" (the source article, when the data has one), "source" (the site's name)}
Words: NOVA's model (the API's LLM: CNQ_NOVA_LLM_KEY, else the engine's key — see server.agent_llm) translates each story ONCE,
into all 5 languages in one call, with its one-line meaning. The result is kept on disk by id + language (data/news_i18n.json,
git-ignored, 7 days), so it costs a few small calls a day. The request never waits for the model: a story not in the cache yet
goes out as it is — Russian: the desk's Russian headline, other languages: the original headline — with an empty meaning, and one
background worker translates it for the next request. Without a model that fallback simply stays.
Never invented: the stories come only from the desk; the model only translates and explains the headline it is given, and an
answer that brings a number not in the input, or the wrong alphabet for its language, is not used."""
from __future__ import annotations

import copy
import json
import os
import re
import sys
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from cnq.agents.i18n import DEFAULT, LANGS
from cnq.agents.llm import parse_json

WINDOW_H = 48
MAX_ITEMS = 5
MIN_MAG = 2
KEEP_DAYS = 7                      # the translation cache forgets stories older than this
RETRY_S = 15 * 60                  # a story the model failed on is asked again after 15 min …
MAX_TRIES = 3                      # … at most 3 times per server run
DIRECTION = {"bull": "up", "bear": "down", "up": "up", "down": "down"}
PERSONAL = {"trump_truth"}         # cnq/agents/news.py PERSONAL: posts, not news
SOURCES = {"coindesk": "CoinDesk", "cointelegraph": "Cointelegraph", "theblock": "The Block", "decrypt": "Decrypt",
           "bitcoinmagazine": "Bitcoin Magazine"}
STOP = {"the", "and", "for", "with", "from", "into", "its", "his", "her", "their", "this", "that", "are", "was", "were", "has",
        "have", "had", "not", "but", "after", "over", "out", "new", "says", "say", "said", "will", "could", "may", "can", "first",
        "crypto", "market", "markets", "price", "amid", "more", "than", "what", "how", "why", "who", "now", "just", "here"}
TITLE_MAX, MEANING_MAX = 160, 220

CYR = re.compile(r"[А-Яа-яЁёЇїІіЄєҐґ]")
GREEK = re.compile(r"[Ͱ-Ͽ]")
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")
NUM = re.compile(r"\d+(?:[.,]\d+)*")

SYSTEM = """You translate and explain ONE crypto-market news headline for a calm investing app for beginners.
The input is JSON: the original headline ("original"), the news desk's Russian headline ("ru_title"), the desk's short Russian note
on why it matters ("note"), the coin it is about ("asset", MARKET = the whole market) and the desk's direction for it ("direction").
Answer with ONLY a JSON object, keys exactly ru, en, uk, el, es, each {"title": "...", "meaning": "..."}:
- title: the same news, faithfully, in that language, at most 12 words. Keep names, companies and tickers (BTC, ETF, SEC) as they
  are. Numbers in digits. No quotes around it, no clickbait, no exclamation marks.
- meaning: ONE short plain sentence, at most 16 words, «what this means» for a beginner — what the news can mean for the coin or the
  market. Calm. No advice to buy or sell, no promises, no price forecasts, no emoji.
- Use ONLY facts from the input. Never add numbers, dates, names or events that are not in it.
- ru = Russian, en = English, uk = Ukrainian (not Russian), el = Greek, es = Spanish."""


def _words(s: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z0-9]+", str(s or "").lower()):
        if len(w) < 3 or w in STOP:
            continue
        out.add(w[:-1] if len(w) > 3 and w.endswith("s") else w)
    return out


def _same_story(a: set[str], b: set[str]) -> bool:
    """two headlines of one story: at least 4 words in common and most of the shorter one"""
    common = len(a & b)
    return common >= 4 and common / max(1, min(len(a), len(b))) >= 0.5


def _junk(x: dict) -> bool:
    t = str(x.get("title_en") or x.get("title") or "").strip()
    if not t or t.startswith("RT @") or str(x.get("source") or "") in PERSONAL:
        return True
    letters = [c for c in t if c.isalpha()]
    if len(letters) >= 20 and sum(c.isupper() for c in letters) / len(letters) > 0.6:      # a shouting post, not a headline
        return True
    return str(x.get("reasoning") or "").startswith("mock")


def _link(v) -> str | None:
    """the article's address without the feed's utm_* tags; only http(s)"""
    s = str(v or "").strip()
    if not re.match(r"https?://[^\s/]+\.[^\s]+$", s):
        return None
    p = urlsplit(s)
    q = urlencode([(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not k.lower().startswith("utm_")])
    return urlunsplit((p.scheme, p.netloc, p.path, q, ""))


def pick(impacts: list, now_ms: int, n: int = MAX_ITEMS) -> list[dict]:
    """the top stories of the last 48 h, merged and ranked (see the module doc); language-free"""
    since = now_ms - WINDOW_H * 3_600_000
    stories: dict[str, dict] = {}
    for x in impacts if isinstance(impacts, list) else []:
        if not isinstance(x, dict):
            continue
        try:
            mag, t = int(x.get("magnitude") or 0), int(x.get("created") or 0)
        except (TypeError, ValueError):
            continue
        nid = str(x.get("news_id") or "").strip()
        if not nid or not since <= t <= now_ms + 600_000 or not 1 <= mag <= 5 or mag < MIN_MAG or _junk(x):
            continue
        if x.get("category") == "other" and mag < 3:
            continue
        asset = str(x.get("asset") or "").upper()
        coin = asset if re.fullmatch(r"[A-Z0-9]{2,10}", asset) and asset != "MARKET" else None
        key = (mag, 1 if coin else 0)                         # the row that speaks for the story: strongest, a coin over MARKET
        cur = stories.get(nid)
        if cur is None or key > cur["_key"]:
            stories[nid] = {"_key": key, "_t": t, "_row": x, "_coin": coin}
    ranked = sorted(stories.items(), key=lambda kv: (kv[1]["_key"][0] - (now_ms - kv[1]["_t"]) / 3_600_000 / 24, kv[1]["_t"]), reverse=True)
    out, seen = [], []
    for nid, s in ranked:
        x = s["_row"]
        w = _words(x.get("title_en") or x.get("title"))
        if any(_same_story(w, v) for v in seen):
            continue
        seen.append(w)
        item = {"id": nid, "time": s["_t"], "direction": DIRECTION.get(str(x.get("direction") or ""), "neutral"),
                "magnitude": s["_key"][0], "category": str(x.get("category") or "other"),
                "title_ru": str(x.get("title") or "").strip(), "title_orig": str(x.get("title_en") or x.get("title") or "").strip(),
                "note_ru": str(x.get("reasoning") or "").strip()}
        if s["_coin"]:
            item["asset"] = s["_coin"]
        link = _link(x.get("link"))
        if link:
            item["link"] = link
        src = str(x.get("source") or "")
        if src:
            item["source"] = SOURCES.get(src, src)
        out.append(item)
        if len(out) >= n:
            break
    return out


def _clean(v, cap: int) -> str:
    s = EMOJI.sub("", str(v or "")).replace("\n", " ").replace("\r", " ")
    s = re.sub(r"\s+", " ", s).strip().strip("\"'«»“”„").strip()
    return s if len(s) <= cap else ""


def _numbers_ok(text: str, source: str) -> bool:
    """every number in the model's text is in the input (1,08 ↔ 1.08; «1» of «1.08» is fine)"""
    whole = {re.sub(r"[.,]", "", m) for m in NUM.findall(source)}
    parts = set(re.findall(r"\d+", source))
    return all(re.sub(r"[.,]", "", m) in whole or m in parts for m in NUM.findall(text))


def _script_ok(lang: str, s: str) -> bool:
    if lang in ("ru", "uk"):
        return bool(CYR.search(s))
    if lang == "el":
        return bool(GREEK.search(s))
    return not CYR.search(s) and not GREEK.search(s)


def check_answer(ans, story: dict) -> dict[str, dict]:
    """the model's answer → {lang: {"title", "meaning"}} for the languages that pass (see the module doc)"""
    out: dict[str, dict] = {}
    if not isinstance(ans, dict):
        return out
    src = " ".join((story.get("title_orig") or "", story.get("title_ru") or "", story.get("note_ru") or ""))
    for lang in LANGS:
        v = ans.get(lang)
        if not isinstance(v, dict):
            continue
        title, meaning = _clean(v.get("title"), TITLE_MAX), _clean(v.get("meaning"), MEANING_MAX)
        if len(title) < 3 or not _script_ok(lang, title) or not _numbers_ok(title, src):
            continue
        if meaning and (not _script_ok(lang, meaning) or not _numbers_ok(meaning, src)):
            meaning = ""
        out[lang] = {"title": title, "meaning": meaning}
    return out


class HomeNews:
    """the news for /v2/home: decision.json (re-read when it changes) + the translation cache + one background translator"""

    def __init__(self, web_dir: str, cache_path: str, llm=None):
        self.web, self.path, self.llm = web_dir, cache_path, llm
        self._lock = threading.Lock()
        self._dec: tuple[float, list] | None = None           # (decision.json mtime, its impacts)
        self._cache: dict | None = None
        self._tries: dict[str, tuple[int, float]] = {}         # story id → (failed calls, time of the last one)
        self._busy = False
        self.last: dict | None = None                          # the last model call: result + time (for a look in tests / logs)

    # ------------------------------------------------------------ data
    def _impacts(self) -> list:
        p = os.path.join(self.web, "decision.json")
        try:
            mt = os.path.getmtime(p)
        except OSError:
            return []
        if self._dec and self._dec[0] == mt:
            return self._dec[1]
        try:
            with open(p, encoding="utf-8") as f:
                im = ((json.load(f) or {}).get("news") or {}).get("impacts") or []
        except Exception:  # noqa: BLE001 — a half-written or broken file: the last good one (or nothing)
            return self._dec[1] if self._dec else []
        self._dec = (mt, im if isinstance(im, list) else [])
        return self._dec[1]

    def _load_cache(self) -> dict:
        if self._cache is None:
            try:
                with open(self.path, encoding="utf-8") as f:
                    d = json.load(f)
                self._cache = d.get("items") if isinstance(d, dict) and isinstance(d.get("items"), dict) else {}
            except Exception:  # noqa: BLE001
                self._cache = {}
        return self._cache

    def _save_cache(self, now_ms: int) -> None:
        cache = self._load_cache()
        old = now_ms - KEEP_DAYS * 86_400_000
        for k in [k for k, v in cache.items() if not isinstance(v, dict) or int(v.get("t") or 0) < old]:
            cache.pop(k, None)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"v": 1, "items": cache}, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, self.path)

    def _ready(self) -> bool:
        a = getattr(self.llm, "available", False) if self.llm is not None else False
        try:
            return bool(a() if callable(a) else a)
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------ the feed
    def feed(self, lang: str = DEFAULT, now_ms: int | None = None, background: bool = True) -> list[dict]:
        now_ms = now_ms or int(time.time() * 1000)
        stories = pick(self._impacts(), now_ms)
        out, missing = [], []
        with self._lock:
            cache = self._load_cache()
            for s in stories:
                c = cache.get(s["id"]) or {}
                tr = c.get(lang) if isinstance(c.get(lang), dict) else None
                if not all(isinstance(c.get(x), dict) for x in LANGS):
                    missing.append(s)
                if tr:
                    title, meaning = tr.get("title") or "", tr.get("meaning") or ""
                else:                                   # untranslated: the desk's Russian headline / the original one
                    title = (s["title_ru"] if lang == DEFAULT else s["title_orig"]) or s["title_orig"] or s["title_ru"]
                    meaning = ""
                item = {k: s[k] for k in ("id", "time", "direction", "magnitude", "category")}
                if s.get("asset"):
                    item["asset"] = s["asset"]
                item["title"], item["meaning"] = title, meaning
                for k in ("link", "source"):
                    if s.get(k):
                        item[k] = s[k]
                out.append(item)
        if missing and background and self._ready():
            self.kick(missing, now_ms)
        return out

    # ------------------------------------------------------------ the translator
    def kick(self, stories: list[dict], now_ms: int) -> None:
        with self._lock:
            if self._busy:
                return
            self._busy = True
        threading.Thread(target=self._work, args=(stories, now_ms), daemon=True).start()

    def _work(self, stories: list[dict], now_ms: int) -> None:
        try:
            self.translate(stories, now_ms)
        except Exception as e:  # noqa: BLE001 — the home feed never depends on this
            print("home news: translate error:", repr(e)[:200], file=sys.stderr, flush=True)
        finally:
            with self._lock:
                self._busy = False

    def translate(self, stories: list[dict], now_ms: int | None = None) -> int:
        """one model call per story that is not in the cache in every language yet; returns the number of calls"""
        now_ms = now_ms or int(time.time() * 1000)
        if not self._ready():
            return 0
        calls = 0
        for s in stories:
            with self._lock:
                c = self._load_cache().get(s["id"]) or {}
                done = all(isinstance(c.get(x), dict) for x in LANGS)
            n, last = self._tries.get(s["id"], (0, 0.0))
            if done or n >= MAX_TRIES or (n and time.time() - last < RETRY_S):
                continue
            llm = copy.copy(self.llm)
            for k, v in (("max_tokens", 2500), ("timeout", 45.0), ("max_wait_429", 0.0)):
                if hasattr(llm, k):
                    setattr(llm, k, v)
            user = json.dumps({"original": s["title_orig"], "ru_title": s["title_ru"], "note": s["note_ru"][:400],
                               "asset": s.get("asset") or "MARKET", "direction": s["direction"]}, ensure_ascii=False)
            t0, got, result = time.time(), {}, "ok"
            try:
                calls += 1
                got = check_answer(parse_json(llm.complete(SYSTEM, user, temperature=0.2) or ""), s)
                result = "ok" if got else "rejected"
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                result = "timeout" if "timed out" in msg.lower() else "rate_limit" if "429" in msg else "llm_error"
                print(f"home news: {result}: {msg[:160]}", file=sys.stderr, flush=True)
            self.last = {"result": result, "id": s["id"], "langs": sorted(got), "ms": round((time.time() - t0) * 1000), "at": int(time.time())}
            with self._lock:
                if got:
                    cache = self._load_cache()
                    entry = cache.get(s["id"]) if isinstance(cache.get(s["id"]), dict) else {}
                    entry.update(got)
                    entry["t"] = s["time"]
                    cache[s["id"]] = entry
                    try:
                        self._save_cache(now_ms)
                    except OSError as e:
                        print("home news: cache not written:", repr(e)[:160], file=sys.stderr, flush=True)
            if len(got) < len(LANGS):
                self._tries[s["id"]] = (n + 1, time.time())
            if result == "rate_limit":
                break
        return calls
