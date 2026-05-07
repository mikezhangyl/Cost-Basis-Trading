from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path
from statistics import median
from typing import Any
from uuid import uuid4

import pandas as pd


STRONG_RELATIONSHIP_TYPES = {"same_direction_duplicate", "opposite_direction_duplicate"}
NO_EVIDENCE_RELATIONSHIP_TYPES = {"insufficient_observations", "constant_values", "malformed_input"}

FORMULA_HINTS: dict[frozenset[str], dict[str, str]] = {
    frozenset({"profit_ratio_asof", "loss_ratio_asof"}): {
        "evidence_type": "metadata_hint",
        "description": "profit_ratio_asof + loss_ratio_asof + at_close_ratio ~= total chip percent",
        "default_primary_hint": "loss_ratio_asof",
    },
    frozenset({"profit_ratio_delta_20d", "loss_ratio_delta_20d"}): {
        "evidence_type": "metadata_hint",
        "description": "20-day profit-ratio and loss-ratio changes are mirror changes when at-close mass is small",
        "default_primary_hint": "loss_ratio_delta_20d",
    },
    frozenset({"cyq_cgo_asof", "weighted_chip_cost_gap_asof"}): {
        "evidence_type": "metadata_hint",
        "description": "Both compare current price with the chip cost distribution; cyq_cgo_asof is marked as proxy while weighted_chip_cost_gap_asof is exact project factor-family implementation.",
        "default_primary_hint": "weighted_chip_cost_gap_asof",
    },
}


@dataclass(frozen=True)
class FactorRedundancyReviewResult:
    review_id: str
    status: str
    output_dir: str
    instrument_count: int
    factor_pair_count: int


def main() -> int:
    parser = argparse.ArgumentParser(description="Review factor redundancy with per-instrument isolation.")
    parser.add_argument("--factor-batch-summary", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--correlation-threshold", type=float, default=0.90)
    parser.add_argument("--min-observations", type=int, default=30)
    parser.add_argument("--method", choices=["pearson", "spearman"], default="pearson")
    parser.add_argument("--min-instruments-for-global-summary", type=int, default=2)
    parser.add_argument("--strong-consensus-ratio", type=float, default=0.80)
    args = parser.parse_args()

    result = review_factor_redundancy(
        factor_batch_summary=args.factor_batch_summary,
        output_dir=args.output_dir,
        correlation_threshold=args.correlation_threshold,
        min_observations=args.min_observations,
        method=args.method,
        min_instruments_for_global_summary=args.min_instruments_for_global_summary,
        strong_consensus_ratio=args.strong_consensus_ratio,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def review_factor_redundancy(
    factor_batch_summary: Path,
    output_dir: Path,
    correlation_threshold: float = 0.90,
    min_observations: int = 30,
    method: str = "pearson",
    min_instruments_for_global_summary: int = 2,
    strong_consensus_ratio: float = 0.80,
    review_id: str | None = None,
) -> dict[str, Any]:
    _validate_config(
        factor_batch_summary=factor_batch_summary,
        output_dir=output_dir,
        correlation_threshold=correlation_threshold,
        min_observations=min_observations,
        method=method,
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
        "correlation_threshold": correlation_threshold,
        "min_observations": min_observations,
        "method": method,
        "min_instruments_for_global_summary": min_instruments_for_global_summary,
        "strong_consensus_ratio": strong_consensus_ratio,
        "instrument_isolation": True,
        "raw_pooled_correlation_policy": "diagnostic_only",
    }
    _write_json(output_dir / "review-config.json", config)

    _append_jsonl(events_path, {"timestamp": _now_iso(), "event": "source_manifest_started", "review_id": review_id})
    manifest = _build_source_manifest(factor_batch_summary)
    _write_json(output_dir / "source-data-manifest.json", manifest)
    _append_jsonl(events_path, {"timestamp": _now_iso(), "event": "source_manifest_completed", "review_id": review_id})

    _append_jsonl(events_path, {"timestamp": _now_iso(), "event": "load_factor_data_started", "review_id": review_id})
    instruments = [_load_instrument_factor_rows(item) for item in manifest["instruments"]]
    _append_jsonl(
        events_path,
        {
            "timestamp": _now_iso(),
            "event": "load_factor_data_completed",
            "review_id": review_id,
            "instrument_count": len(instruments),
        },
    )

    _append_jsonl(events_path, {"timestamp": _now_iso(), "event": "per_instrument_correlation_started", "review_id": review_id})
    all_relationships: list[dict[str, Any]] = []
    for instrument in instruments:
        relationships, decisions, matrix = _review_one_instrument(
            instrument_id=instrument["instrument_id"],
            rows=instrument["rows"],
            method=method,
            correlation_threshold=correlation_threshold,
            min_observations=min_observations,
        )
        all_relationships.extend(relationships)
        instrument_dir = output_dir / "per-instrument" / instrument["instrument_id"]
        instrument_dir.mkdir(parents=True)
        matrix.to_csv(instrument_dir / "factor-correlation-matrix.csv")
        _write_json(instrument_dir / "factor-pair-relationships.json", relationships)
        _write_json(instrument_dir / "factor-retention-decisions.json", decisions)
    _append_jsonl(events_path, {"timestamp": _now_iso(), "event": "per_instrument_correlation_completed", "review_id": review_id})

    _append_jsonl(events_path, {"timestamp": _now_iso(), "event": "cross_object_summary_started", "review_id": review_id})
    cross_object_summary = _build_cross_object_summary(
        all_relationships,
        min_instruments_for_global_summary=min_instruments_for_global_summary,
        strong_consensus_ratio=strong_consensus_ratio,
    )
    _write_json(output_dir / "cross-object-redundancy-summary.json", cross_object_summary)
    pooled_diagnostics = _build_pooled_diagnostics(
        instruments=instruments,
        cross_object_summary=cross_object_summary,
        method=method,
        correlation_threshold=correlation_threshold,
    )
    _write_json(output_dir / "pooled-diagnostics.json", pooled_diagnostics)
    _append_jsonl(events_path, {"timestamp": _now_iso(), "event": "cross_object_summary_completed", "review_id": review_id})

    groups = _build_groups(cross_object_summary)
    _write_json(output_dir / "factor-redundancy-groups.json", groups)
    _write_text(output_dir / "factor-redundancy-report.md", _build_report(config, manifest, cross_object_summary))
    _append_jsonl(events_path, {"timestamp": _now_iso(), "event": "write_artifacts_completed", "review_id": review_id})
    _append_jsonl(events_path, {"timestamp": _now_iso(), "event": "review_completed", "review_id": review_id})

    result = FactorRedundancyReviewResult(
        review_id=review_id,
        status="completed",
        output_dir=str(output_dir),
        instrument_count=len(instruments),
        factor_pair_count=len(all_relationships),
    )
    return asdict(result)


def _validate_config(
    factor_batch_summary: Path,
    output_dir: Path,
    correlation_threshold: float,
    min_observations: int,
    method: str,
) -> None:
    if output_dir.exists():
        raise SystemExit(f"Factor redundancy review artifact already exists and is immutable: {output_dir}")
    if not factor_batch_summary.exists():
        raise SystemExit(f"Factor batch summary does not exist: {factor_batch_summary}")
    if method not in {"pearson", "spearman"}:
        raise SystemExit(f"Unsupported correlation method: {method}")
    if not 0 < correlation_threshold <= 1:
        raise SystemExit("correlation_threshold must be greater than 0 and less than or equal to 1.")
    if min_observations < 2:
        raise SystemExit("min_observations must be at least 2.")


def _build_source_manifest(factor_batch_summary: Path) -> dict[str, Any]:
    summary = _load_json(factor_batch_summary)
    completed_results = [result for result in summary.get("stock_results", []) if result.get("status") == "completed"]
    if not completed_results:
        raise SystemExit("Factor batch summary has no completed stock results.")

    instruments = []
    for result in completed_results:
        instrument_id = result.get("ts_code")
        factor_run_dir = result.get("factor_run_dir")
        if not instrument_id or not factor_run_dir:
            raise SystemExit("Completed stock result must include ts_code and factor_run_dir.")
        factors_jsonl = Path(str(factor_run_dir)) / "stocks" / str(instrument_id) / "factors.jsonl"
        if not factors_jsonl.exists():
            raise SystemExit(f"Expected factors JSONL does not exist: {factors_jsonl}")
        stats = _factor_file_stats(factors_jsonl)
        instruments.append(
            {
                "instrument_id": str(instrument_id),
                "factor_run_dir": str(factor_run_dir),
                "factors_jsonl": str(factors_jsonl),
                **stats,
            }
        )
    return {
        "factor_batch_summary_path": str(factor_batch_summary),
        "factor_run_dirs": [item["factor_run_dir"] for item in instruments],
        "instruments": instruments,
    }


def _factor_file_stats(path: Path) -> dict[str, Any]:
    row_count = 0
    ok_value_count = 0
    dates = []
    for row in _read_jsonl(path):
        row_count += 1
        if row.get("factor_date"):
            dates.append(str(row["factor_date"]))
        if row.get("quality_status") == "OK" and row.get("value") is not None:
            ok_value_count += 1
    return {
        "row_count": row_count,
        "ok_value_count": ok_value_count,
        "date_min": min(dates) if dates else None,
        "date_max": max(dates) if dates else None,
    }


def _load_instrument_factor_rows(instrument: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for row in _read_jsonl(Path(instrument["factors_jsonl"])):
        _validate_factor_row(row)
        value = row.get("value")
        if row.get("quality_status") != "OK" or value is None:
            continue
        rows.append(
            {
                "instrument_id": instrument["instrument_id"],
                "factor_date": str(row["factor_date"]),
                "factor_id": str(row["factor_id"]),
                "value": float(value),
                "source_level": row.get("source_level"),
                "implementation_type": row.get("implementation_type"),
                "explanation": row.get("explanation"),
            }
        )
    return {"instrument_id": instrument["instrument_id"], "rows": rows}


def _validate_factor_row(row: dict[str, Any]) -> None:
    if "factor_id" not in row or "factor_date" not in row:
        raise SystemExit("Factor row must include factor_id and factor_date.")
    value = row.get("value")
    if value is not None and not isinstance(value, int | float):
        raise SystemExit(f"Factor row has non-numeric value: {value}")


def _review_one_instrument(
    instrument_id: str,
    rows: list[dict[str, Any]],
    method: str,
    correlation_threshold: float,
    min_observations: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], pd.DataFrame]:
    table = _pivot_rows(rows)
    corr_matrix = table.corr(method=method) if not table.empty else pd.DataFrame()
    relationships = []
    factor_ids = sorted(table.columns)
    for factor_a, factor_b in combinations(factor_ids, 2):
        pair_values = table[[factor_a, factor_b]].dropna()
        observation_count = len(pair_values)
        correlation: float | None = None
        if observation_count >= 2:
            correlation_value = pair_values[factor_a].corr(pair_values[factor_b], method=method)
            if pd.notna(correlation_value):
                correlation = float(correlation_value)
        relationship_type = _classify_relationship(
            factor_a=factor_a,
            factor_b=factor_b,
            pair_values=pair_values,
            correlation=correlation,
            threshold=correlation_threshold,
            min_observations=min_observations,
        )
        relationships.append(
            _build_pair_relationship(
                instrument_id=instrument_id,
                factor_a=factor_a,
                factor_b=factor_b,
                relationship_type=relationship_type,
                correlation=correlation,
                method=method,
                observation_count=observation_count,
            )
        )
    return relationships, _build_retention_decisions(instrument_id, factor_ids, relationships), corr_matrix


def _pivot_rows(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    return frame.pivot_table(index="factor_date", columns="factor_id", values="value", aggfunc="first")


def _classify_relationship(
    factor_a: str,
    factor_b: str,
    pair_values: pd.DataFrame,
    correlation: float | None,
    threshold: float,
    min_observations: int,
) -> str:
    if len(pair_values) < min_observations:
        return "insufficient_observations"
    if pair_values.nunique(dropna=True).min() <= 1:
        return "constant_values"
    if correlation is None:
        return "weak_or_no_relationship"
    if _is_derived_pair(factor_a, factor_b):
        return "derived_but_not_duplicate"
    if correlation >= threshold:
        return "same_direction_duplicate"
    if correlation <= -threshold:
        return "opposite_direction_duplicate"
    return "weak_or_no_relationship"


def _build_pair_relationship(
    instrument_id: str,
    factor_a: str,
    factor_b: str,
    relationship_type: str,
    correlation: float | None,
    method: str,
    observation_count: int,
) -> dict[str, Any]:
    formula_hint = FORMULA_HINTS.get(frozenset({factor_a, factor_b}))
    primary_factor = _primary_factor(factor_a, factor_b, formula_hint)
    recommendation = _pair_recommendation(relationship_type, formula_hint)
    relationship = {
        "scope": "per_instrument",
        "instrument_id": instrument_id,
        "factor_a": factor_a,
        "factor_b": factor_b,
        "relationship_type": relationship_type,
        "recommendation": recommendation,
        "primary_factor": primary_factor,
        "replacement_factor": primary_factor if recommendation == "exclude" else None,
        "correlation": correlation,
        "correlation_method": method,
        "observation_count": observation_count,
        "formula_evidence": formula_hint,
        "tie_breaker_evidence": _tie_breaker_evidence(primary_factor, formula_hint),
        "plain_language_explanation": _plain_language_explanation(factor_a, factor_b, relationship_type),
    }
    return relationship


def _pair_recommendation(relationship_type: str, formula_hint: dict[str, str] | None) -> str:
    if relationship_type == "derived_but_not_duplicate":
        return "keep_with_warning"
    if relationship_type in STRONG_RELATIONSHIP_TYPES:
        return "exclude" if formula_hint is not None else "downweight"
    if relationship_type in NO_EVIDENCE_RELATIONSHIP_TYPES:
        return "no_decision"
    return "keep_with_warning"


def _is_derived_pair(factor_a: str, factor_b: str) -> bool:
    return _factor_base(factor_a) == _factor_base(factor_b) and {_factor_kind(factor_a), _factor_kind(factor_b)} == {"asof", "delta"}


def _factor_base(factor_id: str) -> str:
    if factor_id.endswith("_asof"):
        return factor_id.removesuffix("_asof")
    if factor_id.endswith("_delta_20d"):
        return factor_id.removesuffix("_delta_20d")
    return factor_id


def _factor_kind(factor_id: str) -> str:
    if factor_id.endswith("_asof"):
        return "asof"
    if factor_id.endswith("_delta_20d"):
        return "delta"
    return "other"


def _primary_factor(factor_a: str, factor_b: str, formula_hint: dict[str, str] | None) -> str | None:
    if formula_hint and formula_hint.get("default_primary_hint") in {factor_a, factor_b}:
        return formula_hint["default_primary_hint"]
    return sorted([factor_a, factor_b])[0]


def _tie_breaker_evidence(primary_factor: str | None, formula_hint: dict[str, str] | None) -> list[str]:
    if primary_factor is None:
        return []
    if formula_hint and formula_hint.get("default_primary_hint") == primary_factor:
        return [f"{primary_factor} is the metadata primary hint for this factor family."]
    return [f"{primary_factor} was selected by stable lexical fallback."]


def _plain_language_explanation(factor_a: str, factor_b: str, relationship_type: str) -> str:
    if relationship_type == "same_direction_duplicate":
        return f"{factor_a} and {factor_b} usually move in the same direction inside this investment object."
    if relationship_type == "opposite_direction_duplicate":
        return f"{factor_a} and {factor_b} usually move in opposite directions inside this investment object."
    if relationship_type == "insufficient_observations":
        return f"{factor_a} and {factor_b} do not have enough overlapping observations for a reliable decision."
    if relationship_type == "constant_values":
        return f"{factor_a} and {factor_b} include a constant series, so correlation is not informative."
    return f"{factor_a} and {factor_b} do not show strong redundancy inside this investment object."


def _build_retention_decisions(
    instrument_id: str,
    factor_ids: list[str],
    relationships: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    formula_primary_factors = {
        relationship["primary_factor"]
        for relationship in relationships
        if relationship["relationship_type"] in STRONG_RELATIONSHIP_TYPES
        and relationship.get("formula_evidence") is not None
        and relationship.get("primary_factor") is not None
    }
    decisions = {
        factor_id: {
            "scope": "per_instrument",
            "instrument_id": instrument_id,
            "factor_id": factor_id,
            "decision": "keep",
            "reason": "No stronger redundant replacement was selected inside this instrument.",
            "related_factors": [],
            "replacement_factor": None,
            "evidence_refs": [],
        }
        for factor_id in factor_ids
    }
    for relationship in relationships:
        if relationship["relationship_type"] not in STRONG_RELATIONSHIP_TYPES:
            continue
        primary = relationship["primary_factor"]
        recommendation = relationship["recommendation"]
        factors = [relationship["factor_a"], relationship["factor_b"]]
        for factor_id in factors:
            related = next(other for other in factors if other != factor_id)
            decisions[factor_id]["related_factors"].append(related)
            decisions[factor_id]["evidence_refs"].append(
                {
                    "artifact": "factor-pair-relationships.json",
                    "factor_a": relationship["factor_a"],
                    "factor_b": relationship["factor_b"],
                }
            )
            if factor_id != primary and recommendation == "exclude":
                decisions[factor_id]["decision"] = "exclude"
                decisions[factor_id]["replacement_factor"] = primary
                decisions[factor_id]["reason"] = f"Highly redundant with {primary} inside this instrument."
            elif (
                factor_id != primary
                and recommendation == "downweight"
                and factor_id not in formula_primary_factors
                and decisions[factor_id]["decision"] != "exclude"
            ):
                decisions[factor_id]["decision"] = "downweight"
                decisions[factor_id]["replacement_factor"] = primary
                decisions[factor_id]["reason"] = f"Overlaps with {primary} inside this instrument; avoid counting it as independent evidence."
            elif (
                factor_id == primary
                and recommendation == "downweight"
                and related in formula_primary_factors
                and factor_id not in formula_primary_factors
                and decisions[factor_id]["decision"] != "exclude"
            ):
                decisions[factor_id]["decision"] = "downweight"
                decisions[factor_id]["replacement_factor"] = related
                decisions[factor_id]["reason"] = f"Overlaps with protected formula-primary factor {related}; avoid counting it as independent evidence."
    return [decisions[factor_id] for factor_id in sorted(decisions)]


def _build_cross_object_summary(
    relationships: list[dict[str, Any]],
    min_instruments_for_global_summary: int,
    strong_consensus_ratio: float,
) -> list[dict[str, Any]]:
    by_pair: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for relationship in relationships:
        pair_key = tuple(sorted([relationship["factor_a"], relationship["factor_b"]]))
        by_pair[pair_key].append(relationship)

    summaries = []
    for (factor_a, factor_b), pair_relationships in sorted(by_pair.items()):
        eligible = [item for item in pair_relationships if item["relationship_type"] not in NO_EVIDENCE_RELATIONSHIP_TYPES]
        if not eligible:
            continue
        strong = [item for item in eligible if item["relationship_type"] in STRONG_RELATIONSHIP_TYPES]
        correlations = [item["correlation"] for item in eligible if item["correlation"] is not None]
        dominant_type = _dominant_relationship_type(strong)
        consensus_ratio = len(strong) / len(eligible) if eligible else 0
        summaries.append(
            {
                "scope": "cross_object_summary",
                "factor_a": factor_a,
                "factor_b": factor_b,
                "eligible_instrument_count": len(eligible),
                "strong_relationship_count": len(strong),
                "insufficient_instrument_count": len(pair_relationships) - len(eligible),
                "consensus_ratio": consensus_ratio,
                "dominant_relationship_type": dominant_type,
                "correlation_median": median(correlations) if correlations else None,
                "correlation_min": min(correlations) if correlations else None,
                "correlation_max": max(correlations) if correlations else None,
                "global_recommendation": _global_recommendation(
                    eligible_count=len(eligible),
                    strong_count=len(strong),
                    consensus_ratio=consensus_ratio,
                    min_instruments=min_instruments_for_global_summary,
                    strong_consensus_ratio=strong_consensus_ratio,
                ),
                "instrument_evidence": [
                    {
                        "instrument_id": item["instrument_id"],
                        "relationship_type": item["relationship_type"],
                        "correlation": item["correlation"],
                        "observation_count": item["observation_count"],
                    }
                    for item in eligible
                ],
                "plain_language_explanation": "This is an aggregation of per-instrument evidence, not a raw pooled correlation.",
            }
        )
    return summaries


def _dominant_relationship_type(strong_relationships: list[dict[str, Any]]) -> str | None:
    if not strong_relationships:
        return None
    return Counter(item["relationship_type"] for item in strong_relationships).most_common(1)[0][0]


def _global_recommendation(
    eligible_count: int,
    strong_count: int,
    consensus_ratio: float,
    min_instruments: int,
    strong_consensus_ratio: float,
) -> str:
    if strong_count == 0:
        return "global_no_decision"
    if eligible_count >= min_instruments and consensus_ratio >= strong_consensus_ratio:
        return "global_downweight_candidate"
    return "global_review_required"


def _build_groups(cross_object_summary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups = []
    for item in cross_object_summary:
        if item["global_recommendation"] not in {"global_downweight_candidate", "global_review_required"}:
            continue
        groups.append(
            {
                "group_id": f"{item['factor_a']}__{item['factor_b']}",
                "group_scope": "cross_object_summary",
                "group_type": item["dominant_relationship_type"] or "mixed_evidence",
                "factors": [item["factor_a"], item["factor_b"]],
                "reason": item["plain_language_explanation"],
            }
        )
    return groups


def _build_pooled_diagnostics(
    instruments: list[dict[str, Any]],
    cross_object_summary: list[dict[str, Any]],
    method: str,
    correlation_threshold: float,
) -> list[dict[str, Any]]:
    rows = [row for instrument in instruments for row in instrument["rows"]]
    pooled_table = _pivot_pooled_rows(rows)
    if pooled_table.empty:
        return []
    summary_by_pair = {
        tuple(sorted([item["factor_a"], item["factor_b"]])): item
        for item in cross_object_summary
    }
    diagnostics = []
    for factor_a, factor_b in combinations(sorted(pooled_table.columns), 2):
        pair_values = pooled_table[[factor_a, factor_b]].dropna()
        if len(pair_values) < 2 or pair_values.nunique(dropna=True).min() <= 1:
            continue
        correlation_value = pair_values[factor_a].corr(pair_values[factor_b], method=method)
        if pd.isna(correlation_value):
            continue
        raw_pooled_correlation = float(correlation_value)
        pair_summary = summary_by_pair.get(tuple(sorted([factor_a, factor_b])))
        if abs(raw_pooled_correlation) >= correlation_threshold and pair_summary and pair_summary["global_recommendation"] == "global_no_decision":
            diagnostics.append(
                {
                    "scope": "diagnostic_only",
                    "diagnostic_type": "pooling_artifact_risk",
                    "factor_a": factor_a,
                    "factor_b": factor_b,
                    "raw_pooled_correlation": raw_pooled_correlation,
                    "correlation_method": method,
                    "pooled_observation_count": len(pair_values),
                    "per_instrument_global_recommendation": pair_summary["global_recommendation"],
                    "plain_language_explanation": "The pair looks highly correlated only after investment objects are pooled; do not use this to exclude factors.",
                }
            )
    return diagnostics


def _pivot_pooled_rows(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows).copy()
    frame["pooled_row_id"] = frame["instrument_id"].astype(str) + ":" + frame["factor_date"].astype(str)
    return frame.pivot_table(index="pooled_row_id", columns="factor_id", values="value", aggfunc="first")


def _build_report(config: dict[str, Any], manifest: dict[str, Any], cross_object_summary: list[dict[str, Any]]) -> str:
    high_confidence = [item for item in cross_object_summary if item["global_recommendation"] == "global_downweight_candidate"]
    needs_review = [item for item in cross_object_summary if item["global_recommendation"] == "global_review_required"]
    no_decision_count = sum(1 for item in cross_object_summary if item["global_recommendation"] == "global_no_decision")
    lines = [
        "# Factor Redundancy Report",
        "",
        "This report reviews whether factors overlap. It is not investment advice.",
        "",
        "This review does not compare one investment object's raw factor values against another investment object's raw factor values.",
        "",
        "## Scope",
        "",
        f"- Review ID: `{config['review_id']}`",
        f"- Instruments reviewed: `{len(manifest['instruments'])}`",
        f"- Correlation method: `{config['method']}`",
        f"- Correlation threshold: `{config['correlation_threshold']}`",
        f"- Minimum observations: `{config['min_observations']}`",
        "",
        "## Beginner Glossary",
        "",
        "- Factor: a numeric feature calculated for one investment object on one date.",
        "- Correlation: a number showing whether two factors usually move together or opposite each other.",
        "- Redundant factor: two factors are redundant if they mostly tell the same story.",
        "- Mirror factor: high values of one factor usually mean low values of another factor.",
        "- Cross-object summary: a summary of patterns found separately inside multiple investment objects.",
        "",
        "## High-Confidence Redundancy Candidates",
        "",
    ]
    if not high_confidence:
        lines.append("No high-confidence cross-object redundancy candidates were found.")
    for item in high_confidence:
        lines.append(
            f"- `{item['factor_a']}` vs `{item['factor_b']}`: "
            f"{item['strong_relationship_count']}/{item['eligible_instrument_count']} strong relationships, "
            f"dominant type=`{item['dominant_relationship_type']}`, "
            f"median correlation=`{_format_correlation(item['correlation_median'])}`."
        )
    lines.extend(
        [
            "",
            "## Needs Review",
            "",
        ]
    )
    if not needs_review:
        lines.append("No mixed-evidence factor pairs require manual review.")
    for item in needs_review:
        lines.append(
            f"- `{item['factor_a']}` vs `{item['factor_b']}`: "
            f"{item['strong_relationship_count']}/{item['eligible_instrument_count']} strong relationships. "
            "Do not exclude globally without checking per-instrument evidence."
        )
    lines.extend(
        [
            "",
            "## No Strong Cross-Object Decision",
            "",
            f"- `{no_decision_count}` factor pairs had no strong cross-object decision.",
            "",
            "## Limitations",
            "",
            "- Formula hints are metadata evidence unless a future implementation verifies them from snapshot rows.",
            "- Global recommendations summarize per-instrument evidence and do not override per-instrument decisions.",
            "",
        ]
    )
    return "\n".join(lines)


def _format_correlation(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.4f}"


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


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _build_review_id() -> str:
    return f"factor-redundancy-review-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
