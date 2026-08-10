from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from importlib.resources import files
from pathlib import Path
from typing import Any

from .models import AgentResult, AgentRole, LoopConfig, StreamEvent

EventSink = Callable[[StreamEvent], Awaitable[None]]


class AgentProcessError(RuntimeError):
    """Raised when Codex exits unsuccessfully or emits an invalid final response."""


class CodexRunner:
    def __init__(self, config: LoopConfig) -> None:
        self.config = config

    def build_command(self, role: AgentRole, session_id: str | None = None) -> list[str]:
        schema = files("loopai").joinpath("schemas", f"{role.value}.json")
        shared = [
            "--json",
            "--model",
            self.config.model_for(role),
            "-c",
            f'model_reasoning_effort="{self.config.reasoning_effort_for(role)}"',
            "--skip-git-repo-check",
            "--output-schema",
            str(schema),
        ]
        if session_id:
            return [
                self.config.codex_binary,
                "exec",
                "resume",
                *shared,
                session_id,
                "-",
            ]
        return [
            self.config.codex_binary,
            "exec",
            *shared,
            "--approve-for-me",
            "--cd",
            str(self.config.workspace),
            "-",
        ]

    async def run(
        self,
        *,
        role: AgentRole,
        ticket: Path,
        round_number: int,
        prompt: str,
        session_id: str | None,
        emit: EventSink,
    ) -> AgentResult:
        command = self.build_command(role, session_id)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=self.config.workspace,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=self.config.subprocess_stream_limit,
            )
        except FileNotFoundError as error:
            raise AgentProcessError(
                f"Codex CLI executable not found: {self.config.codex_binary}. "
                "Install Codex CLI and log in before running LoopAI."
            ) from error
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None

        process.stdin.write(prompt.encode("utf-8"))
        await process.stdin.drain()
        process.stdin.close()

        discovered_session = session_id
        final_text: str | None = None

        async def read_stdout() -> None:
            nonlocal discovered_session, final_text
            async for raw_line in process.stdout:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    payload = {"type": "unparsed_stdout", "text": line}
                if payload.get("type") == "thread.started":
                    discovered_session = payload.get("thread_id", discovered_session)
                candidate = _agent_message_text(payload)
                if candidate is not None:
                    final_text = candidate
                await emit(
                    StreamEvent(
                        kind="agent.event",
                        ticket=ticket,
                        role=role,
                        round_number=round_number,
                        payload=payload,
                    )
                )

        async def read_stderr() -> None:
            async for raw_line in process.stderr:
                text = raw_line.decode("utf-8", errors="replace").rstrip("\n")
                await emit(
                    StreamEvent(
                        kind="agent.stderr",
                        ticket=ticket,
                        role=role,
                        round_number=round_number,
                        payload={"text": text},
                    )
                )

        try:
            await asyncio.gather(read_stdout(), read_stderr())
            exit_code = await process.wait()
        except asyncio.CancelledError:
            if process.returncode is None:
                process.terminate()
            await process.wait()
            raise

        if exit_code != 0:
            raise AgentProcessError(f"{role.value} agent exited with code {exit_code}")
        if not discovered_session:
            raise AgentProcessError(f"{role.value} agent did not emit a session id")
        if final_text is None:
            raise AgentProcessError(f"{role.value} agent did not emit a final message")
        try:
            final = json.loads(final_text)
        except json.JSONDecodeError as error:
            raise AgentProcessError(
                f"{role.value} agent final message was not JSON: {final_text}"
            ) from error
        result_key = "action" if role is AgentRole.COORDINATOR else "status"
        summary_key = "reason" if role is AgentRole.COORDINATOR else "summary"
        if not isinstance(final, dict) or not isinstance(final.get(result_key), str):
            raise AgentProcessError(f"{role.value} agent returned an invalid result")
        summary = final.get(summary_key)
        if not isinstance(summary, str) or not summary.strip():
            raise AgentProcessError(f"{role.value} agent returned an empty summary")
        return AgentResult(
            role=role,
            status=final[result_key],
            summary=summary,
            session_id=discovered_session,
            final_output=final,
        )


def _agent_message_text(payload: dict[str, Any]) -> str | None:
    if payload.get("type") != "item.completed":
        return None
    item = payload.get("item")
    if not isinstance(item, dict) or item.get("type") != "agent_message":
        return None
    text = item.get("text")
    return text if isinstance(text, str) else None
