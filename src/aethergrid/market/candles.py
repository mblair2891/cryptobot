from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from aethergrid.domain.models import Candle
from aethergrid.money import D


def parse_candles(product_id: str, raw: list[Any], granularity: str) -> list[Candle]:
    candles: list[Candle] = []
    for item in raw:
        if isinstance(item, dict):
            ts = item.get("start") or item.get("time")
            candles.append(
                Candle(
                    product_id=product_id,
                    start=datetime.fromtimestamp(int(ts), tz=UTC),
                    open=D(item.get("open") or 0),
                    high=D(item.get("high") or 0),
                    low=D(item.get("low") or 0),
                    close=D(item.get("close") or 0),
                    volume=D(item.get("volume") or 0),
                    granularity=granularity,
                )
            )
        elif isinstance(item, list | tuple) and len(item) >= 6:
            ts, low, high, open_, close, volume = item[:6]
            candles.append(
                Candle(
                    product_id=product_id,
                    start=datetime.fromtimestamp(int(ts), tz=UTC),
                    open=D(open_),
                    high=D(high),
                    low=D(low),
                    close=D(close),
                    volume=D(volume),
                    granularity=granularity,
                )
            )
    candles.sort(key=lambda c: c.start)
    return candles
