from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.agent_workflows.in_dev_review_artifacts import (
    build_artifact_refs,
    build_review_id,
    default_checkpoint_path,
    default_plan_doc_paths,
    default_research_run_root,
    default_review_root,
    write_json,
)
from app.agent_workflows.in_dev_review_client import DeepSeekInDevReviewClient, InDevReviewClient
from app.agent_workflows.in_dev_review_nodes import (
    build_llm_review_node,
    deterministic_plan_check,
    draft_fix_plan,
    human_approval_interrupt,
    load_completed_run_artifacts,
    load_plan_context,
    write_review_state,
)
from app.agent_workflows.in_dev_review_state import InDevReviewState
from app.domain.models import InDevReviewRequest, InDevReviewResponse


class InDevReviewService:
    def __init__(
        self,
        research_run_root: Path | None = None,
        review_root: Path | None = None,
        plan_doc_paths: list[Path] | None = None,
        checkpoint_path: Path | None = None,
        review_client: InDevReviewClient | None = None,
    ) -> None:
        self.research_run_root = research_run_root or default_research_run_root()
        self.review_root = review_root or default_review_root()
        self.plan_doc_paths = plan_doc_paths or default_plan_doc_paths()
        self.checkpoint_path = checkpoint_path or default_checkpoint_path()
        self.review_client = review_client if review_client is not None else DeepSeekInDevReviewClient.from_environment()

    def create_review(self, request: InDevReviewRequest) -> InDevReviewResponse:
        review_id = build_review_id()
        run_dir = self.research_run_root / request.run_id
        if not run_dir.exists():
            raise FileNotFoundError(str(run_dir))
        review_dir = self.review_root / review_id
        review_dir.mkdir(parents=True, exist_ok=True)
        initial_state = _build_initial_state(
            review_id=review_id,
            run_id=request.run_id,
            run_dir=run_dir,
            review_dir=review_dir,
            plan_doc_paths=self.plan_doc_paths,
        )
        write_json(
            review_dir / "review-config.json",
            {
                "review_id": review_id,
                "run_id": request.run_id,
                "status": "created",
                "plan_doc_paths": [str(path) for path in self.plan_doc_paths],
            },
        )
        result = self._invoke(initial_state, review_id)
        status = "awaiting_approval" if "__interrupt__" in result else str(result.get("status", "failed"))
        return _response(review_id, request.run_id, status, review_dir)

    def approve_review(self, review_id: str, approved: bool, notes: str | None = None) -> InDevReviewResponse:
        review_dir = self.review_root / review_id
        if not review_dir.exists():
            raise FileNotFoundError(str(review_dir))
        config = {"configurable": {"thread_id": review_id}}
        with SqliteSaver.from_conn_string(str(self.checkpoint_path)) as checkpointer:
            checkpointer.setup()
            graph = _compile_graph(checkpointer, self.review_client)
            result = graph.invoke(Command(resume={"approved": approved, "notes": notes}), config=config)
        status = str(result.get("status", "approved" if approved else "rejected"))
        run_id = str(result.get("run_id") or _read_review_config(review_dir).get("run_id", ""))
        return _response(review_id, run_id, status, review_dir)

    def get_review(self, review_id: str) -> InDevReviewResponse:
        review_dir = self.review_root / review_id
        if not review_dir.exists():
            raise FileNotFoundError(str(review_dir))
        config = _read_review_config(review_dir)
        graph_state = _read_graph_state(review_dir)
        status = str(graph_state.get("status") or "awaiting_approval")
        return _response(review_id, str(config.get("run_id", "")), status, review_dir)

    def _invoke(self, initial_state: InDevReviewState, thread_id: str) -> dict:
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        config = {"configurable": {"thread_id": thread_id}}
        with SqliteSaver.from_conn_string(str(self.checkpoint_path)) as checkpointer:
            checkpointer.setup()
            graph = _compile_graph(checkpointer, self.review_client)
            return graph.invoke(initial_state, config=config)


def _compile_graph(checkpointer: SqliteSaver, review_client: InDevReviewClient | None):
    builder = StateGraph(InDevReviewState)
    builder.add_node("load_plan_context", load_plan_context)
    builder.add_node("load_completed_run_artifacts", load_completed_run_artifacts)
    builder.add_node("deterministic_plan_check", deterministic_plan_check)
    builder.add_node("llm_in_dev_review", build_llm_review_node(review_client))
    builder.add_node("draft_fix_plan", draft_fix_plan)
    builder.add_node("write_review_state", write_review_state)
    builder.add_node("human_approval_interrupt", human_approval_interrupt)
    builder.add_edge(START, "load_plan_context")
    builder.add_edge("load_plan_context", "load_completed_run_artifacts")
    builder.add_edge("load_completed_run_artifacts", "deterministic_plan_check")
    builder.add_edge("deterministic_plan_check", "llm_in_dev_review")
    builder.add_edge("llm_in_dev_review", "draft_fix_plan")
    builder.add_edge("draft_fix_plan", "write_review_state")
    builder.add_edge("write_review_state", "human_approval_interrupt")
    builder.add_edge("human_approval_interrupt", END)
    return builder.compile(checkpointer=checkpointer)


def _build_initial_state(
    review_id: str,
    run_id: str,
    run_dir: Path,
    review_dir: Path,
    plan_doc_paths: list[Path],
) -> InDevReviewState:
    return {
        "review_id": review_id,
        "run_id": run_id,
        "status": "running",
        "research_run_dir": str(run_dir),
        "review_dir": str(review_dir),
        "plan_doc_paths": [str(path) for path in plan_doc_paths],
        "source_artifact_paths": [],
        "plan_snapshot_path": str(review_dir / "plan-snapshot.json"),
        "source_artifacts_path": str(review_dir / "source-artifacts.json"),
        "findings_path": str(review_dir / "findings.json"),
        "in_dev_report_path": str(review_dir / "in-dev-report.md"),
        "fix_plan_draft_path": str(review_dir / "fix-plan-draft.md"),
        "graph_state_path": str(review_dir / "graph-state.json"),
        "workflow_events_path": str(review_dir / "workflow-events.jsonl"),
        "deterministic_findings": [],
        "llm_findings": [],
        "approval": None,
    }


def _response(review_id: str, run_id: str, status: str, review_dir: Path) -> InDevReviewResponse:
    findings_path = review_dir / "findings.json"
    findings_count = 0
    if findings_path.exists():
        import json

        findings_count = len(json.loads(findings_path.read_text(encoding="utf-8")).get("findings", []))
    return InDevReviewResponse(
        review_id=review_id,
        run_id=run_id,
        status=status,
        artifact_dir=str(review_dir),
        findings_count=findings_count,
        approval_required=status == "awaiting_approval",
        artifact_refs=build_artifact_refs(review_dir),
    )


def _read_review_config(review_dir: Path) -> dict:
    import json

    return json.loads((review_dir / "review-config.json").read_text(encoding="utf-8"))


def _read_graph_state(review_dir: Path) -> dict:
    import json

    path = review_dir / "graph-state.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
