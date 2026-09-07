from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aethergrid.domain.enums import (
    BotStatus,
    GridMode,
    IntentKind,
    OrderSide,
    OrderStatus,
    OrderType,
    SizeMode,
    SlotState,
    StartMode,
    TimeInForce,
    Venue,
)
from aethergrid.money import D, ZERO


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str = "ag") -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


class Product(BaseModel):
    model_config = ConfigDict(frozen=True)

    product_id: str
    base_currency: str
    quote_currency: str
    status: str = "online"
    trading_disabled: bool = False
    product_type: str = "SPOT"
    price: Decimal = ZERO
    quote_increment: Decimal = Decimal("0.01")
    base_increment: Decimal = Decimal("0.00000001")
    quote_min_size: Decimal = ZERO
    base_min_size: Decimal = ZERO
    min_market_funds: Decimal = ZERO
    base_max_size: Decimal | None = None
    quote_max_size: Decimal | None = None
    cancel_only: bool = False
    limit_only: bool = False
    post_only: bool = False
    auction_mode: bool = False
    volume_24h: Decimal = ZERO
    approximate_price: Decimal = ZERO

    @property
    def tradable(self) -> bool:
        return (
            self.status.lower() == "online"
            and not self.trading_disabled
            and not self.cancel_only
            and self.product_type.upper() in {"SPOT", ""}
        )

    def q_price(self, price: Decimal, *, side: OrderSide) -> Decimal:
        from aethergrid.money import buy_price, sell_price

        if side == OrderSide.BUY:
            return buy_price(price, self.quote_increment)
        return sell_price(price, self.quote_increment)

    def q_size(self, size: Decimal) -> Decimal:
        from aethergrid.money import base_size

        return base_size(size, self.base_increment)


class Ticker(BaseModel):
    product_id: str
    price: Decimal
    bid: Decimal = ZERO
    ask: Decimal = ZERO
    volume_24h: Decimal = ZERO
    ts: datetime = Field(default_factory=utcnow)

    @property
    def mid(self) -> Decimal:
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2
        return self.price

    @property
    def last(self) -> Decimal:
        return self.price

    @property
    def spread(self) -> Decimal:
        if self.bid > 0 and self.ask > 0:
            return self.ask - self.bid
        return ZERO

    @property
    def spread_bps(self) -> Decimal:
        mid = self.mid
        if mid <= 0:
            return ZERO
        return (self.spread / mid) * Decimal("10000")


class Candle(BaseModel):
    product_id: str
    start: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    granularity: str = "ONE_HOUR"


class Balance(BaseModel):
    currency: str
    available: Decimal = ZERO
    hold: Decimal = ZERO

    @property
    def total(self) -> Decimal:
        return self.available + self.hold


class FeeRates(BaseModel):
    maker: Decimal = Decimal("0.006")
    taker: Decimal = Decimal("0.008")


class GridConfig(BaseModel):
    """Per-bot Bitsgap-parity grid configuration."""

    product_id: str
    investment: Decimal
    lower_price: Decimal
    upper_price: Decimal
    mode: GridMode = GridMode.GEOMETRIC
    grid_levels: int | None = 21
    grid_step_pct: Decimal | None = None
    size_mode: SizeMode = SizeMode.EQUAL_QUOTE
    start_mode: StartMode = StartMode.QUOTE_ONLY
    trailing_up: bool = False
    trailing_up_trigger_pct: Decimal = Decimal("0.01")
    trailing_down: bool = False
    trailing_down_trigger_pct: Decimal = Decimal("0.01")
    take_profit_pct: Decimal | None = None
    stop_loss_pct: Decimal | None = None
    close_on_stop: bool = True
    flatten_on_stop: bool = False
    pump_dump_protection: bool = True
    pump_dump_return_pct: Decimal = Decimal("0.03")
    pump_dump_volume_mult: Decimal = Decimal("4")
    breakout_protection: bool = True
    breakout_atr_mult: Decimal = Decimal("1.5")
    breakout_pct: Decimal = Decimal("0.02")
    breakout_flatten: bool = False
    inventory_cap: Decimal | None = None
    cooldown_seconds: int = 30
    post_only: bool = False
    name: str = ""

    @field_validator(
        "investment",
        "lower_price",
        "upper_price",
        "grid_step_pct",
        "trailing_up_trigger_pct",
        "trailing_down_trigger_pct",
        "take_profit_pct",
        "stop_loss_pct",
        "pump_dump_return_pct",
        "pump_dump_volume_mult",
        "breakout_atr_mult",
        "breakout_pct",
        "inventory_cap",
        mode="before",
    )
    @classmethod
    def _dec(cls, v: object) -> object:
        if v is None or v == "":
            return None
        return D(v)

    @model_validator(mode="after")
    def _bounds(self) -> GridConfig:
        if self.lower_price <= 0 or self.upper_price <= 0:
            raise ValueError("prices must be positive")
        if self.lower_price >= self.upper_price:
            raise ValueError("lower_price must be < upper_price")
        if self.investment <= 0:
            raise ValueError("investment must be positive")
        if self.grid_levels is None and self.grid_step_pct is None:
            raise ValueError("provide grid_levels or grid_step_pct")
        if self.grid_levels is not None and self.grid_levels < 2:
            raise ValueError("grid_levels must be >= 2 (price points)")
        return self


class GridSlot(BaseModel):
    """One interval between consecutive ladder prices."""

    index: int
    buy_price: Decimal
    sell_price: Decimal
    target_base: Decimal
    held_base: Decimal = ZERO
    buy_remaining: Decimal = ZERO
    sell_remaining: Decimal = ZERO
    state: SlotState = SlotState.EMPTY
    buy_order_id: str | None = None
    sell_order_id: str | None = None
    seq: int = 0

    @property
    def quote_at_buy(self) -> Decimal:
        return self.target_base * self.buy_price


class Inventory(BaseModel):
    base: Decimal = ZERO
    quote: Decimal = ZERO
    quote_reserved: Decimal = ZERO
    base_reserved: Decimal = ZERO
    starting_base: Decimal = ZERO
    starting_quote: Decimal = ZERO
    realized_pnl: Decimal = ZERO
    fees_paid: Decimal = ZERO

    @property
    def starting_equity(self) -> Decimal:
        return self.starting_quote  # quote-denominated grids

    def equity(self, mark: Decimal) -> Decimal:
        return self.quote + self.base * mark

    def unrealized(self, mark: Decimal) -> Decimal:
        return self.equity(mark) - self.starting_equity - self.realized_pnl


class TrailingState(BaseModel):
    enabled_up: bool = False
    enabled_down: bool = False
    original_lower: Decimal = ZERO
    original_upper: Decimal = ZERO
    shifts_up: int = 0
    shifts_down: int = 0
    last_shift_at: datetime | None = None


class ProtectionState(BaseModel):
    entries_paused: bool = False
    pump_dump_until: datetime | None = None
    breakout_active: bool = False
    last_reason: str = ""


class Order(BaseModel):
    client_order_id: str
    product_id: str
    side: OrderSide
    price: Decimal
    size: Decimal
    filled_size: Decimal = ZERO
    filled_value: Decimal = ZERO
    fee: Decimal = ZERO
    status: OrderStatus = OrderStatus.PENDING
    order_type: OrderType = OrderType.LIMIT
    tif: TimeInForce = TimeInForce.GTC
    exchange_order_id: str | None = None
    bot_id: str | None = None
    slot_index: int | None = None
    venue: Venue = Venue.PAPER
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    error: str | None = None

    @property
    def remaining(self) -> Decimal:
        rem = self.size - self.filled_size
        return rem if rem > 0 else ZERO

    @property
    def is_open(self) -> bool:
        return self.status in {OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.PARTIAL}


class Fill(BaseModel):
    fill_id: str = Field(default_factory=lambda: new_id("fill"))
    client_order_id: str
    exchange_order_id: str | None = None
    product_id: str
    side: OrderSide
    price: Decimal
    size: Decimal
    fee: Decimal = ZERO
    bot_id: str | None = None
    slot_index: int | None = None
    venue: Venue = Venue.PAPER
    ts: datetime = Field(default_factory=utcnow)
    liquidity: Literal["MAKER", "TAKER", "UNKNOWN"] = "MAKER"


class OrderIntent(BaseModel):
    kind: IntentKind
    client_order_id: str | None = None
    cancel_client_order_id: str | None = None
    side: OrderSide | None = None
    price: Decimal | None = None
    size: Decimal | None = None
    tif: TimeInForce = TimeInForce.GTC
    slot_index: int | None = None
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class BotRuntime(BaseModel):
    bot_id: str = Field(default_factory=lambda: new_id("bot"))
    config: GridConfig
    status: BotStatus = BotStatus.CREATED
    venue: Venue = Venue.PAPER
    slots: list[GridSlot] = Field(default_factory=list)
    prices: list[Decimal] = Field(default_factory=list)
    inventory: Inventory = Field(default_factory=Inventory)
    trailing: TrailingState = Field(default_factory=TrailingState)
    protection: ProtectionState = Field(default_factory=ProtectionState)
    open_orders: dict[str, Order] = Field(default_factory=dict)
    last_mark: Decimal | None = None
    last_error: str | None = None
    error_count: int = 0
    cooldown_until: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    archived_at: datetime | None = None
    order_seq: int = 0
    peak_equity: Decimal = ZERO
    daily_realized: Decimal = ZERO
    daily_realized_date: str = ""

    @property
    def product_id(self) -> str:
        return self.config.product_id

    @property
    def n_slots(self) -> int:
        return len(self.slots)

    def planned_base(self) -> Decimal:
        return sum((s.target_base for s in self.slots), ZERO)

    def held_base(self) -> Decimal:
        return sum((s.held_base for s in self.slots), ZERO)

    def open_buy_quote(self) -> Decimal:
        total = ZERO
        for order in self.open_orders.values():
            if order.is_open and order.side == OrderSide.BUY:
                total += order.remaining * order.price
        return total

    def open_sell_base(self) -> Decimal:
        total = ZERO
        for order in self.open_orders.values():
            if order.is_open and order.side == OrderSide.SELL:
                total += order.remaining
        return total
