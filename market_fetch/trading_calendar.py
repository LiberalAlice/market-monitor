from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .errors import CalendarCoverageError


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
OFFICIAL_CLOSE_READY = time(15, 5)

# Official SSE/SZSE holiday closures. Weekends are handled separately.
# Source: https://www.sse.com.cn/disclosure/dealinstruc/closed/
HOLIDAYS: dict[int, set[date]] = {
    2025: {
        date(2025, 1, 1),
        *{date(2025, 1, day) for day in range(28, 32)},
        *{date(2025, 2, day) for day in range(1, 5)},
        date(2025, 4, 4),
        *{date(2025, 5, day) for day in range(1, 6)},
        date(2025, 6, 2),
        *{date(2025, 10, day) for day in range(1, 9)},
    },
    2026: {
        date(2026, 1, 1),
        date(2026, 1, 2),
        *{date(2026, 2, day) for day in range(16, 24)},
        date(2026, 4, 6),
        *{date(2026, 5, day) for day in range(1, 6)},
        date(2026, 6, 19),
        date(2026, 9, 25),
        *{date(2026, 10, day) for day in range(1, 8)},
    },
}


def is_trading_day(value: date) -> bool:
    if value.year not in HOLIDAYS:
        raise CalendarCoverageError(
            f"exchange calendar does not cover {value.year}; update HOLIDAYS first"
        )
    return value.weekday() < 5 and value not in HOLIDAYS[value.year]


def latest_trading_day(value: date) -> date:
    candidate = value
    for _ in range(15):
        if is_trading_day(candidate):
            return candidate
        candidate -= timedelta(days=1)
    raise CalendarCoverageError(f"could not resolve a trading day near {value.isoformat()}")


def resolve_default_target(now: datetime | None = None) -> date:
    local_now = (now or datetime.now(SHANGHAI_TZ)).astimezone(SHANGHAI_TZ)
    today = local_now.date()
    if not is_trading_day(today):
        return latest_trading_day(today - timedelta(days=1))
    if local_now.time().replace(tzinfo=None) < OFFICIAL_CLOSE_READY:
        return latest_trading_day(today - timedelta(days=1))
    return today


def ensure_explicit_target_is_ready(target: date, now: datetime | None = None) -> None:
    local_now = (now or datetime.now(SHANGHAI_TZ)).astimezone(SHANGHAI_TZ)
    if target > local_now.date():
        raise CalendarCoverageError("target date is in the future")
    if not is_trading_day(target):
        raise CalendarCoverageError(f"{target.isoformat()} is not an A-share trading day")
    if target == local_now.date() and local_now.time().replace(tzinfo=None) < OFFICIAL_CLOSE_READY:
        raise CalendarCoverageError(
            f"{target.isoformat()} official close is not considered ready before 15:05 Asia/Shanghai"
        )
