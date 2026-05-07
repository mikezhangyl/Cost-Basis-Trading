import json
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from scripts.factor_signal_similarity_review import review_factor_signal_similarity


def test_factor_signal_similarity_review_detects_trigger_and_behavior_similarity(tmp_path: Path) -> None:
    summary_path = _write_batch_summary(
        tmp_path,
        {
            "AAA.SZ": {
                "factor_a": [1, 2, 3, 4, 5, 6],
                "factor_b": [2, 4, 6, 8, 10, 12],
                "factor_c": [6, 1, 5, 2, 4, 3],
            },
            "BBB.SZ": {
                "factor_a": [1, 2, 3, 4, 5, 6],
                "factor_b": [1, 2, 3, 4, 5, 6],
                "factor_c": [3, 4, 1, 6, 2, 5],
            },
        },
    )
    output_dir = tmp_path / "signal-review"

    result = review_factor_signal_similarity(
        factor_batch_summary=summary_path,
        output_dir=output_dir,
        quantile=0.34,
        min_trigger_count=2,
        trigger_similarity_threshold=1.0,
        spread_diff_threshold=0.001,
        review_id="signal-review-test",
    )

    assert result["status"] == "completed"
    aaa_pairs = _load_json(output_dir / "per-instrument" / "AAA.SZ" / "factor-pair-signal-similarity.json")
    aaa_pair = _find_pair(aaa_pairs, "factor_a", "factor_b", offset_days=1)
    assert aaa_pair["relationship_type"] == "trigger_and_behavior_similar"
    assert aaa_pair["best_trigger_overlap"]["jaccard"] == 1.0

    summary = _load_json(output_dir / "cross-object-signal-similarity-summary.json")
    pair_summary = _find_pair(summary, "factor_a", "factor_b", offset_days=1)
    assert pair_summary["global_signal_similarity"] == "cross_object_trigger_and_behavior_similarity_candidate"
    assert pair_summary["trigger_similar_count"] == 2
    assert (output_dir / "factor-signal-similarity-report.md").exists()
    assert (output_dir / "review-events.jsonl").exists()


def test_factor_signal_similarity_review_detects_behavior_without_trigger_overlap(tmp_path: Path) -> None:
    summary_path = _write_batch_summary(
        tmp_path,
        {
            "AAA.SZ": {
                "factor_a": [1, 2, 3, 4, 5, 6],
                "factor_b": [2, 4, 6, 1, 3, 5],
            },
        },
    )
    output_dir = tmp_path / "signal-review"

    review_factor_signal_similarity(
        factor_batch_summary=summary_path,
        output_dir=output_dir,
        quantile=0.34,
        min_trigger_count=2,
        trigger_similarity_threshold=1.0,
        spread_diff_threshold=0.05,
    )

    pairs = _load_json(output_dir / "per-instrument" / "AAA.SZ" / "factor-pair-signal-similarity.json")
    pair = _find_pair(pairs, "factor_a", "factor_b", offset_days=1)
    assert pair["relationship_type"] == "behavior_similar"
    assert pair["best_trigger_overlap"]["jaccard"] < 1.0


def test_factor_signal_similarity_review_detects_mirror_trigger_and_behavior_similarity(tmp_path: Path) -> None:
    summary_path = _write_batch_summary(
        tmp_path,
        {
            "AAA.SZ": {
                "factor_a": [1, 2, 3, 4, 5, 6],
                "factor_b": [6, 5, 4, 3, 2, 1],
            },
        },
    )
    output_dir = tmp_path / "signal-review"

    review_factor_signal_similarity(
        factor_batch_summary=summary_path,
        output_dir=output_dir,
        quantile=0.34,
        min_trigger_count=2,
        trigger_similarity_threshold=1.0,
        spread_diff_threshold=0.001,
    )

    pairs = _load_json(output_dir / "per-instrument" / "AAA.SZ" / "factor-pair-signal-similarity.json")
    pair = _find_pair(pairs, "factor_a", "factor_b", offset_days=1)
    assert pair["relationship_type"] == "trigger_and_behavior_similar"
    assert pair["best_trigger_overlap"]["side"] in {"high_vs_low", "low_vs_high"}
    assert pair["behavior_comparison"]["mirror_alignment"] is True


def test_factor_signal_similarity_review_rejects_mixed_ts_code_rows(tmp_path: Path) -> None:
    summary_path = _write_batch_summary(
        tmp_path,
        {
            "AAA.SZ": {
                "factor_a": [1, 2, 3, 4, 5, 6],
                "factor_b": [2, 4, 6, 8, 10, 12],
            },
        },
    )
    observations_path = tmp_path / "evaluations" / "AAA.SZ" / "factor-forward-observations.jsonl"
    observations_path.write_text(
        observations_path.read_text(encoding="utf-8")
        + json.dumps(
            {
                "ts_code": "BBB.SZ",
                "factor_id": "factor_a",
                "factor_date": "20260107",
                "factor_value": 7,
                "offset_days": 1,
                "forward_return": 0.04,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="unexpected ts_code"):
        review_factor_signal_similarity(
            factor_batch_summary=summary_path,
            output_dir=tmp_path / "signal-review",
        )


def test_factor_signal_similarity_review_marks_low_trigger_count(tmp_path: Path) -> None:
    summary_path = _write_batch_summary(
        tmp_path,
        {
            "AAA.SZ": {
                "factor_a": [1, 2, 3, 4],
                "factor_b": [2, 4, 6, 8],
            },
        },
    )
    output_dir = tmp_path / "signal-review"

    review_factor_signal_similarity(
        factor_batch_summary=summary_path,
        output_dir=output_dir,
        quantile=0.25,
        min_trigger_count=2,
    )

    pairs = _load_json(output_dir / "per-instrument" / "AAA.SZ" / "factor-pair-signal-similarity.json")
    pair = _find_pair(pairs, "factor_a", "factor_b", offset_days=1)
    assert pair["relationship_type"] == "insufficient_trigger_count"


def test_factor_signal_similarity_review_handles_multiple_offsets(tmp_path: Path) -> None:
    summary_path = _write_batch_summary(
        tmp_path,
        {
            "AAA.SZ": {
                "factor_a": [1, 2, 3, 4, 5, 6],
                "factor_b": [2, 4, 6, 8, 10, 12],
            },
        },
        offsets=[1, 3],
    )
    output_dir = tmp_path / "signal-review"

    review_factor_signal_similarity(
        factor_batch_summary=summary_path,
        output_dir=output_dir,
        quantile=0.34,
        min_trigger_count=2,
        trigger_similarity_threshold=1.0,
        spread_diff_threshold=0.001,
    )

    pairs = _load_json(output_dir / "per-instrument" / "AAA.SZ" / "factor-pair-signal-similarity.json")
    assert _find_pair(pairs, "factor_a", "factor_b", offset_days=1)["relationship_type"] == "trigger_and_behavior_similar"
    assert _find_pair(pairs, "factor_a", "factor_b", offset_days=3)["relationship_type"] == "trigger_and_behavior_similar"


def test_factor_signal_similarity_review_refuses_existing_output_dir(tmp_path: Path) -> None:
    summary_path = _write_batch_summary(
        tmp_path,
        {
            "AAA.SZ": {
                "factor_a": [1, 2, 3],
                "factor_b": [3, 2, 1],
            },
        },
    )
    output_dir = tmp_path / "signal-review"
    output_dir.mkdir()

    with pytest.raises(SystemExit, match="immutable"):
        review_factor_signal_similarity(
            factor_batch_summary=summary_path,
            output_dir=output_dir,
        )


def _write_batch_summary(
    tmp_path: Path,
    instruments: dict[str, dict[str, list[float]]],
    offsets: list[int] | None = None,
) -> Path:
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()
    stock_results = []
    for instrument_id, factor_values_by_id in instruments.items():
        evaluation_dir = tmp_path / "evaluations" / instrument_id
        evaluation_dir.mkdir(parents=True)
        _write_observations(
            evaluation_dir / "factor-forward-observations.jsonl",
            instrument_id,
            factor_values_by_id,
            offsets=offsets or [1],
        )
        stock_results.append(
            {
                "ts_code": instrument_id,
                "status": "completed",
                "evaluation_dir": str(evaluation_dir),
            }
        )
    summary_path = batch_dir / "factor-batch-summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "batch_id": "test-batch",
                "status": "completed",
                "stock_results": stock_results,
            }
        ),
        encoding="utf-8",
    )
    return summary_path


def _write_observations(
    path: Path,
    instrument_id: str,
    factor_values_by_id: dict[str, list[float]],
    offsets: list[int],
) -> None:
    lines = []
    returns_by_index = [-0.03, -0.02, -0.01, 0.01, 0.02, 0.03]
    for factor_id, values in factor_values_by_id.items():
        for index, value in enumerate(values):
            for offset in offsets:
                lines.append(
                    json.dumps(
                        {
                            "ts_code": instrument_id,
                            "factor_id": factor_id,
                            "factor_date": f"202601{index + 1:02d}",
                            "factor_value": value,
                            "offset_days": offset,
                            "forward_return": returns_by_index[index],
                        }
                    )
                )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _find_pair(items: list[dict[str, object]], factor_a: str, factor_b: str, offset_days: int) -> dict[str, object]:
    expected = {factor_a, factor_b}
    for item in items:
        if {item["factor_a"], item["factor_b"]} == expected and item["offset_days"] == offset_days:
            return item
    raise AssertionError(f"Missing pair: {factor_a}, {factor_b}, N+{offset_days}")


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))
