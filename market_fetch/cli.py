from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

from . import eastmoney, tencent
from .collector import collect
from .errors import CalendarCoverageError, MarketFetchError
from .http import build_session
from .storage import read_json
from .turnover import collect_turnover
from .trading_calendar import (
    SHANGHAI_TZ,
    ensure_explicit_target_is_ready,
    resolve_default_target,
)
from .validate import prices_match, validate_snapshot


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def _print_result(result) -> None:
    print("159993")
    print(f"Target date: {result.target_date.isoformat()}")
    print(f"Latest market date: {result.market_date.isoformat() if result.market_date else 'N/A'}")
    quote = result.latest_payload.get("quote")
    if quote:
        print(f"Close: {quote['close']}")
    for source in result.latest_payload["sources"]:
        print(f"{source['name'].capitalize()}: {str(source['status']).upper()}")
    print(f"Verification: {result.verification}")
    print(f"Result: {result.status.upper()}")


def command_fetch(target: date | None) -> int:
    now = datetime.now(SHANGHAI_TZ)
    if target is None:
        target = resolve_default_target(now)
    else:
        ensure_explicit_target_is_ready(target, now)
    result = collect(target, PROJECT_ROOT, now)
    _print_result(result)
    return 0 if result.ok else 2


def command_verify() -> int:
    latest = read_json(PROJECT_ROOT / "public" / "latest.json")
    history_path = PROJECT_ROOT / "public" / "history" / "159993.json"
    history_missing = not history_path.exists()
    history = {} if history_missing else read_json(history_path)
    expected = resolve_default_target()
    errors: list[str] = []
    if latest.get("status") != "ok":
        errors.append(f"latest status is {latest.get('status')!r}")
    if latest.get("history_status") != "complete":
        errors.append(f"history status is {latest.get('history_status')!r}")
    if history_missing:
        errors.append("history/159993.json has not been generated")
    if latest.get("market_date") != expected.isoformat():
        errors.append(f"market_date is {latest.get('market_date')!r}, expected {expected.isoformat()}")
    items = history.get("items")
    if not history_missing and (not isinstance(items, list) or len(items) < 30):
        errors.append("history contains fewer than 30 trading days")
    elif not history_missing and items[-1].get("date") != latest.get("market_date"):
        errors.append("history latest date does not match latest.json")
    elif not history_missing and any(item.get("amount") is None for item in items):
        errors.append("history contains rows without exact turnover amount")
    print("159993")
    print(f"Expected market date: {expected.isoformat()}")
    print(f"Published market date: {latest.get('market_date')}")
    print(f"History rows: {len(items) if isinstance(items, list) else 0}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        print("Result: FAILED")
        return 2
    print("Result: OK")
    return 0


def command_smoke_test() -> int:
    session = build_session()
    primary = eastmoney.fetch(session)
    fallback = tencent.fetch(session)
    validate_snapshot(primary)
    validate_snapshot(fallback)
    print(f"Eastmoney: {primary.latest.market_date} close={primary.latest.close}")
    print(f"Tencent: {fallback.latest.market_date} close={fallback.latest.close}")
    if primary.latest.market_date != fallback.latest.market_date:
        print("Result: STALE_SOURCE")
        return 2
    if not prices_match(primary.latest, fallback.latest):
        print("Result: CONFLICT")
        return 2
    print("Verification: cross_checked")
    print("Result: OK")
    return 0


def _format_amount(value: object) -> str:
    if value is None:
        return "N/A"
    amount = float(value)
    return f"{amount:,.0f} CNY ({amount / 1_000_000_000_000:.4f}T)"


def _print_turnover(payload: dict[str, object]) -> None:
    summary = payload.get("summary")
    summary = summary if isinstance(summary, dict) else {}
    history = payload.get("history")
    latest = history[-1] if isinstance(history, list) and history else {}
    print("A-share turnover")
    print(f"Market date: {payload.get('market_date') or 'N/A'}")
    print()
    print(f"Shanghai: {_format_amount(latest.get('shanghai_amount'))}")
    print(f"Shenzhen: {_format_amount(latest.get('shenzhen_amount'))}")
    print(f"Total: {_format_amount(summary.get('current_amount'))}")
    print()
    print(f"5d avg: {_format_amount(summary.get('avg_5d'))}")
    print(f"10d avg: {_format_amount(summary.get('avg_10d'))}")
    print(f"20d avg: {_format_amount(summary.get('avg_20d'))}")
    print()
    pct = summary.get("avg_5d_vs_20d_pct")
    print(f"5d vs 20d: {pct if pct is not None else 'N/A'}%")
    print(f"10d < 1.8T: {str(summary.get('avg_10d_below_1_8t')).lower()}")
    print(f"Current > 2.5T: {str(summary.get('current_above_2_5t')).lower()}")
    print()
    print(f"Result: {str(payload.get('status', 'error')).upper()}")


def command_turnover(*, smoke_test: bool) -> int:
    target = resolve_default_target()
    payload = collect_turnover(
        target,
        PROJECT_ROOT,
        write=not smoke_test,
        use_cache=not smoke_test,
    )
    _print_turnover(payload)
    return 0 if payload.get("status") == "ok" else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect verified A-share closing data")
    subparsers = parser.add_subparsers(dest="command", required=True)
    fetch_parser = subparsers.add_parser("fetch", help="fetch and publish market data")
    fetch_parser.add_argument("--date", type=_parse_date, help="expected market date (YYYY-MM-DD)")
    subparsers.add_parser("verify", help="verify generated public JSON files")
    subparsers.add_parser("smoke-test", help="make real requests without writing public JSON")
    subparsers.add_parser("turnover", help="fetch and publish Shanghai+Shenzhen A-share turnover")
    subparsers.add_parser(
        "turnover-smoke-test",
        help="make real official-exchange turnover requests without writing public JSON",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "fetch":
            return command_fetch(args.date)
        if args.command == "verify":
            return command_verify()
        if args.command == "smoke-test":
            return command_smoke_test()
        if args.command == "turnover":
            return command_turnover(smoke_test=False)
        if args.command == "turnover-smoke-test":
            return command_turnover(smoke_test=True)
    except (MarketFetchError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 1
