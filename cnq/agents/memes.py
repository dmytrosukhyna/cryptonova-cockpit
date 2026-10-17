"""Meme desk — Hyperliquid meme perpetuals + Solana DEX memecoins (docs/research/memes.md). Work in progress."""
from __future__ import annotations

# Hyperliquid perp names (k = ×1000 contract). Names not listed on HL are simply ignored; delisted ones stay for research.
MEME_COINS = [
    "DOGE", "kPEPE", "kBONK", "kSHIB", "kFLOKI", "WIF", "POPCAT", "MEW", "BOME", "BRETT", "MOODENG", "PNUT", "GOAT",
    "CHILLGUY", "FARTCOIN", "SPX", "PENGU", "TRUMP", "MELANIA", "PURR", "MYRO", "NEIROETH", "kNEIRO", "NEIRO", "TURBO",
    "MOG", "kMOG", "PEOPLE", "TST", "ACT", "MEME", "DOGS", "kDOGS", "GRIFFAIN", "ZEREBRO", "AI16Z", "VINE", "JELLY",
    "YZY", "FWOG", "GIGA", "MICHI", "PONKE", "RETARDIO", "SIGMA", "LOCKIN", "MANEKI", "SLERF", "WEN", "HPOS", "TOSHI",
    "DEGEN", "MOTHER", "APU", "USELESS", "HOSICO", "LAUNCHCOIN", "kSATS", "SATS", "kLADYS", "BABYDOGE", "kBABYDOGE",
    "PEPE", "BONK", "SHIB", "FLOKI", "CAT", "kCAT", "SUNDOG", "MAGA", "BODEN", "TREMP", "SNEK", "PUPS", "BRETT",
]
MEME_COINS = list(dict.fromkeys(MEME_COINS))
