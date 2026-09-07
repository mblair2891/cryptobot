from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class DemoPair:
    product_id: str
    mid: Decimal
    quote_increment: Decimal
    base_increment: Decimal
    base_min_size: Decimal
    min_market_funds: Decimal
    regime: str  # range | dump | pump
    sigma: float
    kappa: float
    mu: float
    volume_24h: Decimal


def _p(
    product_id: str,
    mid: str,
    *,
    qinc: str,
    binc: str,
    bmin: str,
    min_funds: str,
    regime: str,
    sigma: float,
    kappa: float,
    mu: float,
    vol: str,
) -> DemoPair:
    return DemoPair(
        product_id=product_id,
        mid=Decimal(mid),
        quote_increment=Decimal(qinc),
        base_increment=Decimal(binc),
        base_min_size=Decimal(bmin),
        min_market_funds=Decimal(min_funds),
        regime=regime,
        sigma=sigma,
        kappa=kappa,
        mu=mu,
        volume_24h=Decimal(vol),
    )


# Fictionalized Coinbase-like spot catalog. Specs mimic Advanced Trade constraints.
DEMO_PAIRS: tuple[DemoPair, ...] = (
    _p("BTC-USD", "80000", qinc="0.01", binc="0.00000001", bmin="0.00001", min_funds="1",
       regime="range", sigma=0.012, kappa=0.10, mu=0.0, vol="12000"),
    _p("ETH-USD", "3500", qinc="0.01", binc="0.00000001", bmin="0.0001", min_funds="1",
       regime="dump", sigma=0.018, kappa=0.005, mu=-0.35, vol="18000"),
    _p("SOL-USD", "145", qinc="0.01", binc="0.00000001", bmin="0.001", min_funds="1",
       regime="range", sigma=0.016, kappa=0.08, mu=0.0, vol="9000"),
    _p("LINK-USD", "18.40", qinc="0.01", binc="0.01", bmin="0.01", min_funds="1",
       regime="range", sigma=0.014, kappa=0.09, mu=0.0, vol="4000"),
    _p("XRP-USD", "0.55", qinc="0.0001", binc="0.000001", bmin="1", min_funds="1",
       regime="range", sigma=0.015, kappa=0.07, mu=0.0, vol="25000"),
    _p("DOGE-USD", "0.14", qinc="0.0001", binc="0.1", bmin="1", min_funds="1",
       regime="pump", sigma=0.022, kappa=0.02, mu=0.12, vol="40000"),
    _p("AVAX-USD", "28.50", qinc="0.01", binc="0.001", bmin="0.01", min_funds="1",
       regime="range", sigma=0.017, kappa=0.08, mu=0.0, vol="3500"),
    _p("LTC-USD", "90.00", qinc="0.01", binc="0.00000001", bmin="0.001", min_funds="1",
       regime="range", sigma=0.013, kappa=0.09, mu=0.0, vol="2200"),
    _p("BCH-USD", "400.00", qinc="0.01", binc="0.00000001", bmin="0.001", min_funds="1",
       regime="range", sigma=0.014, kappa=0.08, mu=0.0, vol="1500"),
    _p("ADA-USD", "0.48", qinc="0.0001", binc="0.01", bmin="1", min_funds="1",
       regime="range", sigma=0.016, kappa=0.07, mu=0.0, vol="12000"),
    _p("DOT-USD", "6.50", qinc="0.001", binc="0.001", bmin="0.1", min_funds="1",
       regime="range", sigma=0.015, kappa=0.08, mu=0.0, vol="2800"),
    _p("ARB-USD", "0.85", qinc="0.0001", binc="0.01", bmin="1", min_funds="1",
       regime="dump", sigma=0.02, kappa=0.01, mu=-0.18, vol="5000"),
)
