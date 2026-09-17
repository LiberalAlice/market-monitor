from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Any


def _number(value: Decimal | None) -> float | int | None:
    if value is None:
        return None
    if value == value.to_integral_value():
        return int(value)
    return float(value)


@dataclass(frozen=True)
class DailyBar:
    market_date: date
    open: Decimal
    close: Decimal
    high: Decimal
    low: Decimal
    volume_lots: Decimal
    amount: Decimal | None
    change: Decimal | None = None
    change_pct: Decimal | None = None

    def with_previous_close(self, previous_close: Decimal | None) -> "DailyBar":
        if previous_close is None or previous_close == 0:
            return self
        change = self.close - previous_close
        change_pct = change / previous_close * Decimal("100")
        return replace(self, change=change, change_pct=change_pct)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "date": self.market_date.isoformat(),
            "open": _number(self.open),
            "close": _number(self.close),
            "high": _number(self.high),
            "low": _number(self.low),
            "previous_close": (
                _number(self.close - self.change) if self.change is not None else None
            ),
            "change": _number(self.change),
            "change_pct": _number(self.change_pct),
            "volume": _number(self.volume_lots),
            "volume_unit": "lot",
            "amount": _number(self.amount),
            "amount_currency": "CNY",
        }


@dataclass(frozen=True)
class SourceSnapshot:
    source: str
    symbol: str
    exchange: str
    name: str
    bars: tuple[DailyBar, ...]
    raw_payload: dict[str, Any]

    @property
    def latest(self) -> DailyBar:
        return self.bars[-1]

    def bar_on(self, target: date) -> DailyBar | None:
        return next((bar for bar in self.bars if bar.market_date == target), None)


@dataclass(frozen=True)
class CollectionResult:
    status: str
    verification: str
    target_date: date
    market_date: date | None
    latest_payload: dict[str, Any]
    history_payload: dict[str, Any] | None

    @property
    def ok(self) -> bool:
        return self.status == "ok"
