# Coordinator model integration

Add a third Codex role that coordinates the existing ticket Executor and independent Verifier.
The Coordinator must invoke `$flyw:agent-initiative-orchestrator`, inspect durable initiative and
repository state, and choose the next constrained orchestration action. Python remains the safety
boundary: dependencies, ticket identity, completion persistence, and allowed transitions are
validated mechanically.

The Executor and Verifier must invoke the installed `$flyw:*` skills. Runtime handoffs remain owned
by their role skills; `writing-for-agents` is not a mandatory runtime dependency.

## Terminal behavior

- A Coordinator session is created once per LoopAI run and resumed for later decisions.
- Completed tickets are skipped from the execution map; ready-for-verification work starts at the
  Verifier.
- Agent results are returned to the Coordinator before the next transition.
- Invalid Coordinator actions stop safely instead of bypassing dependency or verification gates.
- Existing CLI streaming and JSONL behavior remain compatible.
