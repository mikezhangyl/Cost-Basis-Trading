# ECC Artifact Reviewer Execution Plan

## Purpose

Introduce an ECC development workflow that reviews non-code artifacts generated during AI-agent research work. The reviewer checks whether a completed research run's reports, logs, scores, and agent outputs match the current project plan before Codex applies follow-up fixes.

This is not a product backend feature. It is an ECC quality gate for development-time artifacts, similar in intent to code review but aimed at generated reports and research outputs.

## Boundary

In scope:

- review completed `docs/research-runs/<run_id>/` artifacts,
- compare run artifacts with project plans and design docs,
- run deterministic checks for required observation offsets and report coverage,
- prepare a local Codex review packet for the current Codex session,
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
Codex / ECC operator
  -> python scripts/ecc_artifact_reviewer.py --run-id <run_id>
  -> load plan docs
  -> load research-run artifacts
  -> deterministic artifact checks
  -> write Codex review packet
  -> current Codex session performs semantic artifact review
  -> optional external reviewer second opinion
  -> update ECC artifact review report
  -> write fix-plan draft
  -> wait for user approval before code changes
```

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
      codex-review-prompt.md
      artifact-review-report.md
      review-state.json
      workflow-events.jsonl
      external-review-calls.jsonl
```

The review lives beside the run it reviews so a single run directory remains traceable from raw samples through aggregate reports and ECC review.

## Commands

Prepare a review packet for the current Codex session:

```bash
python scripts/ecc_artifact_reviewer.py --run-id <run_id>
```

Ask an external DeepSeek reviewer for a second opinion:

```bash
python scripts/ecc_artifact_reviewer.py --run-id <run_id> --external-reviewer deepseek
```

The default path does not call an external LLM. `--no-llm` remains as a deprecated alias for the default no-external-reviewer behavior.

## Acceptance Criteria

- Product backend does not import or trigger ECC Artifact Reviewer.
- No product API exists for ECC Artifact Reviewer.
- Backend dependencies do not include LangGraph or LangSmith.
- Reviewer artifacts are stored under `docs/research-runs/<run_id>/ecc-artifact-reviews/`.
- `latest.json` points to the latest review for the run.
- Review execution logs local events and optional external-review call summaries.
- Fix plans clearly state whether approval is required.

## Verification

```bash
pytest -v
npm run test
npm run build
git diff --check
```
