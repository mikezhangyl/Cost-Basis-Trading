import json
from pathlib import Path

from app.agent_workflows.in_dev_review_client import StaticInDevReviewClient
from app.agent_workflows.in_dev_review_graph import InDevReviewService
from app.domain.models import InDevReviewRequest


def test_in_dev_review_writes_artifacts_and_waits_for_approval(tmp_path: Path) -> None:
    research_root = tmp_path / "research-runs"
    plan_root = tmp_path / "plans"
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    _write_plan_docs(plan_root)
    _write_research_run(research_root, "run-test-1")
    service = InDevReviewService(
        research_run_root=research_root,
        plan_doc_paths=[plan_root / "current-state.md", plan_root / "architecture.md"],
        checkpoint_path=checkpoint_path,
        review_client=StaticInDevReviewClient("LLM review completed."),
    )

    result = service.create_review(InDevReviewRequest(run_id="run-test-1"))

    assert result.run_id == "run-test-1"
    assert result.status == "awaiting_approval"
    assert result.approval_required is True
    assert result.findings_count == 2
    review_dir = Path(result.artifact_dir)
    assert review_dir.parent == research_root / "run-test-1" / "in-dev-reviews"
    assert (review_dir / "review-config.json").exists()
    assert (review_dir / "source-artifacts.json").exists()
    assert (review_dir / "plan-snapshot.json").exists()
    assert (review_dir / "findings.json").exists()
    assert (review_dir / "fix-plan-draft.md").exists()
    assert (review_dir / "in-dev-report.md").exists()
    assert (review_dir / "graph-state.json").exists()
    assert (review_dir / "workflow-events.jsonl").exists()
    latest = json.loads((research_root / "run-test-1" / "in-dev-reviews" / "latest.json").read_text())
    assert latest["review_id"] == result.review_id
    assert latest["artifact_dir"] == result.artifact_dir

    findings = json.loads((review_dir / "findings.json").read_text())
    assert findings["status"] == "needs_fix"
    assert findings["findings"][0]["category"] == "plan_mismatch"
    assert "N+180" in findings["findings"][0]["expected"]

    fix_plan = (review_dir / "fix-plan-draft.md").read_text()
    assert "Approval required" in fix_plan


def test_in_dev_review_approval_resumes_graph(tmp_path: Path) -> None:
    research_root = tmp_path / "research-runs"
    plan_root = tmp_path / "plans"
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    _write_plan_docs(plan_root)
    _write_research_run(research_root, "run-test-1")
    service = InDevReviewService(
        research_run_root=research_root,
        plan_doc_paths=[plan_root / "current-state.md", plan_root / "architecture.md"],
        checkpoint_path=checkpoint_path,
        review_client=StaticInDevReviewClient("LLM review completed."),
    )
    created = service.create_review(InDevReviewRequest(run_id="run-test-1"))

    approved = service.approve_review(created.review_id, approved=True, notes="Approved for planning.")

    assert approved.status == "approved"
    assert approved.approval_required is False
    graph_state = json.loads((Path(approved.artifact_dir) / "graph-state.json").read_text())
    assert graph_state["approval"]["approved"] is True
    assert graph_state["approval"]["notes"] == "Approved for planning."
    latest = json.loads((research_root / "run-test-1" / "in-dev-reviews" / "latest.json").read_text())
    assert latest["status"] == "approved"


def test_in_dev_review_get_finds_run_local_artifact(tmp_path: Path) -> None:
    research_root = tmp_path / "research-runs"
    plan_root = tmp_path / "plans"
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    _write_plan_docs(plan_root)
    _write_research_run(research_root, "run-test-1")
    service = InDevReviewService(
        research_run_root=research_root,
        plan_doc_paths=[plan_root / "current-state.md", plan_root / "architecture.md"],
        checkpoint_path=checkpoint_path,
        review_client=StaticInDevReviewClient("LLM review completed."),
    )
    created = service.create_review(InDevReviewRequest(run_id="run-test-1"))

    found = service.get_review(created.review_id)

    assert found.review_id == created.review_id
    assert found.artifact_dir == created.artifact_dir


def test_in_dev_review_fails_when_report_is_missing(tmp_path: Path) -> None:
    research_root = tmp_path / "research-runs"
    plan_root = tmp_path / "plans"
    _write_plan_docs(plan_root)
    run_dir = research_root / "run-test-1"
    aggregate_dir = run_dir / "aggregate"
    aggregate_dir.mkdir(parents=True)
    (run_dir / "run-config.json").write_text("{}", encoding="utf-8")
    (run_dir / "run-manifest.json").write_text("{}", encoding="utf-8")
    (aggregate_dir / "ai_review.json").write_text("{}", encoding="utf-8")
    service = InDevReviewService(
        research_run_root=research_root,
        plan_doc_paths=[plan_root / "current-state.md", plan_root / "architecture.md"],
        checkpoint_path=tmp_path / "checkpoints.sqlite",
        review_client=StaticInDevReviewClient("LLM review completed."),
    )

    try:
        service.create_review(InDevReviewRequest(run_id="run-test-1"))
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


def _write_research_run(research_root: Path, run_id: str) -> None:
    run_dir = research_root / run_id
    sample_dir = run_dir / "samples" / "000001.SZ-20260301-N10"
    aggregate_dir = run_dir / "aggregate"
    backtest_dir = sample_dir / "backtest"
    features_dir = sample_dir / "features"
    signals_dir = sample_dir / "signals"
    for directory in (aggregate_dir, backtest_dir, features_dir, signals_dir):
        directory.mkdir(parents=True, exist_ok=True)
    (run_dir / "run-config.json").write_text(
        json.dumps({"run_id": run_id, "observation_offsets": [1, 3, 5, 15, 30, 60, 90, 180]}),
        encoding="utf-8",
    )
    (run_dir / "run-manifest.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    (aggregate_dir / "final_report.md").write_text(
        "This report only discusses N+1, N+3, and N+5.",
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
                            {"offset_days": 1, "period_return": 0.01, "match_label": "NEUTRAL"},
                            {"offset_days": 3, "period_return": 0.02, "match_label": "NEUTRAL"},
                            {"offset_days": 5, "period_return": -0.01, "match_label": "NEUTRAL"},
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (features_dir / "feature_set.json").write_text(json.dumps({"latest_close": 10.0}), encoding="utf-8")
    (signals_dir / "signal_composite_baseline.json").write_text(json.dumps({"action": "HOLD"}), encoding="utf-8")
