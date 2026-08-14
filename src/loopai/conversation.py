from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class InitiativeAlreadyRunningError(RuntimeError):
    """Raised when another live process owns the same initiative."""


class ConversationStore:
    def __init__(self, initiative: Path, working_directory: Path) -> None:
        self.directory = initiative / ".loopai"
        self.conversation_path = self.directory / "conversation.json"
        self.sessions_path = self.directory / "sessions.json"
        self.lock_path = self.directory / "active.lock"
        self.working_directory = working_directory
        self.state: dict[str, Any] = {
            "version": 1,
            "mode": "normal",
            "status": "active",
            "current_ticket_id": None,
            "pending": None,
            "answers": [],
            "updated_at": _now(),
        }
        self.sessions: dict[str, Any] = {"coordinator_session_id": None}
        self._owns_lock = False

    def open(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self._acquire_lock()
        self.state = self._read_json(self.conversation_path, self.state)
        self.sessions = self._read_json(self.sessions_path, self.sessions)
        self._exclude_from_git()

    def close(self) -> None:
        if not self._owns_lock:
            return
        try:
            self.lock_path.unlink(missing_ok=True)
        finally:
            self._owns_lock = False

    @property
    def coordinator_session_id(self) -> str | None:
        value = self.sessions.get("coordinator_session_id")
        return value if isinstance(value, str) and value else None

    @property
    def pending(self) -> dict[str, Any] | None:
        value = self.state.get("pending")
        return value if isinstance(value, dict) else None

    @property
    def mode(self) -> str:
        return "grill" if self.state.get("mode") == "grill" else "normal"

    def set_session(self, session_id: str | None) -> None:
        self.sessions["coordinator_session_id"] = session_id
        self._write_json(self.sessions_path, self.sessions)

    def set_ticket(self, ticket_id: str | None) -> None:
        self.state["current_ticket_id"] = ticket_id
        self._save_state()

    def set_mode(self, mode: str) -> None:
        self.state["mode"] = mode
        self._save_state()

    def require_input(
        self, *, question: str, recommended_answer: str | None, kind: str
    ) -> dict[str, Any]:
        request = {
            "kind": kind,
            "question": question,
            "recommended_answer": recommended_answer,
        }
        self.state["pending"] = request
        self.state["status"] = "awaiting-user-input"
        self._save_state()
        return request

    def mark_handoff(
        self,
        *,
        cause: str,
        summary: str,
        question: str | None = None,
        recommended_answer: str | None = None,
    ) -> dict[str, Any]:
        pending = self.pending
        if pending is None:
            pending = {
                "kind": "handoff",
                "question": question
                or "LoopAI is paused. The Outer Agent must process the blocker before resuming.",
                "recommended_answer": recommended_answer,
            }
        else:
            pending = {
                **pending,
                "original_kind": pending.get("kind"),
                "kind": "handoff",
            }
        pending = {
            **pending,
            "handoff_cause": cause,
            "planner_summary": summary,
        }
        self.state["pending"] = pending
        self.state["status"] = "handoff"
        self._save_state()
        return pending

    def mark_completed(self) -> None:
        self.state["pending"] = None
        self.state["status"] = "completed"
        self._save_state()

    def record_answer(self, answer: str) -> dict[str, Any]:
        pending = self.pending
        if pending is None:
            raise ValueError("No pending Coordinator question exists.")
        entry = {**pending, "answer": answer, "answered_at": _now()}
        answers = self.state.setdefault("answers", [])
        if not isinstance(answers, list):
            answers = []
            self.state["answers"] = answers
        answers.append(entry)
        self.state["pending"] = None
        self.state["status"] = "active"
        self._save_state()
        return entry

    def pop_answer(self) -> dict[str, Any] | None:
        answers = self.state.get("answers")
        if not isinstance(answers, list) or not answers:
            return None
        entry = answers.pop()
        self._save_state()
        return entry if isinstance(entry, dict) else None

    def context(self) -> str:
        answers = self.state.get("answers")
        recent = answers[-10:] if isinstance(answers, list) else []
        return json.dumps(
            {
                "mode": self.mode,
                "current_ticket_id": self.state.get("current_ticket_id"),
                "pending": self.pending,
                "recent_answers": recent,
            },
            ensure_ascii=False,
        )

    def _save_state(self) -> None:
        self.state["updated_at"] = _now()
        self._write_json(self.conversation_path, self.state)

    def _acquire_lock(self) -> None:
        try:
            descriptor = os.open(
                self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
            )
        except FileExistsError:
            owner = self._read_lock_owner()
            if owner is not None and not _process_exists(owner):
                self.lock_path.unlink(missing_ok=True)
                return self._acquire_lock()
            raise InitiativeAlreadyRunningError(
                f"This initiative is already running (pid {owner or 'unknown'}): "
                f"{self.lock_path}"
            ) from None
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"pid": os.getpid(), "created_at": _now()}, stream)
        self._owns_lock = True

    def _read_lock_owner(self) -> int | None:
        try:
            payload = json.loads(self.lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        pid = payload.get("pid") if isinstance(payload, dict) else None
        return pid if isinstance(pid, int) and pid > 0 else None

    def _exclude_from_git(self) -> None:
        git_dir = self.working_directory / ".git"
        exclude = git_dir / "info" / "exclude"
        if not git_dir.is_dir() or not exclude.parent.is_dir():
            return
        current = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        rules = {line.strip() for line in current.splitlines()}
        required_rules = {".loopai/", "LOOPAI_STATUS.md"}
        if required_rules <= rules:
            return
        separator = "" if not current or current.endswith("\n") else "\n"
        additions = "\n".join(rule for rule in sorted(required_rules - rules))
        exclude.write_text(f"{current}{separator}{additions}\n", encoding="utf-8")

    @staticmethod
    def _read_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return dict(fallback)
        except json.JSONDecodeError as error:
            raise ValueError(f"Invalid LoopAI state file: {path}") from error
        if not isinstance(payload, dict):
            raise ValueError(f"LoopAI state file must contain an object: {path}")
        return payload

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
