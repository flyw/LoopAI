# Use the process working directory as LoopAI scope

LoopAI treats the directory from which it is started as its working directory and removes the
separate workspace concept from its public CLI and Python configuration. This makes the directory
visible to the outer agent, spec discovery, configuration, status handoff, and Codex subprocesses
the same scope; callers that need another initiative change the process working directory before
invoking LoopAI.

## Status

accepted
