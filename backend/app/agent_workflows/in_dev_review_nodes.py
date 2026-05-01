import json
from pathlib import Path
from typing import Any

from langgraph.types import interrupt

from app.agent_workflows.in_dev_review_artifacts import (
    append_event,
    collect_plan_snapshot,
    collect_source_artifacts,
    write_json,
    write_text,
)
from app.agent_workflows.in_dev_review_client import InDevReviewClient
from app.agent_workflows.in_dev_review_state import InDevReviewState

EXPECTED_OBSERVATION_OFFSETS = [1, 3, 5, 15, 30, 60, 90, 180]


def load_plan_context(state: InDevReviewState) -> InDevReviewState:
    review_id = state["review_id"]
    events_path = Path(state["workflow_events_path"])
    append_event(events_path, review_id, "load_plan_context", "started")
    plan_paths = [Path(path) for path in state["plan_doc_paths"]]
    plan_snapshot = collect_plan_snapshot(plan_paths)
    write_json(Path(state["plan_snapshot_path"]), plan_snapshot)
    append_event(events_path, review_id, "load_plan_context", "completed", {"document_count": len(plan_snapshot["documents"])})
    return {}


def load_completed_run_artifacts(state: InDevReviewState) -> InDevReviewState:
    review_id = state["review_id"]
    events_path = Path(state["workflow_events_path"])
    append_event(events_path, review_id, "load_completed_run_artifacts", "started")
    source_artifacts = collect_source_artifacts(Path(state["research_run_dir"]))
    write_json(Path(state["source_artifacts_path"]), source_artifacts)
    append_event(
        events_path,
        review_id,
        "load_completed_run_artifacts",
        "completed",
        {"artifact_count": len(source_artifacts["paths"])},
    )
    return {"source_artifact_paths": source_artifacts["paths"]}


def deterministic_plan_check(state: InDevReviewState) -> InDevReviewState:
    review_id = state["review_id"]
    events_path = Path(state["workflow_events_path"])
    append_event(events_path, review_id, "deterministic_plan_check", "started")
    artifacts = _read_json(Path(state["source_artifacts_path"]))
    findings = _build_deterministic_findings(artifacts)
    write_json(Path(state["findings_path"]), _findings_payload(state, findings, []))
    append_event(events_path, review_id, "deterministic_plan_check", "completed", {"findings_count": len(findings)})
    return {"deterministic_findings": findings}


def build_llm_review_node(review_client: InDevReviewClient | None):
    def llm_in_dev_review(state: InDevReviewState) -> InDevReviewState:
        review_id = state["review_id"]
        events_path = Path(state["workflow_events_path"])
        append_event(events_path, review_id, "llm_in_dev_review", "started")
        payload = _build_llm_payload(state)
        if review_client is None:
            llm_result = {
                "report": "LLM in-dev review skipped because no review client is configured.",
                "findings": [],
                "summary": "LLM review skipped.",
            }
        else:
            try:
                llm_result = review_client.review(payload)
            except Exception as error:
                llm_result = {
                    "report": f"LLM in-dev review failed: {error}",
                    "findings": [
                        {
                            "severity": "medium",
                            "category": "llm_review_failure",
                            "title": "LLM in-dev review failed.",
                            "evidence": [str(error)],
                            "expected": "LLM review should complete or fail into an auditable local artifact.",
                            "suggested_fix": "Check DeepSeek/LangSmith configuration and retry the in-dev review.",
                        }
                    ],
                    "summary": "LLM review failed.",
                }
        llm_findings = list(llm_result.get("findings", []))
        report = _build_report(state, str(llm_result.get("report", "")))
        write_text(Path(state["in_dev_report_path"]), report)
        write_json(Path(state["findings_path"]), _findings_payload(state, state.get("deterministic_findings", []), llm_findings))
        append_event(events_path, review_id, "llm_in_dev_review", "completed", {"llm_findings_count": len(llm_findings)})
        return {"llm_findings": llm_findings}

    return llm_in_dev_review


def draft_fix_plan(state: InDevReviewState) -> InDevReviewState:
    review_id = state["review_id"]
    events_path = Path(state["workflow_events_path"])
    append_event(events_path, review_id, "draft_fix_plan", "started")
    all_findings = state.get("deterministic_findings", []) + state.get("llm_findings", [])
    lines = [
        "# Fix Plan Draft",
        "",
        f"Review: `{review_id}`",
        f"Run: `{state['run_id']}`",
        "",
        "Approval required: true",
        "",
    ]
    if not all_findings:
        lines.append("No fixes are proposed. The review did not find actionable issues.")
    else:
        lines.append("Proposed fixes:")
        for index, finding in enumerate(all_findings, start=1):
            lines.append(f"{index}. [{finding.get('severity', 'info')}] {finding.get('title', 'Untitled finding')}")
            lines.append(f"   - Suggested fix: {finding.get('suggested_fix', 'Review manually.')}")
    lines.append("")
    lines.append("Implementation is blocked until the user approves this plan.")
    write_text(Path(state["fix_plan_draft_path"]), "\n".join(lines) + "\n")
    append_event(events_path, review_id, "draft_fix_plan", "completed", {"findings_count": len(all_findings)})
    return {}


def write_review_state(state: InDevReviewState) -> InDevReviewState:
    write_json(Path(state["graph_state_path"]), _serializable_state(state))
    return {}


def human_approval_interrupt(state: InDevReviewState) -> InDevReviewState:
    decision = interrupt(
        {
            "action": "approve_in_dev_fix_plan",
            "review_id": state["review_id"],
            "run_id": state["run_id"],
            "findings_count": len(state.get("deterministic_findings", [])) + len(state.get("llm_findings", [])),
            "fix_plan_draft_path": state["fix_plan_draft_path"],
            "message": "Approve or reject the in-dev review fix plan.",
        }
    )
    approved = bool(decision.get("approved")) if isinstance(decision, dict) else bool(decision)
    notes = decision.get("notes") if isinstance(decision, dict) else None
    approval = {"approved": approved, "notes": notes}
    status = "approved" if approved else "rejected"
    events_path = Path(state["workflow_events_path"])
    append_event(events_path, state["review_id"], "human_approval_interrupt", status, approval)
    next_state = {"approval": approval, "status": status}
    merged_state = {**state, **next_state}
    write_json(Path(state["graph_state_path"]), _serializable_state(merged_state))
    return next_state


def _build_deterministic_findings(artifacts: dict[str, Any]) -> list[dict[str, Any]]:
    findings = []
    run_offsets = artifacts.get("run_config", {}).get("observation_offsets", [])
    if run_offsets != EXPECTED_OBSERVATION_OFFSETS:
        findings.append(
            _finding(
                severity="high",
                category="plan_mismatch",
                title="Run observation offsets do not match the current plan.",
                evidence=[f"run-config observation_offsets={run_offsets}"],
                expected=f"Expected observation offsets {EXPECTED_OBSERVATION_OFFSETS}.",
                suggested_fix="Regenerate the research run after updating the observation-offset contract.",
            )
        )

    report = str(artifacts.get("final_report", ""))
    missing_mentions = [f"N+{offset}" for offset in EXPECTED_OBSERVATION_OFFSETS if f"N+{offset}" not in report]
    if missing_mentions:
        findings.append(
            _finding(
                severity="medium",
                category="plan_mismatch",
                title="Final report does not mention all configured observation offsets.",
                evidence=[f"Missing mentions: {', '.join(missing_mentions)}"],
                expected="Report should discuss N+1/N+3/N+5/N+15/N+30/N+60/N+90/N+180 or explain N/A offsets.",
                suggested_fix="Update the report prompt or run summary payload so the reviewer covers all configured offsets.",
            )
        )

    actual_offsets = set()
    for score_file in artifacts.get("backtest_scores", []):
        for strategy_score in score_file.get("content", {}).get("strategy_scores", []):
            for observation in strategy_score.get("observation_scores", []):
                actual_offsets.add(observation.get("offset_days"))
    missing_score_offsets = [offset for offset in EXPECTED_OBSERVATION_OFFSETS if offset not in actual_offsets]
    if missing_score_offsets:
        findings.append(
            _finding(
                severity="high",
                category="artifact_gap",
                title="Backtest score artifacts are missing configured observation offsets.",
                evidence=[f"Missing score offsets: {missing_score_offsets}"],
                expected=f"Each strategy score should include offsets {EXPECTED_OBSERVATION_OFFSETS}.",
                suggested_fix="Fix backtest scoring artifacts before trusting the generated report.",
            )
        )
    return findings


def _build_llm_payload(state: InDevReviewState) -> dict[str, Any]:
    plan_snapshot = _read_json(Path(state["plan_snapshot_path"]))
    artifacts = _read_json(Path(state["source_artifacts_path"]))
    return {
        "review_id": state["review_id"],
        "run_id": state["run_id"],
        "plan_documents": [
            {
                "path": doc.get("path"),
                "excerpt": str(doc.get("content", ""))[:1800],
            }
            for doc in plan_snapshot.get("documents", [])
        ],
        "run_config": artifacts.get("run_config", {}),
        "run_manifest": artifacts.get("run_manifest", {}),
        "final_report_excerpt": str(artifacts.get("final_report", ""))[:6000],
        "ai_review_status": artifacts.get("ai_review", {}).get("status"),
        "backtest_summary": _summarize_backtest_scores(artifacts.get("backtest_scores", [])),
        "deterministic_findings": state.get("deterministic_findings", []),
    }


def _summarize_backtest_scores(backtest_scores: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries = []
    for score_file in backtest_scores:
        strategies = []
        for strategy_score in score_file.get("content", {}).get("strategy_scores", []):
            observations = strategy_score.get("observation_scores", [])
            strategies.append(
                {
                    "strategy_id": strategy_score.get("strategy_id"),
                    "action": strategy_score.get("signal", {}).get("action"),
                    "average_directional_score": strategy_score.get("average_directional_score"),
                    "match_count": strategy_score.get("match_count"),
                    "mismatch_count": strategy_score.get("mismatch_count"),
                    "neutral_count": strategy_score.get("neutral_count"),
                    "unavailable_count": strategy_score.get("unavailable_count"),
                    "observation_offsets": [observation.get("offset_days") for observation in observations],
                    "na_offsets": [
                        observation.get("offset_days")
                        for observation in observations
                        if observation.get("match_label") == "N/A"
                    ],
                }
            )
        summaries.append({"path": score_file.get("path"), "strategies": strategies})
    return summaries


def _finding(
    severity: str,
    category: str,
    title: str,
    evidence: list[str],
    expected: str,
    suggested_fix: str,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "category": category,
        "title": title,
        "evidence": evidence,
        "expected": expected,
        "suggested_fix": suggested_fix,
    }


def _findings_payload(state: InDevReviewState, deterministic_findings: list[dict], llm_findings: list[dict]) -> dict[str, Any]:
    findings = deterministic_findings + llm_findings
    return {
        "review_id": state["review_id"],
        "run_id": state["run_id"],
        "status": "needs_fix" if findings else "passed",
        "findings": findings,
    }


def _build_report(state: InDevReviewState, llm_report: str) -> str:
    findings = state.get("deterministic_findings", [])
    lines = [
        "# In-Dev Review Report",
        "",
        f"- Review ID: `{state['review_id']}`",
        f"- Research Run: `{state['run_id']}`",
        f"- Status: `{'needs_fix' if findings else 'passed'}`",
        "",
        "## Deterministic Findings",
        "",
    ]
    if findings:
        for finding in findings:
            lines.append(f"- **{finding['severity']}** `{finding['category']}`: {finding['title']}")
    else:
        lines.append("- No deterministic findings.")
    lines.extend(["", "## LLM Review", "", llm_report.strip() or "LLM review did not return content.", ""])
    return "\n".join(lines)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _serializable_state(state: InDevReviewState) -> dict[str, Any]:
    return {key: value for key, value in state.items() if key != "__interrupt__"}
