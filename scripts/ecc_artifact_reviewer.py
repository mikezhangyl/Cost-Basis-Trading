from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol
from uuid import uuid4

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional in non-backend environments
    load_dotenv = None


REQUIRED_RUN_FILES = [
    "run-config.json",
    "run-manifest.json",
    "aggregate/final_report.md",
    "aggregate/ai_review.json",
]
EXPECTED_OBSERVATION_OFFSETS = [1, 3, 5, 15, 30, 60, 90, 180]


@dataclass(frozen=True)
class ArtifactReviewResult:
    review_id: str
    run_id: str
    status: str
    artifact_dir: str
    findings_count: int
    approval_required: bool
    artifact_refs: dict[str, str]


class ArtifactReviewClient(Protocol):
    def review(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...


class StaticArtifactReviewClient:
    def __init__(self, report: str) -> None:
        self.report = report

    def review(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "report": self.report,
            "findings": [],
            "summary": "Static ECC artifact review completed.",
        }


class DeepSeekArtifactReviewClient:
    def __init__(self, client: object, model: str = "deepseek-v4-pro") -> None:
        self.client = client
        self.model = model

    @classmethod
    def from_environment(cls) -> "DeepSeekArtifactReviewClient | None":
        _load_env_files()
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            return None
        try:
            from openai import OpenAI
        except ImportError:
            return None
        client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com", timeout=90.0, max_retries=0)
        return cls(client)

    def review(self, payload: dict[str, Any]) -> dict[str, Any]:
        prompt = _build_llm_prompt(payload)
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "你是 ECC Artifact Reviewer，只审核开发期产物与计划的一致性，不给投资建议。",
                },
                {"role": "user", "content": prompt},
            ],
            stream=False,
            timeout=90.0,
            reasoning_effort="high",
            extra_body={"thinking": {"type": "enabled"}},
        )
        content = response.choices[0].message.content
        return {
            "report": content,
            "findings": [],
            "summary": _first_line(content),
        }


class EccArtifactReviewer:
    def __init__(
        self,
        research_run_root: Path | None = None,
        plan_doc_paths: list[Path] | None = None,
        review_client: ArtifactReviewClient | None = None,
    ) -> None:
        self.research_run_root = research_run_root or default_research_run_root()
        self.plan_doc_paths = plan_doc_paths or default_plan_doc_paths()
        self.review_client = review_client

    def review_run(self, run_id: str) -> ArtifactReviewResult:
        safe_run_id = _validate_simple_name(run_id)
        review_id = _build_review_id()
        run_dir = self.research_run_root / safe_run_id
        if not run_dir.exists():
            raise FileNotFoundError(str(run_dir))
        review_dir = _build_review_dir(run_dir, review_id)
        review_dir.mkdir(parents=True, exist_ok=True)
        events_path = review_dir / "workflow-events.jsonl"

        _append_event(events_path, review_id, "load_plan_context", "started")
        plan_snapshot = collect_plan_snapshot(self.plan_doc_paths)
        _write_json(review_dir / "plan-snapshot.json", plan_snapshot)
        _append_event(events_path, review_id, "load_plan_context", "completed", {"document_count": len(plan_snapshot["documents"])})

        _append_event(events_path, review_id, "load_research_run_artifacts", "started")
        source_artifacts = collect_source_artifacts(run_dir)
        _write_json(review_dir / "source-artifacts.json", source_artifacts)
        _append_event(
            events_path,
            review_id,
            "load_research_run_artifacts",
            "completed",
            {"artifact_count": len(source_artifacts["paths"])},
        )

        _append_event(events_path, review_id, "deterministic_artifact_check", "started")
        deterministic_findings = _build_deterministic_findings(source_artifacts)
        _append_event(
            events_path,
            review_id,
            "deterministic_artifact_check",
            "completed",
            {"findings_count": len(deterministic_findings)},
        )

        _append_event(events_path, review_id, "llm_artifact_review", "started")
        llm_result = self._run_llm_review(review_dir, review_id, safe_run_id, plan_snapshot, source_artifacts, deterministic_findings)
        llm_findings = list(llm_result.get("findings", []))
        _append_event(events_path, review_id, "llm_artifact_review", "completed", {"findings_count": len(llm_findings)})

        findings_payload = _findings_payload(review_id, safe_run_id, deterministic_findings, llm_findings)
        _write_json(review_dir / "findings.json", findings_payload)
        _write_text(review_dir / "artifact-review-report.md", _build_report(review_id, safe_run_id, deterministic_findings, str(llm_result.get("report", ""))))
        _write_text(review_dir / "fix-plan-draft.md", _build_fix_plan(review_id, safe_run_id, findings_payload["findings"]))

        status = "needs_fix" if findings_payload["findings"] else "passed"
        result = ArtifactReviewResult(
            review_id=review_id,
            run_id=safe_run_id,
            status=status,
            artifact_dir=str(review_dir),
            findings_count=len(findings_payload["findings"]),
            approval_required=status == "needs_fix",
            artifact_refs=_build_artifact_refs(review_dir),
        )
        _write_json(
            review_dir / "review-config.json",
            {
                "review_id": review_id,
                "run_id": safe_run_id,
                "reviewer": "ECC Artifact Reviewer",
                "storage": "run_local",
                "research_run_dir": str(run_dir),
                "artifact_dir": str(review_dir),
                "plan_doc_paths": [str(path) for path in self.plan_doc_paths],
            },
        )
        _write_json(
            review_dir / "review-state.json",
            {
                "review_id": review_id,
                "run_id": safe_run_id,
                "status": status,
                "findings_count": result.findings_count,
                "approval_required": result.approval_required,
            },
        )
        _write_json(review_dir.parent / "latest.json", asdict(result))
        _append_event(events_path, review_id, "write_artifact_review", "completed", {"status": status})
        return result

    def _run_llm_review(
        self,
        review_dir: Path,
        review_id: str,
        run_id: str,
        plan_snapshot: dict[str, Any],
        source_artifacts: dict[str, Any],
        deterministic_findings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload = _build_llm_payload(review_id, run_id, plan_snapshot, source_artifacts, deterministic_findings)
        call_log_path = review_dir / "llm-calls.jsonl"
        if self.review_client is None:
            result = {
                "report": "LLM artifact review skipped because no reviewer client is configured.",
                "findings": [],
                "summary": "LLM artifact review skipped.",
            }
            _append_jsonl(call_log_path, _llm_call_log(review_id, "skipped", payload, result))
            return result
        started_at = perf_counter()
        try:
            result = self.review_client.review(payload)
        except Exception as error:
            result = {
                "report": f"LLM artifact review failed: {error}",
                "findings": [
                    {
                        "severity": "medium",
                        "category": "llm_review_failure",
                        "title": "LLM artifact review failed.",
                        "evidence": [str(error)],
                        "expected": "LLM review should complete or fail into an auditable local artifact.",
                        "suggested_fix": "Check reviewer API configuration and rerun ECC Artifact Reviewer.",
                    }
                ],
                "summary": "LLM artifact review failed.",
            }
            _append_jsonl(call_log_path, _llm_call_log(review_id, "error", payload, result, int((perf_counter() - started_at) * 1000)))
            return result
        _append_jsonl(call_log_path, _llm_call_log(review_id, "ok", payload, result, int((perf_counter() - started_at) * 1000)))
        return result


def default_research_run_root() -> Path:
    return Path(__file__).resolve().parents[1] / "docs" / "research-runs"


def default_plan_doc_paths() -> list[Path]:
    repo_root = Path(__file__).resolve().parents[1]
    return [
        repo_root / "docs" / "product-specs" / "current-state.md",
        repo_root / "docs" / "ARCHITECTURE.md",
        repo_root / "docs" / "design" / "multi-agent-research-workflow.md",
        repo_root / "docs" / "design" / "chip-change-feature-set.md",
        repo_root / "docs" / "exec-plans" / "active" / "phase-1-signal-dashboard.md",
    ]


def collect_source_artifacts(run_dir: Path) -> dict[str, Any]:
    for required_file in REQUIRED_RUN_FILES:
        path = run_dir / required_file
        if not path.exists():
            raise FileNotFoundError(str(path))

    backtest_scores = sorted(run_dir.glob("samples/*/backtest/backtest_score.json"))
    feature_sets = sorted(run_dir.glob("samples/*/features/feature_set.json"))
    signal_files = sorted(run_dir.glob("samples/*/signals/signal_*.json"))
    return {
        "run_config": _load_json(run_dir / "run-config.json"),
        "run_manifest": _load_json(run_dir / "run-manifest.json"),
        "final_report": (run_dir / "aggregate" / "final_report.md").read_text(encoding="utf-8"),
        "ai_review": _load_json(run_dir / "aggregate" / "ai_review.json"),
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Run ECC Artifact Reviewer against a completed research run.")
    parser.add_argument("--run-id", required=True, help="Research run id under docs/research-runs.")
    parser.add_argument("--no-llm", action="store_true", help="Skip optional DeepSeek semantic review.")
    args = parser.parse_args()
    client = None if args.no_llm else DeepSeekArtifactReviewClient.from_environment()
    result = EccArtifactReviewer(review_client=client).review_run(args.run_id)
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0


def _build_review_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"artifact-review-{timestamp}-{uuid4().hex[:8]}"


def _build_review_dir(run_dir: Path, review_id: str) -> Path:
    return run_dir / "ecc-artifact-reviews" / review_id


def _build_artifact_refs(review_dir: Path) -> dict[str, str]:
    return {
        "report": str(review_dir / "artifact-review-report.md"),
        "findings": str(review_dir / "findings.json"),
        "fix_plan": str(review_dir / "fix-plan-draft.md"),
        "state": str(review_dir / "review-state.json"),
        "events": str(review_dir / "workflow-events.jsonl"),
        "llm_calls": str(review_dir / "llm-calls.jsonl"),
    }


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


def _build_llm_payload(
    review_id: str,
    run_id: str,
    plan_snapshot: dict[str, Any],
    artifacts: dict[str, Any],
    deterministic_findings: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "review_id": review_id,
        "run_id": run_id,
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
        "deterministic_findings": deterministic_findings,
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


def _build_llm_prompt(payload: dict[str, Any]) -> str:
    return (
        "请对以下 research run 产物做 ECC Artifact Review。\n"
        "目标：比较计划文档与实际 report/artifacts 是否一致。\n"
        "要求：\n"
        "1. 不给真实投资建议。\n"
        "2. 明确列出符合项、偏离项、bug、文档口径问题、需要用户决策的问题。\n"
        "3. 特别检查观察点、N/A、scoring、future-leak、artifact 完整性。\n"
        "4. 最后给出修复计划草案，但说明必须等待用户 approval。\n\n"
        f"PAYLOAD:\n{payload}"
    )


def _build_report(review_id: str, run_id: str, deterministic_findings: list[dict[str, Any]], llm_report: str) -> str:
    status = "needs_fix" if deterministic_findings else "passed"
    lines = [
        "# ECC Artifact Review Report",
        "",
        f"- Review ID: `{review_id}`",
        f"- Research Run: `{run_id}`",
        f"- Status: `{status}`",
        "",
        "## Deterministic Findings",
        "",
    ]
    if deterministic_findings:
        for finding in deterministic_findings:
            lines.append(f"- **{finding['severity']}** `{finding['category']}`: {finding['title']}")
    else:
        lines.append("- No deterministic findings.")
    lines.extend(["", "## LLM Review", "", llm_report.strip() or "LLM review did not return content.", ""])
    return "\n".join(lines)


def _build_fix_plan(review_id: str, run_id: str, findings: list[dict[str, Any]]) -> str:
    lines = [
        "# Fix Plan Draft",
        "",
        f"Review: `{review_id}`",
        f"Run: `{run_id}`",
        "",
        f"Approval required: {str(bool(findings)).lower()}",
        "",
    ]
    if not findings:
        lines.append("No fixes are proposed. The review did not find actionable issues.")
    else:
        lines.append("Proposed fixes:")
        for index, finding in enumerate(findings, start=1):
            lines.append(f"{index}. [{finding.get('severity', 'info')}] {finding.get('title', 'Untitled finding')}")
            lines.append(f"   - Suggested fix: {finding.get('suggested_fix', 'Review manually.')}")
        lines.append("")
        lines.append("Implementation is blocked until the user approves this plan.")
    return "\n".join(lines) + "\n"


def _findings_payload(
    review_id: str,
    run_id: str,
    deterministic_findings: list[dict[str, Any]],
    llm_findings: list[dict[str, Any]],
) -> dict[str, Any]:
    findings = deterministic_findings + llm_findings
    return {
        "review_id": review_id,
        "run_id": run_id,
        "status": "needs_fix" if findings else "passed",
        "findings": findings,
    }


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


def _llm_call_log(
    review_id: str,
    status: str,
    payload: dict[str, Any],
    result: dict[str, Any],
    duration_ms: int = 0,
) -> dict[str, Any]:
    return {
        "timestamp": _now_iso(),
        "review_id": review_id,
        "agent": "ecc-artifact-reviewer",
        "status": status,
        "duration_ms": duration_ms,
        "request_summary": {
            "run_id": payload.get("run_id"),
            "deterministic_findings_count": len(payload.get("deterministic_findings", [])),
            "plan_document_count": len(payload.get("plan_documents", [])),
        },
        "response_summary": {
            "summary": result.get("summary"),
            "findings_count": len(result.get("findings", [])),
        },
    }


def _load_env_files() -> None:
    if load_dotenv is None:
        return
    repo_root = Path(__file__).resolve().parents[1]
    load_dotenv(repo_root / ".env.local")
    load_dotenv(repo_root / "env.local")


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_json_with_path(path: Path) -> dict[str, Any]:
    return {"path": str(path), "content": _load_json(path)}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _append_event(path: Path, review_id: str, node: str, status: str, details: dict[str, Any] | None = None) -> None:
    _append_jsonl(
        path,
        {
            "timestamp": _now_iso(),
            "review_id": review_id,
            "node": node,
            "status": status,
            "details": details or {},
        },
    )


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _validate_simple_name(value: str) -> str:
    cleaned = value.strip()
    if not cleaned or "/" in cleaned or "\\" in cleaned or ".." in cleaned or "*" in cleaned or "?" in cleaned:
        raise ValueError("Identifier must be a simple directory name.")
    return cleaned


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _first_line(content: str) -> str:
    for line in content.splitlines():
        if line.strip():
            return line.strip()
    return "ECC artifact review completed."


if __name__ == "__main__":
    raise SystemExit(main())
