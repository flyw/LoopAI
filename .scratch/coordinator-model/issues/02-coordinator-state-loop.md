# 02 — Coordinator state loop

## Global objective

Let a Coordinator model choose each safe transition from current initiative and agent evidence.

## Dependencies

Ticket 01 completed with its Coordinator prompt/schema/runner contract.

## Current subtask

Call and resume one Coordinator session before role transitions. Supply the current execution map,
candidate ticket, tracker status, agent result, and available session IDs. Validate every returned
action mechanically before launching an agent or completing/stopping the initiative.

## Required outputs

- Updated `src/loopai/orchestrator.py`.
- Orchestration tests covering session reuse, completed-ticket skipping, verification resume,
  feedback routing, and rejection of unsafe decisions.
- Updated README usage and architecture description.

## Non-goals

No parallel ticket execution and no cross-process Codex session persistence.

## Definition of Done

- [ ] One Coordinator session is reused for all decisions in a run.
- [ ] Current tracker/repository context is included in every decision prompt.
- [ ] Completed tickets are never re-executed.
- [ ] Ready-for-verification tickets never run an Executor first.
- [ ] Verifier feedback resumes the same Executor session.
- [ ] Unknown or illegal Coordinator actions stop safely.
- [ ] All existing and new tests pass.

## Tests and calibration

Use fake runner decisions for deterministic state-transition tests, then run the full unittest suite.

## Handoff

Record test commands/results, package build verification, remaining restart limitations, and final
tracker state.
