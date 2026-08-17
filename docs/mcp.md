# MCP integration

LoopAI's MCP server is an optional stdio adapter around the same Python orchestrator. The core
state model remains the initiative `.loopai/` directory plus the working-directory
`LOOPAI_STATUS.md` handoff file.

## Install

The MCP extra requires Python 3.10 or newer:

```bash
python3 -m pip install "loopai-agent-loop[mcp]"
```

For a local checkout:

```bash
python3 -m pip install -e ".[mcp]"
```

For a persistent global command, use either of these isolated tool environments:

```bash
uv tool install --with "mcp>=2,<3" --editable /absolute/path/to/LoopAI
# or
pipx install "/absolute/path/to/LoopAI[mcp]"
```

Run `uv tool update-shell` or `pipx ensurepath` if the installed executable is not yet on `PATH`.

## Server process

Start the server with `loopai-mcp`. The server uses its process working directory as the project
boundary. A tool call cannot select an arbitrary directory.

The server exposes:

```text
loopai_run(spec?: string, answer?: string, wait?: boolean) -> object
loopai_status(spec?: string) -> object
loopai_stop(spec?: string, reason?: string) -> object
```

`wait` defaults to `true`, so the tool preserves the synchronous one-ticket behavior. `answer` is
used to resume an existing handoff. A successful non-final ticket returns
`event: "initiative.ticket-completed"` and asks the host to call `loopai_run` again without an
answer. The result contains:

```json
{
  "schema_version": 1,
  "event": "initiative.handoff",
  "status": "handoff",
  "cause": "blocked",
  "completed": 1,
  "total": 2,
  "current_ticket_id": "02",
  "summary": "External verification is required.",
  "status_file": "/project/LOOPAI_STATUS.md",
  "working_directory": "/project",
  "next_action": "Inspect the status_file, perform the requested external action, then call loopai_run again with answer."
}
```

For a non-final successful turn, the terminal event instead looks like this:

```json
{
  "schema_version": 1,
  "event": "initiative.ticket-completed",
  "status": "ticket-completed",
  "completed": 1,
  "total": 2,
  "current_ticket_id": "01",
  "next_action": "Call loopai_run again to process the next dependency-ready ticket."
}
```

After receiving `status: "handoff"`, the host should read the status file, perform the requested
action, and call `loopai_run` again with the result in `answer`.

After receiving `event: "initiative.ticket-completed"`, the host should call `loopai_run` again to
process the next dependency-ready ticket.

To return immediately while one ticket is executing, pass `wait: false`:

```json
{
  "spec": "spec.md",
  "wait": false
}
```

LoopAI atomically reserves `.loopai/worker.lock`, starts one detached Worker, and returns:

```json
{
  "schema_version": 2,
  "event": "initiative.accepted",
  "status": "starting",
  "worker_pid": 12345,
  "next_action": "Poll loopai_status for the Worker phase and terminal result."
}
```

There is no job queue or public job id. If a Worker is already starting or running, the second call
returns `event: "initiative.already-running"` and does not start another process. The Worker uses
the configured project `cwd`, starts an independent session, redirects stdout and stderr to
`.loopai/worker.log`, and invokes the core `run_loopai_once()` function directly rather than
calling an MCP tool.

## Live status and controlled stop

`loopai_status` is status-oriented and does not acquire the initiative mutation lock. It reads the
durable tracker plus `.loopai/runtime.json`, so it can report a running invocation:

```json
{
  "schema_version": 2,
  "event": "initiative.status",
  "status": "running",
  "phase": "verifier",
  "active": true,
  "worker_pid": 12345,
  "current_ticket_id": "01",
  "round": 2,
  "last_event": "verifier.started",
  "completed": 0,
  "total": 2,
  "stop_requested": false,
  "heartbeat_at": "2026-08-17T12:00:00+00:00"
}
```

The phase is a safe orchestration checkpoint, not a raw model transcript. It can be
`coordinator`, `executor`, `verifier`, or `waiting-input`. The lifecycle can be `starting`,
`running`, `stop_requested`, `handoff`, `stopped`, `ticket-completed`, `completed`, `error`, or
`interrupted`.

When the status indicates that work has diverged, call `loopai_stop`:

```json
{
  "spec": "spec.md",
  "reason": "The verifier left the ticket scope."
}
```

The stop request is written to `.loopai/control.json`. The current agent call is allowed to finish;
the lifecycle becomes `stop_requested` while it waits for the safe boundary. The Orchestrator then
creates an `initiative.handoff` with cause `operator-stop`, writes the request reason to the durable
conversation, changes the lifecycle to `stopped`, releases `.loopai/worker.lock`, and removes the
control request. The host can then correct the guidance and resume:

```text
loopai_run(
  spec="spec.md",
  answer="Re-read the ticket scope and resume from the verification step."
)
```

This control path deliberately does not kill the MCP server or the Codex child process. If Codex is
stuck, the Worker remains `stop_requested` until the child returns. A process kill remains an
emergency recovery action and may leave only partial runtime state.

Resume is state-checked. `answer` is required for `handoff`, `stopped`, and pending user-input
states. A running initiative returns `initiative.already-running`; a completed initiative returns
`initiative.already-completed`. If the recorded Worker PID no longer exists, the next start treats
the Worker lock as stale and reclaims it atomically.

## Codex configuration

Use an absolute path when the host may start with a minimal environment:

```toml
[mcp_servers.loopai]
command = "/absolute/path/to/loopai-mcp"
cwd = "/absolute/path/to/project"
```

When the package is available from an index, `uvx` avoids a global PATH dependency:

```toml
[mcp_servers.loopai]
command = "uvx"
args = ["--from", "loopai-agent-loop[mcp]", "loopai-mcp"]
cwd = "/absolute/path/to/project"
```

For a GitHub checkout, use the git source and add the optional MCP dependency explicitly:

```toml
[mcp_servers.loopai]
command = "uvx"
args = [
  "--from",
  "git+https://github.com/flyw/LoopAI.git",
  "--with",
  "mcp>=2,<3",
  "loopai-mcp",
]
cwd = "/absolute/path/to/project"
```

The server communicates over stdout. Diagnostics belong on stderr; do not wrap it in a shell command
that prints banners or status messages to stdout.

## Concurrency and safety

- `.loopai/worker.lock` is acquired with an atomic create, so two simultaneous `wait: false` calls
  cannot both start a Worker.
- Starting/running states reject another start; `handoff`, `stopped`, and pending user-input states
  require `answer`; `completed` reports completion; `error` can be retried.
- `loopai_status` is safe to call while a turn is running; `loopai_stop` writes a request for that
  running turn and does not take over its mutation lock.
- The host should expose the tool with an approval policy appropriate for repository writes.
- Do not pass credentials in `answer` or ticket content.
- Do not give the MCP server a broader `cwd` than the project it is meant to operate on.
