"""«Состояние системы» for the owner (09.11.2026, owner 21:40: «я сам ничего не понимаю, привести в порядок все сервисы»):
one row per service in plain Russian — what it does, when it last ran, 🟢 работает / 🟡 задержка / 🔴 сбой, and one line on what
to do when it is red. Built only from what the system already records: web/system_status.json (vip_feeds), web/engine.json,
web/gh_runs.json (scripts/ci_runs.py), file timestamps, the channel's sent posts (agents.sqlite channel_posts).
Writes web/owner_health.json — the owner-only card at the very top of cryptonova.vip."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import time
from zoneinfo import ZoneInfo

KYIV = ZoneInfo("Europe/Kyiv")
GREEN, YELLOW, RED = "ok", "delay", "fail"
ICON = {GREEN: "🟢", YELLOW: "🟡", RED: "🔴"}
WORD = {GREEN: "работает", YELLOW: "задержка", RED: "сбой"}
# a failed GitHub step that is GitHub's own trouble, not ours: it passes on the next run by itself
TRANSIENT = re.compile(r"set up job|checkout|setup-python|cache|restore|commit refreshed|push|upload|download|post |complete job", re.I)
ASK = "Напишите Claude: «проверь {x}» — он посмотрит журнал и починит."


def _load(out: str, name: str) -> dict:
    try:
        d = json.load(open(os.path.join(out, name), encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _ms(v) -> int | None:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return int(v if v > 1e12 else v * 1000) if v > 0 else None


def _age_min(t, now) -> float | None:
    t = _ms(t)
    return (now - t) / 60000 if t else None


def _by_age(t, now, ok_min, red_min) -> str:
    a = _age_min(t, now)
    return RED if a is None or a > red_min else YELLOW if a > ok_min else GREEN


def _comp(S: dict, key: str) -> dict:
    return next((c for c in S.get("components") or [] if isinstance(c, dict) and c.get("key") == key), {})


def _row(key, name, what, last, status, note, todo) -> dict:
    return {"key": key, "name": name, "what": what, "last": _ms(last), "status": status, "icon": ICON[status], "word": WORD[status],
            "note": note, "todo": todo if status == RED else None}


def _gh(R: dict, key: str, now: int, ok_min: int, red_min: int, last_t=None) -> tuple[str, str, int | None]:
    """a workflow's state from its last finished runs: success → by age; the last one failed in a GitHub step (checkout, cache,
    push …) or after a success → «временный сбой GitHub, повторится сам»; two failures in a row → сбой"""
    runs = [r for r in (R.get("workflows") or {}).get(key) or [] if r.get("status") == "completed" and r.get("conclusion") != "cancelled"]
    if not runs:
        if not _ms(last_t):
            return YELLOW, "нет данных о запусках GitHub в этом прогоне", None
        return (_by_age(last_t, now, ok_min, red_min), "", _ms(last_t))
    last = runs[0]
    t = max(_ms(last_t) or 0, _ms(last.get("t_end")) or 0) or None
    if last.get("conclusion") == "success":
        return _by_age(t, now, ok_min, red_min), "", t
    step = last.get("failed_step") or ""
    two = len(runs) > 1 and runs[1].get("conclusion") == "failure"
    if not two and (not step or TRANSIENT.search(step)):
        return YELLOW, "временный сбой GitHub, повторится сам" + (f" (шаг «{step}»)" if step else ""), t
    if not two:
        return YELLOW, f"один запуск упал на шаге «{step}» — следующий обычно проходит", t
    return RED, f"два запуска подряд упали{f' на шаге «{step}»' if step else ''}", t


def build(out: str = "web", conn=None, now: int | None = None) -> dict:
    now = int(now or time.time() * 1000)
    kyiv = dt.datetime.fromtimestamp(now / 1000, KYIV)
    today = kyiv.strftime("%Y-%m-%d")
    S, E, R = _load(out, "system_status.json"), _load(out, "engine.json"), _load(out, "gh_runs.json")
    rows = []

    # 1. the real account's autopilot
    ap, pr = _comp(S, "autopilot"), _comp(S, "public_real")
    t_pr = _ms(_load(out, "public_real.json").get("asof")) or pr.get("last_ok")
    st = _by_age(t_pr, now, 35, 75)
    note = ""
    if ap.get("status") == "stopped":
        st, note = YELLOW, "включён глобальный стоп — новых ордеров нет, позиции держат свои стопы"
    elif ap.get("status") in ("stale", "error"):
        st, note = RED, ap.get("note_ru") or "замок автопилота не продлевается"
    rows.append(_row("autopilot", "Автопилот реального счёта (фьючерсы + спот)",
                     "Сам ведёт реальный счёт на OKX: открывает и закрывает сделки по сигналам системы, держит спот-ядро.",
                     t_pr, st, note, "Позиции при этом защищены стопами на бирже. " + ASK.format(x="автопилот на сервере")))
    # 2. trader polling
    t_w = max(_ms(_load(out, "whales_live.json").get("asof")) or 0, _ms(_load(out, "whale_watch.json").get("asof")) or 0) or None
    rows.append(_row("traders", "Опрос трейдеров",
                     "Каждые несколько минут смотрит, что делают отобранные трейдеры, — отсюда сигналы для фьючерсов.",
                     t_w, _by_age(t_w, now, 45, 120), "", ASK.format(x="опрос трейдеров")))
    # 3. the GitHub data pipeline
    t_site = max(_ms(_load(out, "status.json").get("time")) or 0, _ms(_load(out, "version.json").get("time")) or 0) or None
    st, note, t = _gh(R, "site", now, 75, 180, t_site)
    rows.append(_row("pipeline", "Конвейер данных (GitHub, раз в 30 минут)",
                     "Собирает новости, рынок, спот-портфель, отчёты и посты; затем обновляет сайт.", t, st, note,
                     ASK.format(x="конвейер данных site.yml")))
    # 4. publishing the panel
    st, note, t = _gh(R, "publish", now, 60, 180, R.get("pages_t"))
    if R.get("pages_t") and _age_min(R["pages_t"], now) is not None:
        st2 = _by_age(R["pages_t"], now, 60, 180)
        st = st2 if st == GREEN else st
    rows.append(_row("publish", "Публикация панели (сайт cryptonova.vip)",
                     "Выкладывает свежие данные на сайт — то, что вы видите на этой панели.", R.get("pages_t") or t, st, note,
                     ASK.format(x="публикацию сайта")))
    # 5. TG channel posts
    last_post, slots = None, set()
    if conn is not None:
        try:
            r = conn.execute("SELECT MAX(sent_at) FROM channel_posts").fetchone()
            last_post = r[0] if r else None
            slots = {s for (s,) in conn.execute("SELECT slot FROM channel_posts WHERE day=?", (today,)).fetchall()}
        except Exception:  # noqa: BLE001
            pass
    miss = [w for w, h in (("утренний", 9.5), ("вечерний", 22.25)) if kyiv.hour + kyiv.minute / 60 >= h
            and (("morning" if w == "утренний" else "evening") not in slots)]
    if conn is None:
        st, note = YELLOW, "нет данных о постах в этом прогоне"
    elif miss:
        st, note = RED, f"сегодня не вышел {' и '.join(miss)} пост"
    else:
        st, note = (GREEN if last_post else YELLOW), ""
    rows.append(_row("channel", "Посты в Telegram-канал",
                     "Утренний обзор, главные новости и вечерние итоги в канале @crypto_nova_news (с 23:00 до 08:00 тишина).",
                     last_post, st, note, ASK.format(x="посты в канал")))
    # 6. the morning report
    M = _load(out, "morning.json")
    if M.get("day") == today:
        st, note = (GREEN if M.get("audio") else YELLOW), ("" if M.get("audio") else "текст есть, озвучка не получилась")
    elif kyiv.hour < 7 or (kyiv.hour == 7 and kyiv.minute < 30):
        st, note = GREEN, "сегодняшний будет в 07:00"
    else:
        st, note = RED, "сегодняшний доклад не собран"
    rows.append(_row("morning", "Утренний доклад (07:00)", "Короткий доклад о ночи и счёте: пуш в приложение и голос.",
                     M.get("asof"), st, note, ASK.format(x="утренний доклад")))
    # 7. the researcher
    rc = _comp(S, "researcher")
    st = {"ok": GREEN, "stale": YELLOW}.get(rc.get("status"), RED) if rc else RED
    rows.append(_row("researcher", "Дежурный исследователь (раз в час)", "Раз в час проверяет рынок и сделки и пишет короткую запись в журнал дежурства.",
                     rc.get("last_ok"), st, "", ASK.format(x="дежурного исследователя")))
    # 8. the trading journal
    J = _load(out, "journal.json")
    jd = ((J.get("entries") or [{}])[0] or {}).get("date")
    yday = (kyiv - dt.timedelta(days=1)).strftime("%Y-%m-%d")
    st = GREEN if jd == today or (jd == yday and kyiv.hour < 11) else YELLOW if jd == yday else RED
    jt = int(dt.datetime.strptime(jd, "%Y-%m-%d").replace(hour=9, tzinfo=KYIV).timestamp() * 1000) if jd else None
    rows.append(_row("journal", "Дневник сделок (раз в сутки, ~09:00)", "Каждое утро записывает итог суток: счёт, сделки, что было важно.",
                     jt, st, "" if st == GREEN else f"последняя запись — {jd or 'нет'}", ASK.format(x="дневник сделок")))
    # 9. the app's feeds (the server engine)
    t_a = _ms(_load(out, "app_home.json").get("asof"))
    st = _by_age(t_a, now, 10, 30)
    if E.get("errors"):
        st = RED if st == RED else YELLOW
    rows.append(_row("feeds", "Ленты приложения (сервер, каждую минуту)", "Главный экран, сигналы и пуши приложения — их пересчитывает сервер.",
                     t_a, st, ("ошибки шагов: " + ", ".join(map(str, E["errors"]))[:120]) if E.get("errors") else "", ASK.format(x="сервер и ленты приложения")))
    # 10. NOVA chat and voice
    api, vc = _comp(S, "api"), _comp(S, "voice")
    st = {"ok": GREEN, "stale": YELLOW}.get(api.get("status"), RED) if api else YELLOW
    B = _load(out, "brief.json")
    note = ""
    if st == GREEN and B.get("error"):
        st, note = YELLOW, "голос работает запасной"
    rows.append(_row("nova", "NOVA: чат и голос", "Отвечает на вопросы в приложении и озвучивает отчёты.",
                     api.get("last_ok") or vc.get("last_ok"), st, note, ASK.format(x="NOVA (API приложения /v2/health)")))
    worst = RED if any(r["status"] == RED for r in rows) else YELLOW if any(r["status"] == YELLOW for r in rows) else GREEN
    return {"asof": now, "overall": worst, "icon": ICON[worst], "rows": rows,
            "about_ru": "🟢 работает · 🟡 задержка (обычно проходит сама) · 🔴 сбой — нужна помощь. Время — последний успешный запуск."}


def run(out: str = "web", conn=None, now: int | None = None) -> dict:
    d = build(out, conn, now)
    tmp = os.path.join(out, "owner_health.json.tmp")
    json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(tmp, os.path.join(out, "owner_health.json"))
    return d


if __name__ == "__main__":
    import sqlite3
    c = None
    try:
        c = sqlite3.connect("data/agents.sqlite")
    except Exception:  # noqa: BLE001
        c = None
    for r in run("web", c)["rows"]:
        print(r["icon"], r["name"], "|", r["note"])
