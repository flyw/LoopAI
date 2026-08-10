# 01 — Workspace TOML contract

## Global objective

Give each LoopAI workspace a local, editable, validated model configuration.

## Dependencies

None. Read `pyproject.toml`, `LoopConfig`, CLI setup, and existing `.loopai` state behavior.

## Current subtask

Create `<workspace>/.loopai/config.toml` on first startup, load its three role sections, validate a
strict schema, and keep it excluded through the existing `.git/info/exclude` mechanism.

## Required outputs

Configuration module, Python 3.9 TOML dependency fallback, and unit tests for creation and errors.

## Non-goals

Do not change Codex commands in this ticket.

## Definition of Done

- [ ] First load creates the documented default file without overwriting an existing file.
- [ ] All three role sections and only known keys are accepted.
- [ ] Invalid TOML, missing/unknown fields, empty models, and invalid efforts fail clearly.
- [ ] Python 3.9 has a declared TOML parser fallback.

## Tests and calibration

Exercise the public configuration loader against temporary workspaces and real files.

## Handoff

Record files, commands, exit codes, results, and the resolved settings contract for Ticket 02.
