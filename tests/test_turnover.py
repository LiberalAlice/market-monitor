from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from market_fetch.errors import SourceError, ValidationError
from market_fetch.trading_calendar import is_trading_day, recent_trading_days
from market_fetch.turnover import (
    TurnoverRow,
    build_payload,
    compute_summary,
    fetch_history_with_cache,
    fetch_official_row,
    parse_sse_amount,
    parse_szse_amount,
    validate_history,
)


TARGET = date(2026, 9, 17)
GENERATED_AT = datetime(2026, 9, 17, 16, 20, tzinfo=timezone(timedelta(hours=8)))
DATES = recent_trading_days(TARGET, 30)


def make_rows(count: int = 30, *, base: int = 1_000_000_000_000) -> list[TurnoverRow]:
    dates = DATES[-count:]
    return [
        TurnoverRow(day, base // 2 + index, base // 2 + index)
        for index, day in enumerate(dates)
    ]


def test_30_normal_trading_days_are_complete() -> None:
    payload = build_payload(TARGET, DATES, make_rows(), GENERATED_AT)
    assert payload["status"] == "ok"
    assert payload["coverage"]["available_trading_days"] == 30
    assert payload["missing_dates"] == []


def test_only_19_trading_days_has_no_valid_20_day_average() -> None:
    payload = build_payload(TARGET, DATES, make_rows(19), GENERATED_AT)
    assert payload["status"] == "incomplete"
    assert payload["coverage"]["recent_20_complete"] is False
    assert payload["summary"]["avg_20d"] is None
    assert payload["summary"]["avg_5d_vs_20d_pct"] is None


def test_missing_trading_day_is_reported() -> None:
    rows = make_rows()
    missing = rows.pop(-7).market_date
    payload = build_payload(TARGET, DATES, rows, GENERATED_AT)
    assert payload["status"] == "incomplete"
    assert missing.isoformat() in payload["missing_dates"]


def test_duplicate_date_is_rejected() -> None:
    rows = make_rows()
    with pytest.raises(ValidationError, match="duplicate"):
        validate_history([*rows, rows[-1]])


def test_weekend_is_not_a_trading_day() -> None:
    assert is_trading_day(date(2026, 9, 12)) is False
    assert all(day.weekday() < 5 for day in DATES)


def test_negative_amount_is_rejected() -> None:
    with pytest.raises(ValidationError, match="negative"):
        validate_history([TurnoverRow(TARGET, -1, 2)])


def test_shanghai_success_and_shenzhen_failure_rejects_day() -> None:
    def shanghai(_session, _target):
        return 100

    def shenzhen(_session, _target):
        raise SourceError("unavailable")

    with pytest.raises(SourceError, match="Shenzhen"):
        fetch_official_row(object(), TARGET, shanghai, shenzhen)


def test_primary_failure_uses_validated_cache_fallback() -> None:
    cached = make_rows()

    def failed(_day):
        raise SourceError("temporary failure")

    outcome = fetch_history_with_cache(DATES, failed, cached)
    assert list(outcome.rows) == cached
    assert list(outcome.cache_dates) == DATES
    assert len(outcome.errors) == 30


def test_5_day_average() -> None:
    rows = [TurnoverRow(day, 0, value) for day, value in zip(DATES, range(1, 31))]
    assert compute_summary(rows, DATES)["avg_5d"] == 28


def test_10_day_average() -> None:
    rows = [TurnoverRow(day, 0, value) for day, value in zip(DATES, range(1, 31))]
    assert compute_summary(rows, DATES)["avg_10d"] == 25.5


def test_20_day_average() -> None:
    rows = [TurnoverRow(day, 0, value) for day, value in zip(DATES, range(1, 31))]
    assert compute_summary(rows, DATES)["avg_20d"] == 20.5


def test_5_day_vs_20_day_percentage_uses_required_formula() -> None:
    rows = [TurnoverRow(day, 0, value) for day, value in zip(DATES, range(1, 31))]
    assert compute_summary(rows, DATES)["avg_5d_vs_20d_pct"] == 36.59


@pytest.mark.parametrize(
    ("amount", "expected"),
    [(1_799_999_999_999, True), (1_800_000_000_000, False)],
)
def test_1_8_trillion_threshold_is_strict(amount: int, expected: bool) -> None:
    rows = [TurnoverRow(day, 0, amount) for day in DATES]
    assert compute_summary(rows, DATES)["avg_10d_below_1_8t"] is expected


@pytest.mark.parametrize(
    ("amount", "expected"),
    [(2_500_000_000_000, False), (2_500_000_000_001, True)],
)
def test_2_5_trillion_threshold_is_strict(amount: int, expected: bool) -> None:
    rows = [TurnoverRow(day, 0, amount) for day in DATES]
    assert compute_summary(rows, DATES)["current_above_2_5t"] is expected


def test_shanghai_plus_shenzhen_equals_total() -> None:
    row = TurnoverRow(TARGET, 869_769_000_000, 955_201_000_000)
    assert row.total_amount == 1_824_970_000_000


def test_sse_parser_sums_only_main_a_and_star() -> None:
    payload = {
        "result": [
            {"PRODUCT_CODE": "01", "TRADE_DATE": "20260917", "TRADE_AMT": "6084.74"},
            {"PRODUCT_CODE": "02", "TRADE_DATE": "20260917", "TRADE_AMT": "0.62"},
            {"PRODUCT_CODE": "03", "TRADE_DATE": "20260917", "TRADE_AMT": "2612.95"},
            {"PRODUCT_CODE": "17", "TRADE_DATE": "20260917", "TRADE_AMT": "8698.32"},
        ]
    }
    assert parse_sse_amount(payload, TARGET) == 869_769_000_000


def test_szse_parser_sums_only_main_a_and_chinext_a() -> None:
    payload = [
        {
            "metadata": {
                "catalogid": "1803_sczm",
                "tabkey": "tab1",
                "subname": "2026-09-17",
                "cols": {"cjje": "\u6210\u4ea4\u91d1\u989d<br>(\u4ebf\u5143)"},
            },
            "error": None,
            "data": [
                {"lbmc": "\u80a1\u7968", "cjje": "9,552.41"},
                {"lbmc": "&nbsp;\u4e3b\u677fA\u80a1", "cjje": "5,081.56"},
                {"lbmc": "&nbsp;\u4e3b\u677fB\u80a1", "cjje": "0.39"},
                {"lbmc": "&nbsp;\u521b\u4e1a\u677fA\u80a1", "cjje": "4,470.45"},
            ],
        }
    ]
    assert parse_szse_amount(payload, TARGET) == 955_201_000_000
