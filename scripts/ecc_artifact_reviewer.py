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
        plan_doc_paths: list[Path] | None = None,
        review_client: ArtifactReviewClient | None = None,
    ) -> None:
        self.research_run_root = research_run_root or default_research_run_root()
        self.factor_run_root = factor_run_root or default_factor_run_root()
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
    parser.add_argument("--run-id", required=True, help="Run id under docs/research-runs or docs/factor-runs.")
    parser.add_argument(
        "--artifact-type",
        choices=["research", "factor"],
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
        review_client=client,
    )
    if args.artifact_type == "factor":
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
