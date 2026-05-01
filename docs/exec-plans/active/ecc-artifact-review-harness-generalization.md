# ECC Artifact Review Harness Generalization Plan

## Purpose

Upgrade the current project-local ECC Artifact Reviewer into a reusable ECC review harness for LLM/agent-generated artifacts. The goal is to treat non-code artifacts as testable outputs: an agent receives task context and produces files, reports, logs, or decisions; the harness checks whether those artifacts satisfy the project plan, spec, and review profile before Codex continues implementation.

This is a development quality workflow, not product functionality. It should be portable enough to initialize in a new project without depending on Cost-Basis-Trading concepts such as research runs, chip factors, or observation windows.

## Core Idea

Traditional tests validate:

```text
function(input) -> output
```

ECC Artifact Review validates:

```text
agent/task/spec/context -> artifacts
```

The harness should produce structured results that can be read like test output:

```json
{
  "status": "passed | failed | needs_review",
  "findings": [],
  "assertions": [],
  "approval_required": true
}
```

## Target Architecture

```text
ecc_artifact_review/
  core.py              # generic loader, event log, report writer, review packet builder
  contracts.py         # dataclasses or schemas for manifest, profile, findings, result
  profiles.py          # profile loading and validation
  reviewers.py         # sub-agent packet writer plus optional external provider adapters
  adapters/
    generic.py         # default adapter for arbitrary artifact directories
    cost_basis_run.py  # project-specific adapter for current research-run artifacts
```

The core should not know about stock analysis, N+ offsets, strategy scores, or any domain-specific file names. Those belong in adapters and profiles.

## Universal Artifact Contract

Each artifact under review should have a manifest. If a project cannot change its artifact producer yet, the adapter may synthesize this manifest from known files.

Recommended location:

```text
<artifact-dir>/artifact-manifest.json
```

Template:

```jsonc
{
  // Unique identifier for this artifact. Use the run id, task id, issue id, or timestamped output id.
  "artifact_id": "run-20260501-001",

  // Human-readable artifact type. Examples: ai_research_report, generated_prd, migration_plan, agent_decision_log.
  "artifact_type": "ai_research_report",

  // The agent, command, or workflow that produced the artifact. This supports traceability.
  "producer": {
    "name": "research-agent",
    "version": "local",
    "command": "python scripts/run_research.py ..."
  },

  // Specs, plans, architecture docs, tickets, prompts, or requirements that define expected behavior.
  "spec_refs": [
    "docs/product-specs/current-state.md",
    "docs/design/example.md"
  ],

  // Files the reviewer must load to understand the artifact.
  "artifact_refs": [
    "aggregate/final_report.md",
    "aggregate/agent-decisions.jsonl",
    "run-config.json"
  ],

  // Files that must exist before review can start.
  "required_files": [
    "aggregate/final_report.md"
  ],

  // Optional project-specific metadata used by adapters or deterministic checks.
  "metadata": {
    "sample_count": 3,
    "domain": "stock-research"
  }
}
```

## Review Profile Contract

A profile defines how to review an artifact type. It should be declarative enough to copy into new projects and specific enough to make review expectations explicit.

Recommended location:

```text
.ecc/artifact-review/profiles/<profile-name>.json
```

Template:

```jsonc
{
  // Stable profile id used by CLI arguments and review reports.
  "profile_id": "generic-agent-artifact",

  // Which artifact types this profile can review.
  "artifact_types": ["ai_research_report", "generated_prd", "agent_output"],

  // Docs that define the project-level review standard.
  "default_spec_refs": [
    "docs/ARCHITECTURE.md",
    "docs/product-specs/current-state.md"
  ],

  // Human-readable criteria for ECC Quality Sub-Agent semantic review.
  // These become part of codex-review-prompt.md or a future quality-agent prompt.
  "semantic_criteria": [
    "Does the artifact answer the task it claims to answer?",
    "Does the artifact cite or reference the relevant source artifacts?",
    "Are assumptions, uncertainties, and missing data called out?",
    "Is the fix plan actionable and blocked on user approval?"
  ],

  // Deterministic checks that the adapter/core can execute without an LLM.
  // Keep these names stable so later projects can map them to adapter functions.
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

  // Optional extension point for project-specific checks.
  "adapter": {
    "name": "generic",
    "options": {}
  }
}
```

## CLI Shape

The portable CLI should work against arbitrary artifact directories:

```bash
python scripts/ecc_artifact_review.py review \
  --artifact-path <artifact-dir> \
  --profile .ecc/artifact-review/profiles/generic-agent-artifact.json
```

Convenience command for projects that create timestamped outputs:

```bash
python scripts/ecc_artifact_review.py review-latest \
  --artifact-root docs/research-runs \
  --profile .ecc/artifact-review/profiles/research-run.json
```

Optional external reviewer second opinion:

```bash
python scripts/ecc_artifact_review.py review \
  --artifact-path <artifact-dir> \
  --profile <profile.json> \
  --external-reviewer deepseek
```

The default reviewer is the ECC Quality Sub-Agent when the runtime supports sub-agents. If no sub-agent facility is available, the current Codex session may execute the same packet as a fallback. External reviewers are adapters, not the primary path.

## Output Contract

The harness should write review output inside the artifact directory by default:

```text
<artifact-dir>/
  ecc-artifact-reviews/
    latest.json
    artifact-review-<timestamp>-<shortid>/
      review-config.json
      artifact-manifest.snapshot.json
      profile.snapshot.json
      source-artifacts.json
      findings.json
      assertions.json
      codex-review-prompt.md
      artifact-review-report.md
      fix-plan-draft.md
      review-state.json
      workflow-events.jsonl
      external-review-calls.jsonl
```

Rules:

- `latest.json` points to the newest review for quick lookup.
- Snapshot files preserve the exact manifest/profile used for the review.
- `workflow-events.jsonl` records every local step.
- `external-review-calls.jsonl` records only metadata and summaries, not secrets.
- `fix-plan-draft.md` must explicitly state whether user approval is required.

## ECC Quality Sub-Agent Collaboration Model

Preferred mode:

```text
Parent Codex / Orchestrator
  -> decides what needs verification
  -> spawns ECC Quality Sub-Agent
  -> sub-agent runs deterministic tests, E2E, research smoke runs, and artifact review as needed
  -> sub-agent writes quality report, artifact review report, findings, and fix-plan draft
  -> parent Codex reads outputs and summarizes for the user
  -> parent waits for approval before any fix
```

Fallback mode when sub-agents are unavailable:

```text
Parent Codex executes the same verification commands locally
  -> writes the same artifacts
  -> still waits for user approval before fixes
```

The harness must not depend on a specific sub-agent implementation, but ECC workflow guidance should prefer sub-agent execution for all verification work. This includes deterministic unit tests, integration tests, E2E tests, research workflow smoke runs, deterministic artifact checks, and semantic artifact review.

The sub-agent must not directly fix code or product behavior. It may only produce findings, evidence, and a fix-plan draft. Fixes require user approval and are executed by the parent Codex or a separately delegated implementation agent.

## Parent/Sub-Agent Responsibility Split

Parent Codex owns:

- deciding the verification scope,
- selecting or creating the quality sub-agent task,
- reading the quality outputs,
- explaining findings to the user,
- asking for approval before any fix,
- applying or delegating implementation after approval.

ECC Quality Sub-Agent owns:

- running requested tests,
- running research/report generation smoke flows when requested,
- running ECC Artifact Reviewer,
- reading generated review packets,
- performing semantic artifact review,
- writing structured findings and fix-plan drafts,
- reporting residual risks and skipped checks.

## New Project Initialization Checklist

1. Create `.ecc/artifact-review/profiles/`.
2. Copy the generic profile template and rename it for the artifact type.
3. Decide how the project writes or synthesizes `artifact-manifest.json`.
4. Map artifact producer outputs into `artifact_refs` and `required_files`.
5. Add domain-specific deterministic checks only if they are stable and easy to explain.
6. Add a project adapter only when generic checks are insufficient.
7. Run the harness on one known-good artifact and one intentionally incomplete artifact.
8. Document the command and the ECC Quality Sub-Agent delegation prompt in the project `AGENTS.md` or skill file.

## Migration Path For This Project

Phase 1:

- keep `scripts/ecc_artifact_reviewer.py` working for Cost-Basis-Trading research runs,
- add generic manifest/profile templates under `.ecc/artifact-review/`,
- update the skill with new-project initialization guidance.

Phase 2:

- split the script into core/profile/adapter modules,
- implement `generic` adapter,
- implement `cost_basis_run` adapter,
- add CLI commands `review` and `review-latest`.

Phase 3:

- run ECC Artifact Reviewer automatically after selected Codex/ECC test workflows,
- delegate verification execution to an ECC Quality Sub-Agent by default,
- keep user approval as the gate before fixes are applied.

## Acceptance Criteria

- A new project can initialize ECC Artifact Reviewer from documented templates.
- The generic contract does not mention Cost-Basis-Trading-specific concepts.
- Project-specific review logic lives in adapters or profile options.
- ECC Quality Sub-Agent is the preferred verification executor when supported.
- Current Codex remains the fallback semantic reviewer when no sub-agent facility is available.
- External reviewers remain optional providers.
- Review output is traceable and local.
