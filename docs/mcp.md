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
loopai_run(spec?: string, answer?: string) -> object
loopai_status(spec?: string) -> object
loopai_stop(spec?: string, reason?: string) -> object
```

The tool runs one ticket turn. `answer` is used to resume an existing handoff. A successful
non-final ticket returns `event: "initiative.ticket-completed"` and asks the host to call
`loopai_run` again without an answer. The result contains:

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

## Live status and controlled stop

`loopai_status` is status-oriented and does not acquire the initiative mutation lock. It reads the
durable tracker plus `.loopai/runtime.json`, so it can report a running invocation:

```json
{
  "schema_version": 1,
  "event": "initiative.status",
  "status": "running",
  "phase": "verifier",
  "active": true,
  "current_ticket_id": "01",
  "round": 2,
  "last_event": "verifier.started",
  "completed": 0,
  "total": 2,
  "stop_requested": false
}
```

The phase is a safe orchestration checkpoint, not a raw model transcript. It can be
`coordinator`, `executor`, `verifier`, or `waiting-input`.

When the status indicates that work has diverged, call `loopai_stop`:

```json
{
  "spec": "spec.md",
  "reason": "The verifier left the ticket scope."
}
```

The stop request is written to `.loopai/control.json`. The current agent call is allowed to finish;
the Orchestrator then creates an `initiative.handoff` with cause `operator-stop`, writes the request
reason to the durable conversation, and removes the control request. The host can then correct the
guidance and resume:

```text
loopai_run(
  spec="spec.md",
  answer="Re-read the ticket scope and resume from the verification step."
)
```

This control path deliberately does not kill the MCP server or the Codex child process. A process
kill remains an emergency recovery action and may leave only partial runtime state.

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

- The initiative lock prevents two LoopAI turns from mutating the same initiative simultaneously.
- `loopai_status` is safe to call while a turn is running; `loopai_stop` writes a request for that
  running turn and does not take over its mutation lock.
- The host should expose the tool with an approval policy appropriate for repository writes.
- Do not pass credentials in `answer` or ticket content.
- Do not give the MCP server a broader `cwd` than the project it is meant to operate on.
