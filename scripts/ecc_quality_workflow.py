from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.ecc_artifact_reviewer import ArtifactReviewResult, EccArtifactReviewer, default_research_run_root


@dataclass(frozen=True)
class QualityWorkflowResult:
    workflow: str
    run_id: str
    run_dir: str
    review: ArtifactReviewResult
    quality_subagent_prompt: str


def find_latest_research_run(research_run_root: Path | None = None) -> Path:
    root = research_run_root or default_research_run_root()
    runs = sorted(path for path in root.glob("run-*") if path.is_dir())
    if not runs:
        raise FileNotFoundError(f"No research runs found under {root}.")
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
    args = parser.parse_args()

    if args.command == "review-latest-research":
        result = review_latest_research(args.research_run_root)
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return 0
    raise ValueError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
