# 01 — Inject workspace Coordinator instructions

## Global objective

Let each workspace customize only its Coordinator's initial interaction behavior.

## Dependencies

None. Read the workspace TOML loader, `LoopConfig`, Coordinator prompt construction, session resume
and replacement paths, and their tests.

## Current subtask

Accept optional multiline `[coordinator].startup_prompt`, preserve the skill invocation as the first
prompt line, append user instructions after fixed Coordinator instructions on new sessions, and omit
them from valid resumes and both worker roles.

## Required outputs

Updated configuration/model/prompt/orchestrator modules, tests, README, wheel, and installed command.

## Non-goals

No language-specific field, no Executor/Verifier customization, and no change to JSON schemas or
state transitions.

## Definition of Done

- [ ] Existing configuration without `startup_prompt` remains valid.
- [ ] A string or TOML multiline string is accepted; non-string values fail clearly.
- [ ] First and replacement Coordinator sessions receive the configured prompt.
- [ ] Valid Coordinator resumes and worker prompts do not repeat/receive it.
- [ ] Full tests and isolated package installation pass.

## Tests and calibration

Load real temporary TOML and inspect FakeRunner Coordinator prompts across first call, resume, and
stale-session replacement.

## Handoff

Record commands, results, artifact hash, and any remaining prompt-size or trust considerations.
