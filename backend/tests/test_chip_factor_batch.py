import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from scripts.chip_factor_batch import run_factor_batch
from scripts.chip_factor_batch_aggregate import aggregate_factor_batches


def test_chip_factor_batch_isolates_stock_failures_and_aggregates_successes(tmp_path: Path) -> None:
    result = run_factor_batch(
            stock_codes=["000001.SZ", "000002.SZ", "600519.SH"],
        factor_start_date="20260101",
        factor_end_date="20260105",
        offsets=[1, 3],
        batch_root=tmp_path / "factor-batches",
        factor_run_root=tmp_path / "factor-runs",
        cache_root=tmp_path / "factor-cache",
        batch_id="factor-batch-test",
        sleep_between_stocks_seconds=0,
        run_factor_fn=fake_run_factor,
        evaluate_factor_fn=fake_evaluate_factor,
    )

    batch_dir = tmp_path / "factor-batches" / "factor-batch-test"
    assert result.status == "partial"
    assert result.success_count == 2
    assert result.failed_count == 1
    summary = json.loads((batch_dir / "factor-batch-summary.json").read_text())
    config = json.loads((batch_dir / "factor-batch-config.json").read_text())
    assert config["sleep_between_stocks_seconds"] == 0
    assert summary["success_count"] == 2
    assert summary["failed_count"] == 1
    assert {row["ts_code"] for row in summary["stock_results"] if row["status"] == "completed"} == {
        "000001.SZ",
        "600519.SH",
    }
    failed = next(row for row in summary["stock_results"] if row["status"] == "failed")
    assert failed["ts_code"] == "000002.SZ"
    assert "api_key=[REDACTED]" in failed["error_message"]
    profit_n1 = next(
        row
        for row in summary["aggregate_summary"]
        if row["factor_id"] == "profit_ratio_asof" and row["offset_days"] == 1
    )
    assert profit_n1["stock_count"] == 2
    assert profit_n1["positive_correlation_count"] == 2
    assert "Failed Stocks" in (batch_dir / "aggregate-factor-report.md").read_text()


def test_chip_factor_batch_normalizes_bare_stock_codes(tmp_path: Path) -> None:
    result = run_factor_batch(
        stock_codes=["603799"],
        factor_start_date="20260101",
        factor_end_date="20260105",
        offsets=[1],
        batch_root=tmp_path / "factor-batches",
        factor_run_root=tmp_path / "factor-runs",
        cache_root=tmp_path / "factor-cache",
        batch_id="factor-batch-normalize-test",
        sleep_between_stocks_seconds=0,
        run_factor_fn=fake_run_factor,
        evaluate_factor_fn=fake_evaluate_factor,
    )

    batch_dir = tmp_path / "factor-batches" / "factor-batch-normalize-test"
    config = json.loads((batch_dir / "factor-batch-config.json").read_text())
    summary = json.loads((batch_dir / "factor-batch-summary.json").read_text())
    assert result.status == "completed"
    assert config["stock_codes"] == ["603799.SH"]
    assert summary["stock_results"][0]["ts_code"] == "603799.SH"


def test_chip_factor_batch_refuses_to_overwrite_existing_batch(tmp_path: Path) -> None:
    kwargs = {
        "stock_codes": ["000001.SZ"],
        "factor_start_date": "20260101",
        "factor_end_date": "20260105",
        "offsets": [1],
        "batch_root": tmp_path / "factor-batches",
        "factor_run_root": tmp_path / "factor-runs",
        "cache_root": tmp_path / "factor-cache",
        "batch_id": "factor-batch-test",
        "run_factor_fn": fake_run_factor,
        "evaluate_factor_fn": fake_evaluate_factor,
    }
    run_factor_batch(**kwargs)

    with pytest.raises(SystemExit, match="immutable"):
        run_factor_batch(**kwargs)


def test_chip_factor_batch_aggregate_deduplicates_retry_success(tmp_path: Path) -> None:
    batch_root = tmp_path / "factor-batches"
    _write_batch_summary(
        batch_root,
        "factor-batch-first",
        [
            _completed_stock("000001.SZ", 0.2),
            {"ts_code": "600519.SH", "status": "failed", "error_type": "DataUnavailableError", "error_message": "rate limit"},
        ],
    )
    _write_batch_summary(batch_root, "factor-batch-retry", [_completed_stock("600519.SH", 0.3)])

    result = aggregate_factor_batches(
        batch_ids=["factor-batch-first", "factor-batch-retry"],
        batch_root=batch_root,
        aggregate_id="factor-batch-aggregate-test",
    )

    aggregate_dir = batch_root / "factor-batch-aggregate-test"
    assert result.status == "completed"
    assert result.stock_count == 2
    assert result.success_count == 2
    summary = json.loads((aggregate_dir / "factor-batch-summary.json").read_text())
    assert {stock["ts_code"] for stock in summary["stock_results"]} == {"000001.SZ", "600519.SH"}
    retry_stock = next(stock for stock in summary["stock_results"] if stock["ts_code"] == "600519.SH")
    assert retry_stock["status"] == "completed"
    assert retry_stock["source_batch_id"] == "factor-batch-retry"


def test_chip_factor_batch_aggregate_keeps_first_duplicate_success(tmp_path: Path) -> None:
    batch_root = tmp_path / "factor-batches"
    _write_batch_summary(batch_root, "factor-batch-first", [_completed_stock("000001.SZ", 0.2)])
    _write_batch_summary(batch_root, "factor-batch-second", [_completed_stock("000001.SZ", -0.9)])

    aggregate_factor_batches(
        batch_ids=["factor-batch-first", "factor-batch-second"],
        batch_root=batch_root,
        aggregate_id="factor-batch-aggregate-test",
    )

    summary = json.loads((batch_root / "factor-batch-aggregate-test" / "factor-batch-summary.json").read_text())
    assert summary["stock_results"][0]["source_batch_id"] == "factor-batch-first"
    assert summary["aggregate_summary"][0]["mean_pearson_correlation"] == 0.2


def test_chip_factor_batch_reraises_unexpected_programming_errors(tmp_path: Path) -> None:
    with pytest.raises(KeyError, match="schema drift"):
        run_factor_batch(
            stock_codes=["000001.SZ"],
            factor_start_date="20260101",
            factor_end_date="20260105",
            offsets=[1],
            batch_root=tmp_path / "factor-batches",
            factor_run_root=tmp_path / "factor-runs",
            cache_root=tmp_path / "factor-cache",
            batch_id="factor-batch-test",
            run_factor_fn=fake_programming_error_run_factor,
            evaluate_factor_fn=fake_evaluate_factor,
        )


def fake_run_factor(**kwargs: object) -> dict[str, str]:
    stock_code = kwargs["stock_codes"][0]
    run_id = str(kwargs["run_id"])
    artifact_root = Path(kwargs["artifact_root"])
    run_dir = artifact_root / run_id
    if stock_code == "000002.SZ":
        raise SystemExit("fake stock failure api_key=secret-value")
    run_dir.mkdir(parents=True)
    (run_dir / "factor-run-manifest.json").write_text(
        json.dumps({"factor_run_id": run_id, "stock_outputs": []}),
        encoding="utf-8",
    )
    return {"run_id": run_id, "status": "completed", "artifact_dir": str(run_dir)}


def fake_programming_error_run_factor(**kwargs: object) -> dict[str, str]:
    raise KeyError("schema drift")


def fake_evaluate_factor(**kwargs: object) -> SimpleNamespace:
    factor_run_dir = Path(kwargs["factor_run_dir"])
    evaluation_id = str(kwargs["evaluation_id"])
    evaluation_dir = factor_run_dir / "factor-evaluations" / evaluation_id
    evaluation_dir.mkdir(parents=True)
    summary_path = evaluation_dir / "factor-evaluation-summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "factor_run_id": factor_run_dir.name,
                "observation_count": 10,
                "summary_by_factor": [
                    {
                        "factor_id": "profit_ratio_asof",
                        "offsets": [
                            {
                                "offset_days": 1,
                                "available_count": 5,
                                "unavailable_count": 0,
                                "pearson_correlation": 0.2,
                                "top_minus_bottom_return": 0.01,
                            },
                            {
                                "offset_days": 3,
                                "available_count": 4,
                                "unavailable_count": 1,
                                "pearson_correlation": -0.1,
                                "top_minus_bottom_return": -0.02,
                            },
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return SimpleNamespace(
        evaluation_id=evaluation_id,
        artifact_dir=str(evaluation_dir),
        summary_ref=str(summary_path),
    )


def _write_batch_summary(batch_root: Path, batch_id: str, stock_results: list[dict[str, object]]) -> None:
    batch_dir = batch_root / batch_id
    batch_dir.mkdir(parents=True)
    batch_dir.joinpath("factor-batch-summary.json").write_text(
        json.dumps(
            {
                "batch_id": batch_id,
                "status": "partial",
                "stock_results": stock_results,
            }
        ),
        encoding="utf-8",
    )


def _completed_stock(ts_code: str, correlation: float) -> dict[str, object]:
    return {
        "ts_code": ts_code,
        "status": "completed",
        "summary_by_factor": [
            {
                "factor_id": "profit_ratio_asof",
                "offsets": [
                    {
                        "offset_days": 1,
                        "available_count": 5,
                        "unavailable_count": 0,
                        "pearson_correlation": correlation,
                        "top_minus_bottom_return": 0.01,
                    }
                ],
            }
        ],
    }
