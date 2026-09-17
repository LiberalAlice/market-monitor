from __future__ import annotations

from decimal import Decimal

from .errors import ValidationError
from .models import DailyBar, SourceSnapshot


def validate_bar(bar: DailyBar) -> None:
    if bar.close <= 0 or bar.open <= 0:
        raise ValidationError("open and close must be positive")
    if bar.high < bar.low:
        raise ValidationError("high must be greater than or equal to low")
    if bar.high < max(bar.open, bar.close):
        raise ValidationError("high is below open or close")
    if bar.low > min(bar.open, bar.close):
        raise ValidationError("low is above open or close")
    if bar.volume_lots < 0:
        raise ValidationError("volume must not be negative")
    if bar.amount is not None and bar.amount < 0:
        raise ValidationError("amount must not be negative")


def validate_snapshot(snapshot: SourceSnapshot) -> None:
    if snapshot.symbol != "159993" or snapshot.exchange != "SZ":
        raise ValidationError(f"{snapshot.source}: wrong security")
    if not snapshot.bars:
        raise ValidationError(f"{snapshot.source}: no bars")
    dates = [bar.market_date for bar in snapshot.bars]
    if dates != sorted(set(dates)):
        raise ValidationError(f"{snapshot.source}: dates are not unique and ascending")
    for bar in snapshot.bars:
        validate_bar(bar)


def prices_match(left: DailyBar, right: DailyBar, tolerance: Decimal = Decimal("0.001")) -> bool:
    return abs(left.close - right.close) <= tolerance
