from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

from .errors import SourceError
from .models import DailyBar, SourceSnapshot


SOURCE_NAME = "eastmoney"
ENDPOINT = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
PARAMS = {
    "secid": "0.159993",
    "klt": "101",
    "fqt": "0",
    "lmt": "90",
    "end": "20500101",
    "fields1": "f1,f2,f3,f4,f5,f6",
    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
}


def _decimal(value: Any, field: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise SourceError(f"{SOURCE_NAME}: invalid {field}: {value!r}") from exc


def parse_payload(payload: dict[str, Any]) -> SourceSnapshot:
    if payload.get("rc") != 0:
        raise SourceError(f"{SOURCE_NAME}: API rc={payload.get('rc')!r}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise SourceError(f"{SOURCE_NAME}: missing data object")
    if str(data.get("code")) != "159993" or data.get("market") != 0:
        raise SourceError(f"{SOURCE_NAME}: unexpected security identity")
    rows = data.get("klines")
    if not isinstance(rows, list) or not rows:
        raise SourceError(f"{SOURCE_NAME}: empty klines")

    bars: list[DailyBar] = []
    for row in rows:
        parts = row.split(",") if isinstance(row, str) else []
        if len(parts) != 11:
            raise SourceError(f"{SOURCE_NAME}: expected 11 kline fields")
        try:
            market_date = date.fromisoformat(parts[0])
        except ValueError as exc:
            raise SourceError(f"{SOURCE_NAME}: invalid market date {parts[0]!r}") from exc
        bars.append(
            DailyBar(
                market_date=market_date,
                open=_decimal(parts[1], "open"),
                close=_decimal(parts[2], "close"),
                high=_decimal(parts[3], "high"),
                low=_decimal(parts[4], "low"),
                volume_lots=_decimal(parts[5], "volume"),
                amount=_decimal(parts[6], "amount"),
                change_pct=_decimal(parts[8], "change_pct"),
                change=_decimal(parts[9], "change"),
            )
        )

    bars.sort(key=lambda item: item.market_date)
    return SourceSnapshot(
        source=SOURCE_NAME,
        symbol="159993",
        exchange="SZ",
        name=str(data.get("name") or ""),
        bars=tuple(bars),
        raw_payload=payload,
    )


def fetch(session: requests.Session, timeout: float = 12.0) -> SourceSnapshot:
    try:
        response = session.get(
            ENDPOINT,
            params=PARAMS,
            timeout=timeout,
            headers={"Referer": "https://quote.eastmoney.com/sz159993.html"},
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise SourceError(f"{SOURCE_NAME}: request failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise SourceError(f"{SOURCE_NAME}: response is not a JSON object")
    return parse_payload(payload)
