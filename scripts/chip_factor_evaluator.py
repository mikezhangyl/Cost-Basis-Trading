from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from math import sqrt
from pathlib import Path
from statistics import mean
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class FactorEvaluationResult:
    evaluation_id: str
    factor_run_id: str
    status: str
    artifact_dir: str
    summary_ref: str
    report_ref: str


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate factor-run outputs against forward returns.")
    parser.add_argument("--factor-run-id", required=True, help="Factor run id under docs/factor-runs.")
    parser.add_argument("--factor-run-root", type=Path, default=_default_factor_run_root())
    parser.add_argument("--offsets", nargs="+", type=int, default=[1, 3, 5], help="Forward trading-day offsets.")
    parser.add_argument("--evaluation-id", default=None)
    args = parser.parse_args()

    result = evaluate_factor_run(
        factor_run_dir=args.factor_run_root / args.factor_run_id,
        offsets=args.offsets,
        evaluation_id=args.evaluation_id,
    )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0


def evaluate_factor_run(
    factor_run_dir: Path,
    offsets: list[int],
    evaluation_id: str | None = None,
) -> FactorEvaluationResult:
    manifest = _load_json(factor_run_dir / "factor-run-manifest.json")
    factor_run_id = str(manifest["factor_run_id"])
    safe_offsets = sorted({offset for offset in offsets if offset > 0})
    if not safe_offsets:
        raise SystemExit("At least one positive forward-return offset is required.")

    evaluation_id = evaluation_id or _build_evaluation_id()
    evaluation_dir = factor_run_dir / "factor-evaluations" / evaluation_id
    if evaluation_dir.exists():
        raise SystemExit(f"Factor evaluation artifact already exists and is immutable: {evaluation_dir}")
    evaluation_dir.mkdir(parents=True)

    observations = []
    for stock_output in manifest.get("stock_outputs", []):
        observations.extend(_build_stock_observations(factor_run_dir, stock_output, safe_offsets))

    observation_path = evaluation_dir / "factor-forward-observations.jsonl"
    for observation in observations:
        _append_jsonl(observation_path, observation)

    summary = {
        "evaluation_id": evaluation_id,
        "factor_run_id": factor_run_id,
        "created_at": _now_iso(),
        "offsets": safe_offsets,
        "observation_count": len(observations),
        "summary_by_factor": _summarize_observations(observations, safe_offsets),
        "artifact_refs": {
            "observations": "factor-forward-observations.jsonl",
            "summary": "factor-evaluation-summary.json",
            "report": "factor-evaluation-report.md",
        },
    }
    _write_json(evaluation_dir / "factor-evaluation-config.json", {"factor_run_id": factor_run_id, "offsets": safe_offsets})
    _write_json(evaluation_dir / "factor-evaluation-summary.json", summary)
    _write_text(evaluation_dir / "factor-evaluation-report.md", _build_report(summary))
    result = FactorEvaluationResult(
        evaluation_id=evaluation_id,
        factor_run_id=factor_run_id,
        status="completed",
        artifact_dir=str(evaluation_dir),
        summary_ref=str(evaluation_dir / "factor-evaluation-summary.json"),
        report_ref=str(evaluation_dir / "factor-evaluation-report.md"),
    )
    _write_json(evaluation_dir.parent / "latest.json", asdict(result))
    return result


def _build_stock_observations(factor_run_dir: Path, stock_output: dict[str, Any], offsets: list[int]) -> list[dict[str, Any]]:
    ts_code = str(stock_output["ts_code"])
    snapshots = _load_jsonl(factor_run_dir / str(stock_output["snapshot_ref"]))
    factors = _load_jsonl(factor_run_dir / str(stock_output["factor_ref"]))
    close_by_date = {str(row["factor_date"]): row.get("close") for row in snapshots}
    ordered_dates = [str(row["factor_date"]) for row in snapshots]
    index_by_date = {trade_date: index for index, trade_date in enumerate(ordered_dates)}
    observations = []
    for factor in factors:
        if factor.get("quality_status") != "OK" or factor.get("value") is None:
            continue
        factor_date = str(factor["factor_date"])
        current_index = index_by_date.get(factor_date)
        current_close = close_by_date.get(factor_date)
        if current_index is None or current_close in (None, 0):
            continue
        for offset in offsets:
            future_index = current_index + offset
            future_date = ordered_dates[future_index] if future_index < len(ordered_dates) else None
            future_close = close_by_date.get(future_date) if future_date is not None else None
            forward_return = None
            if future_close is not None:
                forward_return = (float(future_close) / float(current_close)) - 1
            observations.append(
                {
                    "ts_code": ts_code,
                    "factor_id": factor["factor_id"],
                    "factor_date": factor_date,
                    "factor_value": float(factor["value"]),
                    "offset_days": offset,
                    "future_date": future_date,
                    "current_close": float(current_close),
                    "future_close": future_close,
                    "forward_return": forward_return,
                }
            )
    return observations


def _summarize_observations(observations: list[dict[str, Any]], offsets: list[int]) -> list[dict[str, Any]]:
    factor_ids = sorted({str(observation["factor_id"]) for observation in observations})
    summaries = []
    for factor_id in factor_ids:
        factor_observations = [observation for observation in observations if observation["factor_id"] == factor_id]
        offset_summaries = []
        for offset in offsets:
            rows = [observation for observation in factor_observations if observation["offset_days"] == offset]
            available = [row for row in rows if row["forward_return"] is not None]
            values = [float(row["factor_value"]) for row in available]
            returns = [float(row["forward_return"]) for row in available]
            bottom, top = _bottom_top_groups(available)
            bottom_mean = _mean_or_none([float(row["forward_return"]) for row in bottom])
            top_mean = _mean_or_none([float(row["forward_return"]) for row in top])
            spread = None if bottom_mean is None or top_mean is None else top_mean - bottom_mean
            offset_summaries.append(
                {
                    "offset_days": offset,
                    "available_count": len(available),
                    "unavailable_count": len(rows) - len(available),
                    "mean_forward_return": _mean_or_none(returns),
                    "pearson_correlation": _pearson(values, returns),
                    "bottom_group_mean_return": bottom_mean,
                    "top_group_mean_return": top_mean,
                    "top_minus_bottom_return": spread,
                    "direction_hint": _direction_hint(spread),
                }
            )
        summaries.append({"factor_id": factor_id, "offsets": offset_summaries})
    return summaries


def _bottom_top_groups(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if len(rows) < 4:
        return ([], [])
    sorted_rows = sorted(rows, key=lambda row: float(row["factor_value"]))
    group_size = max(1, len(sorted_rows) // 5)
    return (sorted_rows[:group_size], sorted_rows[-group_size:])


def _pearson(values: list[float], returns: list[float]) -> float | None:
    if len(values) < 3 or len(values) != len(returns):
        return None
    value_mean = mean(values)
    return_mean = mean(returns)
    numerator = sum((value - value_mean) * (forward_return - return_mean) for value, forward_return in zip(values, returns))
    value_variance = sum((value - value_mean) ** 2 for value in values)
    return_variance = sum((forward_return - return_mean) ** 2 for forward_return in returns)
    denominator = sqrt(value_variance * return_variance)
    if denominator == 0:
        return None
    return numerator / denominator


def _mean_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    return mean(values)


def _direction_hint(spread: float | None) -> str:
    if spread is None:
        return "INSUFFICIENT_DATA"
    if spread > 0:
        return "HIGH_FACTOR_OUTPERFORMS"
    if spread < 0:
        return "LOW_FACTOR_OUTPERFORMS"
    return "NO_SPREAD"


def _build_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Factor Evaluation Report",
        "",
        f"- Factor run: `{summary['factor_run_id']}`",
        f"- Evaluation: `{summary['evaluation_id']}`",
        f"- Offsets: `{summary['offsets']}`",
        f"- Observation rows: `{summary['observation_count']}`",
        "",
        "This report aligns factor values with future close-to-close returns. It is a research diagnostic, not investment advice.",
        "",
        "## Top Signals By Absolute Correlation",
        "",
    ]
    ranked = []
    for factor_summary in summary["summary_by_factor"]:
        for offset_summary in factor_summary["offsets"]:
            correlation = offset_summary.get("pearson_correlation")
            if correlation is not None:
                ranked.append((abs(correlation), factor_summary["factor_id"], offset_summary))
    for _, factor_id, offset_summary in sorted(ranked, reverse=True)[:10]:
        lines.append(
            "- "
            f"`{factor_id}` N+{offset_summary['offset_days']}: "
            f"corr={offset_summary['pearson_correlation']:.4f}, "
            f"top-bottom={_format_optional_percent(offset_summary['top_minus_bottom_return'])}, "
            f"available={offset_summary['available_count']}"
        )
    if not ranked:
        lines.append("- No factor had enough forward-return observations for correlation.")
    return "\n".join(lines) + "\n"


def _format_optional_percent(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value * 100:.2f}%"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


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


def _build_evaluation_id() -> str:
    return f"factor-eval-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _default_factor_run_root() -> Path:
    return Path(__file__).resolve().parents[1] / "docs" / "factor-runs"


if __name__ == "__main__":
    raise SystemExit(main())
