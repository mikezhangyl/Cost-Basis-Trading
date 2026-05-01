# LangGraph In-Dev Reviewer Execution Plan

## Goal

Introduce a LangGraph-based development review workflow that automatically reviews completed research-run reports against the current project plan and writes an auditable in-development review artifact.

The first version reviews only. It must not modify source code or docs. Any fix work must be proposed as a plan and wait for user approval before implementation.

## Scope

Build a manual-trigger workflow:

```text
completed research run
  -> load plan context
  -> load completed run artifacts
  -> deterministic plan-vs-artifact check
  -> LLM in-dev review
  -> write in-dev review artifacts
  -> draft fix plan
  -> human approval interrupt
```

Out of scope for the first version:

- automatic source-code fixes,
- automatic post-research-run triggering,
- replacing the existing research-run workflow with LangGraph,
- portfolio-level strategy simulation,
- relying on LangSmith as the only source of truth.

## Dependencies

Backend dependencies to add:

```text
langgraph
langgraph-checkpoint-sqlite
langsmith
```

LangSmith tracing is optional at runtime. If the environment variables are absent, the workflow still writes local artifacts.

Expected environment variables:

```bash
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=<optional>
LANGSMITH_PROJECT=cost-basis-trading-dev
DEEPSEEK_API_KEY=<required for LLM review>
```

## Directory Layout

New backend modules:

```text
backend/app/agent_workflows/
  __init__.py
  in_dev_review_artifacts.py
  in_dev_review_client.py
  in_dev_review_graph.py
  in_dev_review_nodes.py
  in_dev_review_state.py
```

New runtime directories:

```text
backend/data/langgraph/
  in_dev_review_checkpoints.sqlite

docs/in-dev-reviews/
  .gitignore
  review-<timestamp>-<shortid>/
    review-config.json
    source-artifacts.json
    plan-snapshot.json
    findings.json
    fix-plan-draft.md
    in-dev-report.md
    graph-state.json
    workflow-events.jsonl
```

`docs/in-dev-reviews/review-*/` should be ignored by git, matching `docs/research-runs/run-*/`.

## Graph Nodes

### `load_plan_context`

Reads project planning and design documents:

- `docs/product-specs/current-state.md`
- `docs/ARCHITECTURE.md`
- `docs/design/multi-agent-research-workflow.md`
- `docs/design/chip-change-feature-set.md`
- `docs/exec-plans/active/phase-1-signal-dashboard.md`

Writes:

- `plan-snapshot.json`

### `load_completed_run_artifacts`

Reads existing research-run artifacts. This node does not execute a research run.

Inputs:

- `run_id`

Reads:

- `docs/research-runs/<run_id>/run-config.json`
- `docs/research-runs/<run_id>/run-manifest.json`
- `docs/research-runs/<run_id>/aggregate/final_report.md`
- `docs/research-runs/<run_id>/aggregate/ai_review.json`
- `docs/research-runs/<run_id>/samples/*/backtest/backtest_score.json`
- `docs/research-runs/<run_id>/samples/*/features/feature_set.json`
- `docs/research-runs/<run_id>/samples/*/signals/*.json`

Writes:

- `source-artifacts.json`

### `deterministic_plan_check`

Performs non-LLM checks before the LLM review.

Initial checks:

- report mentions all configured observation offsets or explains unavailable offsets,
- score artifacts contain all configured offsets,
- `N/A` observations are excluded from average directional score,
- `run-config.json` observation offsets match the code contract,
- report avoids direct investment advice,
- key artifact files exist.

Writes:

- deterministic findings into `findings.json`

### `llm_in_dev_review`

Uses the OpenAI-compatible DeepSeek client to review:

- plan snapshot,
- source artifacts,
- deterministic findings,
- final report.

LangSmith tracing:

- wrap the OpenAI client with `langsmith.wrappers.wrap_openai` when LangSmith is configured,
- decorate the review call with `@traceable`.

Writes:

- `in-dev-report.md`
- additional findings into `findings.json`

### `draft_fix_plan`

Creates a proposed fix plan from findings.

Writes:

- `fix-plan-draft.md`

The draft must explicitly say that implementation is blocked until user approval.

### `human_approval_interrupt`

Uses LangGraph `interrupt`.

Behavior:

- returns an interrupt payload containing `review_id`, findings summary, and fix-plan path,
- resumes only with the same `thread_id`,
- first version records approval state only and does not execute fixes.

## State Shape

```python
class InDevReviewState(TypedDict, total=False):
    review_id: str
    run_id: str
    status: str
    research_run_dir: str
    review_dir: str
    plan_doc_paths: list[str]
    source_artifact_paths: list[str]
    plan_snapshot_path: str
    source_artifacts_path: str
    findings_path: str
    in_dev_report_path: str
    fix_plan_draft_path: str
    graph_state_path: str
    workflow_events_path: str
    deterministic_findings: list[dict]
    llm_findings: list[dict]
    approval: dict | None
```

`thread_id` should be the `review_id`.

## API Surface

Manual trigger first:

```text
POST /api/in-dev-reviews
GET  /api/in-dev-reviews/{review_id}
POST /api/in-dev-reviews/{review_id}/approval
```

Create request:

```json
{
  "run_id": "run-20260501-055131-c24c5d52"
}
```

Approval request:

```json
{
  "approved": true,
  "notes": "Approved to implement the proposed fix plan."
}
```

## Testing Plan

Backend tests:

- creates review artifacts from fake research-run artifacts,
- fails clearly when `final_report.md` is missing,
- writes `findings.json`,
- writes `in-dev-report.md`,
- writes `fix-plan-draft.md`,
- pauses at LangGraph interrupt,
- resumes with the same `review_id` thread,
- does not apply fixes before approval.

Frontend can remain unchanged in the first implementation unless API visibility is needed for manual validation.

## Verification Commands

```bash
pytest -v
npm run test
npm run build
git diff --check
```

## Rollout

1. Add dependencies and lock/update backend environment.
2. Implement artifact directory and schema helpers.
3. Implement deterministic checker.
4. Implement LangGraph state graph with SQLite checkpointer.
5. Implement LLM review client with optional LangSmith tracing.
6. Add API endpoints.
7. Add tests.
8. Run InDevReviewer against the latest research run.
9. Review generated `docs/in-dev-reviews/<review_id>/in-dev-report.md`.

## Human Approval Rule

The InDevReviewer may create findings and a fix-plan draft. It must not edit source files. Codex must show the generated fix plan to the user and wait for approval before applying any fix from the review.
