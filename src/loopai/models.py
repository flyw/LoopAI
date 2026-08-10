from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .configuration import SUPPORTED_REASONING_EFFORTS


class AgentRole(str, Enum):
    COORDINATOR = "coordinator"
    EXECUTOR = "executor"
    VERIFIER = "verifier"


@dataclass(frozen=True)
class LoopConfig:
    workspace: Path
    model: str | None = None
    reasoning_effort: str | None = None
    coordinator_model: str | None = None
    coordinator_reasoning_effort: str | None = None
    coordinator_startup_prompt: str | None = None
    executor_model: str | None = None
    executor_reasoning_effort: str | None = None
    verifier_model: str | None = None
    verifier_reasoning_effort: str | None = None
    max_rounds: int = 3
    codex_binary: str = "codex"
    subprocess_stream_limit: int = 64 * 1024 * 1024
    max_questions: int = 20

    def __post_init__(self) -> None:
        workspace = self.workspace.expanduser().resolve()
        if not workspace.is_dir():
            raise ValueError(f"Workspace does not exist: {workspace}")
        if self.max_rounds < 1:
            raise ValueError("max_rounds must be at least 1")
        if self.subprocess_stream_limit < 64 * 1024:
            raise ValueError("subprocess_stream_limit must be at least 64 KiB")
        if self.max_questions < 1:
            raise ValueError("max_questions must be at least 1")
        efforts = {
            "global": self.reasoning_effort,
            "coordinator": self.coordinator_reasoning_effort,
            "executor": self.executor_reasoning_effort,
            "verifier": self.verifier_reasoning_effort,
        }
        for role, effort in efforts.items():
            if effort is not None and effort not in SUPPORTED_REASONING_EFFORTS:
                raise ValueError(f"Unsupported {role} reasoning effort: {effort}")
        models = {
            "global": self.model,
            "coordinator": self.coordinator_model,
            "executor": self.executor_model,
            "verifier": self.verifier_model,
        }
        for role, model in models.items():
            if model is not None and not model.strip():
                raise ValueError(f"{role} model must not be empty")
        if (
            self.coordinator_startup_prompt is not None
            and not isinstance(self.coordinator_startup_prompt, str)
        ):
            raise ValueError("coordinator startup prompt must be a string")
        object.__setattr__(self, "workspace", workspace)

    def model_for(self, role: AgentRole) -> str:
        specific = {
            AgentRole.COORDINATOR: self.coordinator_model,
            AgentRole.EXECUTOR: self.executor_model,
            AgentRole.VERIFIER: self.verifier_model,
        }[role]
        defaults = {
            AgentRole.COORDINATOR: "gpt-5.6-luna",
            AgentRole.EXECUTOR: "gpt-5.6-luna",
            AgentRole.VERIFIER: "gpt-5.6-luna",
        }
        return specific or self.model or defaults[role]

    def reasoning_effort_for(self, role: AgentRole) -> str:
        specific = {
            AgentRole.COORDINATOR: self.coordinator_reasoning_effort,
            AgentRole.EXECUTOR: self.executor_reasoning_effort,
            AgentRole.VERIFIER: self.verifier_reasoning_effort,
        }[role]
        defaults = {
            AgentRole.COORDINATOR: "medium",
            AgentRole.EXECUTOR: "medium",
            AgentRole.VERIFIER: "medium",
        }
        return specific or self.reasoning_effort or defaults[role]


@dataclass(frozen=True)
class StreamEvent:
    kind: str
    ticket: Path | None = None
    role: AgentRole | None = None
    round_number: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "ticket": str(self.ticket) if self.ticket else None,
            "role": self.role.value if self.role else None,
            "round": self.round_number,
            "payload": self.payload,
        }


@dataclass(frozen=True)
class AgentResult:
    role: AgentRole
    status: str
    summary: str
    session_id: str
    final_output: dict[str, Any]


@dataclass(frozen=True)
class TicketResult:
    ticket: Path
    status: str
    rounds: int
    executor_session_id: str | None
    verifier_session_id: str | None
    summary: str
