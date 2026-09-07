from __future__ import annotations

from decimal import Decimal
from math import sqrt

from aethergrid.domain.models import Candle
from aethergrid.money import ZERO
from aethergrid.strategy.protection import atr, donchian, efficiency_ratio


def realized_vol(candles: list[Candle], period: int = 24) -> Decimal:
    if len(candles) < 2:
        return ZERO
    window = candles[-period:] if len(candles) >= period else candles
    rets: list[Decimal] = []
    for i in range(1, len(window)):
        prev = window[i - 1].close
        if prev > 0:
            rets.append((window[i].close - prev) / prev)
    if len(rets) < 2:
        return abs(rets[0]) if rets else ZERO
    mean = sum(rets, ZERO) / Decimal(len(rets))
    var = sum(((r - mean) ** 2 for r in rets), ZERO) / Decimal(len(rets) - 1)
    return Decimal(str(sqrt(float(var))))


def band_crossings(candles: list[Candle], band_pct: Decimal = Decimal("0.008")) -> int:
    """Count sign flips of bar returns that exceed the band (many small up/down crossings)."""
    if len(candles) < 4:
        return 0
    last_side = 0
    crosses = 0
    for i in range(1, len(candles)):
        prev, cur = candles[i - 1].close, candles[i].close
        if prev <= 0:
            continue
        ret = (cur - prev) / prev
        side = 1 if ret >= band_pct else (-1 if ret <= -band_pct else 0)
        if side and last_side and side != last_side:
            crosses += 1
        if side:
            last_side = side
    return crosses


def net_drift_pct(candles: list[Candle]) -> Decimal:
    """|net move| / high-low range. Low = sideways; high = one-way channel."""
    if len(candles) < 2:
        return ZERO
    first, last = candles[0].close, candles[-1].close
    hi = max(c.high for c in candles)
    lo = min(c.low for c in candles)
    width = hi - lo
    floor = first * Decimal("0.02") if first > 0 else Decimal("0.01")
    if width < floor:
        width = floor
    return abs(last - first) / width


def range_quality(candles: list[Candle], period: int = 30) -> dict[str, Decimal]:
    """How mean-reverting vs one-way the recent window looks."""
    window = candles[-period:] if candles else []
    if len(window) < 5:
        return {
            "oscillation": ZERO,
            "inside_frac": ZERO,
            "er": ZERO,
            "atr": ZERO,
            "high": ZERO,
            "low": ZERO,
        }
    high, low = donchian(window, len(window))
    width = high - low
    if width <= 0:
        return {
            "oscillation": ZERO,
            "inside_frac": ZERO,
            "er": efficiency_ratio(window),
            "atr": atr(window),
            "high": high,
            "low": low,
        }
    mid = (high + low) / 2
    band = width * Decimal("0.25")
    crosses = 0
    last_side = 0
    inside = 0
    for c in window:
        if abs(c.close - mid) <= band:
            inside += 1
        side = 1 if c.close >= mid else -1
        if last_side and side != last_side:
            crosses += 1
        last_side = side
    osc = Decimal(crosses) / Decimal(len(window) - 1)
    return {
        "oscillation": osc,
        "inside_frac": Decimal(inside) / Decimal(len(window)),
        "er": efficiency_ratio(window),
        "atr": atr(window),
        "high": high,
        "low": low,
    }
