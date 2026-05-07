from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.chip_factor_batch import _aggregate_stock_results, _build_aggregate_report


@dataclass(frozen=True)
class BatchAggregateResult:
    aggregate_id: str
    status: str
    artifact_dir: str
    stock_count: int
    success_count: int
    failed_count: int
    summary_ref: str
    aggregate_report_ref: str


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate one or more factor batch summaries.")
    parser.add_argument("--batch-ids", nargs="+", required=True)
    parser.add_argument("--batch-root", type=Path, default=_default_batch_root())
    parser.add_argument("--aggregate-id", default=None)
    args = parser.parse_args()

    result = aggregate_factor_batches(
        batch_ids=args.batch_ids,
        batch_root=args.batch_root,
        aggregate_id=args.aggregate_id,
    )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0 if result.status != "failed" else 1


def aggregate_factor_batches(
    batch_ids: list[str],
    batch_root: Path,
    aggregate_id: str | None = None,
) -> BatchAggregateResult:
    safe_batch_ids = [batch_id.strip() for batch_id in batch_ids if batch_id.strip()]
    if not safe_batch_ids:
        raise SystemExit("At least one batch id is required.")

    aggregate_id = aggregate_id or _build_aggregate_id()
    aggregate_dir = batch_root / aggregate_id
    if aggregate_dir.exists():
        raise SystemExit(f"Batch aggregate artifact already exists and is immutable: {aggregate_dir}")
    aggregate_dir.mkdir(parents=True)

    source_summaries = [_load_json(batch_root / batch_id / "factor-batch-summary.json") for batch_id in safe_batch_ids]
    stock_results = _merge_stock_results(source_summaries)
    aggregate_summary = _aggregate_stock_results(aggregate_id, stock_results)
    success_count = sum(1 for result in stock_results if result["status"] == "completed")
    failed_count = len(stock_results) - success_count
    status = "completed" if failed_count == 0 else "partial" if success_count else "failed"
    summary = {
        "batch_id": aggregate_id,
        "status": status,
        "created_at": _now_iso(),
        "source_batch_ids": safe_batch_ids,
        "stock_count": len(stock_results),
        "success_count": success_count,
        "failed_count": failed_count,
        "stock_results": stock_results,
        "aggregate_summary": aggregate_summary,
        "artifact_refs": {
            "summary": "factor-batch-summary.json",
            "aggregate_report": "aggregate-factor-report.md",
        },
    }
    _write_json(aggregate_dir / "factor-batch-summary.json", summary)
    _write_text(aggregate_dir / "aggregate-factor-report.md", _build_aggregate_report(summary))
    return BatchAggregateResult(
        aggregate_id=aggregate_id,
        status=status,
        artifact_dir=str(aggregate_dir),
        stock_count=len(stock_results),
        success_count=success_count,
        failed_count=failed_count,
        summary_ref=str(aggregate_dir / "factor-batch-summary.json"),
        aggregate_report_ref=str(aggregate_dir / "aggregate-factor-report.md"),
    )


def _merge_stock_results(source_summaries: list[dict[str, object]]) -> list[dict[str, object]]:
    merged: dict[str, dict[str, object]] = {}
    for summary in source_summaries:
        source_batch_id = str(summary.get("batch_id"))
        for raw_result in summary.get("stock_results", []):
            result = dict(raw_result)
            ts_code = str(result["ts_code"])
            result["source_batch_id"] = source_batch_id
            existing = merged.get(ts_code)
            if existing is None or _should_replace_stock_result(existing, result):
                merged[ts_code] = result
    return [merged[ts_code] for ts_code in sorted(merged)]


def _should_replace_stock_result(existing: dict[str, object], candidate: dict[str, object]) -> bool:
    existing_completed = existing.get("status") == "completed"
    candidate_completed = candidate.get("status") == "completed"
    return candidate_completed and not existing_completed


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_aggregate_id() -> str:
    return f"factor-batch-aggregate-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _default_batch_root() -> Path:
    return Path(__file__).resolve().parents[1] / "docs" / "factor-batches"


if __name__ == "__main__":
    raise SystemExit(main())
