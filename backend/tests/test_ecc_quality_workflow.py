import json
import subprocess
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from scripts.ecc_quality_workflow import (
    find_latest_factor_run,
    find_latest_research_run,
    review_latest_factor,
    review_latest_research,
    run_quality_gate,
)


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


def test_review_latest_factor_runs_artifact_reviewer_for_newest_run(tmp_path: Path) -> None:
    factor_root = tmp_path / "factor-runs"
    _write_factor_run(factor_root, "factor-run-20260501-000001-aaaaaaaa")
    _write_factor_run(factor_root, "factor-run-20260501-000002-bbbbbbbb")

    result = review_latest_factor(factor_run_root=factor_root)

    assert result.workflow == "review-latest-factor"
    assert result.run_id == "factor-run-20260501-000002-bbbbbbbb"
    assert result.review.status == "needs_fix"
    review_dir = Path(result.review.artifact_dir)
    assert (review_dir.parent / "latest.json").exists()
    assert (review_dir / "review-config.json").exists()
    assert (review_dir / "source-artifacts.json").exists()
    assert result.quality_subagent_prompt == str(review_dir / "quality-subagent-review-prompt.md")


def test_find_latest_factor_run_fails_when_no_runs_exist(tmp_path: Path) -> None:
    try:
        find_latest_factor_run(tmp_path / "factor-runs")
    except FileNotFoundError as error:
        assert "No factor runs found" in str(error)
    else:
        raise AssertionError("Expected missing factor runs to fail.")


def test_find_latest_factor_run_uses_manifest_completed_at_before_name(tmp_path: Path) -> None:
    factor_root = tmp_path / "factor-runs"
    _write_factor_run(factor_root, "factor-run-z-old", completed_at="2026-01-01T00:00:00+00:00")
    _write_factor_run(factor_root, "factor-run-a-new", completed_at="2026-01-02T00:00:00+00:00")

    latest = find_latest_factor_run(factor_root)

    assert latest.name == "factor-run-a-new"


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
    assert set(payload) == {
        "workflow",
        "run_id",
        "run_dir",
        "review",
        "quality_subagent_prompt",
    }
    assert payload["workflow"] == "review-latest-research"
    assert payload["run_id"] == "run-20260501-000001-aaaaaaaa"
    assert payload["quality_subagent_prompt"].endswith("quality-subagent-review-prompt.md")
    assert set(payload["review"]) == {
        "review_id",
        "run_id",
        "status",
        "artifact_dir",
        "findings_count",
        "approval_required",
        "artifact_refs",
    }
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
    review_dir = Path(payload["review"]["artifact_dir"])
    assert Path(payload["quality_subagent_prompt"]).exists()
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


def test_quality_workflow_cli_reviews_latest_factor_run(tmp_path: Path) -> None:
    factor_root = tmp_path / "factor-runs"
    _write_factor_run(factor_root, "factor-run-20260501-000001-aaaaaaaa")

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/ecc_quality_workflow.py",
            "review-latest-factor",
            "--factor-run-root",
            str(factor_root),
        ],
        check=True,
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
    )

    payload = json.loads(completed.stdout)
    assert payload["workflow"] == "review-latest-factor"
    assert payload["run_id"] == "factor-run-20260501-000001-aaaaaaaa"
    assert payload["quality_subagent_prompt"].endswith("quality-subagent-review-prompt.md")


def test_quality_gate_runs_default_checks_in_subagent_order(tmp_path: Path) -> None:
    calls: list[tuple[list[str], Path]] = []

    def fake_runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append((command, cwd))
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    result = run_quality_gate(repo_root=tmp_path, runner=fake_runner)

    assert result.status == "passed"
    assert [step.name for step in result.steps] == [
        "git_diff_check",
        "backend_pytest",
        "frontend_vitest",
        "frontend_build",
    ]
    assert calls == [
        (["git", "diff", "--check"], tmp_path),
        ([sys.executable, "-m", "pytest", "-v"], tmp_path / "backend"),
        (["npm", "run", "test"], tmp_path / "frontend"),
        (["npm", "run", "build"], tmp_path / "frontend"),
    ]


def test_quality_gate_can_include_latest_artifact_review(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    result = run_quality_gate(repo_root=tmp_path, include_artifact_review=True, runner=fake_runner)

    assert result.status == "passed"
    assert [step.name for step in result.steps][-1] == "ecc_artifact_review_latest"
    assert calls[-1] == [
        sys.executable,
        "scripts/ecc_quality_workflow.py",
        "review-latest-research",
    ]


def test_quality_gate_records_failure_and_stops(tmp_path: Path) -> None:
    calls: list[str] = []

    def fake_runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(command[0])
        if command[0] == "git":
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="bad whitespace")
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    result = run_quality_gate(repo_root=tmp_path, runner=fake_runner)

    assert result.status == "failed"
    assert [step.name for step in result.steps] == ["git_diff_check"]
    assert result.steps[0].returncode == 1
    assert result.steps[0].stderr_tail == "bad whitespace"
    assert calls == ["git"]


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


def _write_factor_run(factor_root: Path, run_id: str, completed_at: str = "2026-05-01T00:00:00+00:00") -> None:
    run_dir = factor_root / run_id
    stock_dir = run_dir / "stocks" / "000001.SZ"
    stock_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "factor-run-config.json").write_text(
        json.dumps(
            {
                "factor_run_id": run_id,
                "stock_codes": ["000001.SZ"],
                "factor_start_date": "20260105",
                "factor_end_date": "20260105",
                "dry_run": False,
                "cache_root": str(factor_root / "cache"),
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "factor-run-manifest.json").write_text(
        json.dumps(
            {
                "factor_run_id": run_id,
                "status": "failed",
                "completed_at": completed_at,
                "factor_date_count": 0,
                "warmup_date_count": 0,
                "stock_count": 0,
                "stock_outputs": [],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "api-calls.jsonl").write_text(
        json.dumps({"endpoint": "trade_cal", "status": "failed", "params": {}, "error": "network"}) + "\n",
        encoding="utf-8",
    )
    (run_dir / "api-retry-events.jsonl").write_text("", encoding="utf-8")
    (run_dir / "worker-events.jsonl").write_text(json.dumps({"event": "factor_run_started"}) + "\n", encoding="utf-8")
