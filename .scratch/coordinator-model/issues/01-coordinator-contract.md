# 01 — Coordinator contract

## Global objective

Introduce a constrained Coordinator model without weakening the existing Executor/Verifier gates.

## Dependencies

None. Read `spec.md`, the execution map, `src/loopai/models.py`, `prompts.py`, `runner.py`, and their
tests.

## Current subtask

Add the Coordinator role, first-line skill invocation, structured decision schema, and runner
parsing. Change Executor and Verifier skill names from `$yuanwang:*` to `$flyw:*`.

## Required outputs

- Coordinator model and prompt contract under `src/loopai/`.
- `src/loopai/schemas/coordinator.json`.
- Prompt and runner contract tests.

## Non-goals

Do not yet change ticket transition behavior or persist Codex session IDs across processes.

## Definition of Done

- [ ] Every role selects its matching installed `$flyw:*` skill in the first prompt line.
- [ ] Coordinator output accepts only the declared action vocabulary and required reasoning.
- [ ] New and resumed Coordinator commands select the Coordinator schema.
- [ ] Existing Executor and Verifier process behavior remains tested.

## Tests and calibration

Run the prompt and runner unit/process tests through the public Python interfaces.

## Handoff

Record changed files, exact commands, results, and any open contract issue for Ticket 02.
