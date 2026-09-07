from __future__ import annotations

import json
from typing import Any

from aethergrid.ai.features import OperatorFeatures
from aethergrid.ai.policies import Action, Noop
from aethergrid.config import Settings
from aethergrid.logging import get_logger

log = get_logger("ai.llm")

SYSTEM = """You are AetherGrid's pair-ranking advisor, not a trader.
You MUST reply with a single JSON object matching this schema:
{
  "ranked_products": [{"product_id": "BTC-USD", "reason": "...", "suggested_lower": 0, "suggested_upper": 0}],
  "commentary": "short"
}
Rules:
- Only rank high-liquidity USD or USDC spot pairs.
- Prefer mean-reverting oscillation, not one-way trends.
- Never invent products that were not in the features payload.
- Do not request withdrawals, transfers, or custody.
- This is not financial advice; you only rank.
"""


async def advise(settings: Settings, features: OperatorFeatures) -> dict[str, Any] | None:
    if not settings.has_llm:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        log.warning("openai_sdk_missing")
        return None

    payload = features.model_dump(mode="json")
    # Keep the prompt bounded.
    slim = {
        "mode": payload.get("mode"),
        "cash": payload.get("cash"),
        "equity": payload.get("equity"),
        "pairs": payload.get("pairs", [])[:25],
        "bots": payload.get("bots", [])[:20],
    }
    client = OpenAI(api_key=settings.llm_key, base_url=settings.llm_base_url)
    try:
        resp = client.chat.completions.create(
            model=settings.llm_model,
            temperature=0.2,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": json.dumps(slim, default=str)},
            ],
            response_format={"type": "json_object"},
        )
        text = resp.choices[0].message.content or "{}"
        data = json.loads(text)
        log.info("llm_advise", commentary=str(data.get("commentary", ""))[:200])
        return data
    except Exception as exc:  # noqa: BLE001
        log.warning("llm_failed", error=str(exc))
        return None


def merge_llm_rank(features: OperatorFeatures, advice: dict[str, Any] | None) -> OperatorFeatures:
    if not advice:
        return features
    ranked = advice.get("ranked_products") or []
    boost = {str(item.get("product_id")): i for i, item in enumerate(ranked)}
    for pair in features.pairs:
        if pair.product_id in boost:
            # Higher rank (lower index) gets a modest score bump; validators still rule.
            pair.score += DecimalScore(max(0, 8 - boost[pair.product_id]))
            item = next(x for x in ranked if str(x.get("product_id")) == pair.product_id)
            sl = item.get("suggested_lower")
            su = item.get("suggested_upper")
            try:
                if sl and su and float(sl) < float(su):
                    # Keep deterministic range unless LLM range still contains mark.
                    from decimal import Decimal

                    lo, hi = Decimal(str(sl)), Decimal(str(su))
                    if lo < pair.mark < hi:
                        pair.range_low = lo
                        pair.range_high = hi
            except Exception:  # noqa: BLE001
                pass
    features.pairs.sort(key=lambda p: p.score, reverse=True)
    return features


def DecimalScore(n: int):  # noqa: N802
    from decimal import Decimal

    return Decimal(n)


def parse_action_json(payload: dict[str, Any]) -> Action:
    from pydantic import TypeAdapter

    adapter: TypeAdapter[Action] = TypeAdapter(Action)
    try:
        return adapter.validate_python(payload)
    except Exception:  # noqa: BLE001
        return Noop(reason="invalid LLM/action payload")
