from __future__ import annotations

import asyncio
import json
import random
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from aethergrid.config import Settings
from aethergrid.domain.enums import OrderSide, OrderStatus, OrderType, TimeInForce, Venue
from aethergrid.domain.models import Balance, Candle, FeeRates, Fill, Order, Product, Ticker
from aethergrid.exchange.base import Exchange, TickerCallback, UserCallback
from aethergrid.logging import get_logger
from aethergrid.market.candles import parse_candles
from aethergrid.market.products import PublicMarket, _product_from_payload
from aethergrid.money import D, ZERO
from aethergrid.security import (
    FORBIDDEN_SDK_METHODS,
    assert_no_withdraw_surface,
    load_coinbase_private_key,
)

log = get_logger("coinbase")


class RateLimited(Exception):
    pass


def _is_retryable(exc: BaseException) -> bool:
    text = str(exc).lower()
    if isinstance(exc, RateLimited):
        return True
    return any(s in text for s in ("429", "503", "502", "timeout", "temporarily", "reset"))


def _status_from_cb(raw: str | None) -> OrderStatus:
    if not raw:
        return OrderStatus.UNKNOWN
    key = raw.upper()
    mapping = {
        "PENDING": OrderStatus.PENDING,
        "OPEN": OrderStatus.OPEN,
        "FILLED": OrderStatus.FILLED,
        "CANCELLED": OrderStatus.CANCELLED,
        "CANCELED": OrderStatus.CANCELLED,
        "EXPIRED": OrderStatus.EXPIRED,
        "FAILED": OrderStatus.FAILED,
        "UNKNOWN_ORDER_STATUS": OrderStatus.UNKNOWN,
    }
    if key in mapping:
        return mapping[key]
    if "PARTIAL" in key:
        return OrderStatus.PARTIAL
    return OrderStatus.UNKNOWN


class CoinbaseExchange(Exchange):
    """Authenticated Coinbase Advanced Trade adapter. Trade-only; no withdraw/transfer."""

    venue = Venue.COINBASE

    def __init__(self, settings: Settings, market: PublicMarket | None = None) -> None:
        self.settings = settings
        self.market = market or PublicMarket()
        self._rest: Any = None
        self._ws_task: asyncio.Task[None] | None = None
        self._user_ws_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._last_call = 0.0
        self._min_interval = 0.05  # ~20 rps conservative
        self._order_index: dict[str, Order] = {}

    def _client(self) -> Any:
        if self._rest is not None:
            return self._rest
        from coinbase.rest import RESTClient

        key_name = self.settings.coinbase_api_key_name.strip()
        secret = load_coinbase_private_key(self.settings)
        if not key_name or not secret:
            raise RuntimeError("Coinbase CDP credentials missing")
        self._rest = RESTClient(
            api_key=key_name,
            api_secret=secret,
            timeout=30,
            rate_limit_headers=True,
        )
        for name in FORBIDDEN_SDK_METHODS:
            if hasattr(self._rest, name):

                def _blocked(*_a: Any, _n: str = name, **_k: Any) -> None:
                    raise PermissionError(f"Blocked non-trade Coinbase method: {_n}")

                setattr(self._rest, name, _blocked)
        return self._rest

    async def _call(self, fn_name: str, *args: Any, **kwargs: Any) -> Any:
        assert_no_withdraw_surface(fn_name)

        @retry(
            retry=retry_if_exception(_is_retryable),
            wait=wait_exponential_jitter(initial=0.4, max=8),
            stop=stop_after_attempt(5),
            reraise=True,
        )
        async def _inner() -> Any:
            await self._pace()
            client = self._client()
            fn = getattr(client, fn_name)
            try:
                result = await asyncio.to_thread(fn, *args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                text = str(exc)
                if "429" in text:
                    await asyncio.sleep(0.5 + random.random())
                    raise RateLimited(text) from exc
                raise
            return result

        return await _inner()

    async def _pace(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            wait = self._min_interval - (now - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = loop.time()

    async def list_products(self) -> list[Product]:
        # Prefer public discovery so paper/live share the universe; auth used as fallback.
        try:
            return await self.market.list_products()
        except Exception:  # noqa: BLE001
            raw = await self._call("get_products")
            payload = raw.to_dict() if hasattr(raw, "to_dict") else raw
            items = payload.get("products") or []
            return [_product_from_payload(x) for x in items]

    async def get_product(self, product_id: str) -> Product:
        try:
            return await self.market.get_product(product_id)
        except Exception:  # noqa: BLE001
            raw = await self._call("get_product", product_id)
            payload = raw.to_dict() if hasattr(raw, "to_dict") else raw
            return _product_from_payload(payload)

    async def get_ticker(self, product_id: str) -> Ticker:
        return await self.market.get_ticker(product_id)

    async def get_candles(
        self,
        product_id: str,
        granularity: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 300,
    ) -> list[Candle]:
        try:
            return await self.market.get_candles(product_id, granularity, start, end, limit)
        except Exception:  # noqa: BLE001
            end = end or datetime.now(UTC)
            start = start or (end - timedelta(hours=limit))
            raw = await self._call(
                "get_candles",
                product_id,
                start=str(int(start.timestamp())),
                end=str(int(end.timestamp())),
                granularity=granularity,
            )
            payload = raw.to_dict() if hasattr(raw, "to_dict") else raw
            return parse_candles(product_id, payload.get("candles") or [], granularity)

    async def get_balances(self) -> list[Balance]:
        raw = await self._call("get_accounts", limit=250)
        payload = raw.to_dict() if hasattr(raw, "to_dict") else raw
        accounts = payload.get("accounts") or []
        out: list[Balance] = []
        for acc in accounts:
            avail = acc.get("available_balance") or {}
            hold = acc.get("hold") or {}
            ccy = str(acc.get("currency") or avail.get("currency") or "")
            if not ccy:
                continue
            out.append(
                Balance(
                    currency=ccy,
                    available=D(avail.get("value") or 0),
                    hold=D(hold.get("value") or 0),
                )
            )
        return out

    async def fee_rates(self) -> FeeRates:
        try:
            raw = await self._call("get_transaction_summary")
            payload = raw.to_dict() if hasattr(raw, "to_dict") else raw
            fee_tier = payload.get("fee_tier") or payload
            maker = D(fee_tier.get("maker_fee_rate") or 0.006)
            taker = D(fee_tier.get("taker_fee_rate") or 0.008)
            return FeeRates(maker=maker, taker=taker)
        except Exception as exc:  # noqa: BLE001
            log.warning("fee_rates_fallback", error=str(exc))
            return FeeRates()

    async def preview_limit(
        self,
        *,
        product_id: str,
        side: OrderSide,
        price: Decimal,
        size: Decimal,
    ) -> dict[str, object]:
        try:
            raw = await self._call(
                "preview_limit_order_gtc",
                product_id=product_id,
                side=side.value,
                base_size=str(size),
                limit_price=str(price),
            )
            payload = raw.to_dict() if hasattr(raw, "to_dict") else raw
            return dict(payload)
        except Exception as exc:  # noqa: BLE001
            log.warning("preview_failed", error=str(exc), product_id=product_id)
            return {"errs": [str(exc)]}

    async def place_limit(
        self,
        *,
        client_order_id: str,
        product_id: str,
        side: OrderSide,
        price: Decimal,
        size: Decimal,
        tif: TimeInForce = TimeInForce.GTC,
        post_only: bool = False,
    ) -> Order:
        if tif == TimeInForce.IOC:
            return await self.place_ioc(
                client_order_id=client_order_id,
                product_id=product_id,
                side=side,
                size=size,
                price=price,
            )
        preview = await self.preview_limit(
            product_id=product_id, side=side, price=price, size=size
        )
        errs = preview.get("errs") or preview.get("errors") or []
        if errs:
            raise RuntimeError(f"preview rejected: {errs}")

        method = "limit_order_gtc_buy" if side == OrderSide.BUY else "limit_order_gtc_sell"
        raw = await self._call(
            method,
            client_order_id=client_order_id,
            product_id=product_id,
            base_size=str(size),
            limit_price=str(price),
            post_only=post_only,
        )
        return self._order_from_create(raw, client_order_id, product_id, side, price, size, tif)

    async def place_ioc(
        self,
        *,
        client_order_id: str,
        product_id: str,
        side: OrderSide,
        size: Decimal,
        price: Decimal | None = None,
    ) -> Order:
        if price is None:
            ticker = await self.get_ticker(product_id)
            price = ticker.bid if side == OrderSide.SELL else ticker.ask
        method = "limit_order_ioc_buy" if side == OrderSide.BUY else "limit_order_ioc_sell"
        raw = await self._call(
            method,
            client_order_id=client_order_id,
            product_id=product_id,
            base_size=str(size),
            limit_price=str(price),
        )
        return self._order_from_create(
            raw, client_order_id, product_id, side, price, size, TimeInForce.IOC
        )

    def _order_from_create(
        self,
        raw: Any,
        client_order_id: str,
        product_id: str,
        side: OrderSide,
        price: Decimal,
        size: Decimal,
        tif: TimeInForce,
    ) -> Order:
        payload = raw.to_dict() if hasattr(raw, "to_dict") else (raw if isinstance(raw, dict) else {})
        success = payload.get("success", True)
        success_resp = payload.get("success_response") or {}
        error_resp = payload.get("error_response") or {}
        order_id = success_resp.get("order_id") or payload.get("order_id")
        order = Order(
            client_order_id=client_order_id,
            product_id=product_id,
            side=side,
            price=price,
            size=size,
            tif=tif,
            order_type=OrderType.LIMIT,
            venue=Venue.COINBASE,
            exchange_order_id=str(order_id) if order_id else None,
            status=OrderStatus.OPEN if success else OrderStatus.FAILED,
            error=str(error_resp) if error_resp else None,
        )
        if not success:
            raise RuntimeError(order.error or "create_order failed")
        self._order_index[client_order_id] = order
        if order.exchange_order_id:
            self._order_index[order.exchange_order_id] = order
        return order

    async def cancel(self, order_id: str) -> None:
        await self.cancel_many([order_id])

    async def cancel_many(self, order_ids: list[str]) -> None:
        if not order_ids:
            return
        # Coinbase cancel uses exchange order ids. Map client ids if needed.
        mapped: list[str] = []
        for oid in order_ids:
            order = self._order_index.get(oid)
            mapped.append(order.exchange_order_id or oid if order else oid)
        # Batch in chunks of 100.
        for i in range(0, len(mapped), 100):
            chunk = mapped[i : i + 100]
            await self._call("cancel_orders", order_ids=chunk)

    async def get_order(self, order_id: str) -> Order | None:
        mapped = order_id
        cached = self._order_index.get(order_id)
        if cached and cached.exchange_order_id:
            mapped = cached.exchange_order_id
        try:
            raw = await self._call("get_order", mapped)
        except Exception as exc:  # noqa: BLE001
            log.warning("get_order_failed", order_id=order_id, error=str(exc))
            return cached
        payload = raw.to_dict() if hasattr(raw, "to_dict") else raw
        order_raw = payload.get("order") or payload
        return self._parse_order(order_raw)

    async def list_open_orders(self, product_id: str | None = None) -> list[Order]:
        kwargs: dict[str, Any] = {"order_status": ["OPEN", "PENDING"]}
        if product_id:
            kwargs["product_id"] = product_id
        raw = await self._call("list_orders", **kwargs)
        payload = raw.to_dict() if hasattr(raw, "to_dict") else raw
        orders = [self._parse_order(o) for o in payload.get("orders") or []]
        for o in orders:
            self._order_index[o.client_order_id] = o
            if o.exchange_order_id:
                self._order_index[o.exchange_order_id] = o
        return orders

    async def list_fills(
        self,
        *,
        product_id: str | None = None,
        order_id: str | None = None,
    ) -> list[Fill]:
        kwargs: dict[str, Any] = {}
        if product_id:
            kwargs["product_id"] = product_id
        if order_id:
            kwargs["order_id"] = order_id
        raw = await self._call("get_fills", **kwargs)
        payload = raw.to_dict() if hasattr(raw, "to_dict") else raw
        fills: list[Fill] = []
        for item in payload.get("fills") or []:
            fills.append(
                Fill(
                    fill_id=str(item.get("entry_id") or item.get("trade_id") or item.get("fill_id")),
                    client_order_id=str(item.get("client_order_id") or ""),
                    exchange_order_id=str(item.get("order_id") or ""),
                    product_id=str(item.get("product_id") or product_id or ""),
                    side=OrderSide.BUY if str(item.get("side", "")).upper() == "BUY" else OrderSide.SELL,
                    price=D(item.get("price") or 0),
                    size=D(item.get("size") or item.get("base_size") or 0),
                    fee=D(item.get("commission") or item.get("fee") or 0),
                    venue=Venue.COINBASE,
                    liquidity="MAKER" if str(item.get("trade_type", "")).upper() == "FILL" else "UNKNOWN",
                    ts=_parse_ts(item.get("trade_time") or item.get("created_time")),
                )
            )
        return fills

    def _parse_order(self, raw: dict[str, Any]) -> Order:
        cfg = raw.get("order_configuration") or {}
        limit = cfg.get("limit_limit_gtc") or cfg.get("limit_limit_ioc") or {}
        price = D(limit.get("limit_price") or raw.get("average_filled_price") or 0)
        size = D(limit.get("base_size") or raw.get("filled_size") or 0)
        filled = D(raw.get("filled_size") or 0)
        status = _status_from_cb(raw.get("status"))
        if filled > 0 and filled < size and status == OrderStatus.OPEN:
            status = OrderStatus.PARTIAL
        side_raw = str(raw.get("side") or "BUY").upper()
        tif = TimeInForce.IOC if "ioc" in json.dumps(cfg).lower() else TimeInForce.GTC
        return Order(
            client_order_id=str(raw.get("client_order_id") or raw.get("order_id")),
            exchange_order_id=str(raw.get("order_id") or ""),
            product_id=str(raw.get("product_id") or ""),
            side=OrderSide.BUY if side_raw == "BUY" else OrderSide.SELL,
            price=price,
            size=size if size > 0 else filled,
            filled_size=filled,
            filled_value=D(raw.get("filled_value") or 0),
            fee=D(raw.get("total_fees") or 0),
            status=status,
            tif=tif,
            venue=Venue.COINBASE,
        )

    async def subscribe_user(self, callback: UserCallback) -> None:
        if self._user_ws_task and not self._user_ws_task.done():
            return
        self._user_ws_task = asyncio.create_task(self._user_ws_loop(callback), name="cb-user-ws")

    async def _user_ws_loop(self, callback: UserCallback) -> None:
        from coinbase.websocket import WSClient

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str] = asyncio.Queue()

        def on_message(msg: str) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, msg)

        key_name = self.settings.coinbase_api_key_name.strip()
        secret = load_coinbase_private_key(self.settings)
        ws = WSClient(api_key=key_name, api_secret=secret, on_message=on_message)

        def _run() -> None:
            try:
                ws.open()
                ws.subscribe([], ["heartbeats", "user"])
                while True:
                    ws.sleep_with_exception_check(1)
            except Exception as exc:  # noqa: BLE001
                log.warning("user_ws_thread_exit", error=str(exc))

        thread = threading.Thread(target=_run, name="cb-user-ws", daemon=True)
        thread.start()
        log.info("user_ws_started")
        try:
            while True:
                raw = await queue.get()
                try:
                    payload = json.loads(raw) if isinstance(raw, str) else raw
                except json.JSONDecodeError:
                    continue
                if payload.get("channel") != "user":
                    continue
                for event in payload.get("events") or []:
                    for item in event.get("orders") or []:
                        order = self._parse_order(item)
                        maybe = callback(order)
                        if hasattr(maybe, "__await__"):
                            await maybe  # type: ignore[misc]
                        if str(item.get("status", "")).upper() == "FILLED" and order.exchange_order_id:
                            fills = await self.list_fills(order_id=order.exchange_order_id)
                            for fill in fills:
                                maybe_f = callback(fill)
                                if hasattr(maybe_f, "__await__"):
                                    await maybe_f  # type: ignore[misc]
        except asyncio.CancelledError:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass
            raise

    async def close(self) -> None:
        for task in (self._ws_task, self._user_ws_task):
            if task:
                task.cancel()
        await self.market.close()


def _parse_ts(value: Any) -> datetime:
    if not value:
        return datetime.now(UTC)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value)
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except ValueError:
        try:
            return datetime.fromtimestamp(float(text), tz=UTC)
        except ValueError:
            return datetime.now(UTC)


async def reconcile_orders(
    exchange: Exchange,
    local_open: dict[str, Order],
) -> dict[str, Any]:
    """Compare local working orders with the venue. Used on startup and every N seconds."""
    remote = await exchange.list_open_orders()
    remote_by_client = {o.client_order_id: o for o in remote}
    remote_by_ex = {o.exchange_order_id: o for o in remote if o.exchange_order_id}
    missing_on_venue: list[str] = []
    unknown_on_venue: list[str] = []
    status_mismatch: list[str] = []
    filled_elsewhere: list[str] = []

    for cid, local in local_open.items():
        if not local.is_open:
            continue
        remote_order = remote_by_client.get(cid)
        if not remote_order and local.exchange_order_id:
            remote_order = remote_by_ex.get(local.exchange_order_id)
        if not remote_order:
            fetched = await exchange.get_order(local.exchange_order_id or cid)
            if fetched and fetched.status == OrderStatus.FILLED:
                filled_elsewhere.append(cid)
            else:
                missing_on_venue.append(cid)
            continue
        if fetched_status_changed(local, remote_order):
            status_mismatch.append(cid)

    local_ids = set(local_open)
    local_ex = {o.exchange_order_id for o in local_open.values() if o.exchange_order_id}
    for remote_order in remote:
        if remote_order.client_order_id.startswith("ag_"):
            if (
                remote_order.client_order_id not in local_ids
                and remote_order.exchange_order_id not in local_ex
            ):
                unknown_on_venue.append(remote_order.client_order_id)

    return {
        "missing_on_venue": missing_on_venue,
        "unknown_on_venue": unknown_on_venue,
        "status_mismatch": status_mismatch,
        "filled_elsewhere": filled_elsewhere,
        "remote_open": len(remote),
        "local_open": len(local_open),
    }


def fetched_status_changed(local: Order, remote: Order) -> bool:
    if local.status != remote.status:
        return True
    return local.filled_size != remote.filled_size
