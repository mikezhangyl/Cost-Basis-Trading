from typing import Any, TypedDict


class InDevReviewState(TypedDict, total=False):
    review_id: str
    run_id: str
    status: str
    research_run_dir: str
    review_dir: str
    plan_doc_paths: list[str]
    source_artifact_paths: list[str]
    plan_snapshot_path: str
    source_artifacts_path: str
    findings_path: str
    in_dev_report_path: str
    fix_plan_draft_path: str
    graph_state_path: str
    workflow_events_path: str
    deterministic_findings: list[dict[str, Any]]
    llm_findings: list[dict[str, Any]]
    approval: dict[str, Any] | None
