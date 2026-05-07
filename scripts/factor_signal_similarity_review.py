from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path
from statistics import mean
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class FactorSignalSimilarityReviewResult:
    review_id: str
    status: str
    output_dir: str
    instrument_count: int
    pair_similarity_count: int


def main() -> int:
    parser = argparse.ArgumentParser(description="Review factor trigger and backtest-behavior similarity.")
    parser.add_argument("--factor-batch-summary", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--quantile", type=float, default=0.20)
    parser.add_argument("--min-trigger-count", type=int, default=5)
    parser.add_argument("--trigger-similarity-threshold", type=float, default=0.75)
    parser.add_argument("--spread-diff-threshold", type=float, default=0.005)
    parser.add_argument("--review-id", default=None)
    args = parser.parse_args()

    result = review_factor_signal_similarity(
        factor_batch_summary=args.factor_batch_summary,
        output_dir=args.output_dir,
        quantile=args.quantile,
        min_trigger_count=args.min_trigger_count,
        trigger_similarity_threshold=args.trigger_similarity_threshold,
        spread_diff_threshold=args.spread_diff_threshold,
        review_id=args.review_id,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def review_factor_signal_similarity(
    factor_batch_summary: Path,
    output_dir: Path,
    quantile: float = 0.20,
    min_trigger_count: int = 5,
    trigger_similarity_threshold: float = 0.75,
    spread_diff_threshold: float = 0.005,
    review_id: str | None = None,
) -> dict[str, Any]:
    _validate_config(
        factor_batch_summary=factor_batch_summary,
        output_dir=output_dir,
        quantile=quantile,
        min_trigger_count=min_trigger_count,
        trigger_similarity_threshold=trigger_similarity_threshold,
        spread_diff_threshold=spread_diff_threshold,
    )
    review_id = review_id or _build_review_id()
    output_dir.mkdir(parents=True)
    events_path = output_dir / "review-events.jsonl"
    _append_jsonl(events_path, {"timestamp": _now_iso(), "event": "review_started", "review_id": review_id})

    config = {
        "review_id": review_id,
        "created_at": _now_iso(),
        "input_mode": "factor_batch_summary",
        "factor_batch_summary_path": str(factor_batch_summary),
        "quantile": quantile,
        "min_trigger_count": min_trigger_count,
        "trigger_similarity_threshold": trigger_similarity_threshold,
        "spread_diff_threshold": spread_diff_threshold,
        "instrument_isolation": True,
    }
    _write_json(output_dir / "review-config.json", config)

    _append_jsonl(events_path, {"timestamp": _now_iso(), "event": "source_manifest_started", "review_id": review_id})
    manifest = _build_source_manifest(factor_batch_summary)
    _write_json(output_dir / "source-data-manifest.json", manifest)
    _append_jsonl(events_path, {"timestamp": _now_iso(), "event": "source_manifest_completed", "review_id": review_id})

    _append_jsonl(events_path, {"timestamp": _now_iso(), "event": "load_observations_started", "review_id": review_id})
    instruments = [_load_instrument_observations(item) for item in manifest["instruments"]]
    _append_jsonl(
        events_path,
        {
            "timestamp": _now_iso(),
            "event": "load_observations_completed",
            "review_id": review_id,
            "instrument_count": len(instruments),
        },
    )

    all_pair_similarities = []
    _append_jsonl(events_path, {"timestamp": _now_iso(), "event": "per_instrument_similarity_started", "review_id": review_id})
    for instrument in instruments:
        stats = _build_signal_stats(instrument["rows"], quantile=quantile)
        pair_similarities = _build_pair_similarities(
            instrument_id=instrument["instrument_id"],
            stats=stats,
            min_trigger_count=min_trigger_count,
            trigger_similarity_threshold=trigger_similarity_threshold,
            spread_diff_threshold=spread_diff_threshold,
        )
        all_pair_similarities.extend(pair_similarities)
        instrument_dir = output_dir / "per-instrument" / instrument["instrument_id"]
        instrument_dir.mkdir(parents=True)
        _write_json(instrument_dir / "factor-signal-stats.json", stats)
        _write_json(instrument_dir / "factor-pair-signal-similarity.json", pair_similarities)
    _append_jsonl(events_path, {"timestamp": _now_iso(), "event": "per_instrument_similarity_completed", "review_id": review_id})

    _append_jsonl(events_path, {"timestamp": _now_iso(), "event": "cross_object_summary_started", "review_id": review_id})
    cross_object_summary = _build_cross_object_summary(
        all_pair_similarities,
        trigger_similarity_threshold=trigger_similarity_threshold,
    )
    _write_json(output_dir / "cross-object-signal-similarity-summary.json", cross_object_summary)
    _write_text(output_dir / "factor-signal-similarity-report.md", _build_report(config, manifest, cross_object_summary))
    _append_jsonl(events_path, {"timestamp": _now_iso(), "event": "cross_object_summary_completed", "review_id": review_id})
    _append_jsonl(events_path, {"timestamp": _now_iso(), "event": "review_completed", "review_id": review_id})

    result = FactorSignalSimilarityReviewResult(
        review_id=review_id,
        status="completed",
        output_dir=str(output_dir),
        instrument_count=len(instruments),
        pair_similarity_count=len(all_pair_similarities),
    )
    return asdict(result)


def _validate_config(
    factor_batch_summary: Path,
    output_dir: Path,
    quantile: float,
    min_trigger_count: int,
    trigger_similarity_threshold: float,
    spread_diff_threshold: float,
) -> None:
    if output_dir.exists():
        raise SystemExit(f"Factor signal similarity review artifact already exists and is immutable: {output_dir}")
    if not factor_batch_summary.exists():
        raise SystemExit(f"Factor batch summary does not exist: {factor_batch_summary}")
    if not 0 < quantile <= 0.50:
        raise SystemExit("quantile must be greater than 0 and less than or equal to 0.50.")
    if min_trigger_count < 1:
        raise SystemExit("min_trigger_count must be at least 1.")
    if not 0 < trigger_similarity_threshold <= 1:
        raise SystemExit("trigger_similarity_threshold must be greater than 0 and less than or equal to 1.")
    if spread_diff_threshold < 0:
        raise SystemExit("spread_diff_threshold must be non-negative.")


def _build_source_manifest(factor_batch_summary: Path) -> dict[str, Any]:
    summary = _load_json(factor_batch_summary)
    completed_results = [result for result in summary.get("stock_results", []) if result.get("status") == "completed"]
    if not completed_results:
        raise SystemExit("Factor batch summary has no completed stock results.")

    instruments = []
    for result in completed_results:
        instrument_id = result.get("ts_code")
        evaluation_dir = result.get("evaluation_dir")
        if not instrument_id or not evaluation_dir:
            raise SystemExit("Completed stock result must include ts_code and evaluation_dir.")
        observations_path = Path(str(evaluation_dir)) / "factor-forward-observations.jsonl"
        if not observations_path.exists():
            raise SystemExit(f"Expected factor-forward observations do not exist: {observations_path}")
        stats = _observation_file_stats(observations_path)
        if stats["ts_codes"] and stats["ts_codes"] != [str(instrument_id)]:
            raise SystemExit(
                f"Observation file for {instrument_id} contains unexpected ts_code values: {stats['ts_codes']}"
            )
        instruments.append(
            {
                "instrument_id": str(instrument_id),
                "evaluation_dir": str(evaluation_dir),
                "observations_jsonl": str(observations_path),
                **stats,
            }
        )
    return {
        "factor_batch_summary_path": str(factor_batch_summary),
        "instruments": instruments,
    }


def _observation_file_stats(path: Path) -> dict[str, Any]:
    row_count = 0
    available_count = 0
    dates = []
    factor_ids = set()
    offsets = set()
    ts_codes = set()
    for row in _read_jsonl(path):
        row_count += 1
        if row.get("ts_code"):
            ts_codes.add(str(row["ts_code"]))
        if row.get("factor_date"):
            dates.append(str(row["factor_date"]))
        if row.get("factor_id"):
            factor_ids.add(str(row["factor_id"]))
        if row.get("offset_days") is not None:
            offsets.add(int(row["offset_days"]))
        if row.get("forward_return") is not None:
            available_count += 1
    return {
        "row_count": row_count,
        "available_count": available_count,
        "date_min": min(dates) if dates else None,
        "date_max": max(dates) if dates else None,
        "factor_ids": sorted(factor_ids),
        "offsets": sorted(offsets),
        "ts_codes": sorted(ts_codes),
        "sha256": _file_checksum(path),
    }


def _load_instrument_observations(instrument: dict[str, Any]) -> dict[str, Any]:
    rows = []
    expected_instrument_id = str(instrument["instrument_id"])
    for row in _read_jsonl(Path(instrument["observations_jsonl"])):
        _validate_observation_row(row, expected_instrument_id=expected_instrument_id)
        if row.get("forward_return") is None:
            continue
        rows.append(
            {
                "instrument_id": expected_instrument_id,
                "factor_id": str(row["factor_id"]),
                "factor_date": str(row["factor_date"]),
                "offset_days": int(row["offset_days"]),
                "factor_value": float(row["factor_value"]),
                "forward_return": float(row["forward_return"]),
            }
        )
    return {"instrument_id": instrument["instrument_id"], "rows": rows}


def _validate_observation_row(row: dict[str, Any], expected_instrument_id: str) -> None:
    required = ["ts_code", "factor_id", "factor_date", "offset_days", "factor_value"]
    missing = [key for key in required if key not in row]
    if missing:
        raise SystemExit(f"Factor observation row is missing required fields: {missing}")
    if str(row["ts_code"]) != expected_instrument_id:
        raise SystemExit(
            f"Observation ts_code mismatch for instrument {expected_instrument_id}: found {row['ts_code']}"
        )
    for key in ("offset_days", "factor_value"):
        if not isinstance(row[key], int | float):
            raise SystemExit(f"Factor observation field {key} must be numeric.")
    if row.get("forward_return") is not None and not isinstance(row["forward_return"], int | float):
        raise SystemExit("Factor observation field forward_return must be numeric or null.")


def _build_signal_stats(rows: list[dict[str, Any]], quantile: float) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["factor_id"], row["offset_days"])].append(row)

    stats = []
    for (factor_id, offset_days), factor_rows in sorted(grouped.items()):
        sorted_rows = sorted(factor_rows, key=lambda row: row["factor_value"])
        trigger_count = max(1, int(len(sorted_rows) * quantile))
        low_rows = sorted_rows[:trigger_count]
        high_rows = sorted_rows[-trigger_count:]
        low_return = _mean_or_none([row["forward_return"] for row in low_rows])
        high_return = _mean_or_none([row["forward_return"] for row in high_rows])
        spread = None if low_return is None or high_return is None else high_return - low_return
        stats.append(
            {
                "factor_id": factor_id,
                "offset_days": offset_days,
                "available_count": len(sorted_rows),
                "quantile": quantile,
                "low_trigger_count": len(low_rows),
                "high_trigger_count": len(high_rows),
                "low_trigger_dates": [row["factor_date"] for row in low_rows],
                "high_trigger_dates": [row["factor_date"] for row in high_rows],
                "low_mean_forward_return": low_return,
                "high_mean_forward_return": high_return,
                "low_win_rate": _win_rate(low_rows),
                "high_win_rate": _win_rate(high_rows),
                "top_minus_bottom_return": spread,
                "direction_hint": _direction_hint(spread),
            }
        )
    return stats


def _build_pair_similarities(
    instrument_id: str,
    stats: list[dict[str, Any]],
    min_trigger_count: int,
    trigger_similarity_threshold: float,
    spread_diff_threshold: float,
) -> list[dict[str, Any]]:
    stats_by_offset: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for item in stats:
        stats_by_offset[int(item["offset_days"])].append(item)

    similarities = []
    for offset_days, offset_stats in sorted(stats_by_offset.items()):
        by_factor = {item["factor_id"]: item for item in offset_stats}
        for factor_a, factor_b in combinations(sorted(by_factor), 2):
            stat_a = by_factor[factor_a]
            stat_b = by_factor[factor_b]
            overlap = _best_trigger_overlap(stat_a, stat_b)
            behavior = _behavior_comparison(stat_a, stat_b, overlap)
            relationship_type = _relationship_type(
                overlap=overlap,
                behavior=behavior,
                min_trigger_count=min_trigger_count,
                trigger_similarity_threshold=trigger_similarity_threshold,
                spread_diff_threshold=spread_diff_threshold,
            )
            similarities.append(
                {
                    "scope": "per_instrument",
                    "instrument_id": instrument_id,
                    "offset_days": offset_days,
                    "factor_a": factor_a,
                    "factor_b": factor_b,
                    "relationship_type": relationship_type,
                    "best_trigger_overlap": overlap,
                    "behavior_comparison": behavior,
                    "plain_language_explanation": _plain_language_explanation(factor_a, factor_b, relationship_type),
                }
            )
    return similarities


def _best_trigger_overlap(stat_a: dict[str, Any], stat_b: dict[str, Any]) -> dict[str, Any]:
    candidates = [
        ("high_vs_high", stat_a["high_trigger_dates"], stat_b["high_trigger_dates"]),
        ("low_vs_low", stat_a["low_trigger_dates"], stat_b["low_trigger_dates"]),
        ("high_vs_low", stat_a["high_trigger_dates"], stat_b["low_trigger_dates"]),
        ("low_vs_high", stat_a["low_trigger_dates"], stat_b["high_trigger_dates"]),
    ]
    scored = []
    for side, dates_a, dates_b in candidates:
        scored.append(
            {
                "side": side,
                "jaccard": _jaccard(set(dates_a), set(dates_b)),
                "count_a": len(dates_a),
                "count_b": len(dates_b),
                "intersection_count": len(set(dates_a) & set(dates_b)),
                "union_count": len(set(dates_a) | set(dates_b)),
            }
        )
    return max(scored, key=lambda item: (item["jaccard"], item["intersection_count"], item["side"]))


def _behavior_comparison(stat_a: dict[str, Any], stat_b: dict[str, Any], overlap: dict[str, Any]) -> dict[str, Any]:
    spread_a = stat_a.get("top_minus_bottom_return")
    spread_b = stat_b.get("top_minus_bottom_return")
    mirror_alignment = overlap["side"] in {"high_vs_low", "low_vs_high"}
    spread_diff_abs = None if spread_a is None or spread_b is None else abs(float(spread_a) - float(spread_b))
    mirror_spread_diff_abs = None if spread_a is None or spread_b is None else abs(float(spread_a) + float(spread_b))
    same_direction = stat_a.get("direction_hint") == stat_b.get("direction_hint") and stat_a.get("direction_hint") not in {
        "NO_SPREAD",
        "INSUFFICIENT_DATA",
    }
    opposite_direction = (
        stat_a.get("direction_hint") != stat_b.get("direction_hint")
        and stat_a.get("direction_hint") not in {"NO_SPREAD", "INSUFFICIENT_DATA"}
        and stat_b.get("direction_hint") not in {"NO_SPREAD", "INSUFFICIENT_DATA"}
    )
    return {
        "direction_a": stat_a.get("direction_hint"),
        "direction_b": stat_b.get("direction_hint"),
        "same_direction": same_direction,
        "opposite_direction": opposite_direction,
        "mirror_alignment": mirror_alignment,
        "spread_a": spread_a,
        "spread_b": spread_b,
        "spread_diff_abs": spread_diff_abs,
        "mirror_spread_diff_abs": mirror_spread_diff_abs,
        "high_mean_diff_abs": _optional_abs_diff(stat_a.get("high_mean_forward_return"), stat_b.get("high_mean_forward_return")),
        "low_mean_diff_abs": _optional_abs_diff(stat_a.get("low_mean_forward_return"), stat_b.get("low_mean_forward_return")),
        "cross_high_low_mean_diff_abs": _optional_abs_diff(
            stat_a.get("high_mean_forward_return"),
            stat_b.get("low_mean_forward_return"),
        ),
        "cross_low_high_mean_diff_abs": _optional_abs_diff(
            stat_a.get("low_mean_forward_return"),
            stat_b.get("high_mean_forward_return"),
        ),
    }


def _relationship_type(
    overlap: dict[str, Any],
    behavior: dict[str, Any],
    min_trigger_count: int,
    trigger_similarity_threshold: float,
    spread_diff_threshold: float,
) -> str:
    if min(overlap["count_a"], overlap["count_b"]) < min_trigger_count:
        return "insufficient_trigger_count"
    trigger_similar = overlap["jaccard"] >= trigger_similarity_threshold
    if behavior["mirror_alignment"]:
        behavior_similar = (
            bool(behavior["opposite_direction"])
            and behavior["mirror_spread_diff_abs"] is not None
            and behavior["mirror_spread_diff_abs"] <= spread_diff_threshold
            and behavior["cross_high_low_mean_diff_abs"] is not None
            and behavior["cross_high_low_mean_diff_abs"] <= spread_diff_threshold
            and behavior["cross_low_high_mean_diff_abs"] is not None
            and behavior["cross_low_high_mean_diff_abs"] <= spread_diff_threshold
        )
    else:
        behavior_similar = bool(behavior["same_direction"]) and (
            behavior["spread_diff_abs"] is not None and behavior["spread_diff_abs"] <= spread_diff_threshold
        )
    if trigger_similar and behavior_similar:
        return "trigger_and_behavior_similar"
    if trigger_similar:
        return "trigger_similar"
    if behavior_similar:
        return "behavior_similar"
    return "weak_or_no_similarity"


def _build_cross_object_summary(
    pair_similarities: list[dict[str, Any]],
    trigger_similarity_threshold: float,
) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for item in pair_similarities:
        key = tuple(sorted([item["factor_a"], item["factor_b"]])) + (int(item["offset_days"]),)
        by_key[key].append(item)

    summaries = []
    for (factor_a, factor_b, offset_days), items in sorted(by_key.items()):
        eligible = [item for item in items if item["relationship_type"] != "insufficient_trigger_count"]
        if not eligible:
            continue
        trigger_similar = [
            item
            for item in eligible
            if item["relationship_type"] in {"trigger_similar", "trigger_and_behavior_similar"}
        ]
        behavior_similar = [
            item
            for item in eligible
            if item["relationship_type"] in {"behavior_similar", "trigger_and_behavior_similar"}
        ]
        strongest_overlap = max(item["best_trigger_overlap"]["jaccard"] for item in eligible)
        summaries.append(
            {
                "scope": "cross_object_summary",
                "factor_a": factor_a,
                "factor_b": factor_b,
                "offset_days": offset_days,
                "eligible_instrument_count": len(eligible),
                "trigger_similar_count": len(trigger_similar),
                "behavior_similar_count": len(behavior_similar),
                "trigger_similarity_ratio": len(trigger_similar) / len(eligible),
                "behavior_similarity_ratio": len(behavior_similar) / len(eligible),
                "max_trigger_jaccard": strongest_overlap,
                "global_signal_similarity": _global_signal_similarity(
                    eligible_count=len(eligible),
                    trigger_similar_count=len(trigger_similar),
                    behavior_similar_count=len(behavior_similar),
                    max_trigger_jaccard=strongest_overlap,
                    trigger_similarity_threshold=trigger_similarity_threshold,
                ),
                "instrument_evidence": [
                    {
                        "instrument_id": item["instrument_id"],
                        "relationship_type": item["relationship_type"],
                        "best_trigger_overlap": item["best_trigger_overlap"],
                        "behavior_comparison": item["behavior_comparison"],
                    }
                    for item in eligible
                ],
            }
        )
    return summaries


def _global_signal_similarity(
    eligible_count: int,
    trigger_similar_count: int,
    behavior_similar_count: int,
    max_trigger_jaccard: float,
    trigger_similarity_threshold: float,
) -> str:
    if eligible_count < 2:
        return "single_instrument_only"
    if trigger_similar_count / eligible_count >= 0.60 and behavior_similar_count / eligible_count >= 0.60:
        return "cross_object_trigger_and_behavior_similarity_candidate"
    if trigger_similar_count / eligible_count >= 0.60:
        return "cross_object_trigger_similarity_candidate"
    if behavior_similar_count / eligible_count >= 0.60:
        return "cross_object_behavior_similarity_candidate"
    if max_trigger_jaccard >= trigger_similarity_threshold:
        return "instrument_specific_similarity"
    return "no_cross_object_similarity"


def _build_report(config: dict[str, Any], manifest: dict[str, Any], cross_object_summary: list[dict[str, Any]]) -> str:
    candidates = [
        item
        for item in cross_object_summary
        if item["global_signal_similarity"]
        in {
            "cross_object_trigger_and_behavior_similarity_candidate",
            "cross_object_trigger_similarity_candidate",
            "cross_object_behavior_similarity_candidate",
        }
    ]
    instrument_specific = [
        item for item in cross_object_summary if item["global_signal_similarity"] == "instrument_specific_similarity"
    ]
    lines = [
        "# Factor Signal Similarity Report",
        "",
        "This report checks whether factors trigger on the same dates or behave similarly in backtests. It is not investment advice.",
        "",
        "## Scope",
        "",
        f"- Review ID: `{config['review_id']}`",
        f"- Instruments reviewed: `{len(manifest['instruments'])}`",
        f"- Trigger quantile: `{config['quantile']}`",
        f"- Trigger similarity threshold: `{config['trigger_similarity_threshold']}`",
        f"- Spread difference threshold: `{config['spread_diff_threshold']}`",
        "",
        "## Beginner Glossary",
        "",
        "- Trigger date: a day when a factor is in its high or low range.",
        "- High trigger: the factor value is in the highest slice of its own history.",
        "- Low trigger: the factor value is in the lowest slice of its own history.",
        "- Jaccard overlap: how much two trigger-date sets overlap. `1.0` means identical dates; `0.0` means no overlap.",
        "- Top-bottom spread: high-trigger average future return minus low-trigger average future return.",
        "",
        "## Cross-Object Similarity Candidates",
        "",
    ]
    if not candidates:
        lines.append("No cross-object trigger or behavior similarity candidates were found.")
    for item in candidates[:20]:
        lines.append(
            "- "
            f"`{item['factor_a']}` vs `{item['factor_b']}` N+{item['offset_days']}: "
            f"`{item['global_signal_similarity']}`, "
            f"trigger_similar={item['trigger_similar_count']}/{item['eligible_instrument_count']}, "
            f"behavior_similar={item['behavior_similar_count']}/{item['eligible_instrument_count']}, "
            f"max_jaccard={item['max_trigger_jaccard']:.3f}."
        )
    lines.extend(
        [
            "",
            "## Instrument-Specific Similarity",
            "",
        ]
    )
    if not instrument_specific:
        lines.append("No instrument-specific high-overlap pairs were found.")
    for item in instrument_specific[:20]:
        lines.append(
            "- "
            f"`{item['factor_a']}` vs `{item['factor_b']}` N+{item['offset_days']}: "
            f"max_jaccard={item['max_trigger_jaccard']:.3f}. "
            "This appears in one instrument but is not yet stable across the portfolio."
        )
    lines.extend(
        [
            "",
            "## Limitations",
            "",
            "- This report only studies existing factor evaluation artifacts.",
            "- It does not choose trading rules.",
            "- The first E2E batch is short and should be followed by a 2024-2025 discovery run and a 2026 holdout test.",
            "",
        ]
    )
    return "\n".join(lines)


def _direction_hint(spread: float | None) -> str:
    if spread is None:
        return "INSUFFICIENT_DATA"
    if spread > 0:
        return "HIGH_FACTOR_OUTPERFORMS"
    if spread < 0:
        return "LOW_FACTOR_OUTPERFORMS"
    return "NO_SPREAD"


def _plain_language_explanation(factor_a: str, factor_b: str, relationship_type: str) -> str:
    if relationship_type == "trigger_and_behavior_similar":
        return f"{factor_a} and {factor_b} often trigger on similar dates and show similar backtest behavior."
    if relationship_type == "trigger_similar":
        return f"{factor_a} and {factor_b} often trigger on similar dates, but their return behavior is not close enough."
    if relationship_type == "behavior_similar":
        return f"{factor_a} and {factor_b} show similar return behavior, but they do not usually trigger on the same dates."
    if relationship_type == "insufficient_trigger_count":
        return f"{factor_a} and {factor_b} do not have enough trigger dates for a reliable similarity decision."
    return f"{factor_a} and {factor_b} do not show strong signal or backtest-behavior similarity."


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _win_rate(rows: list[dict[str, Any]]) -> float | None:
    if not rows:
        return None
    return sum(1 for row in rows if row["forward_return"] > 0) / len(rows)


def _mean_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    return mean(values)


def _optional_abs_diff(left: Any, right: Any) -> float | None:
    if left is None or right is None:
        return None
    return abs(float(left) - float(right))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Malformed JSONL row in {path} at line {line_number}: {exc}") from exc
    return rows


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _file_checksum(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_review_id() -> str:
    return f"factor-signal-similarity-review-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
