from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable

import requests

from .errors import SourceError, ValidationError
from .http import build_session
from .storage import read_json, write_json_atomic
from .trading_calendar import SHANGHAI_TZ, recent_trading_days


SSE_ENDPOINT = "https://query.sse.com.cn/commonQuery.do"
SSE_PAGE = "https://www.sse.com.cn/market/stockdata/overview/day/"
SZSE_ENDPOINT = "https://www.szse.cn/api/report/ShowReport/data"
SZSE_PAGE = "https://www.szse.cn/market/overview/"
HISTORY_DAYS = 30
YUAN_PER_YI = Decimal("100000000")
ROUNDING_TOLERANCE_YI = Decimal("0.02")


@dataclass(frozen=True)
class TurnoverRow:
    market_date: date
    shanghai_amount: int
    shenzhen_amount: int

    @property
    def total_amount(self) -> int:
        return self.shanghai_amount + self.shenzhen_amount

    def to_public_dict(self) -> dict[str, int | str]:
        return {
            "date": self.market_date.isoformat(),
            "shanghai_amount": self.shanghai_amount,
            "shenzhen_amount": self.shenzhen_amount,
            "total_amount": self.total_amount,
        }


@dataclass(frozen=True)
class FetchOutcome:
    rows: tuple[TurnoverRow, ...]
    errors: tuple[dict[str, str], ...]
    cache_dates: tuple[date, ...]


def _decimal_yi(value: object, field: str) -> Decimal:
    if value is None:
        raise SourceError(f"missing {field}")
    try:
        parsed = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, AttributeError) as exc:
        raise SourceError(f"invalid {field}: {value!r}") from exc
    if not parsed.is_finite() or parsed < 0:
        raise SourceError(f"invalid {field}: {value!r}")
    return parsed


def _yuan(value_yi: Decimal, field: str) -> int:
    value = value_yi * YUAN_PER_YI
    if value != value.to_integral_value():
        raise SourceError(f"{field} cannot be represented as whole CNY yuan")
    return int(value)


def _get_json(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, str],
    referer: str,
) -> Any:
    try:
        response = session.get(url, params=params, headers={"Referer": referer}, timeout=20)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        raise SourceError(f"request failed: {exc}") from exc


def parse_sse_amount(payload: object, target: date) -> int:
    if not isinstance(payload, dict) or not isinstance(payload.get("result"), list):
        raise SourceError("SSE response has no result list")
    rows = payload["result"]
    by_code: dict[str, dict[str, Any]] = {}
    for row in rows:
        if isinstance(row, dict) and row.get("PRODUCT_CODE") is not None:
            by_code[str(row["PRODUCT_CODE"])] = row
    required = {"01", "02", "03", "17"}
    missing = sorted(required - set(by_code))
    if missing:
        raise SourceError(f"SSE response is missing product codes: {', '.join(missing)}")
    expected_date = target.strftime("%Y%m%d")
    for code in required:
        if str(by_code[code].get("TRADE_DATE")) != expected_date:
            raise SourceError(f"SSE product {code} date does not match {target.isoformat()}")
    main_a = _decimal_yi(by_code["01"].get("TRADE_AMT"), "SSE main-board A TRADE_AMT")
    b_share = _decimal_yi(by_code["02"].get("TRADE_AMT"), "SSE B-share TRADE_AMT")
    star = _decimal_yi(by_code["03"].get("TRADE_AMT"), "SSE STAR TRADE_AMT")
    stock_total = _decimal_yi(by_code["17"].get("TRADE_AMT"), "SSE stock TRADE_AMT")
    if abs(stock_total - (main_a + b_share + star)) > ROUNDING_TOLERANCE_YI:
        raise SourceError("SSE stock total is inconsistent with A/B/STAR components")
    return _yuan(main_a + star, "SSE A-share turnover")


def _clean_label(value: object) -> str:
    text = html.unescape(str(value or ""))
    return re.sub(r"\s+", "", text)


def parse_szse_amount(payload: object, target: date) -> int:
    if not isinstance(payload, list):
        raise SourceError("SZSE response is not a report list")
    report: dict[str, Any] | None = None
    for candidate in payload:
        if not isinstance(candidate, dict):
            continue
        metadata = candidate.get("metadata")
        if (
            isinstance(metadata, dict)
            and metadata.get("catalogid") == "1803_sczm"
            and metadata.get("tabkey") == "tab1"
        ):
            report = candidate
            break
    if report is None:
        raise SourceError("SZSE security-category report is missing")
    metadata = report["metadata"]
    if metadata.get("subname") != target.isoformat():
        raise SourceError(f"SZSE report date does not match {target.isoformat()}")
    cols = metadata.get("cols")
    amount_heading = cols.get("cjje") if isinstance(cols, dict) else None
    if "\u6210\u4ea4\u91d1\u989d" not in str(amount_heading) or "\u4ebf\u5143" not in str(amount_heading):
        raise SourceError("SZSE cjje field is not labelled as turnover in CNY 100 million")
    if report.get("error") not in (None, ""):
        raise SourceError(f"SZSE report error: {report['error']}")
    rows = report.get("data")
    if not isinstance(rows, list):
        raise SourceError("SZSE report has no data list")
    by_label = {
        _clean_label(row.get("lbmc")): row
        for row in rows
        if isinstance(row, dict) and row.get("lbmc") is not None
    }
    required = {"\u80a1\u7968", "\u4e3b\u677fA\u80a1", "\u4e3b\u677fB\u80a1", "\u521b\u4e1a\u677fA\u80a1"}
    missing = sorted(required - set(by_label))
    if missing:
        raise SourceError(f"SZSE response is missing categories: {', '.join(missing)}")
    main_a = _decimal_yi(by_label["\u4e3b\u677fA\u80a1"].get("cjje"), "SZSE main-board A cjje")
    b_share = _decimal_yi(by_label["\u4e3b\u677fB\u80a1"].get("cjje"), "SZSE B-share cjje")
    chinext = _decimal_yi(by_label["\u521b\u4e1a\u677fA\u80a1"].get("cjje"), "SZSE ChiNext A cjje")
    stock_total = _decimal_yi(by_label["\u80a1\u7968"].get("cjje"), "SZSE stock cjje")
    if abs(stock_total - (main_a + b_share + chinext)) > ROUNDING_TOLERANCE_YI:
        raise SourceError("SZSE stock total is inconsistent with A/B/ChiNext components")
    return _yuan(main_a + chinext, "SZSE A-share turnover")


def fetch_shanghai_amount(session: requests.Session, target: date) -> int:
    payload = _get_json(
        session,
        SSE_ENDPOINT,
        params={
            "sqlId": "COMMON_SSE_SJ_GPSJ_CJGK_MRGK_C",
            "PRODUCT_CODE": "01,02,03,11,17",
            "type": "inParams",
            "SEARCH_DATE": target.isoformat(),
        },
        referer=SSE_PAGE,
    )
    return parse_sse_amount(payload, target)


def fetch_shenzhen_amount(session: requests.Session, target: date) -> int:
    payload = _get_json(
        session,
        SZSE_ENDPOINT,
        params={
            "SHOWTYPE": "JSON",
            "CATALOGID": "1803_sczm",
            "TABKEY": "tab1",
            "txtQueryDate": target.isoformat(),
        },
        referer=SZSE_PAGE,
    )
    return parse_szse_amount(payload, target)


def fetch_official_row(
    session: requests.Session,
    target: date,
    shanghai_fetcher: Callable[[requests.Session, date], int] = fetch_shanghai_amount,
    shenzhen_fetcher: Callable[[requests.Session, date], int] = fetch_shenzhen_amount,
) -> TurnoverRow:
    shanghai_error: SourceError | None = None
    shenzhen_error: SourceError | None = None
    try:
        shanghai = shanghai_fetcher(session, target)
    except SourceError as exc:
        shanghai_error = exc
        shanghai = 0
    try:
        shenzhen = shenzhen_fetcher(session, target)
    except SourceError as exc:
        shenzhen_error = exc
        shenzhen = 0
    if shanghai_error or shenzhen_error:
        messages = []
        if shanghai_error:
            messages.append(f"Shanghai: {shanghai_error}")
        if shenzhen_error:
            messages.append(f"Shenzhen: {shenzhen_error}")
        raise SourceError("; ".join(messages))
    row = TurnoverRow(target, shanghai, shenzhen)
    validate_row(row)
    return row


def validate_row(row: TurnoverRow) -> None:
    if row.shanghai_amount < 0:
        raise ValidationError(f"{row.market_date}: shanghai_amount is negative")
    if row.shenzhen_amount < 0:
        raise ValidationError(f"{row.market_date}: shenzhen_amount is negative")
    if row.total_amount != row.shanghai_amount + row.shenzhen_amount:
        raise ValidationError(f"{row.market_date}: total_amount is inconsistent")


def validate_history(rows: Iterable[TurnoverRow]) -> tuple[TurnoverRow, ...]:
    materialized = tuple(rows)
    dates = [row.market_date for row in materialized]
    if len(dates) != len(set(dates)):
        raise ValidationError("turnover history contains duplicate dates")
    if dates != sorted(dates):
        raise ValidationError("turnover history is not sorted oldest first")
    for row in materialized:
        validate_row(row)
    return materialized


def rows_from_public(payload: dict[str, Any]) -> tuple[TurnoverRow, ...]:
    raw_history = payload.get("history")
    if not isinstance(raw_history, list):
        return ()
    rows: list[TurnoverRow] = []
    try:
        for item in raw_history:
            if not isinstance(item, dict):
                return ()
            row = TurnoverRow(
                market_date=date.fromisoformat(str(item["date"])),
                shanghai_amount=int(item["shanghai_amount"]),
                shenzhen_amount=int(item["shenzhen_amount"]),
            )
            if int(item["total_amount"]) != row.total_amount:
                return ()
            rows.append(row)
        return validate_history(rows)
    except (KeyError, TypeError, ValueError, ValidationError):
        return ()


def fetch_history_with_cache(
    dates: Iterable[date],
    official_fetcher: Callable[[date], TurnoverRow],
    cache_rows: Iterable[TurnoverRow] = (),
) -> FetchOutcome:
    cache = {row.market_date: row for row in validate_history(cache_rows)}
    rows: list[TurnoverRow] = []
    errors: list[dict[str, str]] = []
    cache_dates: list[date] = []
    for day in dates:
        try:
            row = official_fetcher(day)
            if row.market_date != day:
                raise ValidationError("official source returned the wrong date")
            validate_row(row)
            rows.append(row)
        except (SourceError, ValidationError) as exc:
            errors.append({"date": day.isoformat(), "error": str(exc)})
            cached = cache.get(day)
            if cached is not None:
                rows.append(cached)
                cache_dates.append(day)
    return FetchOutcome(
        rows=validate_history(sorted(rows, key=lambda row: row.market_date)),
        errors=tuple(errors),
        cache_dates=tuple(cache_dates),
    )


def _average(values: list[int]) -> int | float:
    value = sum(Decimal(item) for item in values) / Decimal(len(values))
    if value == value.to_integral_value():
        return int(value)
    return float(value.quantize(Decimal("0.01")))


def compute_summary(
    rows: Iterable[TurnoverRow], expected_dates: list[date]
) -> dict[str, int | float | bool | None]:
    by_date = {row.market_date: row for row in validate_history(rows)}

    def values_for(count: int) -> list[int] | None:
        dates = expected_dates[-count:]
        if len(dates) != count or any(day not in by_date for day in dates):
            return None
        return [by_date[day].total_amount for day in dates]

    current = by_date.get(expected_dates[-1]) if expected_dates else None
    previous = by_date.get(expected_dates[-2]) if len(expected_dates) >= 2 else None
    values_5 = values_for(5)
    values_10 = values_for(10)
    values_20 = values_for(20)
    avg_5 = _average(values_5) if values_5 else None
    avg_10 = _average(values_10) if values_10 else None
    avg_20 = _average(values_20) if values_20 else None
    pct: float | None = None
    if avg_5 is not None and avg_20 not in (None, 0):
        pct = float(
            ((Decimal(str(avg_5)) / Decimal(str(avg_20)) - 1) * 100).quantize(
                Decimal("0.01")
            )
        )
    return {
        "current_amount": current.total_amount if current else None,
        "previous_amount": previous.total_amount if previous else None,
        "avg_5d": avg_5,
        "avg_10d": avg_10,
        "avg_20d": avg_20,
        "avg_5d_vs_20d_pct": pct,
        "avg_10d_below_1_8t": avg_10 < 1_800_000_000_000 if avg_10 is not None else None,
        "current_above_2_5t": (
            current.total_amount > 2_500_000_000_000 if current else None
        ),
    }


def _trillion(value: int | float | None) -> float | None:
    if value is None:
        return None
    return float((Decimal(str(value)) / Decimal("1000000000000")).quantize(Decimal("0.0001")))


def source_metadata(errors: Iterable[dict[str, str]], cache_dates: Iterable[date]) -> list[dict[str, Any]]:
    error_count = len(tuple(errors))
    cached = tuple(cache_dates)
    status = "ok" if error_count == 0 else ("cached_fallback" if cached else "error")
    return [
        {
            "name": "Shanghai Stock Exchange daily stock overview",
            "url": SSE_PAGE,
            "status": status,
            "fields": ["PRODUCT_CODE=01.TRADE_AMT", "PRODUCT_CODE=03.TRADE_AMT"],
            "scope": "Main Board A shares + STAR Market; excludes B shares and buybacks",
            "source_unit": "CNY 100 million",
        },
        {
            "name": "Shenzhen Stock Exchange security category statistics",
            "url": SZSE_PAGE,
            "status": status,
            "fields": ["\u4e3b\u677fA\u80a1.cjje", "\u521b\u4e1a\u677fA\u80a1.cjje"],
            "scope": "Main Board A shares + ChiNext A shares; excludes B shares",
            "source_unit": "CNY 100 million",
        },
        {
            "name": "previously validated a_share_turnover.json",
            "status": "used" if cached else "not_used",
            "dates": [day.isoformat() for day in cached],
            "role": "local cache fallback only; never changes the market scope",
        },
    ]


def build_payload(
    target: date,
    expected_dates: list[date],
    rows: Iterable[TurnoverRow],
    generated_at: datetime,
    *,
    errors: Iterable[dict[str, str]] = (),
    cache_dates: Iterable[date] = (),
) -> dict[str, Any]:
    history = validate_history(rows)
    by_date = {row.market_date: row for row in history}
    missing = [day for day in expected_dates if day not in by_date]
    recent_20_missing = [day for day in expected_dates[-20:] if day not in by_date]
    summary = compute_summary(history, expected_dates)
    status = "ok" if not missing else "incomplete"
    if not history:
        status = "error"
    return {
        "schema_version": 1,
        "generated_at": generated_at.astimezone(SHANGHAI_TZ).isoformat(),
        "market_date": target.isoformat() if target in by_date else None,
        "status": status,
        "scope": "Shanghai A shares + Shenzhen A shares",
        "includes_bse": False,
        "unit": "CNY",
        "source": source_metadata(errors, cache_dates),
        "coverage": {
            "expected_trading_days": len(expected_dates),
            "available_trading_days": len([day for day in expected_dates if day in by_date]),
            "recent_20_complete": not recent_20_missing,
        },
        "missing_dates": [day.isoformat() for day in missing],
        "summary": summary,
        "summary_trillion_cny": {
            key: _trillion(summary[key])
            for key in ("current_amount", "previous_amount", "avg_5d", "avg_10d", "avg_20d")
        },
        "history": [row.to_public_dict() for row in history],
        "source_errors": list(errors),
    }


def collect_turnover(
    target: date,
    project_root: Path,
    now: datetime | None = None,
    *,
    write: bool = True,
    use_cache: bool = True,
) -> dict[str, Any]:
    generated_at = (now or datetime.now(SHANGHAI_TZ)).astimezone(SHANGHAI_TZ)
    expected_dates = recent_trading_days(target, HISTORY_DAYS)
    output_path = project_root / "public" / "a_share_turnover.json"
    cache_rows: tuple[TurnoverRow, ...] = ()
    if use_cache and output_path.exists():
        try:
            cache_rows = rows_from_public(read_json(output_path))
        except (OSError, ValueError):
            cache_rows = ()
    session = build_session()
    outcome = fetch_history_with_cache(
        expected_dates,
        lambda day: fetch_official_row(session, day),
        cache_rows,
    )
    payload = build_payload(
        target,
        expected_dates,
        outcome.rows,
        generated_at,
        errors=outcome.errors,
        cache_dates=outcome.cache_dates,
    )
    if write:
        write_json_atomic(output_path, payload)
    return payload
