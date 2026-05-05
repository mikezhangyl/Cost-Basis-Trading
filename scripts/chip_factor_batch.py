from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any, Callable
from uuid import uuid4

sys.path.append(str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.code_normalizer import normalize_ts_code
from scripts.chip_factor_evaluator import evaluate_factor_run
from scripts.chip_factor_runner import run_factor_production

RunFactorFn = Callable[..., dict[str, str]]
EvaluateFactorFn = Callable[..., Any]


@dataclass(frozen=True)
class FactorBatchResult:
    batch_id: str
    status: str
    artifact_dir: str
    success_count: int
    failed_count: int
    summary_ref: str
    aggregate_report_ref: str


def main() -> int:
    parser = argparse.ArgumentParser(description="Run factor production/evaluation for a stock batch.")
    parser.add_argument("--stock-codes", nargs="+", required=True)
    parser.add_argument("--factor-start-date", required=True)
    parser.add_argument("--factor-end-date", required=True)
    parser.add_argument("--offsets", nargs="+", type=int, default=[1, 3, 5])
    parser.add_argument("--batch-id", default=None)
    parser.add_argument("--batch-root", type=Path, default=_default_batch_root())
    parser.add_argument("--factor-run-root", type=Path, default=_default_factor_run_root())
    parser.add_argument("--cache-root", type=Path, default=_default_cache_root())
    parser.add_argument("--sleep-between-stocks", type=float, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    result = run_factor_batch(
        stock_codes=args.stock_codes,
        factor_start_date=args.factor_start_date,
        factor_end_date=args.factor_end_date,
        offsets=args.offsets,
        batch_root=args.batch_root,
        factor_run_root=args.factor_run_root,
        cache_root=args.cache_root,
        batch_id=args.batch_id,
        dry_run=args.dry_run,
        sleep_between_stocks_seconds=args.sleep_between_stocks,
    )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0 if result.status != "failed" else 1


def run_factor_batch(
    stock_codes: list[str],
    factor_start_date: str,
    factor_end_date: str,
    offsets: list[int],
    batch_root: Path,
    factor_run_root: Path,
    cache_root: Path,
    batch_id: str | None = None,
    dry_run: bool = False,
    sleep_between_stocks_seconds: float = 0,
    run_factor_fn: RunFactorFn = run_factor_production,
    evaluate_factor_fn: EvaluateFactorFn = evaluate_factor_run,
) -> FactorBatchResult:
    normalized_stock_codes = [normalize_ts_code(code) for code in stock_codes if code.strip()]
    if not normalized_stock_codes:
        raise SystemExit("At least one stock code is required.")
    safe_offsets = sorted({offset for offset in offsets if offset > 0})
    if not safe_offsets:
        raise SystemExit("At least one positive offset is required.")

    batch_id = batch_id or _build_batch_id()
    batch_dir = batch_root / batch_id
    if batch_dir.exists():
        raise SystemExit(f"Factor batch artifact already exists and is immutable: {batch_dir}")
    batch_dir.mkdir(parents=True)

    _write_json(
        batch_dir / "factor-batch-config.json",
        {
            "batch_id": batch_id,
            "created_at": _now_iso(),
            "stock_codes": normalized_stock_codes,
            "factor_start_date": factor_start_date,
            "factor_end_date": factor_end_date,
            "offsets": safe_offsets,
            "dry_run": dry_run,
            "sleep_between_stocks_seconds": sleep_between_stocks_seconds,
            "factor_run_root": str(factor_run_root),
            "cache_root": str(cache_root),
            "immutable_artifacts": True,
        },
    )
    _append_jsonl(batch_dir / "batch-events.jsonl", {"timestamp": _now_iso(), "event": "batch_started", "batch_id": batch_id})

    stock_results = []
    for index, ts_code in enumerate(normalized_stock_codes):
        stock_result = _run_one_stock(
            batch_dir=batch_dir,
            batch_id=batch_id,
            ts_code=ts_code,
            factor_start_date=factor_start_date,
            factor_end_date=factor_end_date,
            offsets=safe_offsets,
            factor_run_root=factor_run_root,
            cache_root=cache_root,
            dry_run=dry_run,
            run_factor_fn=run_factor_fn,
            evaluate_factor_fn=evaluate_factor_fn,
        )
        stock_results.append(stock_result)
        _append_jsonl(batch_dir / "stock-results.jsonl", stock_result)
        if sleep_between_stocks_seconds > 0 and index < len(normalized_stock_codes) - 1:
            _append_jsonl(
                batch_dir / "batch-events.jsonl",
                {
                    "timestamp": _now_iso(),
                    "event": "sleep_between_stocks_started",
                    "batch_id": batch_id,
                    "seconds": sleep_between_stocks_seconds,
                    "after_ts_code": ts_code,
                },
            )
            time.sleep(sleep_between_stocks_seconds)
            _append_jsonl(
                batch_dir / "batch-events.jsonl",
                {
                    "timestamp": _now_iso(),
                    "event": "sleep_between_stocks_completed",
                    "batch_id": batch_id,
                    "after_ts_code": ts_code,
                },
            )

    aggregate_summary = _aggregate_stock_results(batch_id, stock_results)
    success_count = sum(1 for result in stock_results if result["status"] == "completed")
    failed_count = len(stock_results) - success_count
    status = "completed" if failed_count == 0 else "partial" if success_count else "failed"
    summary = {
        "batch_id": batch_id,
        "status": status,
        "completed_at": _now_iso(),
        "stock_count": len(stock_results),
        "success_count": success_count,
        "failed_count": failed_count,
        "stock_results": stock_results,
        "aggregate_summary": aggregate_summary,
        "artifact_refs": {
            "config": "factor-batch-config.json",
            "events": "batch-events.jsonl",
            "stock_results": "stock-results.jsonl",
            "summary": "factor-batch-summary.json",
            "aggregate_report": "aggregate-factor-report.md",
        },
    }
    _write_json(batch_dir / "factor-batch-summary.json", summary)
    _write_text(batch_dir / "aggregate-factor-report.md", _build_aggregate_report(summary))
    _append_jsonl(
        batch_dir / "batch-events.jsonl",
        {
            "timestamp": _now_iso(),
            "event": "batch_completed",
            "batch_id": batch_id,
            "status": status,
            "success_count": success_count,
            "failed_count": failed_count,
        },
    )
    return FactorBatchResult(
        batch_id=batch_id,
        status=status,
        artifact_dir=str(batch_dir),
        success_count=success_count,
        failed_count=failed_count,
        summary_ref=str(batch_dir / "factor-batch-summary.json"),
        aggregate_report_ref=str(batch_dir / "aggregate-factor-report.md"),
    )


def _run_one_stock(
    batch_dir: Path,
    batch_id: str,
    ts_code: str,
    factor_start_date: str,
    factor_end_date: str,
    offsets: list[int],
    factor_run_root: Path,
    cache_root: Path,
    dry_run: bool,
    run_factor_fn: RunFactorFn,
    evaluate_factor_fn: EvaluateFactorFn,
) -> dict[str, Any]:
    safe_stock = _safe_stock_code(ts_code)
    factor_run_id = f"factor-run-{batch_id}-{safe_stock}"
    evaluation_id = f"factor-eval-{batch_id}-{safe_stock}"
    try:
        run_result = run_factor_fn(
            stock_codes=[ts_code],
            factor_start_date=factor_start_date,
            factor_end_date=factor_end_date,
            artifact_root=factor_run_root,
            cache_root=cache_root,
            run_id=factor_run_id,
            dry_run=dry_run,
        )
        evaluation_result = evaluate_factor_fn(
            factor_run_dir=Path(run_result["artifact_dir"]),
            offsets=offsets,
            evaluation_id=evaluation_id,
        )
        evaluation_summary = _load_json(Path(evaluation_result.summary_ref))
        return {
            "ts_code": ts_code,
            "status": "completed",
            "factor_run_id": run_result["run_id"],
            "factor_run_dir": run_result["artifact_dir"],
            "evaluation_id": evaluation_result.evaluation_id,
            "evaluation_dir": evaluation_result.artifact_dir,
            "observation_count": evaluation_summary.get("observation_count", 0),
            "summary_by_factor": evaluation_summary.get("summary_by_factor", []),
        }
    except SystemExit as error:
        return _failed_stock_result(ts_code, factor_run_id, "SystemExit", str(error))
    except Exception as error:
        if _is_operational_stock_error(error):
            return _failed_stock_result(ts_code, factor_run_id, type(error).__name__, str(error))
        raise


def _failed_stock_result(ts_code: str, factor_run_id: str, error_type: str, error_message: str) -> dict[str, Any]:
    return {
        "ts_code": ts_code,
        "status": "failed",
        "factor_run_id": factor_run_id,
        "error_type": error_type,
        "error_message": _sanitize_error_message(error_message),
    }


def _aggregate_stock_results(batch_id: str, stock_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for stock_result in stock_results:
        if stock_result.get("status") != "completed":
            continue
        for factor_summary in stock_result.get("summary_by_factor", []):
            factor_id = factor_summary.get("factor_id")
            for offset_summary in factor_summary.get("offsets", []):
                key = (str(factor_id), int(offset_summary["offset_days"]))
                grouped.setdefault(key, []).append({"ts_code": stock_result["ts_code"], **offset_summary})

    aggregate = []
    for (factor_id, offset), rows in sorted(grouped.items()):
        correlations = [float(row["pearson_correlation"]) for row in rows if row.get("pearson_correlation") is not None]
        spreads = [float(row["top_minus_bottom_return"]) for row in rows if row.get("top_minus_bottom_return") is not None]
        aggregate.append(
            {
                "batch_id": batch_id,
                "factor_id": factor_id,
                "offset_days": offset,
                "stock_count": len(rows),
                "total_available_count": sum(int(row.get("available_count", 0)) for row in rows),
                "total_unavailable_count": sum(int(row.get("unavailable_count", 0)) for row in rows),
                "mean_pearson_correlation": _mean_or_none(correlations),
                "positive_correlation_count": sum(1 for value in correlations if value > 0),
                "negative_correlation_count": sum(1 for value in correlations if value < 0),
                "mean_top_minus_bottom_return": _mean_or_none(spreads),
                "high_factor_outperforms_count": sum(1 for value in spreads if value > 0),
                "low_factor_outperforms_count": sum(1 for value in spreads if value < 0),
                "direction_consistency": _direction_consistency(spreads),
            }
        )
    return aggregate


def _direction_consistency(spreads: list[float]) -> str:
    if not spreads:
        return "INSUFFICIENT_DATA"
    positive = sum(1 for value in spreads if value > 0)
    negative = sum(1 for value in spreads if value < 0)
    if positive == len(spreads):
        return "HIGH_FACTOR_OUTPERFORMS_ALL"
    if negative == len(spreads):
        return "LOW_FACTOR_OUTPERFORMS_ALL"
    if positive > negative:
        return "HIGH_FACTOR_OUTPERFORMS_MAJORITY"
    if negative > positive:
        return "LOW_FACTOR_OUTPERFORMS_MAJORITY"
    return "MIXED"


def _build_aggregate_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Aggregate Factor Batch Report",
        "",
        f"- Batch: `{summary['batch_id']}`",
        f"- Status: `{summary['status']}`",
        f"- Stocks: `{summary['stock_count']}`",
        f"- Success / failed: `{summary['success_count']} / {summary['failed_count']}`",
        "",
        "This report compares factor diagnostics across stocks. It is not investment advice.",
        "",
        "## Strongest Cross-Stock Diagnostics",
        "",
    ]
    ranked = [
        row
        for row in summary["aggregate_summary"]
        if row.get("mean_pearson_correlation") is not None
    ]
    ranked.sort(key=lambda row: abs(float(row["mean_pearson_correlation"])), reverse=True)
    if not ranked:
        lines.append("- No aggregate factor diagnostics had enough observations.")
    for row in ranked[:15]:
        lines.append(
            "- "
            f"`{row['factor_id']}` N+{row['offset_days']}: "
            f"mean_corr={float(row['mean_pearson_correlation']):.4f}, "
            f"mean_top-bottom={_format_optional_percent(row.get('mean_top_minus_bottom_return'))}, "
            f"stocks={row['stock_count']}, "
            f"direction={row['direction_consistency']}"
        )
    failed = [result for result in summary["stock_results"] if result["status"] != "completed"]
    if failed:
        lines.extend(["", "## Failed Stocks", ""])
        for result in failed:
            lines.append(f"- `{result['ts_code']}`: {result.get('error_type')} {result.get('error_message')}")
    return "\n".join(lines) + "\n"


def _safe_stock_code(ts_code: str) -> str:
    return ts_code.replace(".", "-").replace("_", "-")


def _mean_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    return mean(values)


def _format_optional_percent(value: object) -> str:
    if value is None:
        return "N/A"
    return f"{float(value) * 100:.2f}%"


def _is_operational_stock_error(error: Exception) -> bool:
    return type(error).__name__ in {
        "DataUnavailableError",
        "ConnectionError",
        "TimeoutError",
    }


def _sanitize_error_message(message: str, max_length: int = 500) -> str:
    cleaned = message.replace("\n", " ").replace("\r", " ")
    cleaned = re.sub(
        r"(?i)([\"']?\b(?:token|api[_-]?key|secret|password)\b[\"']?\s*[:=]\s*)([\"'])(.*?)(\2)",
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]{match.group(4)}",
        cleaned,
    )
    cleaned = re.sub(
        r"(?i)([\"']?\b(?:token|api[_-]?key|secret|password)\b[\"']?\s*[:=]\s*)(?![\"'])[^\s,;}]+",
        lambda match: f"{match.group(1)}[REDACTED]",
        cleaned,
    )
    return cleaned[:max_length]


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _build_batch_id() -> str:
    return f"factor-batch-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _default_batch_root() -> Path:
    return Path(__file__).resolve().parents[1] / "docs" / "factor-batches"


def _default_factor_run_root() -> Path:
    return Path(__file__).resolve().parents[1] / "docs" / "factor-runs"


def _default_cache_root() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "factor-cache"


if __name__ == "__main__":
    raise SystemExit(main())
