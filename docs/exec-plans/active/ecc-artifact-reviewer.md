# ECC Artifact Reviewer Execution Plan

## Purpose

Introduce an ECC development workflow that reviews non-code artifacts generated during AI-agent research work. The reviewer checks whether a completed research run's reports, logs, scores, and agent outputs match the current project plan before Codex applies follow-up fixes.

This is not a product backend feature. It is an ECC quality gate for development-time artifacts, similar in intent to code review but aimed at generated reports and research outputs.

## Boundary

In scope:

- review completed `docs/research-runs/<run_id>/` artifacts,
- compare run artifacts with project plans and design docs,
- run deterministic checks for required observation offsets and report coverage,
- prepare a local review packet for the ECC Quality Sub-Agent,
- optionally call an external reviewer provider directly through the project-local script,
- write all review inputs, judgments, optional external-review call summaries, findings, and fix plans locally.

Out of scope:

- product API endpoints for artifact review,
- frontend review panels,
- LangGraph orchestration,
- LangSmith as a source of truth,
- automatically applying fixes without user approval.

## Workflow

```text
Parent Codex / ECC orchestrator
  -> spawns ECC Quality Sub-Agent for verification
  -> sub-agent runs python scripts/ecc_artifact_reviewer.py --run-id <run_id>
  -> sub-agent loads plan docs and research-run artifacts
  -> sub-agent runs deterministic artifact checks
  -> sub-agent reads the review packet
  -> sub-agent performs semantic artifact review
  -> sub-agent optionally asks an external reviewer for a second opinion
  -> sub-agent updates ECC artifact review report and fix-plan draft
  -> parent Codex reads outputs
  -> parent waits for user approval before code changes
```

The ECC Quality Sub-Agent is not authorized to fix code or product behavior. It may only run verification, write evidence, update review artifacts, and draft a fix plan. Any implementation work must wait for user approval and then be performed by the parent Codex or a separately delegated implementation agent.

## Runtime Artifacts

```text
docs/research-runs/<run_id>/
  ecc-artifact-reviews/
    latest.json
    artifact-review-<timestamp>-<shortid>/
      review-config.json
      source-artifacts.json
      plan-snapshot.json
      findings.json
      fix-plan-draft.md
      quality-subagent-review-prompt.md
      artifact-review-report.md
      review-state.json
      workflow-events.jsonl
      external-review-calls.jsonl
```

The review lives beside the run it reviews so a single run directory remains traceable from raw samples through aggregate reports and ECC review.

## Commands

Prepare a review packet. Run this inside the ECC Quality Sub-Agent when sub-agents are available:

```bash
python scripts/ecc_artifact_reviewer.py --run-id <run_id>
```

Prepare a review packet for the newest research run:

```bash
python scripts/ecc_quality_workflow.py review-latest-research
```

Parent Codex should prefer this command when the user asks to review the latest run. The command only finds the latest run and prepares the artifact review packet; the ECC Quality Sub-Agent still performs the semantic review and updates review artifacts.

Ask an external DeepSeek reviewer for a second opinion:

```bash
python scripts/ecc_artifact_reviewer.py --run-id <run_id> --external-reviewer deepseek
```

The default path does not call an external LLM. `--no-llm` remains as a deprecated alias for the default no-external-reviewer behavior.

## Acceptance Criteria

- Product backend does not import or trigger ECC Artifact Reviewer.
- No product API exists for ECC Artifact Reviewer.
- Backend dependencies do not include LangGraph or LangSmith.
- Verification execution is delegated to an ECC Quality Sub-Agent when the runtime supports sub-agents.
- `scripts/ecc_quality_workflow.py review-latest-research` can prepare the latest run review packet.
- Reviewer artifacts are stored under `docs/research-runs/<run_id>/ecc-artifact-reviews/`.
- `latest.json` points to the latest review for the run.
- Review execution logs local events and optional external-review call summaries.
- Fix plans clearly state whether approval is required.

## Verification

Run these verification commands inside the ECC Quality Sub-Agent when sub-agents are available. Parent Codex should remain the orchestrator and approval gate.

```bash
pytest -v
npm run test
npm run build
git diff --check
```
