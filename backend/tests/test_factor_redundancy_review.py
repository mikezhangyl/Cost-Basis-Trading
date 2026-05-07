import json
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from scripts.factor_redundancy_review import review_factor_redundancy


def test_factor_redundancy_review_isolates_instruments_before_summary(tmp_path: Path) -> None:
    summary_path = _write_batch_summary(
        tmp_path,
        {
            "AAA.SZ": {
                "factor_a": [1, 2, 3, 4, 5],
                "factor_b": [2, 4, 6, 8, 10],
            },
            "BBB.SZ": {
                "factor_a": [1, 2, 3, 4, 5],
                "factor_b": [5, 1, 4, 2, 3],
            },
        },
    )
    output_dir = tmp_path / "review"

    result = review_factor_redundancy(
        factor_batch_summary=summary_path,
        output_dir=output_dir,
        correlation_threshold=0.95,
        min_observations=3,
    )

    assert result["status"] == "completed"
    aaa_pairs = _load_json(output_dir / "per-instrument" / "AAA.SZ" / "factor-pair-relationships.json")
    bbb_pairs = _load_json(output_dir / "per-instrument" / "BBB.SZ" / "factor-pair-relationships.json")
    aaa_pair = _find_pair(aaa_pairs, "factor_a", "factor_b")
    bbb_pair = _find_pair(bbb_pairs, "factor_a", "factor_b")
    assert aaa_pair["relationship_type"] == "same_direction_duplicate"
    assert bbb_pair["relationship_type"] == "weak_or_no_relationship"
    assert aaa_pair["instrument_id"] == "AAA.SZ"
    assert bbb_pair["instrument_id"] == "BBB.SZ"

    summary = _load_json(output_dir / "cross-object-redundancy-summary.json")
    pair_summary = _find_pair(summary, "factor_a", "factor_b")
    assert pair_summary["eligible_instrument_count"] == 2
    assert pair_summary["strong_relationship_count"] == 1
    assert pair_summary["consensus_ratio"] == 0.5
    assert pair_summary["global_recommendation"] == "global_review_required"


def test_factor_redundancy_review_never_creates_cross_instrument_pairs(tmp_path: Path) -> None:
    summary_path = _write_batch_summary(
        tmp_path,
        {
            "AAA.SZ": {
                "only_a": [1, 2, 3, 4, 5],
            },
            "BBB.SZ": {
                "only_b": [1, 2, 3, 4, 5],
            },
        },
    )
    output_dir = tmp_path / "review"

    review_factor_redundancy(
        factor_batch_summary=summary_path,
        output_dir=output_dir,
        correlation_threshold=0.95,
        min_observations=3,
    )

    aaa_pairs = _load_json(output_dir / "per-instrument" / "AAA.SZ" / "factor-pair-relationships.json")
    bbb_pairs = _load_json(output_dir / "per-instrument" / "BBB.SZ" / "factor-pair-relationships.json")
    summary = _load_json(output_dir / "cross-object-redundancy-summary.json")
    assert aaa_pairs == []
    assert bbb_pairs == []
    assert summary == []


def test_factor_redundancy_review_marks_low_observation_pairs_no_decision(tmp_path: Path) -> None:
    summary_path = _write_batch_summary(
        tmp_path,
        {
            "AAA.SZ": {
                "factor_a": [1, 2],
                "factor_b": [2, 4],
            },
        },
    )
    output_dir = tmp_path / "review"

    review_factor_redundancy(
        factor_batch_summary=summary_path,
        output_dir=output_dir,
        correlation_threshold=0.95,
        min_observations=3,
    )

    pairs = _load_json(output_dir / "per-instrument" / "AAA.SZ" / "factor-pair-relationships.json")
    pair = _find_pair(pairs, "factor_a", "factor_b")
    assert pair["relationship_type"] == "insufficient_observations"
    assert pair["recommendation"] == "no_decision"
    assert pair["observation_count"] == 2


def test_factor_redundancy_review_includes_formula_hint_evidence(tmp_path: Path) -> None:
    summary_path = _write_batch_summary(
        tmp_path,
        {
            "AAA.SZ": {
                "profit_ratio_asof": [10, 20, 30, 40, 50],
                "loss_ratio_asof": [90, 80, 70, 60, 50],
            },
        },
    )
    output_dir = tmp_path / "review"

    review_factor_redundancy(
        factor_batch_summary=summary_path,
        output_dir=output_dir,
        correlation_threshold=0.95,
        min_observations=3,
    )

    pairs = _load_json(output_dir / "per-instrument" / "AAA.SZ" / "factor-pair-relationships.json")
    pair = _find_pair(pairs, "profit_ratio_asof", "loss_ratio_asof")
    assert pair["relationship_type"] == "opposite_direction_duplicate"
    assert pair["formula_evidence"]["evidence_type"] == "metadata_hint"
    assert "at_close_ratio" in pair["formula_evidence"]["description"]


def test_factor_redundancy_review_downweights_non_formula_duplicates(tmp_path: Path) -> None:
    summary_path = _write_batch_summary(
        tmp_path,
        {
            "AAA.SZ": {
                "factor_a": [1, 2, 3, 4, 5],
                "factor_b": [2, 4, 6, 8, 10],
            },
        },
    )
    output_dir = tmp_path / "review"

    review_factor_redundancy(
        factor_batch_summary=summary_path,
        output_dir=output_dir,
        correlation_threshold=0.95,
        min_observations=3,
    )

    pairs = _load_json(output_dir / "per-instrument" / "AAA.SZ" / "factor-pair-relationships.json")
    pair = _find_pair(pairs, "factor_a", "factor_b")
    assert pair["relationship_type"] == "same_direction_duplicate"
    assert pair["recommendation"] == "downweight"
    decisions = _load_json(output_dir / "per-instrument" / "AAA.SZ" / "factor-retention-decisions.json")
    assert _find_decision(decisions, "factor_b")["decision"] == "downweight"


def test_factor_redundancy_review_keeps_asof_delta_relationship_with_warning(tmp_path: Path) -> None:
    summary_path = _write_batch_summary(
        tmp_path,
        {
            "AAA.SZ": {
                "profit_ratio_asof": [1, 2, 3, 4, 5],
                "profit_ratio_delta_20d": [1, 2, 3, 4, 5],
            },
        },
    )
    output_dir = tmp_path / "review"

    review_factor_redundancy(
        factor_batch_summary=summary_path,
        output_dir=output_dir,
        correlation_threshold=0.95,
        min_observations=3,
    )

    pairs = _load_json(output_dir / "per-instrument" / "AAA.SZ" / "factor-pair-relationships.json")
    pair = _find_pair(pairs, "profit_ratio_asof", "profit_ratio_delta_20d")
    assert pair["relationship_type"] == "derived_but_not_duplicate"
    assert pair["recommendation"] == "keep_with_warning"
    decisions = _load_json(output_dir / "per-instrument" / "AAA.SZ" / "factor-retention-decisions.json")
    assert _find_decision(decisions, "profit_ratio_asof")["decision"] == "keep"
    assert _find_decision(decisions, "profit_ratio_delta_20d")["decision"] == "keep"


def test_factor_redundancy_review_preserves_formula_primary_against_non_formula_overlap(tmp_path: Path) -> None:
    summary_path = _write_batch_summary(
        tmp_path,
        {
            "AAA.SZ": {
                "loss_ratio_asof": [1, 2, 3, 4, 5],
                "profit_ratio_asof": [5, 4, 3, 2, 1],
                "cyq_cgo_asof": [1, 2, 3, 4, 5],
            },
        },
    )
    output_dir = tmp_path / "review"

    review_factor_redundancy(
        factor_batch_summary=summary_path,
        output_dir=output_dir,
        correlation_threshold=0.95,
        min_observations=3,
    )

    decisions = _load_json(output_dir / "per-instrument" / "AAA.SZ" / "factor-retention-decisions.json")
    assert _find_decision(decisions, "loss_ratio_asof")["decision"] == "keep"
    assert _find_decision(decisions, "profit_ratio_asof")["decision"] == "exclude"
    assert _find_decision(decisions, "cyq_cgo_asof")["decision"] == "downweight"


def test_factor_redundancy_review_flags_pooled_artifact_risk_without_excluding(tmp_path: Path) -> None:
    summary_path = _write_batch_summary(
        tmp_path,
        {
            "AAA.SZ": {
                "factor_a": [1, 2, 3, 4, 5],
                "factor_b": [3, 1, 4, 2, 5],
            },
            "BBB.SZ": {
                "factor_a": [101, 102, 103, 104, 105],
                "factor_b": [103, 101, 104, 102, 105],
            },
        },
    )
    output_dir = tmp_path / "review"

    review_factor_redundancy(
        factor_batch_summary=summary_path,
        output_dir=output_dir,
        correlation_threshold=0.90,
        min_observations=3,
    )

    diagnostics = _load_json(output_dir / "pooled-diagnostics.json")
    diagnostic = _find_pair(diagnostics, "factor_a", "factor_b")
    assert diagnostic["diagnostic_type"] == "pooling_artifact_risk"
    assert diagnostic["raw_pooled_correlation"] >= 0.90
    summary = _load_json(output_dir / "cross-object-redundancy-summary.json")
    pair_summary = _find_pair(summary, "factor_a", "factor_b")
    assert pair_summary["global_recommendation"] == "global_no_decision"


def test_factor_redundancy_review_refuses_existing_output_dir(tmp_path: Path) -> None:
    summary_path = _write_batch_summary(
        tmp_path,
        {
            "AAA.SZ": {
                "factor_a": [1, 2, 3],
                "factor_b": [3, 2, 1],
            },
        },
    )
    output_dir = tmp_path / "review"
    output_dir.mkdir()

    with pytest.raises(SystemExit, match="immutable"):
        review_factor_redundancy(
            factor_batch_summary=summary_path,
            output_dir=output_dir,
            correlation_threshold=0.95,
            min_observations=3,
        )


def _write_batch_summary(tmp_path: Path, instruments: dict[str, dict[str, list[float]]]) -> Path:
    stock_results = []
    for instrument_id, factors_by_id in instruments.items():
        run_dir = tmp_path / f"factor-run-{instrument_id.replace('.', '-')}"
        stock_dir = run_dir / "stocks" / instrument_id
        stock_dir.mkdir(parents=True)
        rows = []
        for factor_id, values in factors_by_id.items():
            for index, value in enumerate(values, start=1):
                rows.append(
                    {
                        "factor_id": factor_id,
                        "factor_date": f"202601{index:02d}",
                        "value": value,
                        "quality_status": "OK",
                        "source_level": "FACTOR_FAMILY",
                        "implementation_type": "exact",
                        "explanation": factor_id,
                    }
                )
        (stock_dir / "factors.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n",
            encoding="utf-8",
        )
        stock_results.append(
            {
                "ts_code": instrument_id,
                "status": "completed",
                "factor_run_id": run_dir.name,
                "factor_run_dir": str(run_dir),
            }
        )
    summary_path = tmp_path / "factor-batch-summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "batch_id": "factor-batch-test",
                "status": "completed",
                "stock_results": stock_results,
            }
        ),
        encoding="utf-8",
    )
    return summary_path


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_pair(pairs: list[dict[str, object]], factor_a: str, factor_b: str) -> dict[str, object]:
    expected = {factor_a, factor_b}
    for pair in pairs:
        if {str(pair["factor_a"]), str(pair["factor_b"])} == expected:
            return pair
    raise AssertionError(f"Pair not found: {factor_a}, {factor_b}")


def _find_decision(decisions: list[dict[str, object]], factor_id: str) -> dict[str, object]:
    for decision in decisions:
        if decision["factor_id"] == factor_id:
            return decision
    raise AssertionError(f"Decision not found: {factor_id}")
