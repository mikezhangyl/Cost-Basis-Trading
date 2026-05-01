import hashlib
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from scripts.ecc_artifact_reviewer import (
    EccArtifactReviewer,
    StaticArtifactReviewClient,
    collect_factor_source_artifacts,
    collect_source_artifacts,
)


class FindingArtifactReviewClient:
    def review(self, payload: dict[str, object]) -> dict[str, object]:
        return {
            "report": "External reviewer found one issue.",
            "summary": "Issue found.",
            "findings": [
                {
                    "severity": "medium",
                    "category": "external",
                    "title": "External issue.",
                    "evidence": ["example"],
                    "expected": "No external issue.",
                    "suggested_fix": "Review manually.",
                }
            ],
        }


def test_ecc_artifact_reviewer_writes_run_local_artifacts(tmp_path: Path) -> None:
    research_root = tmp_path / "research-runs"
    plan_root = tmp_path / "plans"
    _write_plan_docs(plan_root)
    _write_research_run(research_root, "run-test-1")
    reviewer = EccArtifactReviewer(
        research_run_root=research_root,
        plan_doc_paths=[plan_root / "current-state.md", plan_root / "architecture.md"],
    )

    result = reviewer.review_run("run-test-1")

    assert result.run_id == "run-test-1"
    assert result.status == "needs_fix"
    assert result.findings_count == 2
    review_dir = Path(result.artifact_dir)
    assert review_dir.parent == research_root / "run-test-1" / "ecc-artifact-reviews"
    assert (review_dir / "review-config.json").exists()
    assert (review_dir / "source-artifacts.json").exists()
    assert (review_dir / "plan-snapshot.json").exists()
    assert (review_dir / "findings.json").exists()
    assert (review_dir / "fix-plan-draft.md").exists()
    assert (review_dir / "artifact-review-report.md").exists()
    assert (review_dir / "quality-subagent-review-prompt.md").exists()
    assert (review_dir / "review-state.json").exists()
    assert (review_dir / "workflow-events.jsonl").exists()
    assert (review_dir / "external-review-calls.jsonl").exists()

    latest = json.loads((research_root / "run-test-1" / "ecc-artifact-reviews" / "latest.json").read_text())
    assert latest["review_id"] == result.review_id
    assert latest["artifact_dir"] == result.artifact_dir

    findings = json.loads((review_dir / "findings.json").read_text())
    assert findings["status"] == "needs_fix"
    assert findings["findings"][0]["category"] == "plan_mismatch"
    assert "N+180" in findings["findings"][0]["expected"]

    report = (review_dir / "artifact-review-report.md").read_text()
    assert "# ECC Artifact Review Report" in report
    assert "ECC Quality Sub-Agent Semantic Review" in report
    assert "Pending ECC Quality Sub-Agent review." in report

    quality_prompt = (review_dir / "quality-subagent-review-prompt.md").read_text()
    assert "You are the ECC Quality Sub-Agent for this repository." in quality_prompt

    source_artifacts = json.loads((review_dir / "source-artifacts.json").read_text())
    assert source_artifacts["api_calls"][0]["endpoint"] == "cyq_chips"
    assert source_artifacts["api_retry_events"][0]["status"] == "retrying"
    assert {manifest["content"]["stage"] for manifest in source_artifacts["stage_manifests"]} == {
        "features",
        "signals",
        "backtest",
    }
    assert source_artifacts["decision_logs"][0]["content"][0]["agent"] == "strategy-agent"
    assert any(path.endswith("api-calls.jsonl") for path in source_artifacts["paths"])
    assert any(path.endswith("api-retry-events.jsonl") for path in source_artifacts["paths"])
    assert any(path.endswith("features/manifest.json") for path in source_artifacts["paths"])
    assert any(path.endswith("signals/agent_decision_log.jsonl") for path in source_artifacts["paths"])

    fix_plan = (review_dir / "fix-plan-draft.md").read_text()
    assert "Approval required" in fix_plan


def test_ecc_artifact_reviewer_can_use_explicit_external_reviewer(tmp_path: Path) -> None:
    research_root = tmp_path / "research-runs"
    plan_root = tmp_path / "plans"
    _write_plan_docs(plan_root)
    _write_research_run(research_root, "run-test-1")
    reviewer = EccArtifactReviewer(
        research_run_root=research_root,
        plan_doc_paths=[plan_root / "current-state.md", plan_root / "architecture.md"],
        review_client=StaticArtifactReviewClient("External artifact review completed."),
    )

    result = reviewer.review_run("run-test-1")

    report = (Path(result.artifact_dir) / "artifact-review-report.md").read_text()
    assert "External Reviewer Findings" in report
    assert "External artifact review completed." in report
    call_log = (Path(result.artifact_dir) / "external-review-calls.jsonl").read_text()
    assert '"status": "ok"' in call_log


def test_ecc_artifact_reviewer_report_status_includes_external_findings(tmp_path: Path) -> None:
    research_root = tmp_path / "research-runs"
    plan_root = tmp_path / "plans"
    _write_plan_docs(plan_root)
    _write_research_run(research_root, "run-test-1", complete_offsets=True, complete_report=True)
    reviewer = EccArtifactReviewer(
        research_run_root=research_root,
        plan_doc_paths=[plan_root / "current-state.md", plan_root / "architecture.md"],
        review_client=FindingArtifactReviewClient(),
    )

    result = reviewer.review_run("run-test-1")

    assert result.status == "needs_fix"
    report = (Path(result.artifact_dir) / "artifact-review-report.md").read_text()
    assert "- Status: `needs_fix`" in report


def test_ecc_artifact_reviewer_passes_when_artifacts_match_plan(tmp_path: Path) -> None:
    research_root = tmp_path / "research-runs"
    plan_root = tmp_path / "plans"
    _write_plan_docs(plan_root)
    _write_research_run(research_root, "run-test-1", complete_offsets=True, complete_report=True)
    reviewer = EccArtifactReviewer(
        research_run_root=research_root,
        plan_doc_paths=[plan_root / "current-state.md", plan_root / "architecture.md"],
    )

    result = reviewer.review_run("run-test-1")

    assert result.status == "passed"
    assert result.findings_count == 0
    findings = json.loads((Path(result.artifact_dir) / "findings.json").read_text())
    assert findings["findings"] == []


def test_ecc_artifact_reviewer_does_not_match_observation_labels_by_substring(tmp_path: Path) -> None:
    research_root = tmp_path / "research-runs"
    plan_root = tmp_path / "plans"
    _write_plan_docs(plan_root)
    _write_research_run(research_root, "run-test-1", complete_offsets=True, complete_report=True)
    run_dir = research_root / "run-test-1"
    (run_dir / "aggregate" / "final_report.md").write_text(
        "This report discusses N+15 N+30 N+60 N+90 N+180.",
        encoding="utf-8",
    )
    reviewer = EccArtifactReviewer(
        research_run_root=research_root,
        plan_doc_paths=[plan_root / "current-state.md", plan_root / "architecture.md"],
    )

    result = reviewer.review_run("run-test-1")

    assert result.status == "needs_fix"
    findings = json.loads((Path(result.artifact_dir) / "findings.json").read_text())
    assert "N+1, N+3, N+5" in findings["findings"][0]["evidence"][0]


def test_collect_source_artifacts_includes_traceability_evidence(tmp_path: Path) -> None:
    research_root = tmp_path / "research-runs"
    _write_research_run(research_root, "run-test-1", complete_offsets=True, complete_report=True)

    artifacts = collect_source_artifacts(research_root / "run-test-1")

    assert artifacts["api_calls"][0]["endpoint"] == "cyq_chips"
    assert artifacts["api_retry_events"][0]["endpoint"] == "cyq_chips"
    assert [call["row_count"] for call in artifacts["api_calls"]] == [30]
    assert {manifest["content"]["stage"] for manifest in artifacts["stage_manifests"]} == {
        "features",
        "signals",
        "backtest",
    }
    assert artifacts["decision_logs"][0]["content"][0]["decision_type"] == "rule_signal"
    assert any(path.endswith("backtest/manifest.json") for path in artifacts["paths"])


def test_ecc_artifact_reviewer_fails_when_report_is_missing(tmp_path: Path) -> None:
    research_root = tmp_path / "research-runs"
    plan_root = tmp_path / "plans"
    _write_plan_docs(plan_root)
    run_dir = research_root / "run-test-1"
    aggregate_dir = run_dir / "aggregate"
    aggregate_dir.mkdir(parents=True)
    (run_dir / "run-config.json").write_text("{}", encoding="utf-8")
    (run_dir / "run-manifest.json").write_text("{}", encoding="utf-8")
    (aggregate_dir / "ai_review.json").write_text("{}", encoding="utf-8")
    reviewer = EccArtifactReviewer(
        research_run_root=research_root,
        plan_doc_paths=[plan_root / "current-state.md", plan_root / "architecture.md"],
    )

    try:
        reviewer.review_run("run-test-1")
    except FileNotFoundError as error:
        assert "final_report.md" in str(error)
    else:
        raise AssertionError("Expected missing report to fail.")


def test_ecc_artifact_reviewer_passes_complete_factor_run(tmp_path: Path) -> None:
    factor_root = tmp_path / "factor-runs"
    cache_root = tmp_path / "factor-cache"
    plan_root = tmp_path / "plans"
    _write_plan_docs(plan_root)
    _write_factor_run(factor_root, cache_root, "factor-run-test-1")
    reviewer = EccArtifactReviewer(
        factor_run_root=factor_root,
        plan_doc_paths=[plan_root / "current-state.md", plan_root / "architecture.md"],
    )

    result = reviewer.review_factor_run("factor-run-test-1")

    assert result.status == "passed"
    assert result.findings_count == 0
    review_dir = Path(result.artifact_dir)
    assert review_dir.parent == factor_root / "factor-run-test-1" / "ecc-artifact-reviews"
    review_config = json.loads((review_dir / "review-config.json").read_text())
    assert review_config["artifact_type"] == "factor_run"
    assert "Factor Run" in (review_dir / "artifact-review-report.md").read_text()
    source_artifacts = json.loads((review_dir / "source-artifacts.json").read_text())
    assert source_artifacts["artifact_type"] == "factor_run"
    assert [call["endpoint"] for call in source_artifacts["api_calls"]] == ["trade_cal", "cyq_chips", "daily"]
    assert source_artifacts["stock_artifacts"][0]["files"]["factor_ref"]["row_count"] == 2
    assert source_artifacts["cache_artifacts"][0]["source"]["factor_run_id"] == "factor-run-test-1"


def test_ecc_artifact_reviewer_flags_failed_factor_api_call(tmp_path: Path) -> None:
    factor_root = tmp_path / "factor-runs"
    cache_root = tmp_path / "factor-cache"
    plan_root = tmp_path / "plans"
    _write_plan_docs(plan_root)
    _write_factor_run(factor_root, cache_root, "factor-run-test-1", failed_api=True)
    reviewer = EccArtifactReviewer(
        factor_run_root=factor_root,
        plan_doc_paths=[plan_root / "current-state.md", plan_root / "architecture.md"],
    )

    result = reviewer.review_factor_run("factor-run-test-1")

    assert result.status == "needs_fix"
    findings = json.loads((Path(result.artifact_dir) / "findings.json").read_text())
    assert any(finding["category"] == "api_failure" for finding in findings["findings"])


def test_ecc_artifact_reviewer_flags_factor_checksum_mismatch(tmp_path: Path) -> None:
    factor_root = tmp_path / "factor-runs"
    cache_root = tmp_path / "factor-cache"
    plan_root = tmp_path / "plans"
    _write_plan_docs(plan_root)
    _write_factor_run(factor_root, cache_root, "factor-run-test-1")
    factors_path = factor_root / "factor-run-test-1" / "stocks" / "000001.SZ" / "factors.jsonl"
    factors_path.write_text(factors_path.read_text() + json.dumps({"factor_id": "manual_edit"}) + "\n", encoding="utf-8")
    reviewer = EccArtifactReviewer(
        factor_run_root=factor_root,
        plan_doc_paths=[plan_root / "current-state.md", plan_root / "architecture.md"],
    )

    result = reviewer.review_factor_run("factor-run-test-1")

    assert result.status == "needs_fix"
    findings = json.loads((Path(result.artifact_dir) / "findings.json").read_text())
    assert any("checksum" in finding["title"].lower() for finding in findings["findings"])


def test_ecc_artifact_reviewer_flags_missing_factor_checksum_key(tmp_path: Path) -> None:
    factor_root = tmp_path / "factor-runs"
    cache_root = tmp_path / "factor-cache"
    plan_root = tmp_path / "plans"
    _write_plan_docs(plan_root)
    _write_factor_run(factor_root, cache_root, "factor-run-test-1")
    manifest_path = factor_root / "factor-run-test-1" / "factor-run-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    del manifest["stock_outputs"][0]["checksums"]["factors.jsonl"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    reviewer = EccArtifactReviewer(
        factor_run_root=factor_root,
        plan_doc_paths=[plan_root / "current-state.md", plan_root / "architecture.md"],
    )

    result = reviewer.review_factor_run("factor-run-test-1")

    assert result.status == "needs_fix"
    findings = json.loads((Path(result.artifact_dir) / "findings.json").read_text())
    assert any("factors.jsonl" in finding["evidence"][0] for finding in findings["findings"])


def test_ecc_artifact_reviewer_flags_factor_warmup_snapshot_mismatch(tmp_path: Path) -> None:
    factor_root = tmp_path / "factor-runs"
    cache_root = tmp_path / "factor-cache"
    plan_root = tmp_path / "plans"
    _write_plan_docs(plan_root)
    _write_factor_run(factor_root, cache_root, "factor-run-test-1")
    manifest_path = factor_root / "factor-run-test-1" / "factor-run-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["stock_outputs"][0]["warmup_snapshot_count"] = 3
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    reviewer = EccArtifactReviewer(
        factor_run_root=factor_root,
        plan_doc_paths=[plan_root / "current-state.md", plan_root / "architecture.md"],
    )

    result = reviewer.review_factor_run("factor-run-test-1")

    assert result.status == "needs_fix"
    findings = json.loads((Path(result.artifact_dir) / "findings.json").read_text())
    assert any(finding["category"] == "warmup_gap" for finding in findings["findings"])


def test_collect_factor_source_artifacts_includes_cache_provenance(tmp_path: Path) -> None:
    factor_root = tmp_path / "factor-runs"
    cache_root = tmp_path / "factor-cache"
    _write_factor_run(factor_root, cache_root, "factor-run-test-1")

    artifacts = collect_factor_source_artifacts(factor_root / "factor-run-test-1")

    assert artifacts["run_manifest"]["factor_date_count"] == 1
    assert artifacts["cache_artifacts"][0]["rows_checksum"].startswith("sha256:")
    assert artifacts["stock_artifacts"][0]["files"]["quality_ref"]["content"]["quality_status_counts"] == {"OK": 2}


def _write_plan_docs(plan_root: Path) -> None:
    plan_root.mkdir(parents=True)
    (plan_root / "current-state.md").write_text(
        "Plan requires N+1 N+3 N+5 N+15 N+30 N+60 N+90 N+180 observations.",
        encoding="utf-8",
    )
    (plan_root / "architecture.md").write_text("Reports must explain N/A observations.", encoding="utf-8")


def _write_research_run(
    research_root: Path,
    run_id: str,
    complete_offsets: bool = False,
    complete_report: bool = False,
) -> None:
    run_dir = research_root / run_id
    sample_dir = run_dir / "samples" / "000001.SZ-20260301-N10"
    aggregate_dir = run_dir / "aggregate"
    backtest_dir = sample_dir / "backtest"
    features_dir = sample_dir / "features"
    signals_dir = sample_dir / "signals"
    for directory in (aggregate_dir, backtest_dir, features_dir, signals_dir):
        directory.mkdir(parents=True, exist_ok=True)

    expected_offsets = [1, 3, 5, 15, 30, 60, 90, 180]
    score_offsets = expected_offsets if complete_offsets else [1, 3, 5]
    report_mentions = " ".join(f"N+{offset}" for offset in expected_offsets) if complete_report else "N+1 N+3 N+5"

    (run_dir / "run-config.json").write_text(
        json.dumps({"run_id": run_id, "observation_offsets": expected_offsets}),
        encoding="utf-8",
    )
    (run_dir / "run-manifest.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    (run_dir / "api-calls.jsonl").write_text(
        json.dumps(
            {
                "endpoint": "cyq_chips",
                "params": {"ts_code": "000001.SZ"},
                "status": "OK",
                "row_count": 30,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "api-retry-events.jsonl").write_text(
        json.dumps(
            {
                "endpoint": "cyq_chips",
                "params": {"ts_code": "000001.SZ", "trade_date": "20260301"},
                "attempt": 1,
                "max_retries": 3,
                "error_code": "NETWORK_ERROR",
                "raw_error_message": "temporary gateway timeout",
                "retryable": True,
                "sleep_seconds": 0.5,
                "status": "retrying",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (aggregate_dir / "final_report.md").write_text(
        f"This report discusses {report_mentions}.",
        encoding="utf-8",
    )
    (aggregate_dir / "ai_review.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    (backtest_dir / "backtest_score.json").write_text(
        json.dumps(
            {
                "strategy_scores": [
                    {
                        "strategy_id": "composite_baseline",
                        "observation_scores": [
                            {"offset_days": offset, "period_return": 0.01, "match_label": "NEUTRAL"}
                            for offset in score_offsets
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (features_dir / "feature_set.json").write_text(json.dumps({"latest_close": 10.0}), encoding="utf-8")
    (signals_dir / "signal_composite_baseline.json").write_text(json.dumps({"action": "HOLD"}), encoding="utf-8")
    (features_dir / "manifest.json").write_text(
        json.dumps(
            {
                "stage": "features",
                "input_refs": ["api-calls.jsonl"],
                "output_refs": ["samples/000001.SZ-20260301-N10/features/feature_set.json"],
                "row_counts": {"price_bars": 10, "chip_points": 30},
            }
        ),
        encoding="utf-8",
    )
    (signals_dir / "manifest.json").write_text(
        json.dumps(
            {
                "stage": "signals",
                "input_refs": ["samples/000001.SZ-20260301-N10/features/feature_set.json"],
                "output_refs": ["samples/000001.SZ-20260301-N10/signals/signal_composite_baseline.json"],
            }
        ),
        encoding="utf-8",
    )
    (signals_dir / "agent_decision_log.jsonl").write_text(
        json.dumps(
            {
                "agent": "strategy-agent",
                "decision_type": "rule_signal",
                "strategy_id": "composite_baseline",
                "action": "HOLD",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (backtest_dir / "manifest.json").write_text(
        json.dumps(
            {
                "stage": "backtest",
                "input_refs": ["samples/000001.SZ-20260301-N10/signals/manifest.json"],
                "output_refs": ["samples/000001.SZ-20260301-N10/backtest/backtest_score.json"],
                "date_coverage": {"future_offsets": expected_offsets},
            }
        ),
        encoding="utf-8",
    )


def _write_factor_run(
    factor_root: Path,
    cache_root: Path,
    run_id: str,
    failed_api: bool = False,
) -> None:
    run_dir = factor_root / run_id
    stock_dir = run_dir / "stocks" / "000001.SZ"
    stock_dir.mkdir(parents=True, exist_ok=True)
    factor_ref = "stocks/000001.SZ/factors.jsonl"
    snapshot_ref = "stocks/000001.SZ/daily-chip-snapshots.jsonl"
    quality_ref = "stocks/000001.SZ/factor-quality.json"
    traceability_ref = "stocks/000001.SZ/factor-traceability.json"
    checksums = {
        "daily-chip-snapshots.jsonl": "sha256:snapshots",
        "factors.jsonl": "sha256:factors",
        "factor-quality.json": "sha256:quality",
        "factor-traceability.json": "sha256:traceability",
    }
    (run_dir / "factor-run-config.json").write_text(
        json.dumps(
            {
                "factor_run_id": run_id,
                "stock_codes": ["000001.SZ"],
                "factor_start_date": "20260105",
                "factor_end_date": "20260105",
                "dry_run": False,
                "cache_root": str(cache_root),
                "warmup_trading_days": 1,
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "factor-run-manifest.json").write_text(
        json.dumps(
            {
                "factor_run_id": run_id,
                "status": "completed",
                "factor_date_count": 1,
                "warmup_date_count": 1,
                "stock_count": 1,
                "stock_outputs": [
                    {
                        "ts_code": "000001.SZ",
                        "factor_date_count": 1,
                        "warmup_snapshot_count": 1,
                        "snapshot_ref": snapshot_ref,
                        "factor_ref": factor_ref,
                        "quality_ref": quality_ref,
                        "traceability_ref": traceability_ref,
                        "checksums": checksums,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    api_status = "failed" if failed_api else "ok"
    (run_dir / "api-calls.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"endpoint": "trade_cal", "params": {"start_date": "20251201"}, "status": "ok", "row_count": 2}),
                json.dumps(
                    {
                        "endpoint": "cyq_chips",
                        "params": {"ts_code": "000001.SZ", "start_date": "20251231", "end_date": "20260105"},
                        "status": api_status,
                        "row_count": 4 if not failed_api else None,
                        "error": "temporary failure" if failed_api else None,
                    }
                ),
                json.dumps(
                    {
                        "endpoint": "daily",
                        "params": {"ts_code": "000001.SZ", "start_date": "20251231", "end_date": "20260105"},
                        "status": "ok",
                        "row_count": 2,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "api-retry-events.jsonl").write_text("", encoding="utf-8")
    (run_dir / "worker-events.jsonl").write_text(
        json.dumps({"event": "factor_run_started"}) + "\n" + json.dumps({"event": "factor_run_completed"}) + "\n",
        encoding="utf-8",
    )
    (stock_dir / "daily-chip-snapshots.jsonl").write_text(
        json.dumps({"factor_date": "20251231"}) + "\n" + json.dumps({"factor_date": "20260105"}) + "\n",
        encoding="utf-8",
    )
    (stock_dir / "factors.jsonl").write_text(
        json.dumps({"factor_id": "profit_ratio_asof", "factor_date": "20260105", "quality_status": "OK"}) + "\n"
        + json.dumps({"factor_id": "loss_ratio_asof", "factor_date": "20260105", "quality_status": "OK"}) + "\n",
        encoding="utf-8",
    )
    (stock_dir / "factor-quality.json").write_text(
        json.dumps({"snapshot_count": 2, "factor_count": 2, "quality_status_counts": {"OK": 2}}),
        encoding="utf-8",
    )
    (stock_dir / "factor-traceability.json").write_text(json.dumps({"factors": []}), encoding="utf-8")
    chip_cache_dir = cache_root / "tushare" / "cyq_chips" / "000001.SZ"
    daily_cache_dir = cache_root / "tushare" / "daily" / "000001.SZ"
    chip_cache_dir.mkdir(parents=True, exist_ok=True)
    daily_cache_dir.mkdir(parents=True, exist_ok=True)
    (chip_cache_dir / "20260105.json").write_text(
        json.dumps(
            {
                "source": {"factor_run_id": run_id, "endpoint": "cyq_chips"},
                "rows_checksum": "sha256:chips",
                "rows": [{"price": 10, "percent": 50}],
            }
        ),
        encoding="utf-8",
    )
    (daily_cache_dir / "20251231_20260105.json").write_text(
        json.dumps(
            {
                "source": {"factor_run_id": run_id, "endpoint": "daily"},
                "rows_checksum": "sha256:daily",
                "rows": [{"close": 10}],
            }
        ),
        encoding="utf-8",
    )
    actual_checksums = {
        "daily-chip-snapshots.jsonl": _test_file_checksum(stock_dir / "daily-chip-snapshots.jsonl"),
        "factors.jsonl": _test_file_checksum(stock_dir / "factors.jsonl"),
        "factor-quality.json": _test_file_checksum(stock_dir / "factor-quality.json"),
        "factor-traceability.json": _test_file_checksum(stock_dir / "factor-traceability.json"),
    }
    manifest = json.loads((run_dir / "factor-run-manifest.json").read_text())
    manifest["stock_outputs"][0]["checksums"] = actual_checksums
    (run_dir / "factor-run-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _test_file_checksum(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
