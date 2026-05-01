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
- optional external reviewer credentials, such as `DEEPSEEK_API_KEY`, when explicitly requested

## Command

Prepare the local artifact review packet. When sub-agents are available, run this command inside an ECC Quality Sub-Agent rather than in the parent Codex session:

```bash
python scripts/ecc_artifact_reviewer.py --run-id <run_id>
```

Optionally ask an external reviewer provider for a second opinion:

```bash
python scripts/ecc_artifact_reviewer.py --run-id <run_id> --external-reviewer deepseek
```

`--no-llm` remains as a deprecated alias for the default no-external-reviewer path.

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
    codex-review-prompt.md
    artifact-review-report.md
    review-state.json
    workflow-events.jsonl
    external-review-calls.jsonl
```

## Rules

- Do not expose this workflow as a product API.
- Do not use LangGraph or LangSmith for this reviewer.
- The ECC Quality Sub-Agent is the preferred verification executor when sub-agents are available.
- The parent Codex session is the orchestrator and approval gate, not the default test runner.
- The current Codex session may execute the same workflow only as a fallback when no sub-agent facility is available.
- External LLM reviewers are optional adapters, not the default reviewer.
- Treat local artifacts as the source of truth.
- Never apply fixes from `fix-plan-draft.md` until the user approves the plan.
- The report must distinguish deterministic findings, ECC Quality Sub-Agent semantic review, and optional external reviewer findings.

## ECC Quality Sub-Agent Delegation

Use this pattern when a run/report/test artifact needs verification. The parent Codex should spawn a sub-agent and give it a bounded verification task. The sub-agent can run tests and write review artifacts, but it must not fix code.

Parent Codex responsibilities:

- decide the verification scope,
- spawn the ECC Quality Sub-Agent,
- give exact commands and artifact paths,
- read the resulting quality artifacts,
- summarize findings for the user,
- wait for approval before any fix.

ECC Quality Sub-Agent responsibilities:

- run deterministic tests requested by the parent,
- run E2E or research smoke workflows requested by the parent,
- run ECC Artifact Reviewer,
- perform semantic artifact review from `codex-review-prompt.md`,
- update `findings.json`, `artifact-review-report.md`, `fix-plan-draft.md`, and `review-state.json`,
- report skipped checks and residual risks,
- never apply fixes.

Suggested sub-agent prompt:

```text
You are the ECC Quality Sub-Agent for this repository.

Scope:
- Execute verification only.
- Do not modify product code.
- Do not apply fixes.
- You may write test/review artifacts under the requested artifact directory.

Tasks:
1. Run the specified test commands.
2. If a research run is requested, execute it or inspect the provided run id.
3. Run ECC Artifact Reviewer for the provided artifact.
4. Read codex-review-prompt.md and source artifacts.
5. Update findings.json, artifact-review-report.md, fix-plan-draft.md, and review-state.json.
6. Return a concise summary with status, findings, skipped checks, and artifact paths.

Approval rule:
- If fixes are needed, produce a fix plan only.
- Parent Codex must ask the user for approval before implementation.
```

Recommended verification flow:

```text
Parent Codex
  -> spawn ECC Quality Sub-Agent
  -> sub-agent runs unit/integration/E2E tests as requested
  -> sub-agent runs research workflow if requested
  -> sub-agent runs ECC Artifact Reviewer
  -> sub-agent writes quality artifacts
  -> parent summarizes and asks for approval
```

## New Project Initialization Guide

Use this section when starting a new project that needs ECC Artifact Reviewer. The goal is to review LLM/agent artifacts as an extension of tests, without importing Cost-Basis-Trading-specific assumptions.

### 1. Choose The Artifact Under Test

Define what the agent produces. Examples:

- generated PRD or SRS
- research report
- migration plan
- agent decision log
- generated test plan
- design analysis report

Avoid starting with the reviewer implementation. Start by naming the artifact and the spec it must satisfy.

### 2. Add An Artifact Manifest

Recommended location:

```text
<artifact-dir>/artifact-manifest.json
```

Template with explanatory comments:

```jsonc
{
  // Stable id for this artifact. Use a run id, task id, issue id, or timestamped folder name.
  "artifact_id": "example-run-001",

  // Artifact type lets profiles choose the right review criteria.
  // Examples: ai_research_report, generated_prd, migration_plan, agent_decision_log.
  "artifact_type": "ai_research_report",

  // Producer identifies what generated this artifact. Keep command/model/version if available.
  "producer": {
    "name": "research-agent",
    "version": "local",
    "command": "python scripts/run_research.py --input ..."
  },

  // Specs and plans that define correctness for this artifact.
  // Use project docs, issue specs, design notes, prompts, or acceptance criteria.
  "spec_refs": [
    "docs/product-specs/current-state.md",
    "docs/design/example.md"
  ],

  // Files the reviewer should load to understand the produced artifact.
  "artifact_refs": [
    "report.md",
    "agent-decisions.jsonl",
    "run-config.json"
  ],

  // Files that must exist before review can run.
  "required_files": [
    "report.md"
  ],

  // Free-form metadata for project adapters. Keep this factual and non-secret.
  "metadata": {
    "domain": "example-domain",
    "sample_count": 3
  }
}
```

### 3. Add A Review Profile

Recommended location:

```text
.ecc/artifact-review/profiles/<profile-name>.json
```

Template with explanatory comments:

```jsonc
{
  // Stable profile id used in CLI output and review reports.
  "profile_id": "generic-agent-artifact",

  // Artifact types this profile can review.
  "artifact_types": ["ai_research_report", "generated_prd", "agent_output"],

  // Default specs loaded when the manifest does not provide enough context.
  "default_spec_refs": [
    "docs/ARCHITECTURE.md",
    "docs/product-specs/current-state.md"
  ],

  // Criteria for ECC Quality Sub-Agent semantic review. These should be readable by humans.
  "semantic_criteria": [
    "Does the artifact answer the task it claims to answer?",
    "Does it cite or reference relevant source artifacts?",
    "Are assumptions, uncertainties, and missing data called out?",
    "Is the fix plan actionable and blocked on user approval?"
  ],

  // Deterministic checks are test-like assertions the harness can run without an LLM.
  "deterministic_checks": [
    {
      "id": "required_files_exist",
      "severity": "high",
      "description": "Every required file listed by the manifest must exist."
    },
    {
      "id": "non_empty_report",
      "severity": "medium",
      "description": "Primary report files must not be empty."
    }
  ],

  // Adapter can be generic at first. Add a project adapter only when checks need domain logic.
  "adapter": {
    "name": "generic",
    "options": {}
  }
}
```

### 4. Wire The Command

Target generic command shape:

```bash
python scripts/ecc_artifact_review.py review \
  --artifact-path <artifact-dir> \
  --profile .ecc/artifact-review/profiles/generic-agent-artifact.json
```

Current Cost-Basis-Trading command while the harness is still project-local. Prefer running this inside ECC Quality Sub-Agent:

```bash
python scripts/ecc_artifact_reviewer.py --run-id <run_id>
```

### 5. Decide Whether A Project Adapter Is Needed

Use `generic` when review only needs file existence, non-empty outputs, manifest/spec loading, and ECC Quality Sub-Agent semantic review.

Create a project adapter when deterministic checks need domain-specific logic, such as:

- validating expected observation horizons,
- checking generated score files,
- comparing a report against a known scoring policy,
- verifying a migration plan mentions every database table.

### 6. Keep The Approval Gate

The reviewer may produce a fix plan, but Codex must not apply fixes until the user approves the plan. This keeps artifact review as a quality gate rather than an autonomous code-change loop.
