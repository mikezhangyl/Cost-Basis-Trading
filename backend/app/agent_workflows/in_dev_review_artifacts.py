import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


REQUIRED_RUN_FILES = [
    "run-config.json",
    "run-manifest.json",
    "aggregate/final_report.md",
    "aggregate/ai_review.json",
]


def default_review_root() -> Path:
    return Path(__file__).resolve().parents[3] / "docs" / "in-dev-reviews"


def default_research_run_root() -> Path:
    return Path(__file__).resolve().parents[3] / "docs" / "research-runs"


def build_run_review_root(run_dir: Path) -> Path:
    return run_dir / "in-dev-reviews"


def build_run_review_dir(run_dir: Path, review_id: str) -> Path:
    return build_run_review_root(run_dir) / review_id


def default_checkpoint_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "langgraph" / "in_dev_review_checkpoints.sqlite"


def default_plan_doc_paths() -> list[Path]:
    repo_root = Path(__file__).resolve().parents[3]
    return [
        repo_root / "docs" / "product-specs" / "current-state.md",
        repo_root / "docs" / "ARCHITECTURE.md",
        repo_root / "docs" / "design" / "multi-agent-research-workflow.md",
        repo_root / "docs" / "design" / "chip-change-feature-set.md",
        repo_root / "docs" / "exec-plans" / "active" / "phase-1-signal-dashboard.md",
    ]


def build_review_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"review-{timestamp}-{uuid4().hex[:8]}"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def append_event(path: Path, review_id: str, node: str, status: str, details: dict[str, Any] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "timestamp": datetime.now(UTC).isoformat(),
        "review_id": review_id,
        "node": node,
        "status": status,
        "details": details or {},
    }
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, ensure_ascii=False) + "\n")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def collect_source_artifacts(run_dir: Path) -> dict[str, Any]:
    for required_file in REQUIRED_RUN_FILES:
        path = run_dir / required_file
        if not path.exists():
            raise FileNotFoundError(str(path))

    backtest_scores = sorted(run_dir.glob("samples/*/backtest/backtest_score.json"))
    feature_sets = sorted(run_dir.glob("samples/*/features/feature_set.json"))
    signal_files = sorted(run_dir.glob("samples/*/signals/signal_*.json"))
    return {
        "run_config": load_json(run_dir / "run-config.json"),
        "run_manifest": load_json(run_dir / "run-manifest.json"),
        "final_report": (run_dir / "aggregate" / "final_report.md").read_text(encoding="utf-8"),
        "ai_review": load_json(run_dir / "aggregate" / "ai_review.json"),
        "backtest_scores": [_load_json_with_path(path) for path in backtest_scores],
        "feature_sets": [_load_json_with_path(path) for path in feature_sets],
        "signal_files": [_load_json_with_path(path) for path in signal_files],
        "paths": [str(path) for path in [run_dir / file for file in REQUIRED_RUN_FILES]]
        + [str(path) for path in backtest_scores + feature_sets + signal_files],
    }


def collect_plan_snapshot(plan_doc_paths: list[Path]) -> dict[str, Any]:
    docs = []
    for path in plan_doc_paths:
        if path.exists():
            docs.append({"path": str(path), "content": path.read_text(encoding="utf-8")})
    return {"documents": docs}


def build_artifact_refs(review_dir: Path) -> dict[str, str]:
    return {
        "report": str(review_dir / "in-dev-report.md"),
        "findings": str(review_dir / "findings.json"),
        "fix_plan": str(review_dir / "fix-plan-draft.md"),
        "graph_state": str(review_dir / "graph-state.json"),
        "events": str(review_dir / "workflow-events.jsonl"),
    }


def _load_json_with_path(path: Path) -> dict[str, Any]:
    return {"path": str(path), "content": load_json(path)}
