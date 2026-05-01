from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.ecc_artifact_reviewer import (
    ArtifactReviewResult,
    EccArtifactReviewer,
    default_factor_run_root,
    default_research_run_root,
)


@dataclass(frozen=True)
class QualityWorkflowResult:
    workflow: str
    run_id: str
    run_dir: str
    review: ArtifactReviewResult
    quality_subagent_prompt: str


@dataclass(frozen=True)
class QualityGateStepResult:
    name: str
    command: list[str]
    cwd: str
    status: str
    returncode: int
    duration_seconds: float
    stdout_tail: str
    stderr_tail: str


@dataclass(frozen=True)
class QualityGateResult:
    workflow: str
    status: str
    steps: list[QualityGateStepResult]


CommandRunner = Callable[[list[str], Path], subprocess.CompletedProcess[str]]


def find_latest_research_run(research_run_root: Path | None = None) -> Path:
    root = research_run_root or default_research_run_root()
    runs = sorted(path for path in root.glob("run-*") if path.is_dir())
    if not runs:
        raise FileNotFoundError(f"No research runs found under {root}.")
    return runs[-1]


def find_latest_factor_run(factor_run_root: Path | None = None) -> Path:
    root = factor_run_root or default_factor_run_root()
    runs = sorted((path for path in root.glob("factor-run-*") if path.is_dir()), key=_factor_run_sort_key)
    if not runs:
        raise FileNotFoundError(f"No factor runs found under {root}.")
    return runs[-1]


def review_latest_research(research_run_root: Path | None = None) -> QualityWorkflowResult:
    latest_run = find_latest_research_run(research_run_root)
    review = EccArtifactReviewer(research_run_root=latest_run.parent).review_run(latest_run.name)
    quality_subagent_prompt = review.artifact_refs["quality_subagent_prompt"]
    return QualityWorkflowResult(
        workflow="review-latest-research",
        run_id=latest_run.name,
        run_dir=str(latest_run),
        review=review,
        quality_subagent_prompt=quality_subagent_prompt,
    )


def review_latest_factor(factor_run_root: Path | None = None) -> QualityWorkflowResult:
    latest_run = find_latest_factor_run(factor_run_root)
    review = EccArtifactReviewer(factor_run_root=latest_run.parent).review_factor_run(latest_run.name)
    quality_subagent_prompt = review.artifact_refs["quality_subagent_prompt"]
    return QualityWorkflowResult(
        workflow="review-latest-factor",
        run_id=latest_run.name,
        run_dir=str(latest_run),
        review=review,
        quality_subagent_prompt=quality_subagent_prompt,
    )


def run_quality_gate(
    repo_root: Path | None = None,
    include_artifact_review: bool = False,
    runner: CommandRunner | None = None,
) -> QualityGateResult:
    root = repo_root or Path(__file__).resolve().parents[1]
    command_runner = runner or _run_command
    commands = _quality_gate_commands(root, include_artifact_review)
    results: list[QualityGateStepResult] = []
    for name, command, cwd in commands:
        started = time.monotonic()
        completed = command_runner(command, cwd)
        duration = time.monotonic() - started
        status = "passed" if completed.returncode == 0 else "failed"
        results.append(
            QualityGateStepResult(
                name=name,
                command=command,
                cwd=str(cwd),
                status=status,
                returncode=completed.returncode,
                duration_seconds=round(duration, 3),
                stdout_tail=_tail(completed.stdout),
                stderr_tail=_tail(completed.stderr),
            )
        )
        if completed.returncode != 0:
            return QualityGateResult(workflow="quality-gate", status="failed", steps=results)
    return QualityGateResult(workflow="quality-gate", status="passed", steps=results)


def main() -> int:
    parser = argparse.ArgumentParser(description="ECC Quality workflow helpers for verification orchestration.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    latest_parser = subparsers.add_parser(
        "review-latest-research",
        help="Find the latest research run and prepare an ECC Artifact Review packet.",
    )
    latest_parser.add_argument(
        "--research-run-root",
        type=Path,
        default=None,
        help="Directory containing run-* research artifacts. Defaults to docs/research-runs.",
    )
    latest_factor_parser = subparsers.add_parser(
        "review-latest-factor",
        help="Find the latest factor run and prepare an ECC Artifact Review packet.",
    )
    latest_factor_parser.add_argument(
        "--factor-run-root",
        type=Path,
        default=None,
        help="Directory containing factor-run-* artifacts. Defaults to docs/factor-runs.",
    )
    gate_parser = subparsers.add_parser(
        "quality-gate",
        help="Run the deterministic ECC quality gate intended for ECC Quality Sub-Agent execution.",
    )
    gate_parser.add_argument(
        "--include-artifact-review",
        action="store_true",
        help="Also run review-latest-research as the final gate step.",
    )
    args = parser.parse_args()

    if args.command == "review-latest-research":
        result = review_latest_research(args.research_run_root)
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return 0
    if args.command == "review-latest-factor":
        result = review_latest_factor(args.factor_run_root)
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return 0
    if args.command == "quality-gate":
        result = run_quality_gate(include_artifact_review=args.include_artifact_review)
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return 0 if result.status == "passed" else 1
    raise ValueError(f"Unsupported command: {args.command}")


def _quality_gate_commands(root: Path, include_artifact_review: bool) -> list[tuple[str, list[str], Path]]:
    commands = [
        ("git_diff_check", ["git", "diff", "--check"], root),
        ("backend_pytest", [sys.executable, "-m", "pytest", "-v"], root / "backend"),
        ("frontend_vitest", ["npm", "run", "test"], root / "frontend"),
        ("frontend_build", ["npm", "run", "build"], root / "frontend"),
    ]
    if include_artifact_review:
        commands.append(
            (
                "ecc_artifact_review_latest",
                [sys.executable, "scripts/ecc_quality_workflow.py", "review-latest-research"],
                root,
            )
        )
    return commands


def _run_command(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)


def _factor_run_sort_key(path: Path) -> tuple[str, str]:
    manifest_path = path / "factor-run-manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            timestamp = str(manifest.get("completed_at") or manifest.get("created_at") or "")
            return (timestamp, path.name)
        except json.JSONDecodeError:
            return ("", path.name)
    return ("", path.name)


def _tail(output: str | None, max_chars: int = 4000) -> str:
    if not output:
        return ""
    return output[-max_chars:]


if __name__ == "__main__":
    raise SystemExit(main())
