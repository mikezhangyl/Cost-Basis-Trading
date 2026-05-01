# Design Index

## Direction

First-phase UI should feel like a local research workstation:

- dense but readable
- restrained color use
- clear signal states
- no marketing landing page
- no decorative card-heavy layout

## First Screen

The first screen should be the actual scanner:

- stock-code input
- lookback control defaulting to 10 trading days
- scan action
- result table

Detailed chart design will be specified after the first signal table is working.

## Strategy Feature Design

- [chip-change-feature-set.md](./chip-change-feature-set.md)
  defines the proposed source-traceable feature layer for daily chip-change analysis before candidate strategy implementation.
- [chip-factor-production-plan-2026q1.md](./chip-factor-production-plan-2026q1.md)
  defines the professional factor-production plan for factor dates from 2026-01-01 to 2026-04-30, with plain-language explanations for each chip factor.
- [chip-factor-autoresearch-agent.md](./chip-factor-autoresearch-agent.md)
  defines the auditable AI-agent research loop for scoped stock/date experiments, API-call logs, agent decisions, and multi-horizon validation.
- [a-share-chip-backfill-agent.md](./a-share-chip-backfill-agent.md)
  defines the local, idempotent Tushare `cyq_chips` backfill agent for active A-share chip-distribution history.
- [multi-agent-research-workflow.md](./multi-agent-research-workflow.md)
  defines the Supervisor-Orchestrated, Artifact-Driven Multi-Agent Research Workflow: agent roles, artifact handoffs, readiness gating, manifests, and future-leak prevention.
