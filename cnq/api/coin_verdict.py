"""NOVA's answer to a coin question (22.10.2026, owner: «что думаешь о монете Саи» got a vague, rambling answer). Stdlib only.

  answer(q, lang, data, home, listings, llm) → None (not a coin question: NOVA answers as before) or
      {"answer": text, "source": "coin" | "clarify", "llm": bool, "coins": [tickers]}

What NOVA says, from the system's own data only:
  • the status of web/ideas.json (можно брать / добрать / небольшой суммой / ждём / не брать) and its reason, the checks
    анкета · консультант · перегрев · разлоки · «от максимума» (passed / not passed);
  • whether «CRYPTO NOVA портфель» (web/portfolio.json → portfolios.ideas; else web/app_home.json spot) holds it: share, entry,
    stop and target (the point where it takes half);
  • «Поддержка проекта» (ideas.json support) when the table has it;
  • the futures desk's open trade on it (web/app_home.json futures.open);
  • a plain verdict line first, then 3–5 bullets, then «Это оценка по правилам CRYPTO NOVA, а не финансовый совет».
  Two coins NOVA can't tell apart → one short question («Вы про SUI (Sui) или про SEI (Sei)?»). A coin outside the system's
  universe → said honestly. A «dead» coin (−90% and more from its all-time high, «не брать») → said plainly, no promises.

The model only phrases it: it gets the data and a ready draft as JSON; every number of its reply must be in that JSON
(validate_reply), the first line must carry the status, 3–5 bullets, no «гарантированно». Anything else → the draft itself
(built from the data, so it can't invent a number). Without a model the draft is the answer."""
from __future__ import annotations

import json
import re

from cnq.agents.i18n import norm_lang

from . import coins as C
from . import pf_review
from .nova import LANG, _norm, tidy

CHECKS = ("rating", "consultant", "overheat", "dilution", "ath")
DEAD_ATH = -90.0
MAX_COINS = 2


def _L(ru, en, uk, el, es):
    return {"ru": ru, "en": en, "uk": uk, "el": el, "es": es}


T = {
    "verdict": _L("{coin}: по правилам CRYPTO NOVA сейчас — {label}.", "{coin}: by CRYPTO NOVA's rules, right now — {label}.",
                  "{coin}: за правилами CRYPTO NOVA зараз — {label}.", "{coin}: με τους κανόνες του CRYPTO NOVA, τώρα — {label}.",
                  "{coin}: según las reglas de CRYPTO NOVA, ahora — {label}."),
    "dead": _L("{coin}: {label}. Цена на {p} ниже исторического максимума — по правилам CRYPTO NOVA такие монеты система не "
               "покупает, и обещать возврат цены я не буду.",
               "{coin}: {label}. The price is {p} below its all-time high — by CRYPTO NOVA's rules that's a “dead” coin, and I "
               "won't promise it comes back.",
               "{coin}: {label}. Ціна на {p} нижча за історичний максимум — за правилами CRYPTO NOVA це «мертва» монета, і "
               "обіцяти її повернення я не буду.",
               "{coin}: {label}. Η τιμή είναι {p} κάτω από το ιστορικό υψηλό — με τους κανόνες του CRYPTO NOVA είναι «νεκρό» "
               "νόμισμα και δεν θα υποσχεθώ ότι θα επιστρέψει.",
               "{coin}: {label}. El precio está un {p} por debajo de su máximo histórico: según las reglas de CRYPTO NOVA es una "
               "moneda «muerta» y no prometo que se recupere."),
    "unrated": _L("{coin}: в таблице CRYPTO NOVA оценки для спота нет.", "{coin}: the CRYPTO NOVA table has no spot rating for it.",
                  "{coin}: у таблиці CRYPTO NOVA оцінки для спота немає.",
                  "{coin}: ο πίνακας του CRYPTO NOVA δεν έχει αξιολόγηση spot γι' αυτό.",
                  "{coin}: la tabla de CRYPTO NOVA no tiene valoración de spot para ella."),
    "why": _L("Почему: {r}", "Why: {r}", "Чому: {r}", "Γιατί: {r}", "Por qué: {r}"),
    "passed": _L("Пройдено: {x}.", "Passed: {x}.", "Пройдено: {x}.", "Πέρασε: {x}.", "Superado: {x}."),
    "failed": _L("Не пройдено: {x}.", "Not passed: {x}.", "Не пройдено: {x}.", "Δεν πέρασε: {x}.", "No superado: {x}."),
    "held": _L("В споте CRYPTO NOVA: доля {w}, вход {e}, {s}, {t}.", "In the CRYPTO NOVA portfolio: {w} share, entry {e}, {s}, {t}.",
               "У портфелі CRYPTO NOVA: частка {w}, вхід {e}, {s}, {t}.", "Στο χαρτοφυλάκιο CRYPTO NOVA: μερίδιο {w}, είσοδος {e}, {s}, {t}.",
               "En la cartera de CRYPTO NOVA: peso {w}, entrada {e}, {s}, {t}."),
    "stop": _L("стоп {v}", "stop {v}", "стоп {v}", "stop {v}", "stop {v}"),
    "no_stop": _L("стопа нет", "no stop", "стопу немає", "χωρίς stop", "sin stop"),
    "target": _L("цель {v} (там фиксируем половину)", "target {v} (half is taken there)", "ціль {v} (там фіксуємо половину)",
                 "στόχος {v} (εκεί κλειδώνουμε το μισό)", "objetivo {v} (ahí se toma la mitad)"),
    "no_target": _L("цели нет — держим надолго", "no target — a long hold", "цілі немає — тримаємо надовго",
                    "χωρίς στόχο — μακροπρόθεσμη διακράτηση", "sin objetivo: se mantiene a largo plazo"),
    # 28.10 (build 46 QA): a core coin (BTC, ETH, SOL) — the system's plan for it, no stop line (the core is held without one)
    "core_label": _L("в ядре портфеля", "in the portfolio's core", "у ядрі портфеля", "στον πυρήνα του χαρτοφυλακίου",
                     "en el núcleo de la cartera"),
    "core_hold": _L("{coin} — в ядре портфеля: система держит монету и сейчас не докупает.",
                    "{coin} is in the portfolio's core: the system holds it and isn't buying more right now.",
                    "{coin} — у ядрі портфеля: система тримає монету й зараз не докуповує.",
                    "Το {coin} είναι στον πυρήνα του χαρτοφυλακίου: το σύστημα το κρατά και αυτή τη στιγμή δεν αγοράζει άλλο.",
                    "{coin} está en el núcleo de la cartera: el sistema la mantiene y ahora no compra más."),
    "core_add": _L("{coin} — в ядре портфеля: доля ниже плана, система докупит монету по своим правилам.",
                   "{coin} is in the portfolio's core: its share is below the plan, so the system will top it up by its rules.",
                   "{coin} — у ядрі портфеля: частка нижча за план, система докупить монету за своїми правилами.",
                   "Το {coin} είναι στον πυρήνα του χαρτοφυλακίου: το μερίδιο είναι κάτω από το σχέδιο, το σύστημα θα το συμπληρώσει με τους κανόνες του.",
                   "{coin} está en el núcleo de la cartera: su peso está por debajo del plan y el sistema la completará según sus reglas."),
    "core_trim": _L("{coin} — в ядре портфеля: доля выше плана, лишнее система продаст на пересмотре.",
                    "{coin} is in the portfolio's core: its share is above the plan, so the system will sell the extra at the review.",
                    "{coin} — у ядрі портфеля: частка вища за план, зайве система продасть на перегляді.",
                    "Το {coin} είναι στον πυρήνα του χαρτοφυλακίου: το μερίδιο είναι πάνω από το σχέδιο, το σύστημα θα πουλήσει το επιπλέον στην αναθεώρηση.",
                    "{coin} está en el núcleo de la cartera: su peso supera el plan y el sistema venderá lo que sobra en la revisión."),
    "core_held": _L("Доля в споте CRYPTO NOVA: {w} (план {p}); ядро система держит без стопа и без цели.",
                    "Share of CRYPTO NOVA's spot: {w} (plan {p}); the system holds the core with no stop and no target.",
                    "Частка в споті CRYPTO NOVA: {w} (план {p}); ядро система тримає без стопа й без цілі.",
                    "Μερίδιο στο spot του CRYPTO NOVA: {w} (σχέδιο {p})· τον πυρήνα το σύστημα τον κρατά χωρίς stop και χωρίς στόχο.",
                    "Peso en el spot de CRYPTO NOVA: {w} (plan {p}); el sistema mantiene el núcleo sin stop y sin objetivo."),
    "not_held": _L("В портфеле CRYPTO NOVA её сейчас нет.", "The CRYPTO NOVA portfolio doesn't hold it now.",
                   "У портфелі CRYPTO NOVA її зараз немає.", "Το χαρτοφυλάκιο CRYPTO NOVA δεν το έχει τώρα.",
                   "La cartera de CRYPTO NOVA no la tiene ahora."),
    "support": _L("Поддержка проекта: {s} из 100.", "Project support: {s}/100.", "Підтримка проєкту: {s} зі 100.",
                  "Υποστήριξη έργου: {s}/100.", "Respaldo del proyecto: {s}/100."),
    "futures": _L("Фьючерсы: открыта сделка {side}, вход {e}, стоп {s}, цель {t}, сейчас {p}.",
                  "Futures: an open {side} trade, entry {e}, stop {s}, target {t}, now {p}.",
                  "Ф'ючерси: відкрита угода {side}, вхід {e}, стоп {s}, ціль {t}, зараз {p}.",
                  "Futures: ανοιχτή συναλλαγή {side}, είσοδος {e}, stop {s}, στόχος {t}, τώρα {p}.",
                  "Futuros: operación {side} abierta, entrada {e}, stop {s}, objetivo {t}, ahora {p}."),
    "long": _L("лонг (на рост)", "long (betting on a rise)", "лонг (на зростання)", "long (για άνοδο)", "long (al alza)"),
    "short": _L("шорт (на падение)", "short (betting on a fall)", "шорт (на падіння)", "short (για πτώση)", "short (a la baja)"),
    "price": _L("Цена сейчас: {p}.", "Price now: {p}.", "Ціна зараз: {p}.", "Τιμή τώρα: {p}.", "Precio ahora: {p}."),
    "nfa": _L("Это оценка по правилам CRYPTO NOVA, а не финансовый совет.",
              "This is an assessment by CRYPTO NOVA's rules, not financial advice.",
              "Це оцінка за правилами CRYPTO NOVA, а не фінансова порада.",
              "Αυτή είναι αξιολόγηση με τους κανόνες του CRYPTO NOVA, όχι επενδυτική συμβουλή.",
              "Es una valoración según las reglas de CRYPTO NOVA, no asesoramiento financiero."),
    "no_data": _L("По {coin} у CRYPTO NOVA данных нет: этой монеты нет в списке, который система проверяет, поэтому оценку по "
                  "нашим правилам я дать не могу. Гадать не буду.",
                  "CRYPTO NOVA has no data on {coin}: it isn't in the list of coins the system checks, so I can't give an "
                  "assessment by our rules. I won't guess.",
                  "Щодо {coin} у CRYPTO NOVA даних немає: цієї монети немає в списку, який перевіряє система, тому оцінку за "
                  "нашими правилами я дати не можу. Вгадувати не буду.",
                  "Το CRYPTO NOVA δεν έχει δεδομένα για το {coin}: δεν είναι στη λίστα που ελέγχει το σύστημα, οπότε δεν μπορώ "
                  "να δώσω αξιολόγηση με τους κανόνες μας. Δεν θα μαντέψω.",
                  "CRYPTO NOVA no tiene datos sobre {coin}: no está en la lista de monedas que revisa el sistema, así que no puedo "
                  "dar una valoración según nuestras reglas. No voy a adivinar."),
    "ask": _L("Вы про {a} или про {b}?", "Do you mean {a} or {b}?", "Ви про {a} чи про {b}?", "Εννοείτε το {a} ή το {b};",
              "¿Te refieres a {a} o a {b}?"),
    # the checks, in the app's language (the numbers come from the table's own Russian text)
    "rating": _L("оценка проекта {r} из 100 (минимум {m})", "quality rating {r}/100 (needs {m}+)", "оцінка проєкту {r} зі 100 (мінімум {m})",
                 "βαθμολογία {r}/100 (χρειάζεται {m}+)", "puntuación {r}/100 (mínimo {m})"),
    "rating_nom": _L("оценка проекта {r} из 100", "quality rating {r}/100", "оцінка проєкту {r} зі 100", "βαθμολογία {r}/100", "puntuación {r}/100"),
    "consultant": _L("консультант системы: тренд {a} из 100, фундамент {b} из 100", "advisor: trend {a}/100, fundamentals {b}/100",
                     "консультант системи: тренд {a} зі 100, фундамент {b} зі 100", "σύμβουλος: τάση {a}/100, θεμελιώδη {b}/100",
                     "asesor: tendencia {a}/100, fundamentos {b}/100"),
    "consultant_none": _L("консультант системы монету не выделил", "the advisor didn't pick it", "консультант монету не виділив",
                          "ο σύμβουλος δεν το επέλεξε", "el asesor no la eligió"),
    "overheat": _L("перегрев: за 7 дней {a}, за 30 дней {b}", "overheating: {a} in 7 days, {b} in 30 days",
                   "перегрів: за 7 днів {a}, за 30 днів {b}", "υπερθέρμανση: {a} σε 7 ημέρες, {b} σε 30 ημέρες",
                   "sobrecalentamiento: {a} en 7 días, {b} en 30 días"),
    "dilution": _L("разлоки: в обращении ~{p}% токенов", "unlocks: ~{p}% of tokens in circulation", "розлоки: в обігу ~{p}% токенів",
                   "ξεκλειδώματα: σε κυκλοφορία ~{p}% των tokens", "desbloqueos: ~{p}% de los tokens en circulación"),
    "ath": _L("от максимума {p} (предел {m})", "{p} from its all-time high (limit {m})", "від максимуму {p} (межа {m})",
              "{p} από το ιστορικό υψηλό (όριο {m})", "{p} desde su máximo histórico (límite {m})"),
}

SYSTEM = """Ты — NOVA, AI-помощник приложения CRYPTO NOVA. {voice}
Тебе дают JSON: оценку монеты по правилам CRYPTO NOVA (coins) и готовый черновик ответа (draft). Перескажи черновик живо и очень простыми словами — ответ читают вслух с экрана телефона.
ПРАВИЛА:
• Структура как в черновике: первая строка — короткий вердикт, в ней дословно status_label монеты; затем 3–5 строк, каждая начинается с «• »; последняя строка — ровно disclaimer из JSON. Монет две — так для каждой, disclaimer один в конце.
• Числа — только из JSON и только как они там записаны. Ничего не считай, не округляй по-своему, не добавляй своих чисел, дат и прогнозов цены.
• Статус не меняй и не смягчай: «не брать» — значит не брать; монету с dead: true называй прямо — система такие не покупает, без обещаний, что цена вернётся.
• Не говори «гарантированно», не обещай рост или доход, не называй сумму вложений.
• Без заголовков, без **жирного**, без эмодзи и ссылок.
{how}
{native}"""

BANNED = ("гарант", "guarante", "εγγυ", "garantiz", "to the moon")


# ------------------------------------------------------------------------------------------------ numbers
def _num(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def fmt_price(v) -> str:
    x = _num(v)
    if x is None:
        return "—"
    if abs(x) >= 1000:
        return "$" + f"{x:,.0f}".replace(",", " ")
    if abs(x) >= 1:
        return "$" + f"{x:.2f}".rstrip("0").rstrip(".")
    return "$" + f"{x:.4g}"


def fmt_pct(v, sign: bool = True) -> str:
    x = _num(v)
    if x is None:
        return "—"
    s = f"{abs(x):.1f}".rstrip("0").rstrip(".")
    return ("+" if sign and x > 0 else "−" if x < 0 else "") + s + "%"


_NUM = re.compile(r"\d+(?:[   ]\d{3})*(?:[.,]\d+)?")


def _values(token: str) -> set[float]:
    """«81 571» → {81571}; «0,79» → {0.79}; «1,234» → {1.234, 1234}"""
    t = re.sub(r"[   ]", "", token)
    out = set()
    if "," in t and "." not in t:
        if re.fullmatch(r"\d{1,3}(,\d{3})+", t):
            out.add(float(t.replace(",", "")))
        out.add(float(t.replace(",", ".", 1)) if t.count(",") == 1 else float(t.replace(",", "")))
    else:
        try:
            out.add(float(t.replace(",", "")))
        except ValueError:
            pass
    return out


def numbers(text: str) -> list[str]:
    return _NUM.findall(text or "")


def allowed_numbers(payload) -> set[float]:
    """every number written anywhere in the data given to the model (strings and numbers)"""
    out: set[float] = set()

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
        elif isinstance(v, bool) or v is None:
            return
        elif isinstance(v, (int, float)):
            out.add(abs(float(v)))
        else:
            for tok in numbers(str(v)):
                out.update(_values(tok))
    walk(payload)
    return out


def _ok_number(tok: str, allowed: set[float]) -> bool:
    dec = len(re.split(r"[.,]", re.sub(r"[   ]", "", tok))[-1]) if re.search(r"[.,]\d+$", tok) else 0
    for v in _values(tok):
        tol = 0.5 * 10 ** (-dec) + 1e-9
        if any(abs(v - a) <= tol for a in allowed):
            return True
    parts = re.split(r"[   ]", tok)       # «30 120» read as one number — each part on its own
    return len(parts) > 1 and all(any(abs(v - a) < 1e-9 for v in _values(p) for a in allowed) for p in parts)


def invented_numbers(reply: str, payload) -> list[str]:
    allowed = allowed_numbers(payload)
    return [t for t in numbers(reply) if not _ok_number(t, allowed)]


# ------------------------------------------------------------------------------------------------ the data of one coin
def _rx(pattern: str, s: str):
    m = re.search(pattern, s or "")
    return m.groups() if m else None


def _check_text(key: str, t: str, lang: str, row: dict) -> str | None:
    if key == "rating":
        g = _rx(r"анкета (\d+)/100 \(нужно от (\d+)\)", t)
        if g:
            return T["rating"][lang].format(r=g[0], m=g[1])
        g = _rx(r"анкета (\d+)/100", t)
        return T["rating_nom"][lang].format(r=g[0]) if g else None
    if key == "consultant":
        g = _rx(r"тренд (\d+)/100, фундамент (\d+)/100", t)
        if g:
            return T["consultant"][lang].format(a=g[0], b=g[1])
        return T["consultant_none"][lang] if "не выделил" in t else None
    if key == "overheat":
        g = _rx(r"за 7 дней ([−+-]?[\d.]+%), за 30 дней ([−+-]?[\d.]+%)", t)
        return T["overheat"][lang].format(a=g[0], b=g[1]) if g else None
    if key == "dilution":
        g = _rx(r"~(\d+)%", t)
        return T["dilution"][lang].format(p=g[0]) if g else None
    if key == "ath":
        g = _rx(r"максимума ([−-]?\d+%)(?: \(предел ([−-]?\d+%)\))?", t)
        return T["ath"][lang].format(p=g[0], m=g[1] or "−90%") if g else None
    return None


def _ath(row: dict | None, mk: dict | None) -> float | None:
    for c in (row or {}).get("checks") or []:
        if isinstance(c, dict) and c.get("key") == "ath":
            g = _rx(r"максимума ([−-]?\d+(?:\.\d+)?)%", str(c.get("t") or ""))
            if g:
                return float(g[0].replace("−", "-"))
    g = _rx(r"^([−-]\d+)% от исторического максимума", str((row or {}).get("why_ru") or ""))
    if g:
        return float(g[0].replace("−", "-"))
    v = _num((mk or {}).get("ath_chg"))
    return round(v) if v is not None else None


def _live_share(sym: str, home: dict) -> float | None:
    """28.10 (build 46 QA): the coin's share of the REAL account's spot now (app_home public_portfolio.spot.coins share_spot)"""
    for c in (((home or {}).get("public_portfolio") or {}).get("spot") or {}).get("coins") or []:
        if isinstance(c, dict) and str(c.get("sym") or "").upper() == sym:
            return _num(c.get("share_spot"))
    return None


def _held(sym: str, data, home: dict) -> dict | None:
    pf = ((data.get("portfolio").get("portfolios") or {}).get("ideas") or {}) if data is not None else {}
    live = _live_share(sym, home)
    for p in pf.get("positions") or []:
        if isinstance(p, dict) and str(p.get("symbol") or "").upper() == sym and str(p.get("status") or "OPEN").upper() == "OPEN":
            lv = p.get("levels") or {}
            core = sym in pf_review.CORE or p.get("bucket") == "core"
            return {"weight": round(live, 1) if live is not None else round((_num(p.get("weight")) or 0) * 100, 1), "entry": _num(p.get("entry")),
                    "stop": None if core else _num(lv.get("stop")), "target": None if core else _num(lv.get("take_half")),
                    "core": core, "plan": round((_num(p.get("target_weight")) or 0) * 100, 1) if p.get("target_weight") is not None else None,
                    "live": live is not None}
    if pf.get("positions"):
        return None
    for c in ((home or {}).get("spot") or {}).get("coins") or []:
        if isinstance(c, dict) and str(c.get("sym") or "").upper() == sym:
            return {"weight": _num(c.get("weight")), "entry": _num(c.get("entry")), "stop": _num(c.get("stop")),
                    "target": _num(c.get("target"))}
    return None


def _futures(sym: str, home: dict) -> dict | None:
    for o in ((home or {}).get("futures") or {}).get("open") or []:
        if isinstance(o, dict) and str(o.get("sym") or "").upper() == sym:
            return {"side": "short" if str(o.get("side")).upper() == "SHORT" else "long", "entry": _num(o.get("entry")),
                    "price": _num(o.get("price")), "stop": _num(o.get("stop")), "target": _num(o.get("target")),
                    "pct": _num(o.get("pct"))}
    return None


def _support(sym: str, row: dict | None, ideas: dict) -> dict | None:
    s = (row or {}).get("support")
    if not isinstance(s, dict):
        s = (ideas.get("support") or {}).get(sym)
    if isinstance(s, dict) and _num(s.get("score")) is not None:
        lines = [str(x.get("t")) for x in s.get("lines") or [] if isinstance(x, dict) and x.get("t") and x.get("k") != "nodata"]
        return {"score": int(round(_num(s["score"]))), "lines_ru": lines[:3]}
    if _num(s) is not None:
        return {"score": int(round(_num(s))), "lines_ru": []}
    return None


def card(sym: str, data, home: dict, lang: str, u: C.Universe) -> dict | None:
    """everything the system knows about one coin, in the app's language; None — nothing at all"""
    ideas = data.get("ideas") if data is not None else {}
    statuses = ideas.get("statuses") or {}
    rows = {str(r.get("symbol")).upper(): r for r in ideas.get("rows") or [] if isinstance(r, dict) and r.get("symbol")}
    for s in (sym,) + C.ALT_SYMS.get(sym, ()):
        if s in statuses or s in rows:
            sym = s
            break
    st, row = statuses.get(sym) or {}, rows.get(sym)
    src = {**st, **(row or {})}
    mk = next((c for c in (data.get("markets").get("top") or [] if data is not None else [])
               if isinstance(c, dict) and str(c.get("symbol") or "").upper() == sym), None)
    status = src.get("status") if src.get("status") in pf_review.STATUS_LABEL else None
    held, fut = _held(sym, data, home), _futures(sym, home)
    if not status and not held and not fut:
        return None
    status = status or "unknown"
    name = str(src.get("name") or u.name(sym))
    reason, key, flag = pf_review.reason(str(src.get("why_ru") or ""), status, lang) if status != "unknown" else ("", None, None)
    checks = []
    for k in CHECKS:
        c = next((c for c in (row or {}).get("checks") or [] if isinstance(c, dict) and c.get("key") == k), None)
        if c is None and k == "ath" and mk and _num(mk.get("ath_chg")) is not None:
            v = round(_num(mk["ath_chg"]))
            c = {"key": "ath", "t": f"от исторического максимума {fmt_pct(v)} (предел −90%)", "ok": v > DEAD_ATH}
        if c is None:
            continue
        txt = _check_text(k, str(c.get("t") or ""), lang, row or {})
        if txt:
            checks.append({"key": k, "ok": c.get("ok"), "text": txt})
    ath = _ath(row, mk)
    sup = _support(sym, row, ideas)
    price = _num(src.get("price")) or _num((mk or {}).get("price"))
    label = pf_review._t(pf_review.STATUS_LABEL[status], lang)
    if held and held.get("core"):
        label = T["core_label"][lang]                     # a core coin's verdict is the core rule, not the table's «можно брать»
    return {"sym": sym, "name": name, "status": status, "status_label": label,
            "reason": reason, "reason_key": key, "decided_by": flag, "checks": checks,
            "from_ath": fmt_pct(ath) if ath is not None else None,
            "dead": bool(status == "avoid" and ath is not None and ath <= DEAD_ATH),
            "price": fmt_price(price) if price else None,
            "held": None if not held else {"share": fmt_pct(held["weight"], sign=False), "entry": fmt_price(held["entry"]),
                                           "stop": fmt_price(held["stop"]) if held["stop"] else None,
                                           "target": fmt_price(held["target"]) if held["target"] else None,
                                           "core": bool(held.get("core")),
                                           "plan": fmt_pct(held["plan"], sign=False) if held.get("plan") else None,
                                           "band": _band(held)},
            "support": sup,
            "futures": None if not fut else {"side": fut["side"], "entry": fmt_price(fut["entry"]), "stop": fmt_price(fut["stop"]),
                                             "target": fmt_price(fut["target"]), "now": fmt_pct(fut["pct"])}}


def _band(h: dict) -> str | None:
    """a core coin against its plan (the portfolio's own rule: below ¾ of the plan → it tops up, above 1.5× → it sells the extra)"""
    if not h.get("core") or not h.get("plan") or h.get("weight") is None:
        return None
    w, p = float(h["weight"]), float(h["plan"])
    return "add" if w < 0.75 * p else "trim" if w > 1.5 * p else "hold"


def coin_label(sym: str, name: str | None) -> str:
    return f"{sym} ({name})" if name and _norm(name).replace(" ", "") != _norm(sym) else sym


# ------------------------------------------------------------------------------------------------ the draft (= the answer without a model)
def draft(c: dict, lang: str, bullets_max: int = 5) -> list[str]:
    tl = {k: v[lang] for k, v in T.items()}
    coin = coin_label(c["sym"], c["name"])
    h0 = c.get("held") or {}
    if h0.get("core") and h0.get("band"):
        head = tl["core_" + h0["band"]].format(coin=coin)
    elif c["dead"]:
        head = tl["dead"].format(coin=coin, label=c["status_label"], p=(c["from_ath"] or "").lstrip("−-"))
    elif c["status"] == "unknown":
        head = tl["unrated"].format(coin=coin)
    else:
        head = tl["verdict"].format(coin=coin, label=c["status_label"])
    b: dict[str, str] = {}
    if c["reason"] and not (c["dead"] and c["decided_by"] == "ath") and not h0.get("core"):   # a core coin: the core rule, not the table
        r = c["reason"].rstrip(".")
        b["why"] = tl["why"].format(r=r[:1].lower() + r[1:] if lang != "el" else r) + "."
    good = [x["text"] for x in c["checks"] if x["ok"] is True]
    bad = [x["text"] for x in c["checks"] if x["ok"] is False and not (c["dead"] and x["key"] == "ath")]   # said in the head
    if good:
        b["passed"] = tl["passed"].format(x="; ".join(good))
    if bad:
        b["failed"] = tl["failed"].format(x="; ".join(bad))
    h = c["held"]
    if h and h.get("core"):
        b["held"] = tl["core_held"].format(w=h["share"], p=h.get("plan") or "—")
    elif h:
        b["held"] = tl["held"].format(w=h["share"], e=h["entry"], s=tl["stop"].format(v=h["stop"]) if h["stop"] else tl["no_stop"],
                                      t=tl["target"].format(v=h["target"]) if h["target"] else tl["no_target"])
    elif c["status"] != "unknown":
        b["held"] = tl["not_held"]
    if c["support"]:
        b["support"] = tl["support"].format(s=c["support"]["score"])
    f = c["futures"]
    if f:
        b["futures"] = tl["futures"].format(side=tl[f["side"]], e=f["entry"], s=f["stop"], t=f["target"], p=f["now"])
    if c["price"]:
        b["price"] = tl["price"].format(p=c["price"])
    keep = [k for k in ("why", "failed", "passed", "held", "futures", "support", "price") if k in b]
    if len(keep) > 3 and "price" in keep:
        keep.remove("price")                         # the price only fills a short card
    keep = set(keep[:bullets_max])
    order = [k for k in ("why", "passed", "failed", "held", "support", "futures", "price") if k in keep]
    return [head] + ["• " + b[k] for k in order]


def template(cards: list[dict], lang: str) -> str:
    per = 5 if len(cards) == 1 else 4
    blocks = ["\n".join(draft(c, lang, per)) for c in cards]
    return "\n\n".join(blocks) + "\n\n" + T["nfa"][lang]


# ------------------------------------------------------------------------------------------------ the model's reply, checked
def _is_disclaimer(line: str) -> bool:
    n = _norm(line)
    return "crypto nova" in n and any(w in n for w in ("совет", "advice", "порад", "συμβουλ", "asesoramiento", "consejo"))


def validate_reply(reply: str, cards: list[dict], payload: dict, lang: str) -> str | None:
    """the model's text if it keeps to the data and the format (the exact disclaimer appended), else None"""
    t = tidy(reply or "")
    if not t:
        return None
    lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
    while lines and _is_disclaimer(lines[-1]):
        lines.pop()
    if not lines or lines[0].startswith("•"):
        return None
    body = "\n".join(lines)
    nb = _norm(body)
    if any(_norm(w) in nb for w in BANNED):
        return None
    if invented_numbers(body, payload):
        return None
    bullets = sum(1 for ln in lines if ln.startswith("•"))
    lo, hi = (3, 5) if len(cards) == 1 else (2 * len(cards), 5 * len(cards))
    if not lo <= bullets <= hi:
        return None
    for c in cards:
        if _norm(c["status_label"]) not in nb or (c["sym"].lower() not in nb and _norm(c["name"]) not in nb):
            return None
    out: list[str] = []
    for ln in lines:                                      # a blank line before the second coin's verdict
        if out and not ln.startswith("•") and out[-1].startswith("•"):
            out.append("")
        out.append(ln)
    return "\n".join(out) + "\n\n" + T["nfa"][lang]


def phrase(llm, cards: list[dict], lang: str) -> tuple[str, bool]:
    """(text, from the model?) — the model rephrases the draft; a reply that breaks a rule is replaced by the draft"""
    text = template(cards, lang)
    if llm is None:
        return text, False
    L = LANG[lang]
    payload = {"lang": lang, "coins": cards, "draft": text, "disclaimer": T["nfa"][lang]}
    system = SYSTEM.format(voice=L["voice"], how=L["how"], native=L["native"] or "")
    try:
        raw = llm.complete(system, "JSON:\n" + json.dumps(payload, ensure_ascii=False), temperature=0.3)
    except Exception:  # noqa: BLE001 — the draft is a full answer on its own
        return text, False
    if getattr(llm, "last_finish", None) == "length":
        return text, False
    ok = validate_reply(raw if isinstance(raw, str) else "", cards, payload, lang)
    return (ok, True) if ok else (text, False)


# ------------------------------------------------------------------------------------------------ the entry point
def answer(q: str, lang: str, data, home: dict | None = None, listings: dict | None = None, llm=None,
           universe: C.Universe | None = None) -> dict | None:
    lang = norm_lang(lang)
    held = set()
    if data is not None:
        held = {str(p.get("symbol") or "").upper() for p in
                ((data.get("portfolio").get("portfolios") or {}).get("ideas") or {}).get("positions") or [] if isinstance(p, dict)}
    held |= {str(o.get("sym") or "").upper() for o in ((home or {}).get("futures") or {}).get("open") or [] if isinstance(o, dict)}
    u = universe or C.Universe.build(data, listings, held)
    res = C.resolve(q, u)
    if not C.is_verdict_question(q, res):
        word = C.asks_unknown_coin(q, res)
        if word:
            return {"answer": T["no_data"][lang].format(coin=f"«{word}»"), "source": "coin", "llm": False, "coins": []}
        return None
    if res.ambiguous:
        a, b = res.ambiguous
        return {"answer": T["ask"][lang].format(a=coin_label(a, u.name(a)), b=coin_label(b, u.name(b))), "source": "clarify",
                "llm": False, "coins": [a, b]}
    syms = res.coins[:MAX_COINS]
    cards = [c for c in (card(s, data, home or {}, lang, u) for s in syms) if c]
    done = {c["sym"] for c in cards} | {a for c in cards for a in C.ALT_SYMS.get(c["sym"], ())}
    missing = [s for s in syms if s not in done]
    if not cards:
        return {"answer": T["no_data"][lang].format(coin=coin_label(syms[0], u.name(syms[0]))), "source": "coin", "llm": False,
                "coins": syms}
    text, used = phrase(llm, cards, lang)
    if missing:                                           # «биткоин и тон»: TON is not in the system — said before the disclaimer
        nfa = T["nfa"][lang]
        text = text[: -len(nfa)].rstrip() + "\n\n" + T["no_data"][lang].format(coin=coin_label(missing[0], u.name(missing[0]))) + "\n\n" + nfa
    return {"answer": text, "source": "coin", "llm": used, "coins": [c["sym"] for c in cards]}
