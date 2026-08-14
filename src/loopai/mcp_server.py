"""Optional MCP stdio adapter for the LoopAI CLI workflow."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .configuration import load_working_directory_config
from .models import LoopConfig, StreamEvent
from .orchestrator import InitiativeOrchestrator
from .version import __version__


async def run_loopai_once(
    *,
    spec: str | None = None,
    answer: str | None = None,
) -> dict[str, Any]:
    """Run one LoopAI turn and return a compact result for an outer Agent.

    The process working directory is the project boundary. The MCP tool deliberately does not
    accept an arbitrary directory argument; configure the MCP host's server ``cwd`` instead.
    """

    working_directory = Path.cwd().expanduser().resolve()
    if spec is not None and not spec.strip():
        return _error_result(working_directory, "invalid-spec", "spec must not be empty")

    try:
        role_settings = load_working_directory_config(working_directory)
        config = LoopConfig(
            working_directory=working_directory,
            coordinator_model=role_settings["coordinator"].model,
            coordinator_reasoning_effort=role_settings["coordinator"].reasoning_effort,
            coordinator_startup_prompt=role_settings["coordinator"].startup_prompt,
            executor_model=role_settings["executor"].model,
            executor_reasoning_effort=role_settings["executor"].reasoning_effort,
            verifier_model=role_settings["verifier"].model,
            verifier_reasoning_effort=role_settings["verifier"].reasoning_effort,
        )
        consumed_answer = False

        async def input_provider(request: dict[str, object]) -> str | None:
            del request
            nonlocal consumed_answer
            if answer is None or consumed_answer:
                return None
            consumed_answer = True
            return answer

        orchestrator = InitiativeOrchestrator(
            config,
            input_provider=input_provider if answer is not None else None,
        )
        terminal: StreamEvent | None = None
        async for event in orchestrator.stream(Path(spec) if spec is not None else None):
            if event.kind in {"initiative.completed", "initiative.handoff"}:
                terminal = event

        if answer is not None and not consumed_answer:
            return _error_result(
                working_directory,
                "unused-answer",
                "An answer was supplied but the current initiative had no pending handoff.",
            )
        if terminal is None:
            return _error_result(
                working_directory,
                "missing-terminal-event",
                "LoopAI ended without an initiative.completed or initiative.handoff event.",
            )
        return _compact_terminal_event(working_directory, terminal)
    except Exception as error:
        return _error_result(working_directory, "runtime-error", str(error))


def create_server() -> Any:
    """Create the optional MCP server without importing MCP for CLI-only installs."""

    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError as error:  # pragma: no cover - depends on optional installation
        raise RuntimeError(
            "MCP support is optional. Install LoopAI with `pip install 'loopai-agent-loop[mcp]'` "
            "using Python 3.10 or newer."
        ) from error

    server = MCPServer(
        "LoopAI",
        version=__version__,
        instructions=(
            "Run one resumable LoopAI turn in the server process working directory. "
            "On handoff, inspect LOOPAI_STATUS.md, perform the requested external action, "
            "and call loopai_run again with the result in answer."
        ),
    )

    @server.tool()
    async def loopai_run(
        spec: str | None = None,
        answer: str | None = None,
    ) -> dict[str, Any]:
        """Run or resume one LoopAI turn in the configured project directory."""

        return await run_loopai_once(spec=spec, answer=answer)

    return server


def main() -> None:
    try:
        create_server().run(transport="stdio")
    except RuntimeError as error:
        print(f"loopai-mcp: {error}", file=sys.stderr)
        raise SystemExit(2) from error


def _compact_terminal_event(working_directory: Path, event: StreamEvent) -> dict[str, Any]:
    payload = event.payload
    result: dict[str, Any] = {
        "schema_version": 1,
        "event": event.kind,
        "status": payload.get("status"),
        "cause": payload.get("cause"),
        "completed": payload.get("completed"),
        "total": payload.get("total"),
        "current_ticket_id": payload.get("current_ticket_id"),
        "summary": payload.get("summary"),
        "status_file": payload.get(
            "status_file", str(working_directory / "LOOPAI_STATUS.md")
        ),
        "working_directory": str(working_directory),
    }
    pending = payload.get("pending")
    if pending is not None:
        result["pending"] = pending
    if event.kind == "initiative.handoff":
        result["next_action"] = (
            "Inspect the status_file, perform the requested external action, then call "
            "loopai_run again with answer."
        )
    else:
        result["next_action"] = "The initiative completed successfully."
    return result


def _error_result(working_directory: Path, cause: str, error: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "event": "initiative.error",
        "status": "error",
        "cause": cause,
        "error": error,
        "status_file": str(working_directory / "LOOPAI_STATUS.md"),
        "working_directory": str(working_directory),
    }
