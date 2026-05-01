import json
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from scripts.chip_factor_evaluator import evaluate_factor_run


def test_chip_factor_evaluator_aligns_factors_to_forward_returns(tmp_path: Path) -> None:
    run_dir = _write_factor_run(tmp_path / "factor-run-test")

    result = evaluate_factor_run(run_dir, offsets=[1, 3], evaluation_id="factor-eval-test")

    evaluation_dir = run_dir / "factor-evaluations" / "factor-eval-test"
    assert result.status == "completed"
    assert (evaluation_dir / "factor-forward-observations.jsonl").exists()
    summary = json.loads((evaluation_dir / "factor-evaluation-summary.json").read_text())
    assert summary["factor_run_id"] == "factor-run-test"
    assert summary["offsets"] == [1, 3]
    profit_summary = next(item for item in summary["summary_by_factor"] if item["factor_id"] == "profit_ratio_asof")
    n1_summary = next(item for item in profit_summary["offsets"] if item["offset_days"] == 1)
    assert n1_summary["available_count"] == 5
    assert n1_summary["unavailable_count"] == 0
    assert n1_summary["pearson_correlation"] is not None
    n3_summary = next(item for item in profit_summary["offsets"] if item["offset_days"] == 3)
    assert n3_summary["available_count"] == 3
    assert n3_summary["unavailable_count"] == 2
    assert (evaluation_dir / "factor-evaluation-report.md").read_text().startswith("# Factor Evaluation Report")


def test_chip_factor_evaluator_refuses_to_overwrite_existing_evaluation(tmp_path: Path) -> None:
    run_dir = _write_factor_run(tmp_path / "factor-run-test")

    evaluate_factor_run(run_dir, offsets=[1], evaluation_id="factor-eval-test")

    with pytest.raises(SystemExit, match="immutable"):
        evaluate_factor_run(run_dir, offsets=[1], evaluation_id="factor-eval-test")


def _write_factor_run(run_dir: Path) -> Path:
    stock_dir = run_dir / "stocks" / "000001.SZ"
    stock_dir.mkdir(parents=True)
    dates = [f"2026010{day}" for day in range(1, 7)]
    closes = [10, 11, 12, 11, 13, 15]
    (stock_dir / "daily-chip-snapshots.jsonl").write_text(
        "\n".join(json.dumps({"factor_date": date, "close": close}) for date, close in zip(dates, closes)) + "\n",
        encoding="utf-8",
    )
    factor_rows = []
    for index, date in enumerate(dates[:5]):
        factor_rows.append(
            {
                "factor_id": "profit_ratio_asof",
                "factor_date": date,
                "value": 40 + index * 10,
                "quality_status": "OK",
            }
        )
        factor_rows.append(
            {
                "factor_id": "loss_ratio_asof",
                "factor_date": date,
                "value": 60 - index * 10,
                "quality_status": "OK",
            }
        )
    (stock_dir / "factors.jsonl").write_text(
        "\n".join(json.dumps(row) for row in factor_rows) + "\n",
        encoding="utf-8",
    )
    (run_dir / "factor-run-manifest.json").write_text(
        json.dumps(
            {
                "factor_run_id": run_dir.name,
                "stock_outputs": [
                    {
                        "ts_code": "000001.SZ",
                        "snapshot_ref": "stocks/000001.SZ/daily-chip-snapshots.jsonl",
                        "factor_ref": "stocks/000001.SZ/factors.jsonl",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return run_dir
