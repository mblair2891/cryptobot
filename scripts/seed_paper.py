#!/usr/bin/env python3
"""Print a sample create-bot payload using the live public ticker."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

from aethergrid.market.products import PublicMarket


async def main() -> None:
    market = PublicMarket()
    try:
        ticker = await market.get_ticker("BTC-USD")
        mark = ticker.last
        payload = {
            "product_id": "BTC-USD",
            "investment": "1000",
            "lower_price": str((mark * Decimal("0.95")).quantize(Decimal("0.01"))),
            "upper_price": str((mark * Decimal("1.05")).quantize(Decimal("0.01"))),
            "grid_levels": 11,
            "trailing_up": True,
            "take_profit_pct": "0.08",
            "stop_loss_pct": "0.12",
        }
        print(json.dumps({"mark": str(mark), "create": payload}, indent=2))
    finally:
        await market.close()


if __name__ == "__main__":
    asyncio.run(main())
