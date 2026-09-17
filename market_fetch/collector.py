from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from . import eastmoney, tencent
from .errors import SourceError, ValidationError
from .http import build_session
from .models import CollectionResult, DailyBar, SourceSnapshot
from .storage import read_json, write_json_atomic, write_raw
from .trading_calendar import SHANGHAI_TZ
from .validate import prices_match, validate_snapshot


SYMBOL = "159993"
EXCHANGE = "SZ"
CANONICAL_NAME = "鹏华国证证券龙头交易型开放式指数证券投资基金"


def _source_rows(
    snapshots: dict[str, SourceSnapshot], errors: dict[str, str], target: date
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for source in (eastmoney.SOURCE_NAME, tencent.SOURCE_NAME):
        snapshot = snapshots.get(source)
        if snapshot is None:
            rows.append({"name": source, "status": "error", "error": errors.get(source, "unknown")})
            continue
        exact = snapshot.bar_on(target)
        rows.append(
            {
                "name": source,
                "status": "ok" if exact else "stale",
                "market_date": snapshot.latest.market_date.isoformat(),
                "close": float(exact.close) if exact else float(snapshot.latest.close),
            }
        )
    return rows


def _history_payload(snapshot: SourceSnapshot, generated_at: datetime) -> dict[str, object]:
    bars = list(snapshot.bars)[-90:]
    return {
        "schema_version": 1,
        "symbol": SYMBOL,
        "exchange": EXCHANGE,
        "name": CANONICAL_NAME,
        "generated_at": generated_at.isoformat(),
        "source": snapshot.source,
        "volume_unit": "lot",
        "amount_currency": "CNY",
        "items": [bar.to_public_dict() for bar in bars],
    }


def _merge_fallback_into_existing_history(
    history_path: Path, current_quote: dict[str, object], generated_at: datetime
) -> dict[str, object] | None:
    """Keep exact historical amounts when Tencent is the only live source."""
    try:
        existing = read_json(history_path)
    except (OSError, ValueError):
        return None
    items = existing.get("items")
    if not isinstance(items, list) or len(items) < 30:
        return None
    if any(not isinstance(item, dict) or item.get("amount") is None for item in items):
        return None
    by_date = {str(item["date"]): item for item in items}
    by_date[str(current_quote["date"])] = current_quote
    merged_items = [by_date[key] for key in sorted(by_date)][-90:]
    return {
        **existing,
        "generated_at": generated_at.isoformat(),
        "source": "eastmoney+tencent-fallback",
        "items": merged_items,
    }


def build_result(
    target: date,
    snapshots: Iterable[SourceSnapshot],
    errors: dict[str, str],
    generated_at: datetime,
) -> CollectionResult:
    valid = {snapshot.source: snapshot for snapshot in snapshots}
    source_rows = _source_rows(valid, errors, target)
    candidates: dict[str, DailyBar] = {
        source: bar
        for source, snapshot in valid.items()
        if (bar := snapshot.bar_on(target)) is not None
    }
    base: dict[str, object] = {
        "schema_version": 1,
        "symbol": SYMBOL,
        "exchange": EXCHANGE,
        "name": CANONICAL_NAME,
        "target_date": target.isoformat(),
        "generated_at": generated_at.isoformat(),
        "sources": source_rows,
    }

    if not candidates:
        latest_date = max((snapshot.latest.market_date for snapshot in valid.values()), default=None)
        latest_payload = {
            **base,
            "market_date": latest_date.isoformat() if latest_date else None,
            "status": "stale" if valid else "error",
            "verification": "unverified",
            "quote": None,
        }
        return CollectionResult(
            status=str(latest_payload["status"]),
            verification="unverified",
            target_date=target,
            market_date=latest_date,
            latest_payload=latest_payload,
            history_payload=None,
        )

    primary = candidates.get(eastmoney.SOURCE_NAME)
    fallback = candidates.get(tencent.SOURCE_NAME)
    if primary is not None and fallback is not None and not prices_match(primary, fallback):
        latest_payload = {
            **base,
            "market_date": target.isoformat(),
            "status": "conflict",
            "verification": "conflict",
            "quote": None,
            "candidates": {
                eastmoney.SOURCE_NAME: primary.to_public_dict(),
                tencent.SOURCE_NAME: fallback.to_public_dict(),
            },
        }
        return CollectionResult(
            status="conflict",
            verification="conflict",
            target_date=target,
            market_date=target,
            latest_payload=latest_payload,
            history_payload=None,
        )

    selected_source = eastmoney.SOURCE_NAME if primary is not None else tencent.SOURCE_NAME
    selected = candidates[selected_source]
    verification = "cross_checked" if primary is not None and fallback is not None else (
        "single_source" if primary is not None else "fallback_only"
    )
    latest_payload = {
        **base,
        "market_date": target.isoformat(),
        "status": "ok",
        "verification": verification,
        "selected_source": selected_source,
        "quote": selected.to_public_dict(),
    }
    history_source = valid.get(eastmoney.SOURCE_NAME) or valid.get(tencent.SOURCE_NAME)
    history_payload = _history_payload(history_source, generated_at) if history_source else None
    return CollectionResult(
        status="ok",
        verification=verification,
        target_date=target,
        market_date=target,
        latest_payload=latest_payload,
        history_payload=history_payload,
    )


def collect(target: date, project_root: Path, now: datetime | None = None) -> CollectionResult:
    generated_at = (now or datetime.now(SHANGHAI_TZ)).astimezone(SHANGHAI_TZ)
    session = build_session()
    snapshots: list[SourceSnapshot] = []
    errors: dict[str, str] = {}
    for source_name, fetcher in (
        (eastmoney.SOURCE_NAME, eastmoney.fetch),
        (tencent.SOURCE_NAME, tencent.fetch),
    ):
        try:
            snapshot = fetcher(session)
            validate_snapshot(snapshot)
            snapshots.append(snapshot)
            write_raw(project_root / "data" / "raw", source_name, snapshot.raw_payload, generated_at)
        except (SourceError, ValidationError) as exc:
            errors[source_name] = str(exc)

    result = build_result(target, snapshots, errors, generated_at)
    history_path = project_root / "public" / "history" / f"{SYMBOL}.json"
    history_payload = result.history_payload
    if result.ok and result.latest_payload.get("selected_source") == tencent.SOURCE_NAME:
        history_payload = _merge_fallback_into_existing_history(
            history_path, result.latest_payload["quote"], generated_at
        )
    result.latest_payload["history_status"] = (
        "complete" if result.ok and history_payload is not None else "unavailable"
    )
    if result.ok and history_payload is None:
        result.latest_payload["status"] = "degraded"
        result = replace(result, status="degraded")
    write_json_atomic(project_root / "public" / "latest.json", result.latest_payload)
    if result.ok and history_payload is not None:
        write_json_atomic(
            history_path, history_payload
        )
    return result
