# Hand off blocked runs to the outer agent

LoopAI records a durable handoff and exits whenever its initiative cannot safely continue, instead
of waiting for an in-process terminal prompt. The Planner summary, blocking context, and a resume
instruction are written to the working directory so an outer agent can inspect or repair the
repository and start a new resume invocation; this keeps control with the caller and works equally
for interactive and tool-call execution.

## Status

accepted

## Consequences

Non-completed runs expose a `handoff` event and return exit code `1`; the detailed cause remains in
the handoff record. A completed run returns `0`, while initialization errors remain ordinary fatal
errors. The existing programmatic input-provider seam remains available for embedders and tests,
but the CLI never waits on a terminal prompt.
