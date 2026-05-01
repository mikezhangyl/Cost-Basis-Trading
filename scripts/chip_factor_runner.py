from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

sys.path.append(str(Path(__file__).resolve().parents[1] / "backend"))

from app.domain.models import ChipDistributionPoint, DailyPriceBar
from app.factors.chip_factors import build_daily_chip_snapshot, build_factor_values, factor_traceability_payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Local chip-factor production runner.")
    parser.add_argument("--stock-codes", nargs="+", required=True, help="One or more normalized A-share ts_codes.")
    parser.add_argument("--factor-start-date", required=True, help="Output factor start date in YYYYMMDD format.")
    parser.add_argument("--factor-end-date", required=True, help="Output factor end date in YYYYMMDD format.")
    parser.add_argument("--artifact-root", type=Path, default=_default_artifact_root())
    parser.add_argument("--cache-root", type=Path, default=_default_cache_root())
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--dry-run", action="store_true", help="Use deterministic local fixture data instead of Tushare.")
    args = parser.parse_args()

    result = run_factor_production(
        stock_codes=args.stock_codes,
        factor_start_date=args.factor_start_date,
        factor_end_date=args.factor_end_date,
        artifact_root=args.artifact_root,
        cache_root=args.cache_root,
        run_id=args.run_id,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


def run_factor_production(
    stock_codes: list[str],
    factor_start_date: str,
    factor_end_date: str,
    artifact_root: Path,
    cache_root: Path,
    run_id: str | None = None,
    dry_run: bool = False,
    data_client: Any | None = None,
    warmup_trading_days: int = 20,
) -> dict[str, str]:
    normalized_stock_codes = [code.strip().upper() for code in stock_codes if code.strip()]
    if not normalized_stock_codes:
        raise SystemExit("At least one stock code is required.")

    run_id = run_id or _build_run_id()
    run_dir = artifact_root / run_id
    if run_dir.exists():
        raise SystemExit(f"Factor run artifact already exists and is immutable: {run_dir}")
    run_dir.mkdir(parents=True)

    created_at = _now_iso()
    _write_json(
        run_dir / "factor-run-config.json",
        {
            "factor_run_id": run_id,
            "created_at": created_at,
            "stock_codes": normalized_stock_codes,
            "factor_start_date": factor_start_date,
            "factor_end_date": factor_end_date,
            "dry_run": dry_run,
            "cache_root": str(cache_root),
            "warmup_trading_days": warmup_trading_days,
            "formula_version": "chip-factor-v1",
            "immutable_artifacts": True,
        },
    )
    _touch(run_dir / "api-calls.jsonl")
    _touch(run_dir / "api-retry-events.jsonl")
    _append_jsonl(
        run_dir / "worker-events.jsonl",
        {
            "timestamp": _now_iso(),
            "event": "factor_run_started",
            "factor_run_id": run_id,
            "dry_run": dry_run,
        },
    )

    if dry_run:
        output_dates = _weekday_dates(factor_start_date, factor_end_date)
        all_dates = output_dates
        stock_outputs = [_write_dry_run_stock(run_dir, ts_code, all_dates) for ts_code in normalized_stock_codes]
    else:
        client = data_client or _build_tushare_client()
        previous_retry_handler = getattr(client, "retry_event_handler", None)
        set_retry_event_handler = getattr(client, "set_retry_event_handler", None)
        if callable(set_retry_event_handler):
            set_retry_event_handler(lambda event: _record_retry_event(run_dir, run_id, event))
        try:
            all_dates = _call_with_api_log(
                run_dir,
                "trade_cal",
                _trade_calendar_log_params(factor_start_date, factor_end_date, warmup_trading_days),
                lambda: _resolve_live_factor_dates(client, factor_start_date, factor_end_date, warmup_trading_days),
            )
            output_dates = [trade_date for trade_date in all_dates if factor_start_date <= trade_date <= factor_end_date]
            if not output_dates:
                raise SystemExit("No factor output trading dates found for requested range.")
            stock_outputs = [
                _write_live_stock(run_dir, cache_root, client, run_id, ts_code, all_dates, output_dates)
                for ts_code in normalized_stock_codes
            ]
        finally:
            if callable(set_retry_event_handler):
                set_retry_event_handler(previous_retry_handler)

    manifest = {
        "factor_run_id": run_id,
        "status": "completed",
        "created_at": created_at,
        "completed_at": _now_iso(),
        "immutable": True,
        "dry_run": dry_run,
        "factor_date_count": len(output_dates),
        "warmup_date_count": max(0, len(all_dates) - len(output_dates)),
        "stock_count": len(stock_outputs),
        "output_refs": [
            "factor-run-config.json",
            "factor-run-manifest.json",
            "api-calls.jsonl",
            "api-retry-events.jsonl",
            "worker-events.jsonl",
        ],
        "stock_outputs": stock_outputs,
    }
    _write_json(run_dir / "factor-run-manifest.json", manifest)
    _append_jsonl(
        run_dir / "worker-events.jsonl",
        {
            "timestamp": _now_iso(),
            "event": "factor_run_completed",
            "factor_run_id": run_id,
            "stock_count": len(stock_outputs),
        },
    )

    return {"run_id": run_id, "status": "completed", "artifact_dir": str(run_dir)}


def _write_dry_run_stock(run_dir: Path, ts_code: str, factor_dates: list[str]) -> dict[str, object]:
    stock_dir = run_dir / "stocks" / ts_code
    stock_dir.mkdir(parents=True)
    snapshots = []
    for index, factor_date in enumerate(factor_dates):
        base_price = 10 + index * 0.01
        chip_points = [
            ChipDistributionPoint(ts_code=ts_code, trade_date=factor_date, price=base_price, percent=20),
            ChipDistributionPoint(ts_code=ts_code, trade_date=factor_date, price=base_price + 1, percent=50),
            ChipDistributionPoint(ts_code=ts_code, trade_date=factor_date, price=base_price + 2, percent=30),
        ]
        price_bar = DailyPriceBar(
            ts_code=ts_code,
            trade_date=factor_date,
            open=base_price + 1,
            high=base_price + 2,
            low=base_price,
            close=base_price + 1.5,
        )
        snapshots.append(build_daily_chip_snapshot(ts_code, factor_date, chip_points, price_bar))

    factor_count = 0
    for snapshot in snapshots:
        _append_jsonl(stock_dir / "daily-chip-snapshots.jsonl", snapshot.model_dump(mode="json"))
        factors = build_factor_values(snapshots, snapshot.factor_date, expected_trading_dates=factor_dates)
        factor_count += len(factors)
        for factor in factors:
            _append_jsonl(stock_dir / "factors.jsonl", factor.model_dump(mode="json"))

    _write_json(
        stock_dir / "factor-quality.json",
        {
            "ts_code": ts_code,
            "snapshot_count": len(snapshots),
            "factor_count": factor_count,
            "quality_status_counts": _quality_status_counts_for_path(stock_dir / "factors.jsonl"),
        },
    )
    _write_json(stock_dir / "factor-traceability.json", {"factors": factor_traceability_payload()})
    checksums = _stock_artifact_checksums(stock_dir)
    return {
        "ts_code": ts_code,
        "factor_date_count": len(factor_dates),
        "warmup_snapshot_count": 0,
        "snapshot_ref": f"stocks/{ts_code}/daily-chip-snapshots.jsonl",
        "factor_ref": f"stocks/{ts_code}/factors.jsonl",
        "quality_ref": f"stocks/{ts_code}/factor-quality.json",
        "traceability_ref": f"stocks/{ts_code}/factor-traceability.json",
        "checksums": checksums,
    }


def _write_live_stock(
    run_dir: Path,
    cache_root: Path,
    client: Any,
    run_id: str,
    ts_code: str,
    all_dates: list[str],
    output_dates: list[str],
) -> dict[str, object]:
    stock_dir = run_dir / "stocks" / ts_code
    stock_dir.mkdir(parents=True)
    fetch_start = all_dates[0]
    fetch_end = all_dates[-1]
    chip_points = _call_with_api_log(
        run_dir,
        "cyq_chips",
        {"ts_code": ts_code, "start_date": fetch_start, "end_date": fetch_end},
        lambda: client.get_chip_distribution(ts_code, fetch_start, fetch_end),
    )
    daily_prices = _call_with_api_log(
        run_dir,
        "daily",
        {"ts_code": ts_code, "start_date": fetch_start, "end_date": fetch_end},
        lambda: client.get_daily_prices(ts_code, fetch_start, fetch_end),
    )
    _write_chip_cache(cache_root, run_id, ts_code, chip_points)
    _write_daily_cache(cache_root, run_id, ts_code, fetch_start, fetch_end, daily_prices)

    chip_points_by_date: dict[str, list[ChipDistributionPoint]] = {}
    for point in chip_points:
        chip_points_by_date.setdefault(point.trade_date, []).append(point)
    prices_by_date = {bar.trade_date: bar for bar in daily_prices}
    snapshots = [
        build_daily_chip_snapshot(
            ts_code=ts_code,
            factor_date=trade_date,
            chip_points=chip_points_by_date.get(trade_date, []),
            price_bar=prices_by_date.get(trade_date),
        )
        for trade_date in all_dates
    ]

    factor_count = 0
    output_date_set = set(output_dates)
    for snapshot in snapshots:
        _append_jsonl(stock_dir / "daily-chip-snapshots.jsonl", snapshot.model_dump(mode="json"))
        if snapshot.factor_date not in output_date_set:
            continue
        factors = build_factor_values(snapshots, snapshot.factor_date, expected_trading_dates=all_dates)
        factor_count += len(factors)
        for factor in factors:
            _append_jsonl(stock_dir / "factors.jsonl", factor.model_dump(mode="json"))

    _write_json(
        stock_dir / "factor-quality.json",
        {
            "ts_code": ts_code,
            "snapshot_count": len(snapshots),
            "factor_count": factor_count,
            "quality_status_counts": _quality_status_counts_for_path(stock_dir / "factors.jsonl"),
        },
    )
    _write_json(stock_dir / "factor-traceability.json", {"factors": factor_traceability_payload()})
    checksums = _stock_artifact_checksums(stock_dir)
    return {
        "ts_code": ts_code,
        "factor_date_count": len(output_dates),
        "warmup_snapshot_count": max(0, len(all_dates) - len(output_dates)),
        "snapshot_ref": f"stocks/{ts_code}/daily-chip-snapshots.jsonl",
        "factor_ref": f"stocks/{ts_code}/factors.jsonl",
        "quality_ref": f"stocks/{ts_code}/factor-quality.json",
        "traceability_ref": f"stocks/{ts_code}/factor-traceability.json",
        "checksums": checksums,
    }


def _quality_status_counts_for_path(path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        status = json.loads(line)["quality_status"]
        counts[status] = counts.get(status, 0) + 1
    return counts


def _call_with_api_log(run_dir: Path, endpoint: str, params: dict[str, object], call: Any) -> Any:
    started_at = _now_iso()
    try:
        result = call()
    except Exception as error:
        _append_jsonl(
            run_dir / "api-calls.jsonl",
            {
                "timestamp": started_at,
                "endpoint": endpoint,
                "params": params,
                "status": "failed",
                "error": str(error),
            },
        )
        raise
    _append_jsonl(
        run_dir / "api-calls.jsonl",
        {
            "timestamp": started_at,
            "endpoint": endpoint,
            "params": params,
            "status": "ok",
            "row_count": _row_count(result),
        },
    )
    return result


def _record_retry_event(run_dir: Path, run_id: str, event: dict[str, object]) -> None:
    _append_jsonl(
        run_dir / "api-retry-events.jsonl",
        {
            "timestamp": _now_iso(),
            "factor_run_id": run_id,
            "source": "market_data_client",
            **event,
        },
    )


def _row_count(result: object) -> int | None:
    if isinstance(result, list):
        return len(result)
    return None


def _resolve_live_factor_dates(
    client: Any,
    factor_start_date: str,
    factor_end_date: str,
    warmup_trading_days: int,
) -> list[str]:
    warmup_probe = _warmup_probe_start(factor_start_date, warmup_trading_days)
    resolver = getattr(client, "get_trading_days_between", None)
    if callable(resolver):
        dates = resolver(warmup_probe, factor_end_date)
    else:
        private_resolver = getattr(client, "_trading_days_between")
        dates = private_resolver(warmup_probe, factor_end_date)
    sorted_dates = sorted(str(date) for date in dates)
    output_dates = [date for date in sorted_dates if factor_start_date <= date <= factor_end_date]
    if not output_dates:
        return sorted_dates
    first_output_index = sorted_dates.index(output_dates[0])
    warmup_start_index = max(0, first_output_index - warmup_trading_days)
    return sorted_dates[warmup_start_index:]


def _trade_calendar_log_params(factor_start_date: str, factor_end_date: str, warmup_trading_days: int) -> dict[str, object]:
    return {
        "exchange": "SSE",
        "start_date": _warmup_probe_start(factor_start_date, warmup_trading_days),
        "end_date": factor_end_date,
        "is_open": "1",
        "factor_start_date": factor_start_date,
        "factor_end_date": factor_end_date,
        "warmup_trading_days": warmup_trading_days,
    }


def _warmup_probe_start(factor_start_date: str, warmup_trading_days: int) -> str:
    start = datetime.strptime(factor_start_date, "%Y%m%d").replace(tzinfo=UTC)
    return (start - timedelta(days=max(30, warmup_trading_days * 3))).strftime("%Y%m%d")


def _write_chip_cache(cache_root: Path, run_id: str, ts_code: str, chip_points: list[ChipDistributionPoint]) -> None:
    by_date: dict[str, list[dict[str, object]]] = {}
    for point in chip_points:
        by_date.setdefault(point.trade_date, []).append(point.model_dump(mode="json"))
    for trade_date, rows in by_date.items():
        _write_json(
            cache_root / "tushare" / "cyq_chips" / ts_code / f"{trade_date}.json",
            {
                "source": {
                    "provider": "tushare",
                    "endpoint": "cyq_chips",
                    "factor_run_id": run_id,
                    "ts_code": ts_code,
                    "trade_date": trade_date,
                    "fetched_at": _now_iso(),
                },
                "rows_checksum": _payload_checksum(rows),
                "rows": rows,
            },
        )


def _write_daily_cache(
    cache_root: Path,
    run_id: str,
    ts_code: str,
    start_date: str,
    end_date: str,
    daily_prices: list[DailyPriceBar],
) -> None:
    rows = [bar.model_dump(mode="json") for bar in daily_prices]
    _write_json(
        cache_root / "tushare" / "daily" / ts_code / f"{start_date}_{end_date}.json",
        {
            "source": {
                "provider": "tushare",
                "endpoint": "daily",
                "factor_run_id": run_id,
                "ts_code": ts_code,
                "start_date": start_date,
                "end_date": end_date,
                "fetched_at": _now_iso(),
            },
            "rows_checksum": _payload_checksum(rows),
            "rows": rows,
        },
    )


def _stock_artifact_checksums(stock_dir: Path) -> dict[str, str]:
    refs = [
        "daily-chip-snapshots.jsonl",
        "factors.jsonl",
        "factor-quality.json",
        "factor-traceability.json",
    ]
    return {ref: _file_checksum(stock_dir / ref) for ref in refs}


def _file_checksum(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _payload_checksum(payload: object) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _append_jsonl(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _touch(path: Path) -> None:
    path.touch()


def _build_run_id() -> str:
    return f"factor-run-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _default_artifact_root() -> Path:
    return Path(__file__).resolve().parents[1] / "docs" / "factor-runs"


def _default_cache_root() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "factor-cache"


def _build_tushare_client() -> Any:
    from app.data.tushare_client import TushareMarketDataClient

    return TushareMarketDataClient()


def _weekday_dates(start_date: str, end_date: str) -> list[str]:
    start = datetime.strptime(start_date, "%Y%m%d").date()
    end = datetime.strptime(end_date, "%Y%m%d").date()
    if start > end:
        raise SystemExit("factor-start-date must be before or equal to factor-end-date.")
    dates = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            dates.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    if not dates:
        raise SystemExit("No weekday dry-run factor dates found in the requested range.")
    return dates


if __name__ == "__main__":
    raise SystemExit(main())
