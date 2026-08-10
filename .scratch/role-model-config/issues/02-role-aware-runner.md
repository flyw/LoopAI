# 02 — Role-aware Codex commands

## Global objective

Launch each AgentRole with its independently resolved model and reasoning effort.

## Dependencies

Ticket 01 configuration loader and validated role settings.

## Current subtask

Wire TOML into CLI startup, add global and role-specific CLI overrides, expose deterministic
per-role settings on `LoopConfig`, and use them in new/resumed Codex commands.

## Required outputs

Updated models, CLI, runner, README, command tests, wheel, and installed command.

## Non-goals

Do not change prompts, agent state transitions, or concurrency.

## Definition of Done

- [ ] Precedence is role CLI > global CLI > TOML > built-in default.
- [ ] Coordinator, Executor, and Verifier commands carry their resolved model and effort.
- [ ] Existing global CLI options still override all roles.
- [ ] CLI help and README document automatic generation and examples.
- [ ] Full tests and isolated wheel installation pass.

## Tests and calibration

Parse CLI options and inspect actual `CodexRunner.build_command` output for every role.

## Handoff

Record exact tests, package verification, installed command path, and remaining configuration limits.
