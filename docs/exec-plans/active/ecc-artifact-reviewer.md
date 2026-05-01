# ECC Artifact Reviewer Execution Plan

## Purpose

Introduce an ECC development workflow that reviews non-code artifacts generated during AI-agent research work. The reviewer checks whether a completed research run's reports, logs, scores, and agent outputs match the current project plan before Codex applies follow-up fixes.

This is not a product backend feature. It is an ECC quality gate for development-time artifacts, similar in intent to code review but aimed at generated reports and research outputs.

## Boundary

In scope:

- review completed `docs/research-runs/<run_id>/` artifacts,
- compare run artifacts with project plans and design docs,
- run deterministic checks for required observation offsets and report coverage,
- optionally call a reviewer LLM directly through the project-local script,
- write all review inputs, judgments, LLM call summaries, findings, and fix plans locally.

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
  -> optional LLM semantic artifact review
  -> write ECC artifact review report
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
      artifact-review-report.md
      review-state.json
      workflow-events.jsonl
      llm-calls.jsonl
```

The review lives beside the run it reviews so a single run directory remains traceable from raw samples through aggregate reports and ECC review.

## Commands

Deterministic-only review:

```bash
python scripts/ecc_artifact_reviewer.py --run-id <run_id> --no-llm
```

Review with optional DeepSeek semantic pass:

```bash
python scripts/ecc_artifact_reviewer.py --run-id <run_id>
```

The script loads `.env.local` or `env.local` if present. If `DEEPSEEK_API_KEY` is absent, use `--no-llm` or accept deterministic-only behavior from tests and direct class usage.

## Acceptance Criteria

- Product backend does not import or trigger ECC Artifact Reviewer.
- No product API exists for ECC Artifact Reviewer.
- Backend dependencies do not include LangGraph or LangSmith.
- Reviewer artifacts are stored under `docs/research-runs/<run_id>/ecc-artifact-reviews/`.
- `latest.json` points to the latest review for the run.
- Review execution logs local events and LLM call summaries.
- Fix plans clearly state whether approval is required.

## Verification

```bash
pytest -v
npm run test
npm run build
git diff --check
```
