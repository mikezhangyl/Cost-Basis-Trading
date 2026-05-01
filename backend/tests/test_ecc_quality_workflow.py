import json
import subprocess
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from scripts.ecc_quality_workflow import find_latest_research_run, review_latest_research


def test_review_latest_research_runs_artifact_reviewer_for_newest_run(tmp_path: Path) -> None:
    research_root = tmp_path / "research-runs"
    _write_research_run(research_root, "run-20260501-000001-aaaaaaaa")
    _write_research_run(research_root, "run-20260501-000002-bbbbbbbb")

    result = review_latest_research(research_run_root=research_root)

    assert result.workflow == "review-latest-research"
    assert result.run_id == "run-20260501-000002-bbbbbbbb"
    assert result.review.status == "needs_fix"
    review_dir = Path(result.review.artifact_dir)
    assert (review_dir.parent / "latest.json").exists()
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
    assert result.quality_subagent_prompt == str(review_dir / "quality-subagent-review-prompt.md")


def test_find_latest_research_run_fails_when_no_runs_exist(tmp_path: Path) -> None:
    try:
        find_latest_research_run(tmp_path / "research-runs")
    except FileNotFoundError as error:
        assert "No research runs found" in str(error)
    else:
        raise AssertionError("Expected missing research runs to fail.")


def test_quality_workflow_cli_reviews_latest_research_run(tmp_path: Path) -> None:
    research_root = tmp_path / "research-runs"
    _write_research_run(research_root, "run-20260501-000001-aaaaaaaa")

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/ecc_quality_workflow.py",
            "review-latest-research",
            "--research-run-root",
            str(research_root),
        ],
        check=True,
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
    )

    payload = json.loads(completed.stdout)
    assert payload["workflow"] == "review-latest-research"
    assert payload["run_id"] == "run-20260501-000001-aaaaaaaa"
    assert payload["quality_subagent_prompt"].endswith("quality-subagent-review-prompt.md")
    assert payload["review"]["artifact_refs"]["quality_subagent_prompt"] == payload["quality_subagent_prompt"]
    assert set(payload["review"]["artifact_refs"]) == {
        "report",
        "findings",
        "fix_plan",
        "quality_subagent_prompt",
        "state",
        "events",
        "external_calls",
    }
    assert Path(payload["quality_subagent_prompt"]).exists()


def _write_research_run(research_root: Path, run_id: str) -> None:
    run_dir = research_root / run_id
    sample_dir = run_dir / "samples" / "000001.SZ-20260301-N10"
    aggregate_dir = run_dir / "aggregate"
    backtest_dir = sample_dir / "backtest"
    features_dir = sample_dir / "features"
    signals_dir = sample_dir / "signals"
    for directory in (aggregate_dir, backtest_dir, features_dir, signals_dir):
        directory.mkdir(parents=True, exist_ok=True)

    expected_offsets = [1, 3, 5, 15, 30, 60, 90, 180]
    (run_dir / "run-config.json").write_text(
        json.dumps({"run_id": run_id, "observation_offsets": expected_offsets}),
        encoding="utf-8",
    )
    (run_dir / "run-manifest.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    (aggregate_dir / "final_report.md").write_text("This report discusses N+1 only.", encoding="utf-8")
    (aggregate_dir / "ai_review.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    (backtest_dir / "backtest_score.json").write_text(
        json.dumps(
            {
                "strategy_scores": [
                    {
                        "strategy_id": "composite_baseline",
                        "observation_scores": [
                            {"offset_days": 1, "period_return": 0.01, "match_label": "NEUTRAL"}
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (features_dir / "feature_set.json").write_text(json.dumps({"latest_close": 10.0}), encoding="utf-8")
    (signals_dir / "signal_composite_baseline.json").write_text(json.dumps({"action": "HOLD"}), encoding="utf-8")
