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
- [chip-factor-autoresearch-agent.md](./chip-factor-autoresearch-agent.md)
  defines the auditable AI-agent research loop for scoped stock/date experiments, API-call logs, agent decisions, and `N+1`/`N+3`/`N+5` validation.
