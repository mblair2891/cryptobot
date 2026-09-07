from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from aethergrid.config import Settings

_SECRET_KEYS = {
    "api_key",
    "api_secret",
    "private_key",
    "coinbase_api_private_key",
    "xai_api_key",
    "llm_api_key",
    "authorization",
    "password",
    "secret",
}


def _drop_secrets(_: Any, __: Any, event_dict: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in event_dict.items():
        if str(key).lower() in _SECRET_KEYS or "private_key" in str(key).lower():
            redacted[key] = "***"
        else:
            redacted[key] = value
    return redacted


def setup_logging(settings: Settings | None = None) -> None:
    settings = settings or Settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)

    processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _drop_secrets,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if settings.log_json:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name or "aethergrid")
