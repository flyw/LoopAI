# 01 — Route every user wait through input

## Global objective

Keep LoopAI interactive whenever the Coordinator needs a user decision or external evidence.

## Dependencies

None. Read the Coordinator schema/skill, `InitiativeOrchestrator.coordinate`, CLI input provider,
conversation persistence, and the CropAI Ticket 01 failure trace.

## Current subtask

Route `await-user` through the same question/persistence/resume loop as `ask-user`. When no explicit
question exists, construct one from reason and feedback. Preserve Grill behavior and safety gates.

## Required outputs

Updated orchestrator, Coordinator prompt/skill, tests, README, wheel, and installed command.

## Non-goals

Do not change CropAI ticket scope, Codex sandbox permissions, or Executor/Verifier completion rules.

## Definition of Done

- [ ] Interactive `await-user` reads input and resumes the same Coordinator session.
- [ ] Missing question text receives a useful reason/feedback fallback.
- [ ] Non-interactive `await-user` persists and returns `awaiting-user-input`.
- [ ] `/cancel`, `/status`, `/back`, Grill confirmation, and maximum rounds remain valid.
- [ ] Existing completed/blocked/verification gates remain unchanged.
- [ ] Full tests and isolated installed-command checks pass.

## Tests and calibration

Use FakeRunner decisions to replay the exact `await-user` action with null question in interactive
and non-interactive modes, then run the complete unittest suite.

## Handoff

Record changed files, exact commands, results, package hash, and any remaining external sandbox
limitations. Independent verification still owns completion.
