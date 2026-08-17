"""Optional MCP stdio adapter for the LoopAI CLI workflow."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .configuration import load_working_directory_config
from .frontier import Frontier
from .models import LoopConfig, StreamEvent
from .orchestrator import InitiativeOrchestrator
from .runtime import RuntimeStateStore
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
            if event.kind in {
                "initiative.completed",
                "initiative.ticket-completed",
                "initiative.handoff",
            }:
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
                "LoopAI ended without a terminal initiative event.",
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
            "Run one resumable single-ticket LoopAI turn in the server process working directory. "
            "After initiative.ticket-completed, call loopai_run again without an answer. "
            "Use loopai_status to inspect the live phase and loopai_stop to request a safe stop "
            "before resuming with corrected guidance. "
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

    @server.tool()
    async def loopai_status(spec: str | None = None) -> dict[str, Any]:
        """Inspect the current LoopAI runtime and durable ticket progress."""

        return await get_loopai_status(spec=spec)

    @server.tool()
    async def loopai_stop(
        spec: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Request a safe stop at the next orchestration boundary."""

        return await request_loopai_stop(spec=spec, reason=reason)

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
    elif event.kind == "initiative.ticket-completed":
        result["next_action"] = (
            "Call loopai_run again to process the next dependency-ready ticket."
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


async def get_loopai_status(*, spec: str | None = None) -> dict[str, Any]:
    """Return live runtime state without acquiring the initiative mutation lock."""

    working_directory = Path.cwd().expanduser().resolve()
    if spec is not None and not spec.strip():
        return _error_result(working_directory, "invalid-spec", "spec must not be empty")
    try:
        frontier = Frontier.discover(
            working_directory, Path(spec) if spec is not None else None
        )
        runtime = RuntimeStateStore(frontier.spec.parent)
        snapshot = runtime.read()
        stop_request = runtime.stop_request()
        active_pid = runtime.active_pid
        active = runtime.is_active
        lifecycle = _runtime_string(snapshot, "lifecycle")
        if active:
            status = "stopping" if stop_request is not None else lifecycle or "running"
        elif lifecycle == "running":
            status = "interrupted"
        else:
            status = lifecycle or "idle"
        current_ticket_id = _runtime_string(snapshot, "current_ticket_id")
        current_ticket = next(
            (
                ticket
                for ticket in frontier.tickets
                if ticket.ticket_id == current_ticket_id
            ),
            None,
        )
        current_ticket_path = _runtime_string(snapshot, "current_ticket_path")
        if current_ticket_path is None and current_ticket is not None:
            current_ticket_path = str(current_ticket.path)
        result: dict[str, Any] = {
            "schema_version": 1,
            "event": "initiative.status",
            "status": status,
            "lifecycle": lifecycle,
            "phase": _runtime_string(snapshot, "phase") or "idle",
            "active": active,
            "active_pid": active_pid,
            "stop_requested": stop_request is not None,
            "current_ticket_id": current_ticket_id,
            "current_ticket_path": current_ticket_path,
            "role": _runtime_string(snapshot, "role"),
            "round": snapshot.get("round"),
            "last_event": _runtime_string(snapshot, "last_event"),
            "agent_status": _runtime_string(snapshot, "agent_status"),
            "summary": _runtime_string(snapshot, "summary"),
            "cause": _runtime_string(snapshot, "cause"),
            "completed": len(frontier.completed_ids),
            "total": len(frontier.tickets),
            "spec": str(frontier.spec),
            "execution_map": str(frontier.execution_map),
            "runtime_file": str(runtime.path),
            "status_file": str(working_directory / "LOOPAI_STATUS.md"),
            "working_directory": str(working_directory),
            "updated_at": _runtime_string(snapshot, "updated_at"),
        }
        if stop_request is not None:
            result["stop_reason"] = stop_request.get("reason")
        result["next_action"] = _status_next_action(status)
        return result
    except Exception as error:
        return _error_result(working_directory, "runtime-error", str(error))


async def request_loopai_stop(
    *, spec: str | None = None, reason: str | None = None
) -> dict[str, Any]:
    """Request a running initiative to stop and create a resumable handoff."""

    working_directory = Path.cwd().expanduser().resolve()
    if spec is not None and not spec.strip():
        return _error_result(working_directory, "invalid-spec", "spec must not be empty")
    try:
        frontier = Frontier.discover(
            working_directory, Path(spec) if spec is not None else None
        )
        runtime = RuntimeStateStore(frontier.spec.parent)
        if not runtime.is_active:
            result = await get_loopai_status(spec=spec)
            result.update(
                {
                    "event": "initiative.stop",
                    "status": "not-running",
                    "cause": "no-active-run",
                    "next_action": "Start or resume LoopAI when ready.",
                }
            )
            return result
        request = runtime.request_stop(
            reason or "The Outer Agent requested a controlled stop."
        )
        result = await get_loopai_status(spec=spec)
        result.update(
            {
                "event": "initiative.stop-requested",
                "status": "stop-requested",
                "lifecycle": "stopping",
                "cause": "operator-stop",
                "stop_reason": request["reason"],
                "next_action": (
                    "Wait for loopai_run to return initiative.handoff, then resume "
                    "with loopai_run(answer=...)."
                ),
            }
        )
        return result
    except Exception as error:
        return _error_result(working_directory, "runtime-error", str(error))


def _runtime_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value.strip() else None


def _status_next_action(status: str) -> str:
    if status in {"running", "stopping"}:
        return "Use loopai_stop to request a safe stop, or wait for the current turn to finish."
    if status == "handoff":
        return "Inspect LOOPAI_STATUS.md, then call loopai_run(answer=...) when ready."
    if status == "ticket-completed":
        return "Call loopai_run again to process the next dependency-ready ticket."
    if status == "completed":
        return "The initiative is complete."
    if status == "interrupted":
        return "Inspect the runtime snapshot and repository before resuming LoopAI."
    return "Call loopai_run to start or resume the initiative."
