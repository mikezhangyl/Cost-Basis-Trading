import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from scripts.ecc_artifact_reviewer import EccArtifactReviewer, StaticArtifactReviewClient


def test_ecc_artifact_reviewer_writes_run_local_artifacts(tmp_path: Path) -> None:
    research_root = tmp_path / "research-runs"
    plan_root = tmp_path / "plans"
    _write_plan_docs(plan_root)
    _write_research_run(research_root, "run-test-1")
    reviewer = EccArtifactReviewer(
        research_run_root=research_root,
        plan_doc_paths=[plan_root / "current-state.md", plan_root / "architecture.md"],
        review_client=StaticArtifactReviewClient("LLM artifact review completed."),
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
    assert (review_dir / "review-state.json").exists()
    assert (review_dir / "workflow-events.jsonl").exists()
    assert (review_dir / "llm-calls.jsonl").exists()

    latest = json.loads((research_root / "run-test-1" / "ecc-artifact-reviews" / "latest.json").read_text())
    assert latest["review_id"] == result.review_id
    assert latest["artifact_dir"] == result.artifact_dir

    findings = json.loads((review_dir / "findings.json").read_text())
    assert findings["status"] == "needs_fix"
    assert findings["findings"][0]["category"] == "plan_mismatch"
    assert "N+180" in findings["findings"][0]["expected"]

    report = (review_dir / "artifact-review-report.md").read_text()
    assert "# ECC Artifact Review Report" in report
    assert "LLM artifact review completed." in report

    fix_plan = (review_dir / "fix-plan-draft.md").read_text()
    assert "Approval required" in fix_plan


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
