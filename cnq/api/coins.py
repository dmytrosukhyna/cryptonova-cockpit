"""Coin resolver for NOVA (22.10.2026, owner: «что ты думаешь о монете Саи» — voice input arrives as Russian words, NOVA did not
recognise SUI). Maps free text in any of the five app languages (ru · uk · en · el · es), including speech-to-text spellings, to
tickers. Stdlib only.

  resolve(text, universe)  → Resolution(coins=[tickers in order], ambiguous=(A, B) | None, unknown=[words that look like a coin
                             name but match nothing])
  Universe.build(data, listings)  the system's coins: web/ideas.json (statuses + rows), web/markets.json top, web/coin_profiles.json,
                             the OKX / BingX spot lists the API already caches (tickers only)
  is_verdict_question(text, res)  «что думаешь о X», «стоит ли брать X», «X перспективная?», «should I buy X», a bare coin name …

How a word becomes a ticker (first hit wins, longest phrase first — «биткоин кэш» before «биткоин»):
  1. the alias dictionary (ALIASES, ~150 coins, every language + voice spellings), Russian / Ukrainian case endings allowed
     («биткоине», «солану», «эфирчиком»); an alias listed in AMBIGUOUS gives two coins → NOVA asks one short question
  2. a ticker of the universe written as a ticker (SUI, NEAR); a lower-case ticker only for the coins the table rates and never
     for a common word («near», «link», «one»)
  3. fuzzy: the word (and its Latin transliteration) against the aliases and the coin names, ratio ≥ 0.8; two different coins
     within 0.05 of each other → ambiguous
Words of the question itself (stop words, «думаешь», «монета», «buy» …) are never taken for a coin."""
from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field

from .nova import STOP_N, _norm

# ------------------------------------------------------------------------------------------------ alias dictionary
# ticker → "alias|alias|…" (written naturally; compared after nova._norm: lower case, no accents, й → и, ё → е, spaces removed)
ALIASES: dict[str, str] = {
    "BTC": "bitcoin|btc|bitcon|bitcoins|битиси|битисиз|биткоин|биткойн|биток|битком|биткоинт|бтц|бтк|битказ|биткоинчик|біткоїн|біткойн|біток|"
           "μπιτκοιν|μπιτκόιν|bitcóin",
    "ETH": "ethereum|eth|ether|etherium|eterium|ethereom|эфир|эфирчик|эфириум|эфириум|эфирум|этериум|эфереум|кефир|етх|итх|"
           "ефір|ефірчик|ефіріум|етеріум|εθεριουμ|αιθεριο|ιθεριουμ|éter",
    "SOL": "solana|sol|solano|солана|соланка|сол|сола|солу|соле|солом|солан|σολανα|σολάνα",
    "XRP": "xrp|ripple|рипл|рипол|риппл|рипле|иксарпи|иксэрпи|иксрп|хрп|ксрп|ріпл|ріппл|ρίπλ|ριπλ",
    "BNB": "bnb|binancecoin|бнб|бинанскоин|біенбі|бнбкоин",
    "DOGE": "doge|dogecoin|dogecoins|доги|догик|догикоин|догикойн|догекоин|догкоин|дож|доже|догі|ντοτζ|ντοτζκοιν",
    "TRX": "tron|trx|трон|трх|трикс|τρον",
    "ADA": "cardano|ada|кардано|кардана|ада|аду|адой|καρνταν|καρντανο|καρντάνο",
    "HYPE": "hyperliquid|hype|hyperliquide|хайп|хайпа|хайпер|гиперликвид|хайперликвид|хайпликвид|гіперліквід|хайперліквід",
    "LINK": "chainlink|link|линк|чейнлинк|ченлинк|чеинлинк|чейнлінк|лінк|τσεινλινκ",
    "SUI": "sui|suicoin|суи|сюи|сай|саи|суй|свей|суї|σουι|σούι",
    "XLM": "stellar|xlm|стеллар|стелар|стелларлюменс",
    "BCH": "bitcoincash|bch|биткоинкэш|биткоинкеш|биткойнкэш|биткоинкаш|біткоїнкеш",
    "AVAX": "avalanche|avax|avalanch|авакс|аваланч|аваланш|аваланче|аваланж|аваланчи|авалан|αβαλανς",
    "HBAR": "hedera|hbar|хедера|хбар|хедерахашграф",
    "LTC": "litecoin|ltc|лайткоин|лайткойн|лайт|лтц|лайткоін|λαιτκοιν",
    "TON": "toncoin|ton|тон|тона|тону|тоном|тонкоин|тонкойн|тонкоін|τονκοιν",
    "SHIB": "shiba|shibainu|shib|шиба|шибаину|шиб|шибу|шіба|σιμπα",
    "DOT": "polkadot|dot|полкадот|полькадот|полкадат|πολκαντοτ",
    "XMR": "monero|xmr|монеро|монэро|μονερο",
    "UNI": "uniswap|uni|юнисвоп|юнисвап|юнисвоп|юні|юнісвоп",
    "PEPE": "pepe|pepecoin|пепе|пепа|пепечка|πεπε",
    "AAVE": "aave|ааве|аав|авэ|эйв|ейв",
    "ENA": "ethena|ena|этена|етена|эфена",
    "NEAR": "near|nearprotocol|ниар|нир|неар|ніар|ниэр|ниарпротокол",
    "APT": "aptos|apt|апт|аптос|аптус|аптоса|απτος",
    "TAO": "bittensor|tao|тао|биттензор|битензор|бітензор",
    "ICP": "internetcomputer|icp|интернеткомпьютер|айсипи|ісп|ицп",
    "ETC": "ethereumclassic|эфирклассик|эфириумклассик|эфиркласик|ефірклассік",
    "ONDO": "ondo|ондо",
    "POL": "polygon|matic|pol|полигон|матик|полігон",
    "ARB": "arbitrum|arb|арбитрум|арб|арбітрум",
    "KAS": "kaspa|kas|каспа",
    "ALGO": "algorand|algo|алгоранд|алго",
    "VET": "vechain|vet|вечейн|вичейн|вечеин|вічейн",
    "ATOM": "cosmos|atom|космос|космосатом",
    "RENDER": "render|rndr|рендер",
    "FIL": "filecoin|fil|файлкоин|филкоин|файлкойн|файлкоін",
    "WLD": "worldcoin|wld|ворлдкоин|вордкоин|ворлдкойн",
    "WLFI": "worldliberty|worldlibertyfinancial|wlfi|ворлдлиберти|ворлдліберті",
    "SEI": "sei|сэй|сеи|сеинетворк",
    "OP": "optimism|op|оптимизм|оптимізм",
    "TIA": "celestia|tia|селестия|целестия|тиа|селестія",
    "INJ": "injective|inj|инджектив|инжектив|инж|інджектив",
    "BONK": "bonk|бонк",
    "FET": "fetch|fetchai|fet|фет|фетч|асі",
    "JUP": "jupiter|jup|юпитер|джупитер|джуп",
    "STX": "stacks|stx|стакс|стэкс",
    "IMX": "immutable|immutablex|imx|иммутабл",
    "GRT": "thegraph|grt|зеграф|графтокен",
    "WIF": "dogwifhat|wif|виф|догвифхэт|догвифхет",
    "FLOKI": "floki|флоки|флокі",
    "TRUMP": "trump|officialtrump|трамп|трампкоин|трампкойн",
    "PENGU": "pudgypenguins|pengu|пенгу|пингвин|пингвины|пінгвін",
    "PUMP": "pumpfun|пампфан|пампфан",
    "ENS": "ens|ethereumnameservice",
    "LDO": "lido|ldo|лидо|лідо",
    "CRV": "curve|crv|кёрв|керв|курвдао",
    "SKY": "sky|maker|mkr|мейкер|скай|makerdao",
    "QNT": "quant|qnt|квант",
    "SAND": "sandbox|thesandbox|sand|сендбокс|сэндбокс",
    "MANA": "decentraland|mana|десентраленд|децентраленд",
    "AXS": "axie|axieinfinity|axs|акси|аксиинфинити",
    "GALA": "gala|гала",
    "APE": "apecoin|ape|эйпкоин|эйп|эипкоин",
    "XTZ": "tezos|xtz|тезос",
    "EOS": "eos|эос",
    "FLOW": "flow|флоу",
    "THETA": "theta|тета|тэта",
    "EGLD": "multiversx|elrond|egld|элронд|мультиверс",
    "IOTA": "iota|йота|иота",
    "NEO": "neo|нео",
    "ZEC": "zcash|zec|зикэш|зикеш|зкэш|зеккеш|зекеш|зек|зікеш",
    "DASH": "dash|даш|дэш",
    "CAKE": "pancakeswap|cake|кейк|панкейк|панкейксвоп",
    "RUNE": "thorchain|rune|торчейн|торчеин",
    "JASMY": "jasmy|jasmycoin|джасми|жасми",
    "CHZ": "chiliz|chz|чилиз",
    "PYTH": "pyth|pythnetwork|пит|пайт",
    "JTO": "jito|jto|джито",
    "STRK": "starknet|strk|старкнет",
    "ZK": "zksync|zk|зксинк",
    "BERA": "berachain|bera|бера|берачейн",
    "VIRTUAL": "virtuals|virtualsprotocol|виртуалс|виртуал",
    "FARTCOIN": "fartcoin|фарткоин|фарткойн",
    "POPCAT": "popcat|попкэт|попкат",
    "BRETT": "brett|бретт",
    "NOT": "notcoin|ноткоин|ноткойн",
    "DOGS": "dogs|догс",
    "HMSTR": "hamster|hamsterkombat|hmstr|хамстер|хомяк|хамстеркомбат",
    "CATI": "catizen|кэтизен|катизен",
    "ORDI": "ordi|орди",
    "ZRO": "layerzero|zro|лейерзеро|лэйерзиро|леерзеро",
    "W": "wormhole|вормхол",
    "ETHFI": "etherfi|ethfi",
    "PENDLE": "pendle|пендл|пендле",
    "AERO": "aerodrome|aero|аэродром|аэро",
    "MORPHO": "morpho|морфо",
    "GMX": "gmx",
    "DYDX": "dydx",
    "SNX": "synthetix|snx|синтетикс",
    "COMP": "compound|компаунд",
    "1INCH": "1inch|ванинч|уанинч",
    "RAY": "raydium|рейдиум|раидиум",
    "OKB": "okb|окб",
    "CRO": "cronos|cro|кронос|кро",
    "LEO": "unussedleo|leo|лео",
    "MNT": "mantle|mnt|мантл",
    "BGB": "bitgettoken|bgb",
    "XDC": "xdc|xdcnetwork",
    "CFX": "conflux|cfx|конфлюкс",
    "AR": "arweave|арвив",
    "AKT": "akash|akt|акаш",
    "MINA": "minaprotocol",
    "ROSE": "oasis|oasisnetwork",
    "KSM": "kusama|ksm|кусама",
    "ZIL": "zilliqa|zil|зилика",
    "ONE": "harmony|harmonyone",
    "HNT": "helium|hnt|гелиум",
    "GMT": "stepn|gmt|степн",
    "BLUR": "blur|блюр|блур",
    "ARKM": "arkham|arkm|аркхэм|аркхем",
    "OM": "mantra|om|мантракоин",
    "PI": "pinetwork|picoin|пинетворк|паинетворк|пикоин",
    "S": "sonic|fantom|ftm|соник|фантом",
    "USDT": "tether|usdt|тезер|тетер|юсдт|юэсдэт|тезерюсдт",
    "USDC": "usdc|usdcoin|юсдс|юсдиси|юсдц",
    "USDE": "usde",
    "PAXG": "paxg|paxgold",
    "XAUT": "xaut|tethergold",
    "KAIA": "kaia|кайа",
    "GNO": "gnosis|гносис",
    "BAT": "basicattentiontoken|bat",
    "ENJ": "enjin|enj|энджин",
    "IOTX": "iotex|iotx",
    "ANKR": "ankr|анкр",
    "BOME": "bookofmeme|bome|боми",
    "AI16Z": "ai16z",
    "MOVE": "movement|movementlabs",
    "IP": "storyprotocol",
    "SPX": "spx6900",
    "MEW": "catinadogsworld|mew",
    "MOODENG": "moodeng|мудэнг|муденг",
    "GIGA": "gigachad|гигачад",
    "ZEN": "horizen|zen",
    "QTUM": "qtum|кутум",
    "RSR": "reserverights|rsr",
    "SUSHI": "sushiswap|sushi",
    "LUNC": "terraclassic|lunc|луна классик|лунаклассик",
    "LUNA": "terra|terraluna",
    "GT": "gatetoken",
    "KCS": "kucointoken|kcs",
    "TWT": "trustwallettoken|twt",
    "SAFE": "safetoken",
    "DAI": "dai|makerdai",
    "FDUSD": "fdusd|firstdigitalusd",
}

# an alias that names two coins → NOVA asks which one
AMBIGUOUS: dict[str, tuple[str, str]] = {
    "ворлд": ("WLD", "WLFI"), "world": ("WLD", "WLFI"), "ворд": ("WLD", "WLFI"), "ворлдс": ("WLD", "WLFI"),
    "дог": ("DOGE", "DOGS"), "dog": ("DOGE", "DOGS"),
    "юсд": ("USDT", "USDC"), "юэсди": ("USDT", "USDC"),
    "луна": ("LUNA", "LUNC"), "luna": ("LUNA", "LUNC"),
}

# the names the app shows, when the data has none (the table's own name wins)
NAMES = {"BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana", "XRP": "XRP", "BNB": "BNB", "DOGE": "Dogecoin", "TRX": "TRON",
         "ADA": "Cardano", "HYPE": "Hyperliquid", "LINK": "Chainlink", "SUI": "Sui", "AVAX": "Avalanche", "TON": "Toncoin",
         "SHIB": "Shiba Inu", "DOT": "Polkadot", "LTC": "Litecoin", "PEPE": "Pepe", "NEAR": "NEAR Protocol", "APT": "Aptos",
         "WLD": "Worldcoin", "WLFI": "World Liberty Financial", "SEI": "Sei", "DOGS": "DOGS", "USDT": "Tether", "USDC": "USD Coin",
         "LUNA": "Terra", "LUNC": "Terra Classic", "ETC": "Ethereum Classic", "BCH": "Bitcoin Cash", "XMR": "Monero"}

# tickers that renamed: the data may use either
ALT_SYMS = {"POL": ("MATIC",), "MATIC": ("POL",), "RENDER": ("RNDR",), "RNDR": ("RENDER",), "S": ("FTM",), "FTM": ("S",),
            "SKY": ("MKR",), "MKR": ("SKY",), "FET": ("ASI",)}

# Latin words that are also tickers: taken for a coin only when written in capitals (NEAR, LINK, ONE) or right after a coin word
COMMON_LATIN = {"near", "link", "ton", "one", "dot", "op", "uni", "hype", "gas", "ray", "sun", "act", "ai", "pump", "move", "core",
                "beam", "flow", "rose", "mask", "high", "just", "sand", "ape", "bonk", "sky", "comp", "safe", "eos", "neo", "zen",
                "w", "s", "ip", "om", "pi", "ar", "gt", "bat", "leo", "mina", "dash", "atom", "cake", "fil", "trump", "quant",
                "blur", "rune", "gala", "mana", "theta", "curve", "maker", "fetch", "render", "cosmos", "sonic", "stellar",
                "spx", "mew", "giga", "dai", "dog", "dogs", "world", "luna", "not", "sei", "sui", "sol", "tao", "ena", "ondo",
                "aero", "virtual", "pengu", "kas", "algo", "vet", "glm", "lido", "jito", "terra", "oasis", "helium", "mantra",
                "sushi", "ordi", "apt", "arb", "zk", "inj", "tia", "jup", "imx", "grt", "wif", "ens", "crv", "axs", "xtz",
                "rsr", "zec", "ada", "bnb", "bch", "ltc", "icp", "etc", "kaia", "bera", "stx", "pol", "eth", "btc",
                "compound", "sandbox", "harmony", "story", "movement", "immutable", "optimism", "tron", "graph",
                "ether", "cardano", "solana", "avalanche", "polygon", "ripple", "monero", "chainlink", "uniswap", "pepe", "doge"}
# … of which these are safe in lower case too (no common word in the five languages)
SAFE_LOWER = {"btc", "eth", "bnb", "xrp", "ltc", "bch", "doge", "trx", "xmr", "zec", "avax", "shib", "pepe", "aave",
              "hbar", "xlm", "arb", "tia", "inj", "apt", "icp", "fil", "wld", "wlfi", "jup", "imx", "grt", "wif", "ldo", "crv",
              "axs", "xtz", "egld", "chz", "strk", "dydx", "snx", "okb", "cro", "mnt", "bgb", "ksm", "hnt", "arkm", "usdt",
              "usdc", "floki", "brett", "popcat", "fartcoin", "ether", "cardano", "solana", "avalanche", "polygon", "ripple",
              "monero", "chainlink", "uniswap", "tron", "optimism", "immutable", "stellar"}

# words of the question itself (any language) — never a coin, not even by fuzzy match
QUESTION_WORDS = """
думаешь думаете думает думаю думать мнение мнения оцени оцените оценка оценку оценишь стоит стоить брать покупать купить
взять вложиться вкладывать инвестировать держать продавать продать докупить добрать перспективная перспективный перспективно
перспективы перспектива монета монету монеты монете монетой монетка монетку токен токена токены токене крипта крипту крипте
криптовалюта криптовалюту криптовалюты биржа бирже рынок рынке рынка портфель портфеле сделка сделку сигнал сейчас вообще
скажешь скажете нормальная нормальный хорошая хороший плохая плохой надежная надежный прогноз вырастет расти будет смысл
короче слушай смотри смотрите понимаешь привет здравствуй спасибо нова nova novа думка думаєш думаєте варто брати купувати
купити взяти тримати продавати перспективна перспективний скажеш токени крипти ринок біржа зараз взагалі привіт дякую
think thoughts opinion worth buying buy hold sell invest investing investment should would good bad promising prospects
future coin coins token tokens crypto cryptocurrency market price today about what whats hello thanks rate verdict potential
forecast prediction bullish bearish moon long term project
проект проекта проекте проекты проектов альт альты альтов альткоин альткоины мем мемы мемкоин мемкоины стейбл стейблкоин
стейблкоины altcoin altcoins meme memes memecoin stablecoin
πιστευεις πιστευετε γνωμη αξιζει αγορασω αγοραζω αγορα πουλησω κρατησω νομισμα νομισματα κρυπτο κρυπτονομισμα προοπτικες
προοπτικη λες λετε καλη επενδυση τωρα
opinas opina piensas opinion vale pena merece deberia comprar comprarla comprarlo invertir vender mantener moneda monedas
cripto criptomoneda prometedor prometedora futuro tal buena bueno mala malo inversion pronostico ahora hola gracias
"""
COIN_WORDS = ("монет", "токен", "крипт", "coin", "token", "crypto", "νομισμ", "κρυπτ", "moned", "cript")

ENDINGS = {"а", "я", "у", "ю", "е", "и", "ы", "і", "ом", "ем", "ой", "ей", "ою", "ею", "ам", "ям", "ами", "ями", "ах", "ях",
           "ов", "ев", "ым", "ую", "ая", "ое", "ые", "ого", "ому", "ові", "еві", "ику", "ика", "ике", "иком"}
VOWELS = set("аяоеиыуюьэєії")

_TR = {"а": "a", "б": "b", "в": "v", "г": "g", "ґ": "g", "д": "d", "е": "e", "є": "ye", "ж": "zh", "з": "z", "и": "i", "і": "i",
       "ї": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
       "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
       "α": "a", "β": "b", "γ": "g", "δ": "d", "ε": "e", "ζ": "z", "η": "i", "θ": "th", "ι": "i", "κ": "k", "λ": "l", "μ": "m",
       "ν": "n", "ξ": "x", "ο": "o", "π": "p", "ρ": "r", "σ": "s", "τ": "t", "υ": "i", "φ": "f", "χ": "ch", "ψ": "ps", "ω": "o"}
_GREEK_PAIRS = (("μπ", "b"), ("ντ", "d"), ("ου", "u"), ("αι", "e"), ("ει", "i"), ("οι", "i"), ("τζ", "dz"), ("γκ", "g"))

FUZZY_MIN, FUZZY_TIE = 0.8, 0.05


def translit(w: str) -> str:
    """a Cyrillic / Greek word in Latin letters, as a speech-to-text would hear it («аптос» → aptos, «σολανα» → solana)"""
    for a, b in _GREEK_PAIRS:
        w = w.replace(a, b)
    return "".join(_TR.get(ch, ch) for ch in w)


def _key(s: str) -> str:
    return _norm(s).replace(" ", "")


def _latin(w: str) -> bool:
    return bool(w) and all(ch.isascii() for ch in w)


QWORDS = {_key(w) for w in QUESTION_WORDS.split()} | STOP_N
QSTEMS = {w[:5] for w in QWORDS if len(w) >= 5}

ALIAS_IDX: dict[str, str] = {}
for _sym, _al in ALIASES.items():
    for _a in _al.split("|"):
        k = _key(_a)
        if k and k not in ALIAS_IDX:
            ALIAS_IDX[k] = _sym
_FUZZ_ALIASES = [(k, s) for k, s in ALIAS_IDX.items() if len(k) >= 5]


def _is_qword(w: str) -> bool:
    return w in QWORDS or (len(w) >= 5 and w[:5] in QSTEMS) or w.startswith(COIN_WORDS)


def _alias(w: str) -> str | tuple[str, str] | None:
    """the alias dictionary with Russian / Ukrainian case endings («биткоине», «солану»)"""
    if w in AMBIGUOUS:
        return AMBIGUOUS[w]
    if w in ALIAS_IDX:
        return ALIAS_IDX[w]
    if _latin(w):
        return None
    for n in (4, 3, 2, 1):
        if len(w) - n < 4:
            continue
        stem, end = w[:-n], w[-n:]
        if end not in ENDINGS:
            continue
        if stem in ALIAS_IDX and not _latin(stem):          # «биткоин» + «е»
            return ALIAS_IDX[stem]
        for v in "аяоеиу":                                  # «солан» + «у» ← «солана»
            if stem + v in ALIAS_IDX and not _latin(stem):
                return ALIAS_IDX[stem + v]
    return None


# ------------------------------------------------------------------------------------------------ the system's coins
@dataclass
class Universe:
    rated: set[str] = field(default_factory=set)          # every coin the spot table rates (web/ideas.json statuses)
    known: set[str] = field(default_factory=set)          # + markets, profiles, exchange lists (tickers)
    names: dict[str, str] = field(default_factory=dict)   # ticker → display name
    _fuzz: list[tuple[str, str]] = field(default_factory=list)

    @classmethod
    def build(cls, data=None, listings: dict | None = None, held: set[str] | None = None) -> Universe:
        """data: cnq.api.pf_review.Data (or anything with get(name) → dict)"""
        u = cls()
        get = (lambda n: (data.get(n) or {})) if data is not None else (lambda n: {})
        ideas = get("ideas")
        u.rated = {str(s).upper() for s in (ideas.get("statuses") or {})}
        for r in ideas.get("rows") or []:
            if isinstance(r, dict) and r.get("symbol"):
                u.rated.add(str(r["symbol"]).upper())
                if r.get("name"):
                    u.names.setdefault(str(r["symbol"]).upper(), str(r["name"]))
        for s, n in NAMES.items():
            u.names.setdefault(s, n)
        for c in get("markets").get("top") or []:
            if isinstance(c, dict) and c.get("symbol"):
                s = str(c["symbol"]).upper()
                u.known.add(s)
                if c.get("name"):
                    u.names.setdefault(s, str(c["name"]))
        for s, c in (get("coin_profiles").get("coins") or {}).items():
            u.known.add(str(s).upper())
            if isinstance(c, dict) and c.get("name"):
                u.names.setdefault(str(s).upper(), str(c["name"]))
        for v in (listings or {}).values():
            u.known |= {str(x).upper() for x in v or ()}
        u.known |= u.rated | set(held or ())
        seen = set()
        for s, n in u.names.items():
            k = _key(n)
            if len(k) >= 4 and (k, s) not in seen:
                seen.add((k, s))
                u._fuzz.append((k, s))
        return u

    def name(self, sym: str) -> str:
        return self.names.get(sym) or sym


@dataclass
class Resolution:
    coins: list[str] = field(default_factory=list)
    ambiguous: tuple[str, str] | None = None
    unknown: list[str] = field(default_factory=list)
    spans: list[tuple[int, int]] = field(default_factory=list)   # token positions taken by coins (and the ambiguous word)
    words: list[str] = field(default_factory=list)               # the question's normalized words


def _fuzzy(w: str, u: Universe) -> list[tuple[float, str]]:
    """best ratio per coin, best first: the word against the aliases (Cyrillic / Greek with Cyrillic / Greek, else in Latin
    letters) and the coin names; only keys of 5+ letters that start with the same sound («кардона» → cardano)"""
    best: dict[str, float] = {}
    tw = w if _latin(w) else translit(w)
    for k, s in _FUZZ_ALIASES + u._fuzz:
        a = tw if _latin(k) else w
        if _latin(w) and not _latin(k):
            continue
        if len(k) < 5 or abs(len(k) - len(a)) > 3 or k[0] != a[0]:
            continue
        sm = difflib.SequenceMatcher(None, a, k)
        if sm.real_quick_ratio() < FUZZY_MIN or sm.quick_ratio() < FUZZY_MIN:
            continue
        r = sm.ratio()
        if r > best.get(s, 0):
            best[s] = r
    return sorted(((r, s) for s, r in best.items()), reverse=True)


def _tokens(text: str) -> tuple[list[str], list[str]]:
    raw = re.findall(r"\w+", unicodedata.normalize("NFC", text or ""))
    pairs = [(r, _norm(r).replace(" ", "")) for r in raw]
    pairs = [(r, n) for r, n in pairs if n]
    return [r for r, _n in pairs], [n for _r, n in pairs]


AFTER = {"about", "of", "on", "buy", "про", "о", "об", "за", "sobre", "de", "για", "το", "in", "into", "щодо", "насчет"}


def resolve(text: str, u: Universe | None = None) -> Resolution:
    u = u or Universe.build()
    raw, words = _tokens(text)
    res = Resolution(words=words)
    i = 0
    while i < len(words):
        hit, n_used = None, 1
        for n in (3, 2):                                   # «bitcoin cash», «шиба ину», «иксарпи» spoken as «икс эр пи»
            if i + n <= len(words):
                k = "".join(words[i:i + n])
                h = AMBIGUOUS.get(k) or ALIAS_IDX.get(k)
                if h:
                    hit, n_used = h, n
                    break
        w, r = words[i], raw[i]
        prev = words[i - 1] if i else ""
        marked = prev.startswith(COIN_WORDS) or prev in AFTER   # «о монете X», «about X»
        written = r.isupper() or (i > 0 and r[:1].isupper())    # NEAR, or «Near» inside a sentence
        if hit is None and not (w in QWORDS and w not in ALIAS_IDX and w not in AMBIGUOUS):
            a = _alias(w)
            if a and w in COMMON_LATIN and w not in SAFE_LOWER and not (written or marked):
                a = None                                    # «near», «link», «a ton of» as plain English words
            hit = a
        if hit is None and _latin(w) and w.upper() in u.known and not _is_qword(w):
            if (r.isupper() and len(w) >= 2) or (marked and w.upper() in u.rated and len(w) >= 3):
                hit = w.upper()
        if hit is None and len(w) >= 4 and not _is_qword(w) and not w.isdigit():
            cand = _fuzzy(w, u)
            if cand and cand[0][0] >= FUZZY_MIN:
                if len(cand) > 1 and cand[1][0] >= FUZZY_MIN and cand[0][0] - cand[1][0] < FUZZY_TIE:
                    hit = (cand[0][1], cand[1][1])
                else:
                    hit = cand[0][1]
        if isinstance(hit, tuple):
            if res.ambiguous is None:
                res.ambiguous = hit
            res.spans.append((i, i + n_used))
        elif hit:
            if hit not in res.coins:
                res.coins.append(hit)
            res.spans.append((i, i + n_used))
        i += n_used
    if not res.coins and not res.ambiguous:               # «о монете Зюзя», «what about XYZQ»: a coin name NOVA doesn't know
        for j, (r, w) in enumerate(zip(raw, words)):
            prev = words[j - 1] if j else ""
            if _is_qword(w) or w in STOP_N or w.isdigit() or len(w) < 2:
                continue
            if prev.startswith(COIN_WORDS) or (_latin(w) and r.isupper() and 2 <= len(w) <= 10):
                res.unknown.append(r)
    return res


# ------------------------------------------------------------------------------------------------ is it a «what about X» question?
_INTENT = re.compile("|".join([
    # ru / uk
    r"\bдума(ешь|ете|ет|єш|єте|ю)\b", r"\bмнени", r"\bдумк", r"\bоцен", r"\bоцін", r"\bстоит ли\b", r"\bварто\b", r"\bчи (брати|купувати|купити|взяти|тримати|продавати)\b",
    r"\bстоит (ее |его |их |сейчас |ли )?(брать|покупать|купить|взять|вкладывать|вложиться|инвестировать|держать|продавать|докупать)",
    r"\b(брать|покупать|купить|взять|докупать|продавать|держать|вкладываться) ли\b",
    r"\b(можно|нужно|надо|можна|треба|пора|есть смысл|имеет смысл|є сенс|має сенс) (ли )?(сейчас |зараз )?(брать|покупать|купить|взять|докупить|"
    r"брати|купувати|купити|взяти|продавать|продать|вкладывать|вкладываться|держать)",
    r"\bперспектив", r"\bкак (тебе|вам)\b", r"\bяк (тобі|вам)\b", r"\b(что|що) (скажешь|скажете|скажеш)\b", r"\bпрогноз",
    r"^(а )?(что|що) (по|с|з|насчет|щодо|там с|там по)\b", r"\b(хорош|плох|надежн|надіин|нормальн)\w* (ли )?(монет|токен|проект|вложени|актив)",
    r"\bвырастет\b|\bбудет расти\b|\bвиросте\b|\bзросте\b",
    r"\bнорм\w* (ли )?(монет|токен|проект|вложени|актив|тема|вариант)", r"\b(монет|токен)\w* (норм|хорош|нормальн)",
    # en
    r"\bthink (of|about)\b", r"\bopinion\b", r"\bthoughts on\b", r"\bworth (buying|it|holding|a buy)\b", r"\bshould i (buy|get|hold|sell|invest)",
    r"\bgood (buy|investment|coin|project)\b", r"\bbuy or (sell|not)\b", r"\bprospects?\b", r"\bfuture of\b", r"\bpromising\b",
    r"\bhow about\b", r"\bwhat about\b", r"\bverdict\b", r"\bforecast\b", r"\bprediction\b", r"\bbullish on\b", r"\bis \w+ a good\b",
    # el (no accents)
    r"\bτι πιστευει?σ\b|\bτι πιστευετε\b", r"\bγνωμη", r"(?<!ποσο )\bαξιζει", r"\bνα αγορασω\b", r"\bπροοπτικ", r"\bτι λεσ\b|\bτι λετε\b",
    r"\bκαλη επενδυση", r"\bπροβλεψη",
    # es
    r"\bopinas\b|\bopina\b|\bpiensas\b", r"\bopinion\b", r"\bvale la pena\b|\bmerece la pena\b", r"\bdeberia (comprar|invertir|vender)",
    r"\bcomprar o\b", r"\bbuena (inversion|compra|moneda)\b", r"\bprometedor", r"\bfuturo de\b", r"\bque tal\b", r"\bperspectiv",
    r"\bpronostico\b", r"\b(puedo|conviene) (comprar|invertir)\b", r"\bes buena\b",
]))
EXPLAIN = re.compile(r"\b(что такое|что за|кто такой|що таке|що за|what is|whats|what are|τι ειναι|que es|explica|объясни|поясни|"
                     r"расскажи|розкажи|explain|tell me about|εξηγησε|cuentame)\b")
FILLER = {_key(w) for w in ["короче", "ну", "вот", "смотрите", "смотри", "слушай", "а", "и", "как", "же", "так", "скажи-ка", "да", "hey", "ok", "okay", "so", "well", "bueno", "pues", "λοιπον"]}


def is_verdict_question(text: str, res: Resolution) -> bool:
    """«что думаешь о X», «стоит ли брать X», «X перспективная?», «should I buy X» — or the question is just a coin's name
    («Солана?», «SUI») — and it names a coin (or two that NOVA must tell apart)"""
    if not res.coins and not res.ambiguous:
        return False
    t = _norm(text)
    if _INTENT.search(t):
        return True
    if EXPLAIN.search(t):
        return False
    taken = {j for a, b in res.spans for j in range(a, b)}
    rest = [w for j, w in enumerate(res.words) if j not in taken and w not in FILLER and w not in STOP_N
            and not w.startswith(COIN_WORDS)]
    return not rest


def asks_unknown_coin(text: str, res: Resolution) -> str | None:
    """«что думаешь о монете Зюзя» — a coin question about a word that is no coin the system knows: that word, else None"""
    if res.coins or res.ambiguous or not res.unknown:
        return None
    return res.unknown[0] if _INTENT.search(_norm(text)) else None
