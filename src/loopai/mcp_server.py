"""Optional MCP stdio adapter for the LoopAI CLI workflow."""

from __future__ import annotations

import json
import os
import subprocess
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


async def run_loopai(
    *,
    spec: str | None = None,
    answer: str | None = None,
    wait: bool = True,
) -> dict[str, Any]:
    """Run one turn synchronously or dispatch it to the single background Worker."""

    if wait:
        return await _run_loopai_wait(spec=spec, answer=answer)
    return await _start_loopai_background(spec=spec, answer=answer)


async def _run_loopai_wait(
    *, spec: str | None, answer: str | None
) -> dict[str, Any]:
    working_directory = Path.cwd().expanduser().resolve()
    if spec is not None and not spec.strip():
        return _error_result(working_directory, "invalid-spec", "spec must not be empty")
    try:
        frontier = Frontier.discover(
            working_directory, Path(spec) if spec is not None else None
        )
        runtime = RuntimeStateStore(frontier.spec.parent)
        stale_stop = runtime.stop_request()
        state_error = _validate_run_start(frontier, runtime, answer, working_directory)
        if state_error is not None:
            return state_error
        if runtime.reserve_worker() is None:
            return _already_running_result(working_directory, frontier, runtime)
        runtime.assign_worker(os.getpid())
        if stale_stop is not None:
            runtime.clear_stop_request(expected=stale_stop)
        runtime.update(
            lifecycle="starting",
            phase="launching",
            pid=os.getpid(),
            worker_pid=None,
            spec=str(frontier.spec),
            execution_map=str(frontier.execution_map),
            completed=len(frontier.completed_ids),
            total=len(frontier.tickets),
            last_event="worker.starting",
            cause=None,
            last_result=None,
        )
        try:
            result = await run_loopai_once(spec=str(frontier.spec), answer=answer)
            runtime.update(last_result=result)
            return result
        finally:
            runtime.clear_worker_request()
            runtime.release_worker()
    except Exception as error:
        return _error_result(working_directory, "runtime-error", str(error))


async def _start_loopai_background(
    *, spec: str | None, answer: str | None
) -> dict[str, Any]:
    working_directory = Path.cwd().expanduser().resolve()
    if spec is not None and not spec.strip():
        return _error_result(working_directory, "invalid-spec", "spec must not be empty")
    try:
        frontier = Frontier.discover(
            working_directory, Path(spec) if spec is not None else None
        )
        runtime = RuntimeStateStore(frontier.spec.parent)
        stale_stop = runtime.stop_request()
        state_error = _validate_run_start(frontier, runtime, answer, working_directory)
        if state_error is not None:
            return state_error
        if runtime.reserve_worker() is None:
            return _already_running_result(working_directory, frontier, runtime)

        try:
            if stale_stop is not None:
                runtime.clear_stop_request(expected=stale_stop)
            runtime.write_worker_request(spec=str(frontier.spec), answer=answer)
            runtime.update(
                lifecycle="starting",
                phase="launching",
                pid=None,
                worker_pid=None,
                spec=str(frontier.spec),
                execution_map=str(frontier.execution_map),
                completed=len(frontier.completed_ids),
                total=len(frontier.tickets),
                last_event="worker.starting",
                cause=None,
                last_result=None,
            )
            command = [
                sys.executable,
                "-m",
                "loopai.worker",
                "--initiative",
                str(frontier.spec.parent),
                "--request-file",
                str(runtime.worker_request_path),
            ]
            runtime.directory.mkdir(parents=True, exist_ok=True)
            log_stream = runtime.worker_log_path.open("a", encoding="utf-8")
            try:
                process = subprocess.Popen(
                    command,
                    cwd=str(working_directory),
                    stdin=subprocess.DEVNULL,
                    stdout=log_stream,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                )
            finally:
                log_stream.close()
            runtime.assign_worker(process.pid)
            return _accepted_result(working_directory, frontier, runtime, process.pid)
        except Exception as error:
            runtime.clear_worker_request()
            runtime.update(
                lifecycle="error",
                phase="error",
                last_event="worker.start-failed",
                cause="worker-start-failed",
                summary=str(error),
            )
            runtime.release_worker()
            return _error_result(working_directory, "worker-start-failed", str(error))
    except Exception as error:
        return _error_result(working_directory, "runtime-error", str(error))


def _validate_run_start(
    frontier: Frontier,
    runtime: RuntimeStateStore,
    answer: str | None,
    working_directory: Path,
) -> dict[str, Any] | None:
    if runtime.worker_is_active or runtime.is_active:
        return _already_running_result(working_directory, frontier, runtime)

    snapshot = runtime.read()
    lifecycle = _runtime_string(snapshot, "lifecycle")
    if lifecycle == "completed" or len(frontier.completed_ids) == len(frontier.tickets):
        return _already_completed_result(working_directory, frontier, runtime)

    pending = _read_pending(frontier.spec.parent)
    resumable = {
        "handoff",
        "stopped",
        "awaiting-user-input",
        "awaiting-user-verification",
        "operator-stop",
    }
    if (lifecycle in resumable or pending is not None) and answer is None:
        return _error_result(
            working_directory,
            "answer-required",
            "The current initiative is waiting for corrected guidance in answer.",
        )
    return None


def _accepted_result(
    working_directory: Path,
    frontier: Frontier,
    runtime: RuntimeStateStore,
    worker_pid: int,
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "event": "initiative.accepted",
        "status": "starting",
        "lifecycle": "starting",
        "active": True,
        "worker_pid": worker_pid,
        "completed": len(frontier.completed_ids),
        "total": len(frontier.tickets),
        "spec": str(frontier.spec),
        "runtime_file": str(runtime.path),
        "worker_log": str(runtime.worker_log_path),
        "status_file": str(working_directory / "LOOPAI_STATUS.md"),
        "working_directory": str(working_directory),
        "next_action": "Poll loopai_status for the Worker phase and terminal result.",
    }


def _already_running_result(
    working_directory: Path,
    frontier: Frontier,
    runtime: RuntimeStateStore,
) -> dict[str, Any]:
    snapshot = runtime.read()
    lifecycle = _runtime_string(snapshot, "lifecycle") or "running"
    status = "starting" if lifecycle == "starting" else lifecycle
    worker_pid = runtime.worker_pid
    if worker_pid is None and runtime.is_active:
        worker_pid = runtime.active_pid
    return {
        "schema_version": 2,
        "event": "initiative.already-running",
        "status": status,
        "lifecycle": lifecycle,
        "active": True,
        "worker_pid": worker_pid,
        "completed": len(frontier.completed_ids),
        "total": len(frontier.tickets),
        "current_ticket_id": _runtime_string(snapshot, "current_ticket_id"),
        "runtime_file": str(runtime.path),
        "worker_log": str(runtime.worker_log_path),
        "status_file": str(working_directory / "LOOPAI_STATUS.md"),
        "working_directory": str(working_directory),
        "next_action": "Poll loopai_status for the current Worker phase.",
    }


def _already_completed_result(
    working_directory: Path,
    frontier: Frontier,
    runtime: RuntimeStateStore,
) -> dict[str, Any]:
    snapshot = runtime.read()
    return {
        "schema_version": 2,
        "event": "initiative.already-completed",
        "status": "completed",
        "lifecycle": "completed",
        "active": False,
        "completed": len(frontier.completed_ids),
        "total": len(frontier.tickets),
        "summary": _runtime_string(snapshot, "summary"),
        "runtime_file": str(runtime.path),
        "status_file": str(working_directory / "LOOPAI_STATUS.md"),
        "working_directory": str(working_directory),
        "next_action": "The initiative is already complete.",
    }


def _read_pending(initiative: Path) -> dict[str, Any] | None:
    path = initiative / ".loopai" / "conversation.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(payload, dict):
        return None
    pending = payload.get("pending")
    return pending if isinstance(pending, dict) else None


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
            "loopai_run waits by default; pass wait=false to start a detached single Worker and "
            "poll loopai_status. "
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
        wait: bool = True,
    ) -> dict[str, Any]:
        """Run or resume one turn; pass wait=false for detached Worker execution."""

        return await run_loopai(spec=spec, answer=answer, wait=wait)

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
        return _status_payload(working_directory, frontier, runtime)
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
        if not (runtime.worker_is_active or runtime.is_active):
            result = _status_payload(working_directory, frontier, runtime)
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
        snapshot = runtime.read()
        runtime.update(
            lifecycle="stop_requested",
            phase=_runtime_string(snapshot, "phase") or "unknown",
            last_event="initiative.stop-requested",
            cause="operator-stop",
        )
        result = _status_payload(working_directory, frontier, runtime)
        result.update(
            {
                "event": "initiative.stop-requested",
                "status": "stop_requested",
                "lifecycle": "stop_requested",
                "cause": "operator-stop",
                "stop_reason": request["reason"],
                "next_action": (
                    "Waiting for the current Codex call to reach a safe boundary; "
                    "then resume with loopai_run(answer=...)."
                ),
            }
        )
        return result
    except Exception as error:
        return _error_result(working_directory, "runtime-error", str(error))


def _status_payload(
    working_directory: Path,
    frontier: Frontier,
    runtime: RuntimeStateStore,
) -> dict[str, Any]:
    snapshot = runtime.read()
    stop_request = runtime.stop_request()
    worker_active = runtime.worker_is_active
    orchestrator_active = runtime.is_active
    active = worker_active or orchestrator_active
    lifecycle = _runtime_string(snapshot, "lifecycle")
    if active:
        if stop_request is not None or lifecycle == "stop_requested":
            status = "stop_requested"
        else:
            status = lifecycle or "running"
    elif lifecycle in {"running", "starting", "stop_requested"}:
        status = "interrupted"
    else:
        status = lifecycle or "idle"
    reported_lifecycle = (
        "stop_requested"
        if active and stop_request is not None
        else lifecycle
    )

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
    active_pid = None
    if worker_active:
        active_pid = runtime.worker_pid
    if active_pid is None and orchestrator_active:
        active_pid = runtime.active_pid
    persisted_worker_pid = snapshot.get("worker_pid")
    if not isinstance(persisted_worker_pid, int) or persisted_worker_pid <= 0:
        persisted_worker_pid = runtime.worker_pid
    result: dict[str, Any] = {
        "schema_version": 2,
        "event": "initiative.status",
        "status": status,
        "lifecycle": reported_lifecycle,
        "phase": _runtime_string(snapshot, "phase") or "idle",
        "active": active,
        "active_pid": active_pid,
        "worker_pid": persisted_worker_pid,
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
        "worker_log": str(runtime.worker_log_path),
        "status_file": str(working_directory / "LOOPAI_STATUS.md"),
        "working_directory": str(working_directory),
        "updated_at": _runtime_string(snapshot, "updated_at"),
        "heartbeat_at": _runtime_string(snapshot, "heartbeat_at"),
    }
    pending = _read_pending(frontier.spec.parent)
    if pending is not None:
        result["pending"] = pending
    last_result = snapshot.get("last_result")
    if isinstance(last_result, dict):
        result["last_result"] = last_result
    if stop_request is not None:
        result["stop_reason"] = stop_request.get("reason")
    result["next_action"] = _status_next_action(status)
    return result


def _runtime_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value.strip() else None


def _status_next_action(status: str) -> str:
    if status == "starting":
        return "Wait for the Worker to start, then poll loopai_status."
    if status == "stop_requested":
        return "Waiting for the current Codex call to reach a safe boundary."
    if status == "running":
        return "Use loopai_stop to request a safe stop, or wait for the current turn to finish."
    if status == "handoff":
        return "Inspect LOOPAI_STATUS.md, then call loopai_run(answer=...) when ready."
    if status == "stopped":
        return "Inspect LOOPAI_STATUS.md, then call loopai_run(answer=...) when ready."
    if status == "ticket-completed":
        return "Call loopai_run again to process the next dependency-ready ticket."
    if status == "completed":
        return "The initiative is complete."
    if status == "error":
        return "Inspect the error, then call loopai_run to retry when appropriate."
    if status == "interrupted":
        return "Inspect the runtime snapshot and repository before resuming LoopAI."
    return "Call loopai_run to start or resume the initiative."
