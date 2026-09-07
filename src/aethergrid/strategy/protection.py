from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from aethergrid.domain.models import BotRuntime, Candle, GridConfig
from aethergrid.money import ZERO


def true_range(candle: Candle, prev_close: Decimal | None) -> Decimal:
    high_low = candle.high - candle.low
    if prev_close is None:
        return high_low
    return max(high_low, abs(candle.high - prev_close), abs(candle.low - prev_close))


def atr(candles: list[Candle], period: int = 14) -> Decimal:
    if not candles:
        return ZERO
    trs: list[Decimal] = []
    prev: Decimal | None = None
    for c in candles:
        trs.append(true_range(c, prev))
        prev = c.close
    window = trs[-period:] if len(trs) >= period else trs
    if not window:
        return ZERO
    return sum(window, ZERO) / Decimal(len(window))


def efficiency_ratio(candles: list[Candle], period: int = 20) -> Decimal:
    if len(candles) < 2:
        return ZERO
    window = candles[-period:] if len(candles) >= period else candles
    change = abs(window[-1].close - window[0].close)
    path = sum((abs(window[i].close - window[i - 1].close) for i in range(1, len(window))), ZERO)
    if path == 0:
        return ZERO
    return change / path


def donchian(candles: list[Candle], period: int = 20) -> tuple[Decimal, Decimal]:
    window = candles[-period:] if candles else []
    if not window:
        return ZERO, ZERO
    return max(c.high for c in window), min(c.low for c in window)


def returns(candles: list[Candle]) -> list[Decimal]:
    out: list[Decimal] = []
    for i in range(1, len(candles)):
        prev = candles[i - 1].close
        if prev > 0:
            out.append((candles[i].close - prev) / prev)
    return out


def pump_dump_triggered(
    candles_1m: list[Candle],
    candles_5m: list[Candle],
    config: GridConfig,
) -> tuple[bool, str]:
    if not config.pump_dump_protection:
        return False, ""
    threshold = config.pump_dump_return_pct
    for label, candles, n in (("1m", candles_1m, 1), ("5m", candles_5m, 1)):
        if len(candles) < 2:
            continue
        window = candles[-n:]
        start = candles[-(n + 1)].close if len(candles) > n else candles[0].open
        if start <= 0:
            continue
        ret = abs((window[-1].close - start) / start)
        if ret >= threshold:
            return True, f"{label} velocity {float(ret):.4f} >= {float(threshold)}"
        vols = [c.volume for c in candles[:-1][-20:]]
        if vols:
            avg = sum(vols, ZERO) / Decimal(len(vols))
            if avg > 0 and window[-1].volume >= avg * config.pump_dump_volume_mult:
                return True, f"{label} volume spike {float(window[-1].volume / avg):.1f}x"
    return False, ""


def breakout_triggered(
    mark: Decimal,
    config: GridConfig,
    atr_value: Decimal,
) -> tuple[bool, str]:
    if not config.breakout_protection:
        return False, ""
    up = config.upper_price
    lo = config.lower_price
    atr_buf = atr_value * config.breakout_atr_mult if atr_value > 0 else ZERO
    pct_up = up * (Decimal("1") + config.breakout_pct)
    pct_dn = lo * (Decimal("1") - config.breakout_pct)
    ceiling = max(up + atr_buf, pct_up) if atr_buf else pct_up
    floor = min(lo - atr_buf, pct_dn) if atr_buf else pct_dn
    if mark > ceiling:
        return True, f"breakout above range mark={mark} ceiling={ceiling}"
    if mark < floor:
        return True, f"breakout below range mark={mark} floor={floor}"
    return False, ""


def inventory_skew(runtime: BotRuntime) -> Decimal:
    planned = runtime.planned_base()
    if planned <= 0:
        return ZERO
    return runtime.held_base() / planned


def apply_protection(
    runtime: BotRuntime,
    *,
    mark: Decimal,
    candles_1m: list[Candle],
    candles_5m: list[Candle],
    candles_1h: list[Candle],
    now: datetime | None = None,
) -> tuple[BotRuntime, bool, str]:
    """Pause new entries on pump/dump or breakout. Returns (state, halt_entries, reason)."""
    now = now or datetime.now(UTC)
    cfg = runtime.config
    atr_value = atr(candles_1h or candles_5m, 14)
    pd, pd_reason = pump_dump_triggered(candles_1m, candles_5m, cfg)
    br, br_reason = breakout_triggered(mark, cfg, atr_value)
    halt = False
    reason = ""
    if pd:
        halt = True
        reason = pd_reason
        runtime.protection.pump_dump_until = now + timedelta(minutes=5)
    if br:
        halt = True
        reason = br_reason if not reason else f"{reason}; {br_reason}"
        runtime.protection.breakout_active = True
    if runtime.protection.pump_dump_until and now < runtime.protection.pump_dump_until:
        halt = True
        reason = reason or "pump/dump cooldown"
    if not pd and runtime.protection.pump_dump_until and now >= runtime.protection.pump_dump_until:
        runtime.protection.pump_dump_until = None
    if not br:
        runtime.protection.breakout_active = False
    runtime.protection.entries_paused = halt or runtime.status.value == "paused"
    runtime.protection.last_reason = reason
    return runtime, halt, reason
