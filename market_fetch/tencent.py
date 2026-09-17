from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

from .errors import SourceError
from .models import DailyBar, SourceSnapshot


SOURCE_NAME = "tencent"
ENDPOINT = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
PARAMS = {"param": "sz159993,day,,,90,"}


def _decimal(value: Any, field: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise SourceError(f"{SOURCE_NAME}: invalid {field}: {value!r}") from exc


def parse_payload(payload: dict[str, Any]) -> SourceSnapshot:
    if payload.get("code") != 0:
        raise SourceError(f"{SOURCE_NAME}: API code={payload.get('code')!r}")
    security = (payload.get("data") or {}).get("sz159993")
    if not isinstance(security, dict):
        raise SourceError(f"{SOURCE_NAME}: missing sz159993 data")
    rows = security.get("day")
    quote_values = ((security.get("qt") or {}).get("sz159993"))
    if not isinstance(rows, list) or not rows:
        raise SourceError(f"{SOURCE_NAME}: empty daily klines")
    if not isinstance(quote_values, list) or len(quote_values) < 36:
        raise SourceError(f"{SOURCE_NAME}: malformed quote array")
    if str(quote_values[2]) != "159993":
        raise SourceError(f"{SOURCE_NAME}: unexpected security identity")

    quote_date_text = str(quote_values[30])[:8]
    try:
        quote_date = date(
            int(quote_date_text[:4]), int(quote_date_text[4:6]), int(quote_date_text[6:8])
        )
    except (ValueError, TypeError) as exc:
        raise SourceError(f"{SOURCE_NAME}: invalid quote timestamp") from exc

    amount_parts = str(quote_values[35]).split("/")
    latest_amount = _decimal(amount_parts[2], "amount") if len(amount_parts) == 3 else None
    previous_close = _decimal(quote_values[4], "previous_close")

    bars: list[DailyBar] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            raise SourceError(f"{SOURCE_NAME}: malformed daily kline")
        try:
            market_date = date.fromisoformat(str(row[0]))
        except ValueError as exc:
            raise SourceError(f"{SOURCE_NAME}: invalid market date {row[0]!r}") from exc
        bar = DailyBar(
            market_date=market_date,
            open=_decimal(row[1], "open"),
            close=_decimal(row[2], "close"),
            high=_decimal(row[3], "high"),
            low=_decimal(row[4], "low"),
            volume_lots=_decimal(row[5], "volume"),
            amount=latest_amount if market_date == quote_date else None,
        )
        if market_date == quote_date:
            bar = replace(
                bar,
                change=_decimal(quote_values[31], "change"),
                change_pct=_decimal(quote_values[32], "change_pct"),
            )
        elif bars:
            bar = bar.with_previous_close(bars[-1].close)
        bars.append(bar)

    bars.sort(key=lambda item: item.market_date)
    return SourceSnapshot(
        source=SOURCE_NAME,
        symbol="159993",
        exchange="SZ",
        name=str(quote_values[1]),
        bars=tuple(bars),
        raw_payload=payload,
    )


def fetch(session: requests.Session, timeout: float = 12.0) -> SourceSnapshot:
    try:
        response = session.get(
            ENDPOINT,
            params=PARAMS,
            timeout=timeout,
            headers={"Referer": "https://gu.qq.com/sz159993/gp"},
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise SourceError(f"{SOURCE_NAME}: request failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise SourceError(f"{SOURCE_NAME}: response is not a JSON object")
    return parse_payload(payload)
