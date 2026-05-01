---
name: ecc-artifact-reviewer
description: Review generated development artifacts against project plans, especially AI-agent research-run outputs, and write traceable local review reports and fix-plan drafts.
origin: project
---

# ECC Artifact Reviewer

Use this skill when a completed AI-agent research run needs development-time review before Codex changes code or plans. This is an ECC quality gate, not a product feature.

## Inputs

- `docs/research-runs/<run_id>/`
- project plan and design docs
- optional `DEEPSEEK_API_KEY` in `.env.local` or `env.local`

## Command

Run deterministic checks only:

```bash
python scripts/ecc_artifact_reviewer.py --run-id <run_id> --no-llm
```

Run deterministic checks plus optional semantic LLM review:

```bash
python scripts/ecc_artifact_reviewer.py --run-id <run_id>
```

## Output

The reviewer writes:

```text
docs/research-runs/<run_id>/ecc-artifact-reviews/
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

## Rules

- Do not expose this workflow as a product API.
- Do not use LangGraph or LangSmith for this reviewer.
- Treat local artifacts as the source of truth.
- Never apply fixes from `fix-plan-draft.md` until the user approves the plan.
- The report must distinguish deterministic findings from LLM semantic review.
