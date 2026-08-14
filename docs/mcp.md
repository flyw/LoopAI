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
```

The tool runs one LoopAI turn. `answer` is used to resume an existing handoff. The result contains:

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

After receiving `status: "handoff"`, the host should read the status file, perform the requested
action, and call `loopai_run` again with the result in `answer`.

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
- The host should expose the tool with an approval policy appropriate for repository writes.
- Do not pass credentials in `answer` or ticket content.
- Do not give the MCP server a broader `cwd` than the project it is meant to operate on.
