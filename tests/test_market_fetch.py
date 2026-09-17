from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
import requests

from market_fetch import collector, eastmoney
from market_fetch.collector import build_result
from market_fetch.errors import SourceError, ValidationError
from market_fetch.models import DailyBar, SourceSnapshot
from market_fetch.storage import read_json, write_json_atomic
from market_fetch.trading_calendar import resolve_default_target
from market_fetch.validate import validate_bar


TARGET = date(2026, 9, 17)
GENERATED_AT = datetime(2026, 9, 17, 16, 30, tzinfo=timezone.utc)


def make_bar(day: date = TARGET, close: str = "1.124") -> DailyBar:
    return DailyBar(
        market_date=day,
        open=Decimal("1.131"),
        close=Decimal(close),
        high=Decimal("1.136"),
        low=Decimal("1.123"),
        volume_lots=Decimal("338580"),
        amount=Decimal("38160260"),
        change=Decimal("-0.013"),
        change_pct=Decimal("-1.14"),
    )


def make_snapshot(source: str, bar: DailyBar | None = None) -> SourceSnapshot:
    selected = bar or make_bar()
    return SourceSnapshot(
        source=source,
        symbol="159993",
        exchange="SZ",
        name="证券ETF鹏华",
        bars=(selected,),
        raw_payload={"source": source},
    )


def test_normal_return_for_target_date() -> None:
    result = build_result(
        TARGET,
        [make_snapshot("eastmoney"), make_snapshot("tencent")],
        {},
        GENERATED_AT,
    )
    assert result.ok
    assert result.verification == "cross_checked"
    assert result.latest_payload["quote"]["close"] == 1.124


def test_only_previous_day_is_stale() -> None:
    previous = make_bar(date(2026, 9, 16), "1.137")
    result = build_result(
        TARGET,
        [make_snapshot("eastmoney", previous), make_snapshot("tencent", previous)],
        {},
        GENERATED_AT,
    )
    assert result.status == "stale"
    assert result.market_date == date(2026, 9, 16)


def test_empty_eastmoney_payload_is_rejected() -> None:
    payload = {
        "rc": 0,
        "data": {"code": "159993", "market": 0, "name": "x", "klines": []},
    }
    with pytest.raises(SourceError, match="empty klines"):
        eastmoney.parse_payload(payload)


def test_http_failure_is_wrapped() -> None:
    class FailedSession:
        def get(self, *args, **kwargs):
            raise requests.Timeout("timeout")

    with pytest.raises(SourceError, match="request failed"):
        eastmoney.fetch(FailedSession())


def test_invalid_close_is_rejected() -> None:
    with pytest.raises(ValidationError, match="positive"):
        validate_bar(make_bar(close="0"))


def test_eastmoney_failure_and_fallback_success() -> None:
    result = build_result(
        TARGET,
        [make_snapshot("tencent")],
        {"eastmoney": "timeout"},
        GENERATED_AT,
    )
    assert result.ok
    assert result.verification == "fallback_only"
    assert result.latest_payload["selected_source"] == "tencent"


def test_initial_fallback_does_not_publish_invented_history(monkeypatch, tmp_path) -> None:
    def failed_primary(_session):
        raise SourceError("timeout")

    monkeypatch.setattr(collector.eastmoney, "fetch", failed_primary)
    monkeypatch.setattr(collector.tencent, "fetch", lambda _session: make_snapshot("tencent"))
    result = collector.collect(TARGET, tmp_path, GENERATED_AT)
    assert result.status == "degraded"
    assert result.latest_payload["history_status"] == "unavailable"
    assert not (tmp_path / "public" / "history" / "159993.json").exists()


def test_fallback_appends_to_existing_complete_history(monkeypatch, tmp_path) -> None:
    def failed_primary(_session):
        raise SourceError("timeout")

    history_path = tmp_path / "public" / "history" / "159993.json"
    items = []
    for offset in range(30):
        item = make_bar(date(2026, 7, 1) + timedelta(days=offset)).to_public_dict()
        items.append(item)
    write_json_atomic(history_path, {"items": items, "source": "eastmoney"})
    monkeypatch.setattr(collector.eastmoney, "fetch", failed_primary)
    monkeypatch.setattr(collector.tencent, "fetch", lambda _session: make_snapshot("tencent"))
    result = collector.collect(TARGET, tmp_path, GENERATED_AT)
    published = read_json(history_path)
    assert result.ok
    assert result.latest_payload["history_status"] == "complete"
    assert published["source"] == "eastmoney+tencent-fallback"
    assert published["items"][-1]["date"] == TARGET.isoformat()


def test_two_sources_with_conflicting_close_are_rejected() -> None:
    result = build_result(
        TARGET,
        [make_snapshot("eastmoney"), make_snapshot("tencent", make_bar(close="1.130"))],
        {},
        GENERATED_AT,
    )
    assert result.status == "conflict"
    assert result.latest_payload["quote"] is None
    assert set(result.latest_payload["candidates"]) == {"eastmoney", "tencent"}


def test_asia_shanghai_cutoff_is_used() -> None:
    # 07:04 UTC is 15:04 in Shanghai, so the prior trading day is expected.
    before_cutoff = datetime(2026, 9, 17, 7, 4, tzinfo=timezone.utc)
    at_cutoff = datetime(2026, 9, 17, 7, 5, tzinfo=timezone.utc)
    assert resolve_default_target(before_cutoff) == date(2026, 9, 16)
    assert resolve_default_target(at_cutoff) == TARGET
