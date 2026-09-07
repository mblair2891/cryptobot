from __future__ import annotations

from enum import StrEnum


class Venue(StrEnum):
    DEMO = "demo"
    PAPER = "paper"
    COINBASE = "coinbase"


class BotStatus(StrEnum):
    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    COOLDOWN = "cooldown"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ARCHIVED = "archived"
    ERROR = "error"


class GridMode(StrEnum):
    GEOMETRIC = "geometric"
    ARITHMETIC = "arithmetic"


class SizeMode(StrEnum):
    EQUAL_QUOTE = "equal_quote"
    EQUAL_BASE = "equal_base"


class StartMode(StrEnum):
    QUOTE_ONLY = "quote_only"
    SPLIT = "split"


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class OrderType(StrEnum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class TimeInForce(StrEnum):
    GTC = "GTC"
    IOC = "IOC"


class IntentKind(StrEnum):
    PLACE = "place"
    CANCEL = "cancel"
    REPLACE = "replace"
    FLATTEN = "flatten"
    PAUSE = "pause"
    RESUME = "resume"
    STOP = "stop"
    ARCHIVE = "archive"
    NOOP = "noop"


class SlotState(StrEnum):
    EMPTY = "empty"
    BUY_OPEN = "buy_open"
    HOLDING = "holding"
    SELL_OPEN = "sell_open"


class RiskSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    KILL = "kill"
