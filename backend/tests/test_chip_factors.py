import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.domain.models import ChipDistributionPoint, DailyPriceBar
from app.factors.chip_factors import ACTIVE_FACTOR_IDS, PRUNED_FACTOR_IDS, build_daily_chip_snapshot, build_factor_values
from scripts.chip_factor_runner import run_factor_production


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

    assert by_id["loss_ratio_asof"].value == 80
    assert by_id["loss_ratio_delta_20d"].value is None
    assert by_id["loss_ratio_delta_20d"].quality_status == "INSUFFICIENT_HISTORY"
    assert by_id["loss_ratio_delta_20d"].lookback_days == 20
    assert "profit_ratio_asof" not in by_id
    assert "profit_ratio_delta_20d" not in by_id
    assert "cyq_cgo_asof" not in by_id


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

    assert by_id["loss_ratio_delta_20d"].quality_status == "INSUFFICIENT_HISTORY"
    assert by_id["loss_ratio_delta_20d"].value is None


def test_factor_values_exclude_formula_pruned_duplicate_factors() -> None:
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
    factor_ids = {factor.factor_id for factor in factors}

    assert len(factor_ids) == 10
    assert factor_ids == set(ACTIVE_FACTOR_IDS)
    assert factor_ids.isdisjoint(PRUNED_FACTOR_IDS)
    assert {"loss_ratio_asof", "loss_ratio_delta_20d", "weighted_chip_cost_gap_asof"}.issubset(factor_ids)


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
    assert (stock_dir / "factor-retention-policy.json").exists()
    manifest = json.loads((run_dir / "factor-run-manifest.json").read_text())
    config = json.loads((run_dir / "factor-run-config.json").read_text())
    assert manifest["status"] == "completed"
    assert manifest["immutable"] is True
    assert config["factor_retention_policy"]["active_factor_ids"] == list(ACTIVE_FACTOR_IDS)
    assert config["factor_retention_policy"]["excluded_factor_ids"] == sorted(PRUNED_FACTOR_IDS)
    factors = [
        json.loads(line)
        for line in (stock_dir / "factors.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert {factor["factor_id"] for factor in factors} == set(ACTIVE_FACTOR_IDS)
    factor_dates = {factor["factor_date"] for factor in factors}
    assert "20260101" in factor_dates
    assert "20260430" in factor_dates
    assert manifest["stock_outputs"][0]["factor_date_count"] == len(factor_dates)


def test_chip_factor_runner_normalizes_bare_stock_codes(tmp_path: Path) -> None:
    result = run_factor_production(
        stock_codes=["603799"],
        factor_start_date="20260101",
        factor_end_date="20260105",
        artifact_root=tmp_path / "factor-runs",
        cache_root=tmp_path / "factor-cache",
        run_id="factor-run-normalize-test",
        dry_run=True,
    )

    run_dir = Path(result["artifact_dir"])
    config = json.loads((run_dir / "factor-run-config.json").read_text())
    manifest = json.loads((run_dir / "factor-run-manifest.json").read_text())
    assert config["stock_codes"] == ["603799.SH"]
    assert manifest["stock_outputs"][0]["ts_code"] == "603799.SH"
    assert (run_dir / "stocks" / "603799.SH" / "factors.jsonl").exists()


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


def test_chip_factor_runner_live_mode_uses_local_cache_and_retry_logs(tmp_path: Path) -> None:
    client = FakeFactorDataClient()

    result = run_factor_production(
        stock_codes=["000001.SZ"],
        factor_start_date="20260105",
        factor_end_date="20260107",
        artifact_root=tmp_path / "factor-runs",
        cache_root=tmp_path / "factor-cache",
        run_id="factor-run-live-test",
        dry_run=False,
        data_client=client,
        warmup_trading_days=20,
    )

    run_dir = tmp_path / "factor-runs" / "factor-run-live-test"
    stock_dir = run_dir / "stocks" / "000001.SZ"
    assert result["status"] == "completed"
    assert client.retry_event_handler is None
    assert (tmp_path / "factor-cache" / "tushare" / "cyq_chips" / "000001.SZ" / "20251201.json").exists()
    assert (tmp_path / "factor-cache" / "tushare" / "daily" / "000001.SZ" / "20251201_20260107.json").exists()

    api_calls = [
        json.loads(line)
        for line in (run_dir / "api-calls.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert [call["endpoint"] for call in api_calls] == ["trade_cal", "cyq_chips", "daily"]
    assert all(call["status"] == "ok" for call in api_calls)
    assert api_calls[0]["params"]["start_date"] == "20251106"
    assert api_calls[0]["params"]["factor_start_date"] == "20260105"

    retry_events = [
        json.loads(line)
        for line in (run_dir / "api-retry-events.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert retry_events[0]["endpoint"] == "cyq_chips"
    assert retry_events[0]["status"] == "retrying"

    factors = [
        json.loads(line)
        for line in (stock_dir / "factors.jsonl").read_text().splitlines()
        if line.strip()
    ]
    factor_dates = {factor["factor_date"] for factor in factors}
    assert factor_dates == {"20260105", "20260106", "20260107"}
    by_date_and_id = {(factor["factor_date"], factor["factor_id"]): factor for factor in factors}
    assert by_date_and_id[("20260105", "loss_ratio_delta_20d")]["quality_status"] == "OK"
    assert by_date_and_id[("20260105", "loss_ratio_delta_20d")]["value"] is not None
    manifest = json.loads((run_dir / "factor-run-manifest.json").read_text())
    assert manifest["dry_run"] is False
    assert manifest["stock_outputs"][0]["factor_date_count"] == 3
    assert manifest["stock_outputs"][0]["warmup_snapshot_count"] == 20
    assert manifest["stock_outputs"][0]["checksums"]["factors.jsonl"].startswith("sha256:")
    assert manifest["stock_outputs"][0]["checksums"]["factor-retention-policy.json"].startswith("sha256:")
    chip_cache = json.loads(
        (tmp_path / "factor-cache" / "tushare" / "cyq_chips" / "000001.SZ" / "20251201.json").read_text()
    )
    assert chip_cache["source"]["factor_run_id"] == "factor-run-live-test"
    assert chip_cache["rows_checksum"].startswith("sha256:")


def test_chip_factor_runner_live_mode_restores_retry_handler_when_api_call_fails(tmp_path: Path) -> None:
    client = FailingFactorDataClient()
    original_handler = lambda event: None
    client.set_retry_event_handler(original_handler)

    with pytest.raises(RuntimeError, match="temporary failure"):
        run_factor_production(
            stock_codes=["000001.SZ"],
            factor_start_date="20260105",
            factor_end_date="20260107",
            artifact_root=tmp_path / "factor-runs",
            cache_root=tmp_path / "factor-cache",
            run_id="factor-run-live-failure-test",
            dry_run=False,
            data_client=client,
            warmup_trading_days=20,
        )

    run_dir = tmp_path / "factor-runs" / "factor-run-live-failure-test"
    assert client.retry_event_handler is original_handler
    api_calls = [
        json.loads(line)
        for line in (run_dir / "api-calls.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert [(call["endpoint"], call["status"]) for call in api_calls] == [
        ("trade_cal", "ok"),
        ("cyq_chips", "failed"),
    ]
    assert "temporary failure" in api_calls[-1]["error"]


def test_chip_factor_runner_live_mode_supports_private_trading_day_resolver(tmp_path: Path) -> None:
    client = PrivateTradingDaysFactorDataClient()

    run_factor_production(
        stock_codes=["000001.SZ"],
        factor_start_date="20260105",
        factor_end_date="20260105",
        artifact_root=tmp_path / "factor-runs",
        cache_root=tmp_path / "factor-cache",
        run_id="factor-run-live-private-resolver-test",
        dry_run=False,
        data_client=client,
        warmup_trading_days=2,
    )

    assert client.private_resolver_called is True


class FakeFactorDataClient:
    def __init__(self) -> None:
        self.retry_event_handler = None

    def set_retry_event_handler(self, handler: object) -> None:
        self.retry_event_handler = handler

    def get_trading_days_between(self, start_date: str, end_date: str) -> list[str]:
        return [f"202512{day:02d}" for day in range(1, 21)] + ["20260105", "20260106", "20260107"]

    def get_chip_distribution(self, ts_code: str, start_date: str, end_date: str) -> list[ChipDistributionPoint]:
        if self.retry_event_handler is not None:
            self.retry_event_handler(
                {
                    "endpoint": "cyq_chips",
                    "params": {"ts_code": ts_code, "trade_date": "20260102"},
                    "attempt": 1,
                    "max_retries": 3,
                    "status": "retrying",
                    "retryable": True,
                    "sleep_seconds": 0,
                    "error_code": "NETWORK_ERROR",
                    "error_message": "fake retry",
                    "raw_error_message": "temporary",
                }
            )
        rows: list[ChipDistributionPoint] = []
        for index, trade_date in enumerate(self.get_trading_days_between(start_date, end_date)):
            base = 10 + index
            rows.extend(
                [
                    ChipDistributionPoint(ts_code=ts_code, trade_date=trade_date, price=base, percent=20),
                    ChipDistributionPoint(ts_code=ts_code, trade_date=trade_date, price=base + 1, percent=50),
                    ChipDistributionPoint(ts_code=ts_code, trade_date=trade_date, price=base + 2, percent=30),
                ]
            )
        return rows

    def get_daily_prices(self, ts_code: str, start_date: str, end_date: str) -> list[DailyPriceBar]:
        return [
            DailyPriceBar(
                ts_code=ts_code,
                trade_date=trade_date,
                open=10 + index,
                high=12 + index,
                low=9 + index,
                close=11.5 + index,
                vol=1000 + index,
                amount=10000 + index,
            )
            for index, trade_date in enumerate(self.get_trading_days_between(start_date, end_date))
        ]


class FailingFactorDataClient(FakeFactorDataClient):
    def get_chip_distribution(self, ts_code: str, start_date: str, end_date: str) -> list[ChipDistributionPoint]:
        raise RuntimeError("temporary failure")


class PrivateTradingDaysFactorDataClient(FakeFactorDataClient):
    get_trading_days_between = None

    def __init__(self) -> None:
        super().__init__()
        self.private_resolver_called = False

    def _trading_days_between(self, start_date: str, end_date: str) -> list[str]:
        self.private_resolver_called = True
        return ["20251201", "20251202", "20260105"]

    def get_chip_distribution(self, ts_code: str, start_date: str, end_date: str) -> list[ChipDistributionPoint]:
        rows: list[ChipDistributionPoint] = []
        for index, trade_date in enumerate(self._trading_days_between(start_date, end_date)):
            base = 10 + index
            rows.extend(
                [
                    ChipDistributionPoint(ts_code=ts_code, trade_date=trade_date, price=base, percent=20),
                    ChipDistributionPoint(ts_code=ts_code, trade_date=trade_date, price=base + 1, percent=50),
                    ChipDistributionPoint(ts_code=ts_code, trade_date=trade_date, price=base + 2, percent=30),
                ]
            )
        return rows

    def get_daily_prices(self, ts_code: str, start_date: str, end_date: str) -> list[DailyPriceBar]:
        return [
            DailyPriceBar(
                ts_code=ts_code,
                trade_date=trade_date,
                open=10 + index,
                high=12 + index,
                low=9 + index,
                close=11.5 + index,
                vol=1000 + index,
                amount=10000 + index,
            )
            for index, trade_date in enumerate(self._trading_days_between(start_date, end_date))
        ]
