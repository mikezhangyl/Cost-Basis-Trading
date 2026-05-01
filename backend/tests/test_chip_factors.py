import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.domain.models import ChipDistributionPoint, DailyPriceBar
from app.factors.chip_factors import build_daily_chip_snapshot, build_factor_values


def test_daily_chip_snapshot_calculates_hand_checked_values() -> None:
    snapshot = build_daily_chip_snapshot(
        ts_code="000001.SZ",
        factor_date="20260105",
        chip_points=[
            ChipDistributionPoint(ts_code="000001.SZ", trade_date="20260105", price=10, percent=20),
            ChipDistributionPoint(ts_code="000001.SZ", trade_date="20260105", price=11, percent=50),
            ChipDistributionPoint(ts_code="000001.SZ", trade_date="20260105", price=12, percent=30),
        ],
        price_bar=DailyPriceBar(
            ts_code="000001.SZ",
            trade_date="20260105",
            open=11,
            high=12,
            low=10,
            close=11.5,
        ),
    )

    assert snapshot.weighted_chip_cost == pytest.approx(11.1)
    assert snapshot.dominant_peak_price == 11
    assert snapshot.dominant_peak_percent == 50
    assert snapshot.profit_ratio == 70
    assert snapshot.loss_ratio == 30
    assert snapshot.at_close_ratio == 0
    assert snapshot.cyq_cgo == pytest.approx((11.5 - 11.1) / 11.5)
    assert snapshot.concentration_width_70 == pytest.approx(1 / 11.5)
    assert snapshot.concentration_width_90 == pytest.approx(2 / 11.5)
    assert snapshot.concentration_width_70 <= snapshot.concentration_width_90
    assert snapshot.chip_weighted_std == pytest.approx(0.7 / 11.5)
    assert snapshot.max_input_date == "20260105"


def test_daily_chip_snapshot_marks_price_dependent_fields_missing_without_close() -> None:
    snapshot = build_daily_chip_snapshot(
        ts_code="000001.SZ",
        factor_date="20260105",
        chip_points=[
            ChipDistributionPoint(ts_code="000001.SZ", trade_date="20260105", price=10, percent=40),
            ChipDistributionPoint(ts_code="000001.SZ", trade_date="20260105", price=11, percent=60),
        ],
        price_bar=None,
    )

    assert snapshot.weighted_chip_cost == pytest.approx(10.6)
    assert snapshot.dominant_peak_price == 11
    assert snapshot.profit_ratio is None
    assert snapshot.loss_ratio is None
    assert snapshot.cyq_cgo is None
    assert snapshot.concentration_width_70 is None
    assert snapshot.chip_weighted_std is None


def test_factor_values_mark_lookback_factors_insufficient_without_warmup() -> None:
    snapshot = build_daily_chip_snapshot(
        ts_code="000001.SZ",
        factor_date="20260105",
        chip_points=[
            ChipDistributionPoint(ts_code="000001.SZ", trade_date="20260105", price=10, percent=20),
            ChipDistributionPoint(ts_code="000001.SZ", trade_date="20260105", price=11, percent=80),
        ],
        price_bar=DailyPriceBar(
            ts_code="000001.SZ",
            trade_date="20260105",
            open=10,
            high=11,
            low=9,
            close=10.5,
        ),
    )

    factors = build_factor_values([snapshot], factor_date="20260105")
    by_id = {factor.factor_id: factor for factor in factors}

    assert by_id["profit_ratio_asof"].value == 20
    assert by_id["loss_ratio_asof"].value == 80
    assert by_id["profit_ratio_delta_20d"].value is None
    assert by_id["profit_ratio_delta_20d"].quality_status == "INSUFFICIENT_HISTORY"
    assert by_id["profit_ratio_delta_20d"].lookback_days == 20


def test_factor_values_require_complete_expected_trading_window_for_lookback() -> None:
    expected_dates = [f"202601{day:02d}" for day in range(1, 22)]
    snapshots = [
        build_daily_chip_snapshot(
            ts_code="000001.SZ",
            factor_date=trade_date,
            chip_points=[
                ChipDistributionPoint(ts_code="000001.SZ", trade_date=trade_date, price=10, percent=50),
                ChipDistributionPoint(ts_code="000001.SZ", trade_date=trade_date, price=11, percent=50),
            ],
            price_bar=DailyPriceBar(
                ts_code="000001.SZ",
                trade_date=trade_date,
                open=10,
                high=11,
                low=9,
                close=10.5,
            ),
        )
        for trade_date in expected_dates
        if trade_date != "20260110"
    ]

    factors = build_factor_values(
        snapshots,
        factor_date="20260121",
        expected_trading_dates=expected_dates,
    )
    by_id = {factor.factor_id: factor for factor in factors}

    assert by_id["profit_ratio_delta_20d"].quality_status == "INSUFFICIENT_HISTORY"
    assert by_id["profit_ratio_delta_20d"].value is None


def test_chip_factor_runner_dry_run_writes_immutable_artifact_package(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/chip_factor_runner.py",
            "--stock-codes",
            "000001.SZ",
            "--factor-start-date",
            "20260101",
            "--factor-end-date",
            "20260430",
            "--artifact-root",
            str(tmp_path),
            "--run-id",
            "factor-run-test",
            "--dry-run",
        ],
        check=True,
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
    )

    payload = json.loads(completed.stdout)
    run_dir = tmp_path / "factor-run-test"
    stock_dir = run_dir / "stocks" / "000001.SZ"
    assert payload["run_id"] == "factor-run-test"
    assert payload["status"] == "completed"
    assert (run_dir / "factor-run-config.json").exists()
    assert (run_dir / "factor-run-manifest.json").exists()
    assert (run_dir / "api-calls.jsonl").exists()
    assert (run_dir / "api-retry-events.jsonl").exists()
    assert (run_dir / "worker-events.jsonl").exists()
    assert (stock_dir / "daily-chip-snapshots.jsonl").exists()
    assert (stock_dir / "factors.jsonl").exists()
    assert (stock_dir / "factor-quality.json").exists()
    assert (stock_dir / "factor-traceability.json").exists()
    manifest = json.loads((run_dir / "factor-run-manifest.json").read_text())
    assert manifest["status"] == "completed"
    assert manifest["immutable"] is True
    factors = [
        json.loads(line)
        for line in (stock_dir / "factors.jsonl").read_text().splitlines()
        if line.strip()
    ]
    factor_dates = {factor["factor_date"] for factor in factors}
    assert "20260101" in factor_dates
    assert "20260430" in factor_dates
    assert manifest["stock_outputs"][0]["factor_date_count"] == len(factor_dates)


def test_chip_factor_runner_refuses_to_overwrite_existing_run(tmp_path: Path) -> None:
    command = [
        sys.executable,
        "scripts/chip_factor_runner.py",
        "--stock-codes",
        "000001.SZ",
        "--factor-start-date",
        "20260101",
        "--factor-end-date",
        "20260430",
        "--artifact-root",
        str(tmp_path),
        "--run-id",
        "factor-run-test",
        "--dry-run",
    ]
    subprocess.run(
        command,
        check=True,
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
    )

    completed = subprocess.run(
        command,
        check=False,
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "immutable" in completed.stderr
