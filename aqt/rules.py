from __future__ import annotations

from dataclasses import dataclass

from .models import Bar


LOT_SIZE = 100


@dataclass(frozen=True)
class FeeModel:
    commission_rate: float = 0.00025
    min_commission: float = 5.0
    stamp_tax_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001

    def fees(self, side: str, shares: int, price: float) -> float:
        if shares <= 0:
            return 0.0
        notional = shares * price
        commission = max(self.min_commission, notional * self.commission_rate)
        stamp_tax = notional * self.stamp_tax_rate if side.lower() == "sell" else 0.0
        transfer_fee = notional * self.transfer_fee_rate
        return commission + stamp_tax + transfer_fee


def round_lot(shares: float, lot_size: int = LOT_SIZE) -> int:
    if shares <= 0:
        return 0
    return int(shares // lot_size) * lot_size


def can_buy(bar: Bar) -> bool:
    if bar.paused or bar.open <= 0:
        return False
    return bar.open < bar.limit_up - 1e-6


def can_sell(bar: Bar) -> bool:
    if bar.paused or bar.open <= 0:
        return False
    return bar.open > bar.limit_down + 1e-6

