from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from datetime import timedelta
from pathlib import Path
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
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--dry-run", action="store_true", help="Use deterministic local fixture data instead of Tushare.")
    args = parser.parse_args()

    if not args.dry_run:
        raise SystemExit("Only --dry-run is implemented in the first chip-factor skeleton.")

    run_id = args.run_id or _build_run_id()
    run_dir = args.artifact_root / run_id
    if run_dir.exists():
        raise SystemExit(f"Factor run artifact already exists and is immutable: {run_dir}")
    run_dir.mkdir(parents=True)

    stock_codes = [code.strip().upper() for code in args.stock_codes if code.strip()]
    created_at = _now_iso()
    _write_json(
        run_dir / "factor-run-config.json",
        {
            "factor_run_id": run_id,
            "created_at": created_at,
            "stock_codes": stock_codes,
            "factor_start_date": args.factor_start_date,
            "factor_end_date": args.factor_end_date,
            "dry_run": True,
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
            "dry_run": True,
        },
    )

    dry_run_dates = _weekday_dates(args.factor_start_date, args.factor_end_date)
    stock_outputs = []
    for ts_code in stock_codes:
        stock_outputs.append(_write_dry_run_stock(run_dir, ts_code, dry_run_dates))

    manifest = {
        "factor_run_id": run_id,
        "status": "completed",
        "created_at": created_at,
        "completed_at": _now_iso(),
        "immutable": True,
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

    print(json.dumps({"run_id": run_id, "status": "completed", "artifact_dir": str(run_dir)}, ensure_ascii=False))
    return 0


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
    return {
        "ts_code": ts_code,
        "factor_date_count": len(factor_dates),
        "snapshot_ref": f"stocks/{ts_code}/daily-chip-snapshots.jsonl",
        "factor_ref": f"stocks/{ts_code}/factors.jsonl",
        "quality_ref": f"stocks/{ts_code}/factor-quality.json",
        "traceability_ref": f"stocks/{ts_code}/factor-traceability.json",
    }


def _quality_status_counts_for_path(path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        status = json.loads(line)["quality_status"]
        counts[status] = counts.get(status, 0) + 1
    return counts


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _append_jsonl(path: Path, payload: dict[str, object]) -> None:
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
