"""Setup Discovery Engine — first two families, written as explicit rules.

Everything is evaluated at the CLOSE of candle `i` using only candles[:i+1].
An entry decided at candle i is executed at the OPEN of candle i+1 by the paper
engine. Nothing here looks at candle i+1.

LEVEL_BOUNCE (LONG at support / SHORT at resistance)
  ATTENTION          price within `attention_atr` ATR of an ACTIVE/RECLAIMED level
  WAIT_CONFIRMATION  candle traded into the zone (touch)
  CALL               a later candle closes back on the "right" side of the zone
                     with volume evidence (rel. volume ≥ rv_min OR delta agrees);
                     stop = touch extreme ∓ stop_pad_atr * ATR
  REJECTED           close beyond the zone by `invalid_atr` ATR (level failed)
  EXPIRED            no confirmation within `max_wait` candles

FALSE_BREAKOUT_REVERSAL (SHORT after a failed break of resistance / LONG of support)
  FORECAST           candle closes beyond the level (breakout candidate)
  WAIT_CONFIRMATION  still beyond, waiting for acceptance or failure
  CALL               within `fb_window` candles price closes back inside/behind the
                     level → fade; stop beyond the breakout extreme
  REJECTED           `accept` consecutive closes beyond level with volume → real breakout
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..bingx.rest import Candle
from ..features.candles import arrays, atr, delta, relative_volume
from ..features.levels import Level, LevelContext, build_levels, find_swings, nearest_levels
from .base import Setup


@dataclass(frozen=True)
class DiscoveryParams:
    # LEVEL_LIMIT: higher-timeframe levels (tf_factor × base interval), limit at the zone edge
    families: tuple[str, ...] = ("LEVEL_LIMIT",)
    htf_factor: int = 16            # 15m × 16 = 4h levels
    htf_lookback: int = 300
    htf_left: int = 3
    htf_right: int = 3
    limit_min_touches: int = 2
    limit_pad_atr: float = 0.25
    limit_cooldown: int = 8
    # LIQ_CASCADE_LONG: 15m bar return ≤ −liq_sigma·σ(30d), long liquidations ≥ liq_pct quantile (90d),
    # lower wick ≥ liq_wick of range → market LONG at next open, stop = low − liq_stop_atr·ATR, hold ≤ liq_hold bars
    liq_sigma: float = 3.5
    liq_pct: float = 0.98
    liq_wick: float = 0.5
    liq_stop_atr: float = 0.5
    liq_hold: int = 32
    liq_target_r: float = 1.5
    attention_atr: float = 1.0
    stop_pad_atr: float = 0.25
    invalid_atr: float = 0.5
    rv_min: float = 1.2
    max_wait: int = 12
    fb_window: int = 6
    accept: int = 3
    min_level_score: float = 35.0
    rebuild_every: int = 4
    level_lookback: int = 600


class Discovery:
    def __init__(self, symbol: str, candles: list[Candle], params: DiscoveryParams | None = None,
                 deriv: dict | None = None):
        """deriv: optional per-bar derivatives aligned with candles: {"long_liq_usd": array,
        "is_imputed": array} — required for the LIQ_CASCADE_LONG family."""
        self.symbol = symbol
        self.candles = candles
        self.p = params or DiscoveryParams()
        self.deriv = deriv
        self._liq_ready = False
        a = arrays(candles)
        self.a = a
        self.atr = atr(a["h"], a["l"], a["c"])
        self.rv = relative_volume(a["v"])
        self.delta = delta(a["v"], a["tb"])
        self.levels: list[Level] = []
        self._levels_built_at = -1
        self._legacy = bool({"LEVEL_BOUNCE", "FALSE_BREAKOUT_REVERSAL"} & set(self.p.families))
        self._swings = find_swings(candles, 5, 5) if self._legacy else []   # causal via confirm_index filtering
        self.htf = (LevelContext(candles, self.p.htf_factor, every=1, lookback=self.p.htf_lookback,
                                 left=self.p.htf_left, right=self.p.htf_right, min_score=0.0)
                    if "LEVEL_LIMIT" in self.p.families else None)
        self._last_limit: dict[str, int] = {}
        self.setups: list[Setup] = []

    # ------------------------------------------------------------- per candle
    def step(self, i: int) -> list[Setup]:
        """Advance to candle i (its close is known). Returns setups that became CALL now."""
        if i < 30:
            return []
        if self._legacy and i - self._levels_built_at >= self.p.rebuild_every:
            self.levels = build_levels(self.candles, upto=i + 1, max_age=self.p.level_lookback,
                                       swings=self._swings, a=self.a, atr_now=float(self.atr[i]))
            self._levels_built_at = i
        self._update_active(i)
        self._discover(i)
        return [s for s in self.setups if s.status == "CALL" and s.evidence.get("call_index") == i]

    # ---------------------------------------------------------------- helpers
    def _level_by_id(self, lid: str) -> Level | None:
        return next((L for L in self.levels if L.id == lid), None)

    def _has_open(self, lid: str, family: str, direction: str) -> bool:
        return any(s.active and s.level_id == lid and s.family == family and s.direction == direction
                   for s in self.setups)

    def _discover(self, i: int) -> None:
        c = self.candles[i]
        t = c.open_time
        atr_i = float(self.atr[i])
        if "LEVEL_LIMIT" in self.p.families:
            self._discover_limit(i, c, t, atr_i)
        if "LIQ_CASCADE_LONG" in self.p.families and self.deriv is not None:
            self._discover_liq(i, c, t, atr_i)
        legacy = {"LEVEL_BOUNCE", "FALSE_BREAKOUT_REVERSAL"} & set(self.p.families)
        for L in (nearest_levels(self.levels, c.close, atr_i, within_atr=self.p.attention_atr) if legacy else []):
            if L.score < self.p.min_level_score:
                continue
            # ----- LEVEL_BOUNCE: approaching from the correct side
            if L.kind in ("support", "both") and c.close > L.hi \
                    and not self._has_open(L.id, "LEVEL_BOUNCE", "LONG"):
                self.setups.append(self._new(L, "LEVEL_BOUNCE", "LONG", i, t, "ATTENTION",
                                             f"price {c.close:.2f} within {self.p.attention_atr} ATR above support"))
            if L.kind in ("resistance", "both") and c.close < L.lo \
                    and not self._has_open(L.id, "LEVEL_BOUNCE", "SHORT"):
                self.setups.append(self._new(L, "LEVEL_BOUNCE", "SHORT", i, t, "ATTENTION",
                                             f"price {c.close:.2f} within {self.p.attention_atr} ATR below resistance"))
            # ----- FALSE_BREAKOUT_REVERSAL: close beyond the level
            if L.kind in ("resistance", "both") and c.close > L.hi \
                    and not self._has_open(L.id, "FALSE_BREAKOUT_REVERSAL", "SHORT"):
                s = self._new(L, "FALSE_BREAKOUT_REVERSAL", "SHORT", i, t, "FORECAST",
                              f"close {c.close:.2f} above resistance {L.hi:.2f}: breakout candidate")
                s.touch_high, s.touch_index = c.high, i
                s.expires_index = i + self.p.fb_window
                self.setups.append(s)
            if L.kind in ("support", "both") and c.close < L.lo \
                    and not self._has_open(L.id, "FALSE_BREAKOUT_REVERSAL", "LONG"):
                s = self._new(L, "FALSE_BREAKOUT_REVERSAL", "LONG", i, t, "FORECAST",
                              f"close {c.close:.2f} below support {L.lo:.2f}: breakdown candidate")
                s.touch_low, s.touch_index = c.low, i
                s.expires_index = i + self.p.fb_window
                self.setups.append(s)

    def _discover_limit(self, i: int, c: Candle, t: int, atr_i: float) -> None:
        """LEVEL_LIMIT: price trades into an ACTIVE higher-TF level zone (≥N touches) coming
        from the right side → limit fill at the zone edge, stop beyond the far edge + pad."""
        prev_close = float(self.a["c"][i - 1])
        pad = self.p.limit_pad_atr * atr_i
        for L in self.htf.levels_at(i):
            if L.status != "ACTIVE" or L.touch_count < self.p.limit_min_touches:
                continue
            if i - self._last_limit.get(L.id, -10**9) < self.p.limit_cooldown:
                continue
            if L.kind in ("support", "both") and prev_close > L.hi and c.low <= L.hi:
                direction, entry, stop = "LONG", L.hi, L.lo - pad
            elif L.kind in ("resistance", "both") and prev_close < L.lo and c.high >= L.lo:
                direction, entry, stop = "SHORT", L.lo, L.hi + pad
            else:
                continue
            self._last_limit[L.id] = i
            s = self._new(L, "LEVEL_LIMIT", direction, i, t, "WAIT_CONFIRMATION",
                          f"price entered {L.kind} zone {L.lo:.2f}–{L.hi:.2f} ({L.touch_count} touches)")
            s.entry_type, s.entry, s.stop, s.invalidation = "LIMIT", entry, stop, stop
            s.entry_zone = (L.lo, L.hi)
            s.touch_index = i
            s.confirm_rule = "limit order at zone edge; fills when price trades into the zone"
            s.evidence.update({"call_index": i, "rel_volume": round(float(self.rv[i]), 2),
                               "delta": round(float(self.delta[i]), 4), "atr": round(atr_i, 2),
                               "htf_factor": self.p.htf_factor})
            s.move("CALL", t, f"limit {entry:.2f} stop {stop:.2f}")
            self.setups.append(s)

    def _prep_liq(self) -> None:
        from ..research.derivatives import trailing_quantile
        ll = np.abs(np.asarray(self.deriv["long_liq_usd"], dtype=float))
        self._liq_thr = trailing_quantile(ll, 96 * 90, self.p.liq_pct)
        self._liq_ll = ll
        self._liq_imp = np.asarray(self.deriv.get("is_imputed", np.zeros(len(ll))), dtype=float)
        ret = np.concatenate([[0.0], np.diff(self.a["c"]) / self.a["c"][:-1]])
        self._liq_sig = np.array([ret[max(0, k - 96 * 30): k].std() if k >= 96 * 30 else np.nan
                                  for k in range(0, len(ret), 96)])
        self._liq_ret = ret
        self._liq_last = -10**9
        self._liq_ready = True

    def _discover_liq(self, i: int, c: Candle, t: int, atr_i: float) -> None:
        if not self._liq_ready:
            self._prep_liq()
        if i < 96 * 90 or self._liq_imp[i] or i - self._liq_last < self.p.liq_hold:
            return
        sig = self._liq_sig[i // 96]
        if not sig or np.isnan(sig) or np.isnan(self._liq_thr[i]):
            return
        rng = c.high - c.low
        if rng <= 0 or self._liq_ret[i] > -self.p.liq_sigma * sig or self._liq_ll[i] < self._liq_thr[i]:
            return
        if (min(c.open, c.close) - c.low) / rng < self.p.liq_wick:
            return
        self._liq_last = i
        s = Setup(self.symbol, "LIQ_CASCADE_LONG", "LONG", "LIQ", c.low, c.close, i, t, updated_time=t)
        s.entry_type = "MARKET"
        s.entry = c.close                      # provisional; fills at next open
        s.stop = c.low - self.p.liq_stop_atr * atr_i
        s.invalidation = s.stop
        s.confirm_rule = "market at next open; stop below cascade low; hold ≤ %d bars; target %.1fR" % (self.p.liq_hold, self.p.liq_target_r)
        s.evidence.update({"call_index": i, "ret_pct": round(float(self._liq_ret[i]) * 100, 2),
                           "sigma_mult": round(float(-self._liq_ret[i] / sig), 2), "liq_usd": float(self._liq_ll[i]),
                           "atr": round(atr_i, 4), "rel_volume": round(float(self.rv[i]), 2),
                           "delta": round(float(self.delta[i]), 4), "level_score": 100.0, "level_touches": 0,
                           "max_bars": self.p.liq_hold})
        s.move("CALL", t, f"cascade {s.evidence['ret_pct']}% ({s.evidence['sigma_mult']}σ), long liquidations ${s.evidence['liq_usd']/1e6:.1f}M, wick ok")
        self.setups.append(s)

    def _new(self, L: Level, family: str, direction: str, i: int, t: int, status: str, why: str) -> Setup:
        s = Setup(self.symbol, family, direction, L.id, L.lo, L.hi, i, t, updated_time=t)
        s.evidence["level_score"] = L.score
        s.evidence["level_touches"] = L.touch_count
        s.move(status, t, why)
        return s

    def _update_active(self, i: int) -> None:
        c = self.candles[i]
        t = c.open_time
        atr_i = float(self.atr[i])
        for s in self.setups:
            if not s.active or s.status in ("CALL", "POSITION_OPEN"):
                continue
            L = self._level_by_id(s.level_id)
            if L is None or L.status == "BROKEN" and s.family == "LEVEL_BOUNCE":
                s.move("CANCELLED", t, "level no longer active")
                continue
            if s.family == "LEVEL_BOUNCE":
                self._step_bounce(s, c, i, t, atr_i)
            else:
                self._step_false_breakout(s, c, i, t, atr_i)

    def _step_bounce(self, s: Setup, c: Candle, i: int, t: int, atr_i: float) -> None:
        long = s.direction == "LONG"
        inv = s.level_lo - self.p.invalid_atr * atr_i if long else s.level_hi + self.p.invalid_atr * atr_i
        s.invalidation = inv
        if (long and c.close < inv) or (not long and c.close > inv):
            s.move("REJECTED", t, f"close {c.close:.2f} beyond invalidation {inv:.2f}")
            return
        touched = (c.low <= s.level_hi) if long else (c.high >= s.level_lo)
        if s.status == "ATTENTION":
            if touched:
                s.touch_low, s.touch_high, s.touch_index = c.low, c.high, i
                s.expires_index = i + self.p.max_wait
                s.entry_zone = (s.level_lo, s.level_hi)
                s.confirm_rule = ("close above zone with rel.volume≥%.1f or positive delta" % self.p.rv_min
                                  if long else "close below zone with rel.volume≥%.1f or negative delta" % self.p.rv_min)
                s.move("WAIT_CONFIRMATION", t, f"touched zone (low {c.low:.2f}, high {c.high:.2f})")
            elif i - s.created_index > self.p.max_wait:
                s.move("EXPIRED", t, "price never reached the zone")
            return
        if s.status == "WAIT_CONFIRMATION":
            if touched:
                s.touch_low = min(s.touch_low or c.low, c.low)
                s.touch_high = max(s.touch_high or c.high, c.high)
            closed_right = c.close > s.level_hi if long else c.close < s.level_lo
            vol_ok = self.rv[i] >= self.p.rv_min or (self.delta[i] > 0 if long else self.delta[i] < 0)
            if closed_right and vol_ok and i > (s.touch_index or i):
                s.stop = (s.touch_low - self.p.stop_pad_atr * atr_i if long
                          else s.touch_high + self.p.stop_pad_atr * atr_i)
                s.entry = c.close  # provisional; paper engine fills at next open
                s.evidence.update({"call_index": i, "rel_volume": round(float(self.rv[i]), 2),
                                   "delta": round(float(self.delta[i]), 4), "atr": round(atr_i, 2)})
                s.move("CALL", t, f"close {c.close:.2f} back on the right side of zone; rv={self.rv[i]:.2f}")
            elif s.expires_index is not None and i >= s.expires_index:
                s.move("EXPIRED", t, "no confirmation within window")

    def _step_false_breakout(self, s: Setup, c: Candle, i: int, t: int, atr_i: float) -> None:
        short = s.direction == "SHORT"       # fading an upside break
        beyond = c.close > s.level_hi if short else c.close < s.level_lo
        if short:
            s.touch_high = max(s.touch_high or c.high, c.high)
        else:
            s.touch_low = min(s.touch_low or c.low, c.low)
        n_beyond = s.evidence.get("closes_beyond", 1)
        if beyond:
            n_beyond += 1
            s.evidence["closes_beyond"] = n_beyond
            if s.status == "FORECAST":
                s.confirm_rule = "close back inside the level within %d candles" % self.p.fb_window
                s.move("WAIT_CONFIRMATION", t, "still beyond level; waiting for acceptance or failure")
            if n_beyond >= self.p.accept and self.rv[i] >= 1.0:
                s.move("REJECTED", t, f"{n_beyond} closes beyond level with volume: accepted breakout")
            elif s.expires_index is not None and i >= s.expires_index:
                s.move("EXPIRED", t, "breakout neither failed nor accepted in window")
            return
        # closed back inside → failed breakout → fade
        if i > s.created_index:
            s.stop = (s.touch_high + self.p.stop_pad_atr * atr_i if short
                      else s.touch_low - self.p.stop_pad_atr * atr_i)
            s.entry = c.close
            s.invalidation = s.stop
            s.evidence.update({"call_index": i, "rel_volume": round(float(self.rv[i]), 2),
                               "delta": round(float(self.delta[i]), 4), "atr": round(atr_i, 2),
                               "break_extreme": s.touch_high if short else s.touch_low})
            s.move("CALL", t, f"close {c.close:.2f} back inside level: failed breakout")
