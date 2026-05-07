from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
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
REQUIRED_FACTOR_RUN_FILES = [
    "factor-run-config.json",
    "factor-run-manifest.json",
    "api-calls.jsonl",
    "api-retry-events.jsonl",
    "worker-events.jsonl",
]
REQUIRED_FACTOR_BATCH_FILES = [
    "factor-batch-summary.json",
    "aggregate-factor-report.md",
]
REQUIRED_FACTOR_REDUNDANCY_REVIEW_FILES = [
    "review-config.json",
    "source-data-manifest.json",
    "review-events.jsonl",
    "cross-object-redundancy-summary.json",
    "pooled-diagnostics.json",
    "factor-redundancy-groups.json",
    "factor-redundancy-report.md",
]
EXPECTED_OBSERVATION_OFFSETS = [1, 3, 5, 15, 30, 60, 90, 180]
EXPECTED_BATCH_DIRECTIONS = {
    "HIGH_FACTOR_OUTPERFORMS_ALL",
    "HIGH_FACTOR_OUTPERFORMS_MAJORITY",
    "LOW_FACTOR_OUTPERFORMS_ALL",
    "LOW_FACTOR_OUTPERFORMS_MAJORITY",
    "MIXED",
    "INSUFFICIENT_DATA",
}


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
        prompt = _build_external_review_prompt(payload)
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
        factor_run_root: Path | None = None,
        factor_batch_root: Path | None = None,
        factor_redundancy_review_root: Path | None = None,
        plan_doc_paths: list[Path] | None = None,
        review_client: ArtifactReviewClient | None = None,
    ) -> None:
        self.research_run_root = research_run_root or default_research_run_root()
        self.factor_run_root = factor_run_root or default_factor_run_root()
        self.factor_batch_root = factor_batch_root or default_factor_batch_root()
        self.factor_redundancy_review_root = factor_redundancy_review_root or default_factor_redundancy_review_root()
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

        quality_prompt = _build_quality_subagent_review_prompt(review_id, safe_run_id, review_dir, deterministic_findings)
        _write_text(review_dir / "quality-subagent-review-prompt.md", quality_prompt)

        _append_event(events_path, review_id, "external_artifact_review", "started")
        external_result = self._run_external_review(
            review_dir,
            review_id,
            safe_run_id,
            plan_snapshot,
            source_artifacts,
            deterministic_findings,
        )
        external_findings = list(external_result.get("findings", []))
        _append_event(events_path, review_id, "external_artifact_review", "completed", {"findings_count": len(external_findings)})

        findings_payload = _findings_payload(review_id, safe_run_id, deterministic_findings, external_findings)
        _write_json(review_dir / "findings.json", findings_payload)
        _write_text(
            review_dir / "artifact-review-report.md",
            _build_report(review_id, safe_run_id, deterministic_findings, external_result, external_findings=external_findings),
        )
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
                "primary_reviewer": "ecc_quality_subagent",
                "external_reviewer": external_result.get("provider", "none"),
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
                "primary_reviewer": "ecc_quality_subagent",
                "quality_subagent_semantic_review": "pending",
                "external_reviewer": external_result.get("provider", "none"),
                "findings_count": result.findings_count,
                "approval_required": result.approval_required,
            },
        )
        _write_json(review_dir.parent / "latest.json", asdict(result))
        _append_event(events_path, review_id, "write_artifact_review", "completed", {"status": status})
        return result

    def review_factor_redundancy_review(self, redundancy_review_id: str) -> ArtifactReviewResult:
        safe_review_id = _validate_simple_name(redundancy_review_id)
        review_id = _build_review_id()
        redundancy_review_dir = self.factor_redundancy_review_root / safe_review_id
        if not redundancy_review_dir.exists():
            raise FileNotFoundError(str(redundancy_review_dir))
        review_dir = _build_review_dir(redundancy_review_dir, review_id)
        review_dir.mkdir(parents=True, exist_ok=True)
        events_path = review_dir / "workflow-events.jsonl"

        _append_event(events_path, review_id, "load_plan_context", "started")
        plan_snapshot = collect_plan_snapshot(self.plan_doc_paths)
        _write_json(review_dir / "plan-snapshot.json", plan_snapshot)
        _append_event(events_path, review_id, "load_plan_context", "completed", {"document_count": len(plan_snapshot["documents"])})

        _append_event(events_path, review_id, "load_factor_redundancy_review_artifacts", "started")
        source_artifacts = collect_factor_redundancy_review_source_artifacts(redundancy_review_dir)
        _write_json(review_dir / "source-artifacts.json", source_artifacts)
        _append_event(
            events_path,
            review_id,
            "load_factor_redundancy_review_artifacts",
            "completed",
            {"artifact_count": len(source_artifacts["paths"])},
        )

        _append_event(events_path, review_id, "deterministic_artifact_check", "started")
        deterministic_findings = _build_factor_redundancy_review_deterministic_findings(source_artifacts)
        _append_event(
            events_path,
            review_id,
            "deterministic_artifact_check",
            "completed",
            {"findings_count": len(deterministic_findings)},
        )

        quality_prompt = _build_quality_subagent_review_prompt(
            review_id,
            safe_review_id,
            review_dir,
            deterministic_findings,
            artifact_label="Factor Redundancy Review",
        )
        _write_text(review_dir / "quality-subagent-review-prompt.md", quality_prompt)

        _append_event(events_path, review_id, "external_artifact_review", "started")
        payload = _build_factor_redundancy_review_external_review_payload(
            review_id,
            safe_review_id,
            plan_snapshot,
            source_artifacts,
            deterministic_findings,
        )
        external_result = self._run_external_review_payload(review_dir, review_id, payload)
        external_findings = list(external_result.get("findings", []))
        _append_event(events_path, review_id, "external_artifact_review", "completed", {"findings_count": len(external_findings)})

        findings_payload = _findings_payload(review_id, safe_review_id, deterministic_findings, external_findings)
        _write_json(review_dir / "findings.json", findings_payload)
        _write_text(
            review_dir / "artifact-review-report.md",
            _build_report(
                review_id,
                safe_review_id,
                deterministic_findings,
                external_result,
                external_findings=external_findings,
                artifact_label="Factor Redundancy Review",
            ),
        )
        _write_text(review_dir / "fix-plan-draft.md", _build_fix_plan(review_id, safe_review_id, findings_payload["findings"]))

        status = "needs_fix" if findings_payload["findings"] else "passed"
        result = ArtifactReviewResult(
            review_id=review_id,
            run_id=safe_review_id,
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
                "run_id": safe_review_id,
                "reviewer": "ECC Artifact Reviewer",
                "primary_reviewer": "ecc_quality_subagent",
                "external_reviewer": external_result.get("provider", "none"),
                "storage": "run_local",
                "artifact_type": "factor_redundancy_review",
                "factor_redundancy_review_dir": str(redundancy_review_dir),
                "artifact_dir": str(review_dir),
                "plan_doc_paths": [str(path) for path in self.plan_doc_paths],
            },
        )
        _write_json(
            review_dir / "review-state.json",
            {
                "review_id": review_id,
                "run_id": safe_review_id,
                "status": status,
                "primary_reviewer": "ecc_quality_subagent",
                "quality_subagent_semantic_review": "pending",
                "external_reviewer": external_result.get("provider", "none"),
                "findings_count": result.findings_count,
                "approval_required": result.approval_required,
            },
        )
        _write_json(review_dir.parent / "latest.json", asdict(result))
        _append_event(events_path, review_id, "write_artifact_review", "completed", {"status": status})
        return result

    def review_factor_batch(self, batch_id: str) -> ArtifactReviewResult:
        safe_batch_id = _validate_simple_name(batch_id)
        review_id = _build_review_id()
        batch_dir = self.factor_batch_root / safe_batch_id
        if not batch_dir.exists():
            raise FileNotFoundError(str(batch_dir))
        review_dir = _build_review_dir(batch_dir, review_id)
        review_dir.mkdir(parents=True, exist_ok=True)
        events_path = review_dir / "workflow-events.jsonl"

        _append_event(events_path, review_id, "load_plan_context", "started")
        plan_snapshot = collect_plan_snapshot(self.plan_doc_paths)
        _write_json(review_dir / "plan-snapshot.json", plan_snapshot)
        _append_event(events_path, review_id, "load_plan_context", "completed", {"document_count": len(plan_snapshot["documents"])})

        _append_event(events_path, review_id, "load_factor_batch_artifacts", "started")
        source_artifacts = collect_factor_batch_source_artifacts(batch_dir)
        _write_json(review_dir / "source-artifacts.json", source_artifacts)
        _append_event(
            events_path,
            review_id,
            "load_factor_batch_artifacts",
            "completed",
            {"artifact_count": len(source_artifacts["paths"])},
        )

        _append_event(events_path, review_id, "deterministic_artifact_check", "started")
        deterministic_findings = _build_factor_batch_deterministic_findings(source_artifacts)
        _append_event(
            events_path,
            review_id,
            "deterministic_artifact_check",
            "completed",
            {"findings_count": len(deterministic_findings)},
        )

        quality_prompt = _build_quality_subagent_review_prompt(
            review_id,
            safe_batch_id,
            review_dir,
            deterministic_findings,
            artifact_label="Factor Batch",
        )
        _write_text(review_dir / "quality-subagent-review-prompt.md", quality_prompt)

        _append_event(events_path, review_id, "external_artifact_review", "started")
        payload = _build_factor_batch_external_review_payload(
            review_id,
            safe_batch_id,
            plan_snapshot,
            source_artifacts,
            deterministic_findings,
        )
        external_result = self._run_external_review_payload(review_dir, review_id, payload)
        external_findings = list(external_result.get("findings", []))
        _append_event(events_path, review_id, "external_artifact_review", "completed", {"findings_count": len(external_findings)})

        findings_payload = _findings_payload(review_id, safe_batch_id, deterministic_findings, external_findings)
        _write_json(review_dir / "findings.json", findings_payload)
        _write_text(
            review_dir / "artifact-review-report.md",
            _build_report(
                review_id,
                safe_batch_id,
                deterministic_findings,
                external_result,
                external_findings=external_findings,
                artifact_label="Factor Batch",
            ),
        )
        _write_text(review_dir / "fix-plan-draft.md", _build_fix_plan(review_id, safe_batch_id, findings_payload["findings"]))

        status = "needs_fix" if findings_payload["findings"] else "passed"
        result = ArtifactReviewResult(
            review_id=review_id,
            run_id=safe_batch_id,
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
                "run_id": safe_batch_id,
                "reviewer": "ECC Artifact Reviewer",
                "primary_reviewer": "ecc_quality_subagent",
                "external_reviewer": external_result.get("provider", "none"),
                "storage": "run_local",
                "artifact_type": "factor_batch",
                "factor_batch_dir": str(batch_dir),
                "artifact_dir": str(review_dir),
                "plan_doc_paths": [str(path) for path in self.plan_doc_paths],
            },
        )
        _write_json(
            review_dir / "review-state.json",
            {
                "review_id": review_id,
                "run_id": safe_batch_id,
                "status": status,
                "primary_reviewer": "ecc_quality_subagent",
                "quality_subagent_semantic_review": "pending",
                "external_reviewer": external_result.get("provider", "none"),
                "findings_count": result.findings_count,
                "approval_required": result.approval_required,
            },
        )
        _write_json(review_dir.parent / "latest.json", asdict(result))
        _append_event(events_path, review_id, "write_artifact_review", "completed", {"status": status})
        return result

    def review_factor_run(self, run_id: str) -> ArtifactReviewResult:
        safe_run_id = _validate_simple_name(run_id)
        review_id = _build_review_id()
        run_dir = self.factor_run_root / safe_run_id
        if not run_dir.exists():
            raise FileNotFoundError(str(run_dir))
        review_dir = _build_review_dir(run_dir, review_id)
        review_dir.mkdir(parents=True, exist_ok=True)
        events_path = review_dir / "workflow-events.jsonl"

        _append_event(events_path, review_id, "load_plan_context", "started")
        plan_snapshot = collect_plan_snapshot(self.plan_doc_paths)
        _write_json(review_dir / "plan-snapshot.json", plan_snapshot)
        _append_event(events_path, review_id, "load_plan_context", "completed", {"document_count": len(plan_snapshot["documents"])})

        _append_event(events_path, review_id, "load_factor_run_artifacts", "started")
        source_artifacts = collect_factor_source_artifacts(run_dir)
        _write_json(review_dir / "source-artifacts.json", source_artifacts)
        _append_event(
            events_path,
            review_id,
            "load_factor_run_artifacts",
            "completed",
            {"artifact_count": len(source_artifacts["paths"])},
        )

        _append_event(events_path, review_id, "deterministic_artifact_check", "started")
        deterministic_findings = _build_factor_deterministic_findings(source_artifacts)
        _append_event(
            events_path,
            review_id,
            "deterministic_artifact_check",
            "completed",
            {"findings_count": len(deterministic_findings)},
        )

        quality_prompt = _build_quality_subagent_review_prompt(
            review_id,
            safe_run_id,
            review_dir,
            deterministic_findings,
            artifact_label="Factor Run",
        )
        _write_text(review_dir / "quality-subagent-review-prompt.md", quality_prompt)

        _append_event(events_path, review_id, "external_artifact_review", "started")
        payload = _build_factor_external_review_payload(review_id, safe_run_id, plan_snapshot, source_artifacts, deterministic_findings)
        external_result = self._run_external_review_payload(review_dir, review_id, payload)
        external_findings = list(external_result.get("findings", []))
        _append_event(events_path, review_id, "external_artifact_review", "completed", {"findings_count": len(external_findings)})

        findings_payload = _findings_payload(review_id, safe_run_id, deterministic_findings, external_findings)
        _write_json(review_dir / "findings.json", findings_payload)
        _write_text(
            review_dir / "artifact-review-report.md",
            _build_report(
                review_id,
                safe_run_id,
                deterministic_findings,
                external_result,
                external_findings=external_findings,
                artifact_label="Factor Run",
            ),
        )
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
                "primary_reviewer": "ecc_quality_subagent",
                "external_reviewer": external_result.get("provider", "none"),
                "storage": "run_local",
                "artifact_type": "factor_run",
                "factor_run_dir": str(run_dir),
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
                "primary_reviewer": "ecc_quality_subagent",
                "quality_subagent_semantic_review": "pending",
                "external_reviewer": external_result.get("provider", "none"),
                "findings_count": result.findings_count,
                "approval_required": result.approval_required,
            },
        )
        _write_json(review_dir.parent / "latest.json", asdict(result))
        _append_event(events_path, review_id, "write_artifact_review", "completed", {"status": status})
        return result

    def _run_external_review(
        self,
        review_dir: Path,
        review_id: str,
        run_id: str,
        plan_snapshot: dict[str, Any],
        source_artifacts: dict[str, Any],
        deterministic_findings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload = _build_external_review_payload(review_id, run_id, plan_snapshot, source_artifacts, deterministic_findings)
        return self._run_external_review_payload(review_dir, review_id, payload)

    def _run_external_review_payload(
        self,
        review_dir: Path,
        review_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        call_log_path = review_dir / "external-review-calls.jsonl"
        if self.review_client is None:
            result = {
                "provider": "none",
                "report": "External artifact review was not requested. Current Codex session is the primary semantic reviewer.",
                "findings": [],
                "summary": "External artifact review not requested.",
            }
            _append_jsonl(call_log_path, _external_review_call_log(review_id, "not_requested", payload, result))
            return result
        started_at = perf_counter()
        try:
            result = self.review_client.review(payload)
            result = {"provider": "external", **result}
        except Exception as error:
            result = {
                "provider": "external",
                "report": f"External artifact review failed: {error}",
                "findings": [
                    {
                        "severity": "medium",
                        "category": "external_review_failure",
                        "title": "External artifact review failed.",
                        "evidence": [str(error)],
                        "expected": "External reviewer should complete or fail into an auditable local artifact.",
                "suggested_fix": "Check external reviewer configuration or rerun with ECC Quality Sub-Agent review only.",
                    }
                ],
                "summary": "External artifact review failed.",
            }
            _append_jsonl(
                call_log_path,
                _external_review_call_log(review_id, "error", payload, result, int((perf_counter() - started_at) * 1000)),
            )
            return result
        _append_jsonl(
            call_log_path,
            _external_review_call_log(review_id, "ok", payload, result, int((perf_counter() - started_at) * 1000)),
        )
        return result


def default_research_run_root() -> Path:
    return Path(__file__).resolve().parents[1] / "docs" / "research-runs"


def default_factor_run_root() -> Path:
    return Path(__file__).resolve().parents[1] / "docs" / "factor-runs"


def default_factor_batch_root() -> Path:
    return Path(__file__).resolve().parents[1] / "docs" / "factor-batches"


def default_factor_redundancy_review_root() -> Path:
    return Path(__file__).resolve().parents[1] / "docs" / "factor-redundancy-reviews"


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

    api_call_log = run_dir / "api-calls.jsonl"
    backtest_scores = sorted(run_dir.glob("samples/*/backtest/backtest_score.json"))
    feature_sets = sorted(run_dir.glob("samples/*/features/feature_set.json"))
    signal_files = sorted(run_dir.glob("samples/*/signals/signal_*.json"))
    stage_manifests = sorted(run_dir.glob("samples/*/*/manifest.json"))
    decision_logs = sorted(run_dir.glob("samples/*/signals/agent_decision_log.jsonl"))
    aggregate_decision_log = run_dir / "aggregate" / "agent-decisions.jsonl"
    decision_log_paths = decision_logs + ([aggregate_decision_log] if aggregate_decision_log.exists() else [])
    api_retry_log = run_dir / "api-retry-events.jsonl"
    optional_paths = [path for path in [api_call_log, api_retry_log] if path.exists()] + stage_manifests + decision_log_paths
    return {
        "run_config": _load_json(run_dir / "run-config.json"),
        "run_manifest": _load_json(run_dir / "run-manifest.json"),
        "final_report": (run_dir / "aggregate" / "final_report.md").read_text(encoding="utf-8"),
        "ai_review": _load_json(run_dir / "aggregate" / "ai_review.json"),
        "api_calls": _load_jsonl(api_call_log) if api_call_log.exists() else [],
        "api_retry_events": _load_jsonl(api_retry_log) if api_retry_log.exists() else [],
        "backtest_scores": [_load_json_with_path(path) for path in backtest_scores],
        "feature_sets": [_load_json_with_path(path) for path in feature_sets],
        "signal_files": [_load_json_with_path(path) for path in signal_files],
        "stage_manifests": [_load_json_with_path(path) for path in stage_manifests],
        "decision_logs": [_load_jsonl_with_path(path) for path in decision_log_paths],
        "paths": [str(path) for path in [run_dir / file for file in REQUIRED_RUN_FILES]]
        + [str(path) for path in backtest_scores + feature_sets + signal_files + optional_paths],
    }


def collect_factor_source_artifacts(run_dir: Path) -> dict[str, Any]:
    for required_file in REQUIRED_FACTOR_RUN_FILES:
        path = run_dir / required_file
        if not path.exists():
            raise FileNotFoundError(str(path))

    run_config = _load_json(run_dir / "factor-run-config.json")
    run_manifest = _load_json(run_dir / "factor-run-manifest.json")
    api_call_log = run_dir / "api-calls.jsonl"
    api_retry_log = run_dir / "api-retry-events.jsonl"
    worker_events_log = run_dir / "worker-events.jsonl"
    api_calls = _load_jsonl(api_call_log)
    stock_artifacts = _collect_factor_stock_artifacts(run_dir, run_manifest)
    cache_artifacts = _collect_factor_cache_artifacts(run_config, api_calls)
    stock_paths = []
    for stock in stock_artifacts:
        stock_paths.extend(str(item.get("path")) for item in stock.get("files", {}).values() if item.get("path"))
    return {
        "artifact_type": "factor_run",
        "run_config": run_config,
        "run_manifest": run_manifest,
        "api_calls": api_calls,
        "api_retry_events": _load_jsonl(api_retry_log),
        "worker_events": _load_jsonl(worker_events_log),
        "stock_artifacts": stock_artifacts,
        "cache_artifacts": cache_artifacts,
        "paths": [str(run_dir / file) for file in REQUIRED_FACTOR_RUN_FILES] + stock_paths,
    }


def collect_factor_batch_source_artifacts(batch_dir: Path) -> dict[str, Any]:
    for required_file in REQUIRED_FACTOR_BATCH_FILES:
        path = batch_dir / required_file
        if not path.exists():
            raise FileNotFoundError(str(path))

    config_path = batch_dir / "factor-batch-config.json"
    events_path = batch_dir / "batch-events.jsonl"
    stock_results_path = batch_dir / "stock-results.jsonl"
    optional_paths = [path for path in (config_path, events_path, stock_results_path) if path.exists()]
    return {
        "artifact_type": "factor_batch",
        "batch_summary": _load_json(batch_dir / "factor-batch-summary.json"),
        "aggregate_report": (batch_dir / "aggregate-factor-report.md").read_text(encoding="utf-8"),
        "batch_config": _load_json(config_path) if config_path.exists() else {},
        "batch_events": _load_jsonl(events_path) if events_path.exists() else [],
        "stock_results_log": _load_jsonl(stock_results_path) if stock_results_path.exists() else [],
        "paths": [str(batch_dir / file) for file in REQUIRED_FACTOR_BATCH_FILES] + [str(path) for path in optional_paths],
    }


def collect_factor_redundancy_review_source_artifacts(review_dir: Path) -> dict[str, Any]:
    for required_file in REQUIRED_FACTOR_REDUNDANCY_REVIEW_FILES:
        path = review_dir / required_file
        if not path.exists():
            raise FileNotFoundError(str(path))

    manifest = _load_json(review_dir / "source-data-manifest.json")
    per_instrument_artifacts = []
    for instrument in manifest.get("instruments", []):
        instrument_id = str(instrument.get("instrument_id"))
        instrument_dir = review_dir / "per-instrument" / instrument_id
        per_instrument_artifacts.append(
            {
                "instrument_id": instrument_id,
                "dir": str(instrument_dir),
                "correlation_matrix": _file_presence(instrument_dir / "factor-correlation-matrix.csv"),
                "pair_relationships": _json_file_summary(instrument_dir / "factor-pair-relationships.json"),
                "retention_decisions": _json_file_summary(instrument_dir / "factor-retention-decisions.json"),
            }
        )
    required_paths = [review_dir / file for file in REQUIRED_FACTOR_REDUNDANCY_REVIEW_FILES]
    instrument_paths = [
        Path(str(item["correlation_matrix"]["path"]))
        for item in per_instrument_artifacts
        if item["correlation_matrix"].get("path")
    ]
    instrument_paths.extend(
        Path(str(item["pair_relationships"]["path"]))
        for item in per_instrument_artifacts
        if item["pair_relationships"].get("path")
    )
    instrument_paths.extend(
        Path(str(item["retention_decisions"]["path"]))
        for item in per_instrument_artifacts
        if item["retention_decisions"].get("path")
    )
    return {
        "artifact_type": "factor_redundancy_review",
        "review_config": _load_json(review_dir / "review-config.json"),
        "source_manifest": manifest,
        "review_events": _load_jsonl(review_dir / "review-events.jsonl"),
        "cross_object_summary": _load_json(review_dir / "cross-object-redundancy-summary.json"),
        "pooled_diagnostics": _load_json(review_dir / "pooled-diagnostics.json"),
        "redundancy_groups": _load_json(review_dir / "factor-redundancy-groups.json"),
        "report": (review_dir / "factor-redundancy-report.md").read_text(encoding="utf-8"),
        "per_instrument_artifacts": per_instrument_artifacts,
        "paths": [str(path) for path in required_paths + instrument_paths],
    }


def _file_presence(path: Path) -> dict[str, Any]:
    payload: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if path.exists():
        payload["sha256"] = _file_checksum(path)
    return payload


def _json_file_summary(path: Path) -> dict[str, Any]:
    payload = _file_presence(path)
    if path.exists():
        content = _load_json(path)
        payload["content"] = content
        payload["item_count"] = len(content) if isinstance(content, list) else None
    return payload


def _collect_factor_stock_artifacts(run_dir: Path, run_manifest: dict[str, Any]) -> list[dict[str, Any]]:
    stock_artifacts = []
    for stock_output in run_manifest.get("stock_outputs", []):
        ts_code = stock_output.get("ts_code")
        files = {}
        for field in ("snapshot_ref", "factor_ref", "quality_ref", "traceability_ref"):
            ref = stock_output.get(field)
            if not ref:
                files[field] = {"path": None, "exists": False}
                continue
            path = run_dir / str(ref)
            files[field] = _factor_file_summary(path)
        stock_artifacts.append(
            {
                "ts_code": ts_code,
                "stock_output": stock_output,
                "files": files,
            }
        )
    return stock_artifacts


def _factor_file_summary(path: Path) -> dict[str, Any]:
    summary: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        return summary
    summary["sha256"] = _file_checksum(path)
    if path.suffix == ".jsonl":
        rows = _load_jsonl(path)
        summary["row_count"] = len(rows)
        if rows:
            summary["first_row"] = rows[0]
            summary["last_row"] = rows[-1]
            if "factor_date" in rows[0]:
                summary["unique_factor_dates"] = sorted(
                    {row.get("factor_date") for row in rows if row.get("factor_date") is not None}
                )
    elif path.suffix == ".json":
        summary["content"] = _load_json(path)
    return summary


def _factor_checksum_mismatches(files: dict[str, Any], expected_checksums: dict[str, Any]) -> list[str]:
    mismatches = []
    for summary in files.values():
        path = summary.get("path")
        if not path or not summary.get("exists"):
            continue
        filename = Path(str(path)).name
        expected = expected_checksums.get(filename)
        actual = summary.get("sha256")
        if expected is not None and actual is not None and expected != actual:
            mismatches.append(f"{filename}: expected={expected} actual={actual}")
    return mismatches


def _missing_factor_checksum_keys(files: dict[str, Any], expected_checksums: dict[str, Any]) -> list[str]:
    required_keys = []
    for summary in files.values():
        path = summary.get("path")
        if path and summary.get("exists"):
            required_keys.append(Path(str(path)).name)
    return sorted(key for key in required_keys if key not in expected_checksums)


def _collect_factor_cache_artifacts(run_config: dict[str, Any], api_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cache_root_raw = run_config.get("cache_root")
    if not cache_root_raw:
        return []
    cache_root = Path(str(cache_root_raw))
    cache_artifacts = []
    for api_call in api_calls:
        endpoint = api_call.get("endpoint")
        params = api_call.get("params", {})
        ts_code = params.get("ts_code")
        if endpoint == "cyq_chips" and ts_code:
            cache_artifacts.extend(
                _factor_cache_summaries(
                    cache_root / "tushare" / "cyq_chips" / str(ts_code),
                    start_date=str(params.get("start_date", "")),
                    end_date=str(params.get("end_date", "")),
                )
            )
        elif endpoint == "daily" and ts_code:
            start_date = params.get("start_date")
            end_date = params.get("end_date")
            if start_date and end_date:
                cache_artifacts.extend(
                    _factor_cache_summaries(cache_root / "tushare" / "daily" / str(ts_code), f"{start_date}_{end_date}.json")
                )
    return cache_artifacts


def _factor_cache_summaries(
    directory: Path,
    filename: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    paths = [directory / filename] if filename is not None else sorted(directory.glob("*.json"))
    if start_date and end_date:
        paths = [path for path in paths if start_date <= path.stem <= end_date]
    summaries = []
    for path in paths:
        summary: dict[str, Any] = {"path": str(path), "exists": path.exists()}
        if path.exists():
            payload = _load_json(path)
            summary["source"] = payload.get("source", {})
            summary["rows_checksum"] = payload.get("rows_checksum")
            summary["row_count"] = len(payload.get("rows", []))
        summaries.append(summary)
    return summaries


def collect_plan_snapshot(plan_doc_paths: list[Path]) -> dict[str, Any]:
    docs = []
    for path in plan_doc_paths:
        if path.exists():
            docs.append({"path": str(path), "content": path.read_text(encoding="utf-8")})
    return {"documents": docs}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run ECC Artifact Reviewer against a completed run artifact.")
    parser.add_argument("--run-id", required=True, help="Run id under docs/research-runs, docs/factor-runs, docs/factor-batches, or docs/factor-redundancy-reviews.")
    parser.add_argument(
        "--artifact-type",
        choices=["research", "factor", "factor-batch", "factor-redundancy-review"],
        default="research",
        help="Artifact type to review. Defaults to research for backward compatibility.",
    )
    parser.add_argument(
        "--research-run-root",
        type=Path,
        default=None,
        help="Directory containing research run artifacts. Defaults to docs/research-runs.",
    )
    parser.add_argument(
        "--factor-run-root",
        type=Path,
        default=None,
        help="Directory containing factor run artifacts. Defaults to docs/factor-runs.",
    )
    parser.add_argument(
        "--factor-batch-root",
        type=Path,
        default=None,
        help="Directory containing factor batch artifacts. Defaults to docs/factor-batches.",
    )
    parser.add_argument(
        "--factor-redundancy-review-root",
        type=Path,
        default=None,
        help="Directory containing factor redundancy review artifacts. Defaults to docs/factor-redundancy-reviews.",
    )
    parser.add_argument(
        "--external-reviewer",
        choices=["none", "deepseek"],
        default="none",
        help="Optional external reviewer provider. Default prepares review for ECC Quality Sub-Agent.",
    )
    parser.add_argument("--no-llm", action="store_true", help="Deprecated alias for --external-reviewer none.")
    args = parser.parse_args()
    external_reviewer = "none" if args.no_llm else args.external_reviewer
    client = DeepSeekArtifactReviewClient.from_environment() if external_reviewer == "deepseek" else None
    reviewer = EccArtifactReviewer(
        research_run_root=args.research_run_root,
        factor_run_root=args.factor_run_root,
        factor_batch_root=args.factor_batch_root,
        factor_redundancy_review_root=args.factor_redundancy_review_root,
        review_client=client,
    )
    if args.artifact_type == "factor-redundancy-review":
        result = reviewer.review_factor_redundancy_review(args.run_id)
    elif args.artifact_type == "factor-batch":
        result = reviewer.review_factor_batch(args.run_id)
    elif args.artifact_type == "factor":
        result = reviewer.review_factor_run(args.run_id)
    else:
        result = reviewer.review_run(args.run_id)
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
        "quality_subagent_prompt": str(review_dir / "quality-subagent-review-prompt.md"),
        "state": str(review_dir / "review-state.json"),
        "events": str(review_dir / "workflow-events.jsonl"),
        "external_calls": str(review_dir / "external-review-calls.jsonl"),
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
    missing_mentions = [
        f"N+{offset}"
        for offset in EXPECTED_OBSERVATION_OFFSETS
        if not _contains_observation_label(report, f"N+{offset}")
    ]
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


def _build_factor_deterministic_findings(artifacts: dict[str, Any]) -> list[dict[str, Any]]:
    findings = []
    manifest = artifacts.get("run_manifest", {})
    config = artifacts.get("run_config", {})

    if manifest.get("status") != "completed":
        findings.append(
            _finding(
                severity="high",
                category="artifact_gap",
                title="Factor run manifest is not completed.",
                evidence=[f"status={manifest.get('status')}"],
                expected="factor-run-manifest.json should have status=completed before review passes.",
                suggested_fix="Rerun factor production or inspect the failed API call log before trusting outputs.",
            )
        )

    if manifest.get("factor_date_count", 0) <= 0:
        findings.append(
            _finding(
                severity="high",
                category="artifact_gap",
                title="Factor run has no output factor dates.",
                evidence=[f"factor_date_count={manifest.get('factor_date_count')}"],
                expected="A factor run should produce at least one output factor date.",
                suggested_fix="Check the requested date range and trade calendar resolution.",
            )
        )

    if manifest.get("warmup_date_count", 0) <= 0:
        findings.append(
            _finding(
                severity="medium",
                category="warmup_gap",
                title="Factor run has no warmup dates.",
                evidence=[f"warmup_date_count={manifest.get('warmup_date_count')}"],
                expected="Lookback factors should be generated with warmup snapshots before the output window.",
                suggested_fix="Increase warmup_trading_days or widen the trade calendar probe.",
            )
        )

    api_calls = artifacts.get("api_calls", [])
    endpoints = [call.get("endpoint") for call in api_calls]
    for endpoint in ("trade_cal", "cyq_chips", "daily"):
        if endpoint not in endpoints:
            findings.append(
                _finding(
                    severity="high",
                    category="artifact_gap",
                    title=f"Factor run is missing {endpoint} API audit log.",
                    evidence=[f"api endpoints={endpoints}"],
                    expected="Factor runs should log trade_cal, cyq_chips, and daily API calls.",
                    suggested_fix="Fix factor runner API logging before using the run as evidence.",
                )
            )
    failed_calls = [call for call in api_calls if call.get("status") != "ok"]
    if failed_calls:
        findings.append(
            _finding(
                severity="high",
                category="api_failure",
                title="Factor run contains failed API calls.",
                evidence=[f"{call.get('endpoint')} status={call.get('status')} error={call.get('error')}" for call in failed_calls],
                expected="A completed factor run should not contain failed API calls.",
                suggested_fix="Rerun after resolving Tushare/network failures or mark the run unusable.",
            )
        )

    for stock in artifacts.get("stock_artifacts", []):
        stock_output = stock.get("stock_output", {})
        ts_code = stock.get("ts_code")
        checksums = stock_output.get("checksums", {})
        files = stock.get("files", {})
        missing_checksum_keys = _missing_factor_checksum_keys(files, checksums)
        if not checksums or missing_checksum_keys or any(not str(value).startswith("sha256:") for value in checksums.values()):
            findings.append(
                _finding(
                    severity="medium",
                    category="traceability_gap",
                    title="Stock factor artifacts are missing sha256 checksums.",
                    evidence=[f"ts_code={ts_code} missing_keys={missing_checksum_keys} checksums={checksums}"],
                    expected="Each stock output should include sha256 checksums for snapshots, factors, quality, and traceability.",
                    suggested_fix="Regenerate the factor run with checksum-enabled artifact writer.",
                )
            )
        checksum_mismatches = _factor_checksum_mismatches(files, checksums)
        if checksum_mismatches:
            findings.append(
                _finding(
                    severity="high",
                    category="traceability_gap",
                    title="Stock factor artifact checksum does not match the referenced file.",
                    evidence=checksum_mismatches,
                    expected="Manifest sha256 checksums should match the current artifact file bytes.",
                    suggested_fix="Regenerate the factor run or restore the immutable artifact file from a trusted copy.",
                )
            )
        missing_refs = [name for name, summary in files.items() if not summary.get("exists")]
        if missing_refs:
            findings.append(
                _finding(
                    severity="high",
                    category="artifact_gap",
                    title="Stock output references missing files.",
                    evidence=[f"ts_code={ts_code} missing={missing_refs}"],
                    expected="All stock output refs in the manifest should resolve to local files.",
                    suggested_fix="Fix factor artifact writing before reviewing the run.",
                )
            )
        factor_summary = files.get("factor_ref", {})
        snapshot_summary = files.get("snapshot_ref", {})
        expected_snapshot_count = stock_output.get("factor_date_count", 0) + stock_output.get("warmup_snapshot_count", 0)
        if snapshot_summary.get("row_count") != expected_snapshot_count:
            findings.append(
                _finding(
                    severity="high",
                    category="warmup_gap",
                    title="Snapshot row count does not match warmup plus output date coverage.",
                    evidence=[
                        f"ts_code={ts_code} snapshot_rows={snapshot_summary.get('row_count')} "
                        f"factor_date_count={stock_output.get('factor_date_count')} "
                        f"warmup_snapshot_count={stock_output.get('warmup_snapshot_count')}"
                    ],
                    expected="daily-chip-snapshots.jsonl rows should equal warmup_snapshot_count + factor_date_count.",
                    suggested_fix="Fix warmup date slicing or rerun factor production before evaluating factors.",
                )
            )
        if stock_output.get("warmup_snapshot_count") != manifest.get("warmup_date_count"):
            findings.append(
                _finding(
                    severity="medium",
                    category="warmup_gap",
                    title="Stock warmup snapshot count does not match manifest warmup date count.",
                    evidence=[
                        f"ts_code={ts_code} stock_warmup={stock_output.get('warmup_snapshot_count')} "
                        f"manifest_warmup={manifest.get('warmup_date_count')}"
                    ],
                    expected="Single-stock factor runs should keep stock and manifest warmup counts aligned.",
                    suggested_fix="Check manifest aggregation or stock output metadata.",
                )
            )
        factor_dates = factor_summary.get("unique_factor_dates", [])
        if len(factor_dates) != stock_output.get("factor_date_count"):
            findings.append(
                _finding(
                    severity="medium",
                    category="date_window_mismatch",
                    title="Factor output date count does not match factors.jsonl.",
                    evidence=[f"ts_code={ts_code} manifest={stock_output.get('factor_date_count')} factors={len(factor_dates)}"],
                    expected="The manifest factor_date_count should match unique factor dates in factors.jsonl.",
                    suggested_fix="Check output date filtering in the factor runner.",
                )
            )
        quality = files.get("quality_ref", {}).get("content", {})
        non_ok_count = sum(
            count
            for status, count in quality.get("quality_status_counts", {}).items()
            if status != "OK"
        )
        if non_ok_count:
            findings.append(
                _finding(
                    severity="medium",
                    category="factor_quality",
                    title="Factor run contains non-OK factor quality statuses.",
                    evidence=[f"ts_code={ts_code} quality_status_counts={quality.get('quality_status_counts')}"],
                    expected="Smoke factor runs should have OK quality for generated factors before broader evaluation.",
                    suggested_fix="Inspect warmup coverage and missing chip/price data before expanding the sample.",
                )
            )

    return findings


def _build_factor_batch_deterministic_findings(artifacts: dict[str, Any]) -> list[dict[str, Any]]:
    findings = []
    summary = artifacts.get("batch_summary", {})
    stock_results = summary.get("stock_results", [])
    aggregate_summary = summary.get("aggregate_summary", [])
    stock_count = int(summary.get("stock_count", 0) or 0)
    success_count = int(summary.get("success_count", 0) or 0)
    failed_count = int(summary.get("failed_count", 0) or 0)

    if summary.get("status") != "completed":
        findings.append(
            _finding(
                severity="high",
                category="artifact_gap",
                title="Factor batch summary is not completed.",
                evidence=[f"status={summary.get('status')} success_count={success_count} failed_count={failed_count}"],
                expected="factor-batch-summary.json should have status=completed before aggregate conclusions are trusted.",
                suggested_fix="Rerun failed stocks or aggregate a successful retry batch before reviewing cross-stock factor diagnostics.",
            )
        )

    if success_count + failed_count != stock_count:
        findings.append(
            _finding(
                severity="high",
                category="artifact_gap",
                title="Factor batch stock counts are internally inconsistent.",
                evidence=[f"stock_count={stock_count} success_count={success_count} failed_count={failed_count}"],
                expected="success_count + failed_count should equal stock_count.",
                suggested_fix="Regenerate the batch summary from stock-results.jsonl before trusting aggregate statistics.",
            )
        )

    if failed_count:
        findings.append(
            _finding(
                severity="high",
                category="artifact_gap",
                title="Factor batch contains failed stocks.",
                evidence=[f"failed_count={failed_count}"],
                expected="Batch-level factor ranking should only pass review when all requested stocks completed or failures are explicitly excluded by a new aggregate.",
                suggested_fix="Retry failed stocks and create a completed combined aggregate artifact.",
            )
        )

    ts_codes = [str(result.get("ts_code")) for result in stock_results if result.get("ts_code")]
    duplicate_ts_codes = sorted({ts_code for ts_code in ts_codes if ts_codes.count(ts_code) > 1})
    if duplicate_ts_codes:
        findings.append(
            _finding(
                severity="high",
                category="traceability_gap",
                title="Factor batch stock results contain duplicate stock codes.",
                evidence=[f"duplicates={duplicate_ts_codes}"],
                expected="Each stock should appear once after retry merge so aggregate rows are not double-counted.",
                suggested_fix="Fix batch aggregation merge logic or regenerate the combined batch.",
            )
        )
    if len(ts_codes) != stock_count:
        findings.append(
            _finding(
                severity="high",
                category="artifact_gap",
                title="Factor batch stock result rows do not match stock_count.",
                evidence=[f"stock_count={stock_count} stock_result_rows={len(ts_codes)}"],
                expected="stock_results should include one row per requested stock.",
                suggested_fix="Regenerate factor-batch-summary.json from the stock result log.",
            )
        )

    completed_results = [result for result in stock_results if result.get("status") == "completed"]
    failed_results = [result for result in stock_results if result.get("status") != "completed"]
    stock_results_log = artifacts.get("stock_results_log", [])
    if len(completed_results) != success_count or len(failed_results) != failed_count:
        findings.append(
            _finding(
                severity="medium",
                category="artifact_gap",
                title="Factor batch success/failure counters do not match stock result statuses.",
                evidence=[
                    f"success_count={success_count} completed_rows={len(completed_results)} "
                    f"failed_count={failed_count} failed_rows={len(failed_results)}"
                ],
                expected="Summary counters should be derived directly from stock_results statuses.",
                suggested_fix="Regenerate the batch summary or inspect manual edits to factor-batch-summary.json.",
            )
        )
    if stock_results_log and len(stock_results_log) != len(stock_results):
        findings.append(
            _finding(
                severity="medium",
                category="traceability_gap",
                title="Factor batch stock result log does not match summary rows.",
                evidence=[f"stock_results_log_rows={len(stock_results_log)} summary_stock_results={len(stock_results)}"],
                expected="When stock-results.jsonl exists, it should contain one row per summary stock result.",
                suggested_fix="Regenerate the summary from stock-results.jsonl or preserve the immutable original log.",
            )
        )
    elif stock_results_log:
        summary_rows_by_stock = {str(result.get("ts_code")): result for result in stock_results if result.get("ts_code")}
        log_rows_by_stock = {str(result.get("ts_code")): result for result in stock_results_log if result.get("ts_code")}
        if set(summary_rows_by_stock) != set(log_rows_by_stock):
            findings.append(
                _finding(
                    severity="high",
                    category="traceability_gap",
                    title="Factor batch stock result log stock set differs from summary.",
                    evidence=[
                        f"summary_ts_codes={sorted(summary_rows_by_stock)} "
                        f"log_ts_codes={sorted(log_rows_by_stock)}"
                    ],
                    expected="factor-batch-summary.json should preserve the same stock set as the immutable stock-results.jsonl log.",
                    suggested_fix="Regenerate the summary from stock-results.jsonl or investigate artifact tampering.",
                )
            )
        else:
            mismatched_log_rows = [
                ts_code
                for ts_code, summary_row in summary_rows_by_stock.items()
                if _canonical_stock_result_for_log(summary_row) != _canonical_stock_result_for_log(log_rows_by_stock[ts_code])
            ]
            if mismatched_log_rows:
                findings.append(
                    _finding(
                        severity="high",
                        category="traceability_gap",
                        title="Factor batch summary rows differ from immutable stock result log.",
                        evidence=[f"mismatched_ts_codes={sorted(mismatched_log_rows)}"],
                        expected="Summary stock results should match stock-results.jsonl for status, provenance refs, observations, and factor payload shape.",
                        suggested_fix="Regenerate factor-batch-summary.json from stock-results.jsonl before reviewing the aggregate.",
                    )
                )

    for result in completed_results:
        ts_code = result.get("ts_code")
        missing_refs = [
            field
            for field in ("factor_run_id", "factor_run_dir", "evaluation_id", "evaluation_dir")
            if not result.get(field)
        ]
        if missing_refs:
            findings.append(
                _finding(
                    severity="high",
                    category="traceability_gap",
                    title="Completed stock result is missing provenance references.",
                    evidence=[f"ts_code={ts_code} missing={missing_refs}"],
                    expected="Completed stock results should link to factor run and evaluation artifact directories.",
                    suggested_fix="Fix the batch runner return payload before using the aggregate as evidence.",
                )
            )
        missing_dirs = [
            field
            for field in ("factor_run_dir", "evaluation_dir")
            if result.get(field) and not Path(str(result[field])).exists()
        ]
        if missing_dirs:
            findings.append(
                _finding(
                    severity="high",
                    category="traceability_gap",
                    title="Completed stock result references missing artifact directories.",
                    evidence=[f"ts_code={ts_code} missing_dirs={missing_dirs}"],
                    expected="factor_run_dir and evaluation_dir should resolve to local immutable artifacts.",
                    suggested_fix="Restore the referenced run/evaluation artifacts or regenerate the batch with valid refs.",
                )
            )
        if int(result.get("observation_count", 0) or 0) <= 0:
            findings.append(
                _finding(
                    severity="high",
                    category="artifact_gap",
                    title="Completed stock result has no evaluation observations.",
                    evidence=[f"ts_code={ts_code} observation_count={result.get('observation_count')}"],
                    expected="Each completed stock should have positive backtest/evaluation observations.",
                    suggested_fix="Inspect evaluator date alignment and future-price coverage for this stock.",
                )
            )
        if not result.get("summary_by_factor"):
            findings.append(
                _finding(
                    severity="high",
                    category="artifact_gap",
                    title="Completed stock result has no factor summaries.",
                    evidence=[f"ts_code={ts_code} summary_by_factor is empty"],
                    expected="Each completed stock should contribute per-factor diagnostics to the aggregate.",
                    suggested_fix="Rerun factor evaluation for the affected stock.",
                )
            )

    if success_count and not aggregate_summary:
        findings.append(
            _finding(
                severity="high",
                category="artifact_gap",
                title="Completed factor batch has no aggregate factor summary.",
                evidence=[f"success_count={success_count} aggregate_summary_length=0"],
                expected="Successful batches should produce aggregate rows across factor_id and observation offsets.",
                suggested_fix="Fix aggregate summary generation before reviewing factor ranking.",
            )
        )

    expected_aggregate_keys = _expected_factor_batch_aggregate_keys(completed_results)
    actual_aggregate_keys = {
        (str(row.get("factor_id")), int(row.get("offset_days")))
        for row in aggregate_summary
        if row.get("factor_id") is not None and row.get("offset_days") is not None
    }
    missing_aggregate_keys = sorted(expected_aggregate_keys - actual_aggregate_keys)
    if missing_aggregate_keys:
        findings.append(
            _finding(
                severity="high",
                category="coverage_gap",
                title="Aggregate factor summary is missing expected factor/offset rows.",
                evidence=[f"missing_keys={missing_aggregate_keys[:20]} total_missing={len(missing_aggregate_keys)}"],
                expected="aggregate_summary should include every factor_id and offset_days pair emitted by completed stock evaluations.",
                suggested_fix="Regenerate aggregate_summary from completed stock summary_by_factor entries.",
            )
        )

    for row in aggregate_summary:
        factor_id = row.get("factor_id")
        offset_days = row.get("offset_days")
        row_stock_count = int(row.get("stock_count", 0) or 0)
        if row_stock_count <= 0 or row_stock_count > success_count:
            findings.append(
                _finding(
                    severity="high",
                    category="artifact_gap",
                    title="Aggregate factor row has invalid stock coverage.",
                    evidence=[f"factor_id={factor_id} offset_days={offset_days} stock_count={row_stock_count} success_count={success_count}"],
                    expected="Aggregate row stock_count should be positive and no larger than completed stock count.",
                    suggested_fix="Check aggregate grouping and completed-stock filtering.",
                )
            )
        elif row_stock_count < success_count:
            findings.append(
                _finding(
                    severity="medium",
                    category="coverage_gap",
                    title="Aggregate factor row does not cover every completed stock.",
                    evidence=[f"factor_id={factor_id} offset_days={offset_days} stock_count={row_stock_count} success_count={success_count}"],
                    expected="Current batch factors are expected to be produced for every completed stock unless documented otherwise.",
                    suggested_fix="Inspect missing stock/factor combinations and document intentional exclusions.",
                )
            )
        if int(row.get("total_available_count", 0) or 0) <= 0:
            findings.append(
                _finding(
                    severity="high",
                    category="artifact_gap",
                    title="Aggregate factor row has no available observations.",
                    evidence=[f"factor_id={factor_id} offset_days={offset_days} total_available_count={row.get('total_available_count')}"],
                    expected="Aggregate rows should have positive available observations before being ranked.",
                    suggested_fix="Exclude unavailable rows or fix future-return coverage.",
                )
            )
        direction = row.get("direction_consistency")
        if direction not in EXPECTED_BATCH_DIRECTIONS:
            findings.append(
                _finding(
                    severity="medium",
                    category="schema_mismatch",
                    title="Aggregate factor row has an unknown direction_consistency value.",
                    evidence=[f"factor_id={factor_id} offset_days={offset_days} direction_consistency={direction}"],
                    expected=f"direction_consistency should be one of {sorted(EXPECTED_BATCH_DIRECTIONS)}.",
                    suggested_fix="Update the batch report schema or reviewer enum together.",
                )
            )

    report = str(artifacts.get("aggregate_report", ""))
    expected_mentions = [
        f"Batch: `{summary.get('batch_id')}`",
        f"Status: `{summary.get('status')}`",
        f"Success / failed: `{success_count} / {failed_count}`",
    ]
    missing_report_mentions = [mention for mention in expected_mentions if mention not in report]
    if missing_report_mentions:
        findings.append(
            _finding(
                severity="medium",
                category="report_gap",
                title="Aggregate factor report is missing batch summary fields.",
                evidence=[f"Missing mentions: {missing_report_mentions}"],
                expected="Report should state batch id, completion status, and success/failed counts.",
                suggested_fix="Update the aggregate report renderer and regenerate the batch artifact.",
            )
        )

    return findings


def _build_factor_redundancy_review_deterministic_findings(artifacts: dict[str, Any]) -> list[dict[str, Any]]:
    findings = []
    config = artifacts.get("review_config", {})
    manifest = artifacts.get("source_manifest", {})
    instruments = manifest.get("instruments", [])
    cross_object_summary = artifacts.get("cross_object_summary", [])
    pooled_diagnostics = artifacts.get("pooled_diagnostics", [])
    report = str(artifacts.get("report", ""))

    if config.get("instrument_isolation") is not True:
        findings.append(
            _finding(
                severity="high",
                category="methodology_violation",
                title="Factor redundancy review does not declare instrument isolation.",
                evidence=[f"instrument_isolation={config.get('instrument_isolation')}"],
                expected="review-config.json should set instrument_isolation=true.",
                suggested_fix="Regenerate the review with per-instrument correlation enabled.",
            )
        )
    if config.get("raw_pooled_correlation_policy") != "diagnostic_only":
        findings.append(
            _finding(
                severity="high",
                category="methodology_violation",
                title="Raw pooled correlation policy is not diagnostic-only.",
                evidence=[f"raw_pooled_correlation_policy={config.get('raw_pooled_correlation_policy')}"],
                expected="Raw pooled correlation may only be diagnostic and must not drive exclusion.",
                suggested_fix="Regenerate the review with raw_pooled_correlation_policy=diagnostic_only.",
            )
        )
    if not instruments:
        findings.append(
            _finding(
                severity="high",
                category="coverage_gap",
                title="Factor redundancy review has no source instruments.",
                evidence=["source-data-manifest.json instruments is empty"],
                expected="The review should reference at least one investment object.",
                suggested_fix="Regenerate the review from a completed factor batch or factor run directory.",
            )
        )

    instrument_ids = {str(instrument.get("instrument_id")) for instrument in instruments if instrument.get("instrument_id")}
    per_instrument_artifacts = artifacts.get("per_instrument_artifacts", [])
    artifact_ids = {str(item.get("instrument_id")) for item in per_instrument_artifacts if item.get("instrument_id")}
    if instrument_ids != artifact_ids:
        findings.append(
            _finding(
                severity="high",
                category="traceability_gap",
                title="Per-instrument artifact set does not match source manifest.",
                evidence=[f"manifest_instruments={sorted(instrument_ids)} artifact_instruments={sorted(artifact_ids)}"],
                expected="Every source instrument should have exactly one per-instrument review artifact directory.",
                suggested_fix="Regenerate the factor redundancy review artifacts.",
            )
        )

    for item in per_instrument_artifacts:
        instrument_id = str(item.get("instrument_id"))
        for field in ("correlation_matrix", "pair_relationships", "retention_decisions"):
            if not item.get(field, {}).get("exists"):
                findings.append(
                    _finding(
                        severity="high",
                        category="artifact_gap",
                        title="Per-instrument review artifact is missing.",
                        evidence=[f"instrument_id={instrument_id} missing={field}"],
                        expected="Each instrument should have a correlation matrix, pair relationships, and retention decisions.",
                        suggested_fix="Regenerate the review for the affected instrument.",
                    )
                )
        pair_relationships = item.get("pair_relationships", {}).get("content", [])
        for relationship in pair_relationships:
            if relationship.get("scope") != "per_instrument" or relationship.get("instrument_id") != instrument_id:
                findings.append(
                    _finding(
                        severity="high",
                        category="methodology_violation",
                        title="Pair relationship is not scoped to its instrument.",
                        evidence=[f"instrument_dir={instrument_id} relationship={relationship}"],
                        expected="Every pair relationship should include scope=per_instrument and the matching instrument_id.",
                        suggested_fix="Fix relationship serialization before using redundancy conclusions.",
                    )
                )
                break
        retention_decisions = item.get("retention_decisions", {}).get("content", [])
        for decision in retention_decisions:
            if decision.get("scope") != "per_instrument" or decision.get("instrument_id") != instrument_id:
                findings.append(
                    _finding(
                        severity="high",
                        category="methodology_violation",
                        title="Retention decision is not scoped to its instrument.",
                        evidence=[f"instrument_dir={instrument_id} decision={decision}"],
                        expected="Every retention decision should include scope=per_instrument and the matching instrument_id.",
                        suggested_fix="Fix retention decision serialization before using redundancy conclusions.",
                    )
                )
                break

    for summary in cross_object_summary:
        if summary.get("scope") != "cross_object_summary":
            findings.append(
                _finding(
                    severity="high",
                    category="methodology_violation",
                    title="Cross-object summary row has incorrect scope.",
                    evidence=[str(summary)[:500]],
                    expected="Cross-object rows should set scope=cross_object_summary.",
                    suggested_fix="Regenerate cross-object summary from per-instrument evidence.",
                )
            )
            break
        if "global_exclude" in str(summary.get("global_recommendation")):
            findings.append(
                _finding(
                    severity="high",
                    category="methodology_violation",
                    title="Cross-object summary attempts a global exclude.",
                    evidence=[f"summary={summary}"],
                    expected="Global layer may warn or downweight, but must not globally exclude factors.",
                    suggested_fix="Change global recommendations to per-instrument-safe recommendations.",
                )
            )
            break
        if not isinstance(summary.get("instrument_evidence"), list):
            findings.append(
                _finding(
                    severity="high",
                    category="traceability_gap",
                    title="Cross-object summary is missing instrument evidence.",
                    evidence=[str(summary)[:500]],
                    expected="Cross-object conclusions should aggregate per-instrument evidence.",
                    suggested_fix="Regenerate cross-object summary with instrument_evidence refs.",
                )
            )
            break

    for diagnostic in pooled_diagnostics:
        if diagnostic.get("scope") != "diagnostic_only":
            findings.append(
                _finding(
                    severity="high",
                    category="methodology_violation",
                    title="Pooled diagnostic row is not diagnostic-only.",
                    evidence=[str(diagnostic)[:500]],
                    expected="Pooled correlations may only be emitted as diagnostic_only.",
                    suggested_fix="Regenerate pooled diagnostics with diagnostic-only scope.",
                )
            )
            break

    if "does not compare one investment object's raw factor values against another" not in report:
        findings.append(
            _finding(
                severity="medium",
                category="report_gap",
                title="Human report does not state the instrument-isolation guarantee.",
                evidence=["factor-redundancy-report.md is missing the required isolation sentence."],
                expected="Report should explicitly explain that raw factor values are not compared across investment objects.",
                suggested_fix="Regenerate the report with the required beginner-facing methodology note.",
            )
        )

    return findings


def _canonical_stock_result_for_log(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "ts_code": result.get("ts_code"),
        "status": result.get("status"),
        "factor_run_id": result.get("factor_run_id"),
        "factor_run_dir": result.get("factor_run_dir"),
        "evaluation_id": result.get("evaluation_id"),
        "evaluation_dir": result.get("evaluation_dir"),
        "observation_count": result.get("observation_count"),
        "summary_by_factor": result.get("summary_by_factor", []),
        "error_type": result.get("error_type"),
        "error_message": result.get("error_message"),
    }


def _expected_factor_batch_aggregate_keys(completed_results: list[dict[str, Any]]) -> set[tuple[str, int]]:
    keys = set()
    for result in completed_results:
        for factor_summary in result.get("summary_by_factor", []):
            factor_id = factor_summary.get("factor_id")
            if factor_id is None:
                continue
            for offset_summary in factor_summary.get("offsets", []):
                offset_days = offset_summary.get("offset_days")
                if offset_days is not None:
                    keys.add((str(factor_id), int(offset_days)))
    return keys


def _build_external_review_payload(
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


def _build_factor_external_review_payload(
    review_id: str,
    run_id: str,
    plan_snapshot: dict[str, Any],
    artifacts: dict[str, Any],
    deterministic_findings: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "review_id": review_id,
        "run_id": run_id,
        "artifact_type": "factor_run",
        "plan_documents": [
            {
                "path": doc.get("path"),
                "excerpt": str(doc.get("content", ""))[:1800],
            }
            for doc in plan_snapshot.get("documents", [])
        ],
        "run_config": artifacts.get("run_config", {}),
        "run_manifest": artifacts.get("run_manifest", {}),
        "api_call_summary": [
            {
                "endpoint": call.get("endpoint"),
                "status": call.get("status"),
                "row_count": call.get("row_count"),
                "params": call.get("params"),
            }
            for call in artifacts.get("api_calls", [])
        ],
        "retry_event_count": len(artifacts.get("api_retry_events", [])),
        "stock_quality_summary": [
            {
                "ts_code": stock.get("ts_code"),
                "stock_output": stock.get("stock_output"),
                "quality": stock.get("files", {}).get("quality_ref", {}).get("content", {}),
            }
            for stock in artifacts.get("stock_artifacts", [])
        ],
        "cache_artifact_count": len(artifacts.get("cache_artifacts", [])),
        "deterministic_findings": deterministic_findings,
    }


def _build_factor_batch_external_review_payload(
    review_id: str,
    run_id: str,
    plan_snapshot: dict[str, Any],
    artifacts: dict[str, Any],
    deterministic_findings: list[dict[str, Any]],
) -> dict[str, Any]:
    summary = artifacts.get("batch_summary", {})
    return {
        "review_id": review_id,
        "run_id": run_id,
        "artifact_type": "factor_batch",
        "plan_documents": [
            {
                "path": doc.get("path"),
                "excerpt": str(doc.get("content", ""))[:1800],
            }
            for doc in plan_snapshot.get("documents", [])
        ],
        "batch_status": {
            "batch_id": summary.get("batch_id"),
            "status": summary.get("status"),
            "stock_count": summary.get("stock_count"),
            "success_count": summary.get("success_count"),
            "failed_count": summary.get("failed_count"),
            "source_batch_ids": summary.get("source_batch_ids", []),
        },
        "stock_results_summary": [
            {
                "ts_code": result.get("ts_code"),
                "status": result.get("status"),
                "factor_run_id": result.get("factor_run_id"),
                "evaluation_id": result.get("evaluation_id"),
                "observation_count": result.get("observation_count"),
                "factor_summary_count": len(result.get("summary_by_factor", [])),
                "error_type": result.get("error_type"),
            }
            for result in summary.get("stock_results", [])
        ],
        "top_aggregate_rows": _top_factor_batch_rows(summary.get("aggregate_summary", [])),
        "aggregate_report_excerpt": str(artifacts.get("aggregate_report", ""))[:6000],
        "deterministic_findings": deterministic_findings,
    }


def _build_factor_redundancy_review_external_review_payload(
    review_id: str,
    run_id: str,
    plan_snapshot: dict[str, Any],
    artifacts: dict[str, Any],
    deterministic_findings: list[dict[str, Any]],
) -> dict[str, Any]:
    config = artifacts.get("review_config", {})
    manifest = artifacts.get("source_manifest", {})
    cross_object_summary = artifacts.get("cross_object_summary", [])
    high_confidence = [
        item for item in cross_object_summary if item.get("global_recommendation") == "global_downweight_candidate"
    ]
    needs_review = [
        item for item in cross_object_summary if item.get("global_recommendation") == "global_review_required"
    ]
    return {
        "review_id": review_id,
        "run_id": run_id,
        "artifact_type": "factor_redundancy_review",
        "plan_documents": [
            {
                "path": doc.get("path"),
                "excerpt": str(doc.get("content", ""))[:1800],
            }
            for doc in plan_snapshot.get("documents", [])
        ],
        "review_config": {
            "review_id": config.get("review_id"),
            "correlation_threshold": config.get("correlation_threshold"),
            "min_observations": config.get("min_observations"),
            "method": config.get("method"),
            "instrument_isolation": config.get("instrument_isolation"),
            "raw_pooled_correlation_policy": config.get("raw_pooled_correlation_policy"),
        },
        "source_scope": {
            "instrument_count": len(manifest.get("instruments", [])),
            "instruments": [
                {
                    "instrument_id": instrument.get("instrument_id"),
                    "row_count": instrument.get("row_count"),
                    "ok_value_count": instrument.get("ok_value_count"),
                    "date_min": instrument.get("date_min"),
                    "date_max": instrument.get("date_max"),
                }
                for instrument in manifest.get("instruments", [])
            ],
        },
        "high_confidence_pairs": high_confidence[:20],
        "needs_review_pairs": needs_review[:20],
        "pooled_diagnostics": artifacts.get("pooled_diagnostics", [])[:20],
        "report_excerpt": str(artifacts.get("report", ""))[:6000],
        "deterministic_findings": deterministic_findings,
    }


def _top_factor_batch_rows(rows: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    sortable = [row for row in rows if row.get("mean_pearson_correlation") is not None]
    sortable.sort(key=lambda row: abs(float(row["mean_pearson_correlation"])), reverse=True)
    return [
        {
            "factor_id": row.get("factor_id"),
            "offset_days": row.get("offset_days"),
            "stock_count": row.get("stock_count"),
            "total_available_count": row.get("total_available_count"),
            "mean_pearson_correlation": row.get("mean_pearson_correlation"),
            "mean_top_minus_bottom_return": row.get("mean_top_minus_bottom_return"),
            "direction_consistency": row.get("direction_consistency"),
        }
        for row in sortable[:limit]
    ]


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


def _build_external_review_prompt(payload: dict[str, Any]) -> str:
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


def _build_quality_subagent_review_prompt(
    review_id: str,
    run_id: str,
    review_dir: Path,
    deterministic_findings: list[dict[str, Any]],
    artifact_label: str = "Research Run",
) -> str:
    return "\n".join(
        [
            "# ECC Quality Sub-Agent Artifact Review Prompt",
            "",
            "You are the ECC Quality Sub-Agent for this repository.",
            "",
            f"Review the generated {artifact_label.lower()} artifacts against the current project plan.",
            "Do not provide investment advice. Focus on artifact correctness, traceability, plan alignment, scoring policy, N/A handling, and future-leak risk.",
            "Do not modify product code or apply fixes. You may only update review artifacts and draft a fix plan.",
            "",
            f"- Review ID: `{review_id}`",
            f"- {artifact_label}: `{run_id}`",
            f"- Review Directory: `{review_dir}`",
            f"- Deterministic Findings: `{len(deterministic_findings)}`",
            "",
            "Read these local files before writing conclusions:",
            "",
            f"- `{review_dir / 'plan-snapshot.json'}`",
            f"- `{review_dir / 'source-artifacts.json'}`",
            f"- `{review_dir / 'findings.json'}`",
            f"- `{review_dir / 'artifact-review-report.md'}`",
            f"- `{review_dir / 'fix-plan-draft.md'}`",
            "",
            "After review, update `artifact-review-report.md`, `findings.json`, `fix-plan-draft.md`, and `review-state.json` if you find additional issues.",
            "Do not apply fixes. Parent Codex must ask the user for approval before implementation.",
            "",
        ]
    )


def _build_report(
    review_id: str,
    run_id: str,
    deterministic_findings: list[dict[str, Any]],
    external_result: dict[str, Any],
    external_findings: list[dict[str, Any]] | None = None,
    artifact_label: str = "Research Run",
) -> str:
    status = "needs_fix" if deterministic_findings or external_findings else "passed"
    lines = [
        "# ECC Artifact Review Report",
        "",
        f"- Review ID: `{review_id}`",
        f"- {artifact_label}: `{run_id}`",
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
    lines.extend(
        [
            "",
            "## ECC Quality Sub-Agent Semantic Review",
            "",
            "Pending ECC Quality Sub-Agent review. Use `quality-subagent-review-prompt.md` as the local review packet.",
            "",
            "## External Reviewer Findings",
            "",
            str(external_result.get("report", "")).strip() or "External artifact review was not requested.",
            "",
        ]
    )
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
    external_findings: list[dict[str, Any]],
) -> dict[str, Any]:
    findings = deterministic_findings + external_findings
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


def _contains_observation_label(text: str, label: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(label)}(?!\d)", text) is not None


def _external_review_call_log(
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


def _load_jsonl(path: Path) -> list[Any]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            rows.append(json.loads(stripped))
    return rows


def _load_jsonl_with_path(path: Path) -> dict[str, Any]:
    return {"path": str(path), "content": _load_jsonl(path)}


def _file_checksum(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


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
