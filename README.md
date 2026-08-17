# LoopAI

LoopAI is a spec-first, resumable orchestration loop for coding agents.

It coordinates a Planner, an Executor, and an independent Verifier to implement one ticket at a
time in dependency order. After a ticket completes, the turn ends so the next invocation can start
the next dependency-ready ticket. When progress is blocked, LoopAI writes a durable handoff summary
and returns control to the outer agent instead of waiting for terminal input.

> LoopAI is currently alpha software. It runs local Codex CLI sessions and can modify the project
> directory. Review the [security model](#security-model) before using it on an untrusted project.

## What it does

- Discovers an initiative from `spec.md` and `issues/*.md`.
- Builds and validates the ticket dependency frontier.
- Runs one Planner session for orchestration.
- Runs a dedicated Executor and an independent Verifier for each ticket.
- Stops after one ticket completes or requires a handoff; repeated invocations advance the frontier.
- Persists tracker state and agent sessions under the initiative's `.loopai/` directory.
- Resumes completed and verification-ready work after a process restart.
- Emits JSONL events for scripts, CI jobs, and outer agents.
- Hands control back to an outer agent through `LOOPAI_STATUS.md` when a decision, external action,
  verification result, or safe stopping point is required.

LoopAI does not call an LLM API directly. It starts the local `codex exec --json` command and streams
the resulting events. Authentication, provider configuration, network behavior, and data handling
therefore come from the Codex CLI and its configured provider.

## Requirements

- Python 3.9 or newer for the core CLI.
- Codex CLI installed and authenticated on the machine running LoopAI.
- A project directory containing an initiative spec. Git is recommended but is not required by the
  CLI boundary.

The default prompts are self-contained. LoopAI does not require private or third-party skills.

## Install

### Install from a local checkout

The `pyproject.toml` file registers the `loopai` console command.

With [uv](https://docs.astral.sh/uv/):

```bash
uv tool install --editable /absolute/path/to/LoopAI
uv tool update-shell
loopai --version
```

With [pipx](https://pipx.pypa.io/):

```bash
pipx install /absolute/path/to/LoopAI
pipx ensurepath
loopai --version
```

To install the optional MCP adapter in the same persistent environment, include the MCP extra:

```bash
uv tool install --with "mcp>=2,<3" --editable /absolute/path/to/LoopAI
# or
pipx install "/absolute/path/to/LoopAI[mcp]"
```

`uv tool install` puts the package's console scripts in an isolated environment and exposes them
on `PATH` after `uv tool update-shell`; `pipx ensurepath` does the equivalent for pipx.

The command and the Python package have different names: the distribution is
`loopai-agent-loop`, while the executable is `loopai`.

### Install from PyPI

After a release is published:

```bash
uv tool install loopai-agent-loop
# or
pipx install loopai-agent-loop
```

For the MCP adapter, install the optional extra:

```bash
uv tool install 'loopai-agent-loop[mcp]'
# or
pipx install 'loopai-agent-loop[mcp]'
```

### Run directly from GitHub

This is useful for an outer agent or CI job that should not install a persistent global command:

```bash
uvx \
  --from git+https://github.com/flyw/LoopAI.git \
  loopai \
  --json
```

The host process must still provide a working Codex CLI and authentication.

## Quick start

An initiative has one `spec.md` and one `issues/` directory:

```text
my-project/
├── spec.md
├── README.md                 # optional human documentation
├── issues/
│   ├── 01-foundation.md
│   └── 02-follow-up.md
└── artifacts/
```

Each ticket file starts with a numeric id. Optional metadata defines its initial status and
dependencies:

```markdown
# Add the foundation

**Status:** ready-for-agent
**Blocked by:**

Implement the foundation described by the initiative spec.
```

Run LoopAI from the project directory. The process working directory is the project boundary;
there is no separate public `workspace` argument:

```bash
cd /absolute/path/to/my-project
loopai --spec spec.md
```

When the directory contains exactly one `spec.md`, `--spec` may be omitted:

```bash
cd /absolute/path/to/my-project
loopai
```

Relative spec paths are resolved from the process working directory and must stay inside it.

## Planner handoff and resume

LoopAI is deliberately non-interactive. If it cannot safely continue, it:

1. asks the Planner to summarize the current repository and tracker state;
2. atomically writes that summary to `LOOPAI_STATUS.md` in the process working directory;
3. emits an `initiative.handoff` event; and
4. exits with status code `1`.

The outer agent should read the status file, perform the requested external action, and resume with
the result:

```bash
cd /absolute/path/to/my-project
loopai --answer "The external action is complete. Please re-check the repository and continue."
```

`--answer` can be supplied more than once for scripted multi-round handoffs. A supplied answer with
no pending handoff is an error. Do not place passwords, API keys, or other credentials in an answer;
the answer is persisted in the initiative conversation history.

`LOOPAI_STATUS.md` is a fast outer-agent entry point. Detailed state remains under the initiative's
`.loopai/` directory:

```text
initiative/.loopai/
├── conversation.json
├── sessions.json
├── execution.json
├── runtime.json
├── control.json           # transient stop request, when present
└── active.lock
```

LoopAI adds `.loopai/` and `LOOPAI_STATUS.md` to the local repository's `.git/info/exclude` when
possible. It does not change the shared `.gitignore`.

## Machine-readable CLI protocol

Use `--json` when another program or agent is consuming the output:

```bash
loopai --json --spec spec.md
```

Every stdout line is a JSON object with `schema_version: 1`, `kind`, `ticket`, `role`, `round`, and
`payload` fields. Important event kinds include:

- `initiative.started`
- `ticket.started`
- `agent.event` — raw Codex JSONL events
- `agent.stderr`
- `agent.completed`
- `ticket.completed`
- `user.input.required` — a machine-readable request immediately followed by handoff when no answer
  provider is available
- `initiative.ticket-completed` — one ticket completed; invoke LoopAI again for the next ticket
- `initiative.completed`
- `initiative.handoff`

The terminal event is the outer agent's control signal:

| Exit code | Meaning | Outer-agent action |
| --- | --- | --- |
| `0` | One ticket completed, or the entire initiative completed | Invoke again for the next ticket, unless the initiative is complete |
| `1` | LoopAI handed control back safely | Read `LOOPAI_STATUS.md`, act, then resume |
| `2` | Configuration, initialization, or runtime error | Report or repair the error |

Exit code `1` is an expected workflow result, not a process crash.

The complete outer-agent contract is in [docs/agent-integration.md](docs/agent-integration.md).

## Configuration

The first run creates `<current-directory>/.loopai/config.toml`:

```toml
[coordinator]
model = "gpt-5.6-luna"
reasoning_effort = "medium"

[executor]
model = "gpt-5.6-luna"
reasoning_effort = "medium"

[verifier]
model = "gpt-5.6-luna"
reasoning_effort = "medium"
```

Every role must define both `model` and `reasoning_effort`. The Coordinator's optional
`startup_prompt` is omitted by default and can be added only when a project needs extra Planner
instructions.

Role-specific CLI options override global CLI options, which override this file:

```bash
loopai \
  --model gpt-5.6-luna \
  --reasoning-effort medium \
  --coordinator-model gpt-5.6-luna \
  --verifier-reasoning-effort high
```

Useful runtime options:

```text
--spec PATH                         Select an initiative spec.
--model MODEL                       Set the model for all roles.
--coordinator-model MODEL           Override the Planner model.
--executor-model MODEL              Override the Executor model.
--verifier-model MODEL              Override the Verifier model.
--reasoning-effort LEVEL             Set the effort for all roles.
--max-rounds N                      Limit Executor/Verifier rounds per ticket.
--max-questions N                   Limit Planner question rounds.
--codex-binary PATH                 Select a Codex executable.
--automatic-approval                Use Codex automatic approval (default).
--no-automatic-approval             Do not pass Codex's automatic approval flag.
--answer TEXT                      Provide an outer-agent handoff result.
--json                             Emit JSONL events.
```

## Security model

LoopAI starts model-driven Codex sessions in the current project directory. By default it passes
`--approve-for-me` for new sessions so the workflow can run without a terminal prompt. This lets the
Codex approval reviewer authorize work in the configured write-access sandbox. Review the project,
the initiative files, and the Codex configuration before enabling automatic execution.

To omit that flag:

```bash
loopai --no-automatic-approval
```

This mode may stop or fail if Codex requires an approval that cannot be answered non-interactively.
Do not run LoopAI on untrusted repositories with credentials available to child processes.

The outer-agent answer, Planner summaries, tracker state, and Codex session ids are local files.
Avoid putting secrets in prompts, ticket files, `--answer`, or persisted startup prompts.

## MCP integration

The core CLI works with any Agent that can call shell commands. For hosts that expose native MCP
tools, install the optional MCP extra on Python 3.10 or newer:

```bash
pip install "loopai-agent-loop[mcp]"
loopai-mcp
```

`loopai-mcp` uses stdio and takes its project directory from its process `cwd`. It exposes three
tools: `loopai_run`, `loopai_status`, and `loopai_stop`.

```json
{
  "spec": "spec.md",
  "answer": "The external action is complete. Please continue."
}
```

Both fields are optional. An omitted `answer` starts or continues a normal turn. An `answer`
resumes a pending handoff. The result is a compact object containing `status`, `cause`, progress,
the current ticket, Planner summary, `status_file`, and the next action.

Use `loopai_status` to inspect the live phase without acquiring the initiative mutation lock:

```json
{
  "spec": "spec.md"
}
```

It reports the lifecycle (`running`, `stopping`, `handoff`, `ticket-completed`, `completed`, or
`interrupted`), phase (`coordinator`, `executor`, `verifier`, or `waiting-input`), current ticket,
round, last significant event, durable ticket progress, and whether a stop request is pending.

If the current work has diverged from the ticket, request a controlled stop:

```json
{
  "spec": "spec.md",
  "reason": "The verifier is checking files outside the ticket scope."
}
```

`loopai_stop` does not kill the MCP or Codex process. It asks the Orchestrator to stop at the next
safe agent boundary, persist an `operator-stop` handoff, and release the initiative lock. After
inspecting the handoff, resume with corrected guidance:

```json
{
  "spec": "spec.md",
  "answer": "Re-read the ticket scope, discard the unrelated change, and resume verification."
}
```

Do not modify a prompt belonging to an already-running Codex child. Stop first, then provide the
correction through the resumed handoff.

Do not pass a project directory as a tool argument. Configure the MCP server's process working
directory instead. For example, a Codex TOML configuration can use an absolute executable path:

```toml
[mcp_servers.loopai]
command = "/absolute/path/to/loopai-mcp"
cwd = "/absolute/path/to/my-project"
```

Or use `uvx` for a GitHub or PyPI installation:

```toml
[mcp_servers.loopai]
command = "uvx"
args = ["--from", "loopai-agent-loop[mcp]", "loopai-mcp"]
cwd = "/absolute/path/to/my-project"
```

For an unreleased GitHub checkout, add the MCP SDK as an extra dependency explicitly:

```bash
uvx --from git+https://github.com/flyw/LoopAI.git --with "mcp>=2,<3" loopai-mcp
```

MCP is an optional adapter around the same Python orchestrator. It does not create a second state
model or a second working-directory concept. See [docs/mcp.md](docs/mcp.md) for the full adapter
contract.

## Python API

```python
import asyncio
from pathlib import Path

from loopai import InitiativeOrchestrator, LoopConfig


async def main() -> None:
    project = Path.cwd()
    config = LoopConfig(
        working_directory=project,
        model="gpt-5.6-luna",
        reasoning_effort="medium",
        max_rounds=3,
    )
    orchestrator = InitiativeOrchestrator(config)

    async for event in orchestrator.stream(Path("spec.md")):
        print(event.as_dict())


asyncio.run(main())
```

The library retains an `input_provider` seam for embedders and tests. The public CLI and MCP server
use the outer-agent handoff protocol and never read terminal input.

## Development

Run the unit tests without connecting to Codex:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python3 -m unittest discover -s tests -v
```

Build the package:

```bash
python3 -m pip install build
python3 -m build
```

The macOS single-file build remains available through `scripts/build-macos.sh`. It packages LoopAI,
not Codex CLI or authentication, so the target machine still needs Codex CLI configured.

## License

LoopAI is released under the [MIT License](LICENSE).
