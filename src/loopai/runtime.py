from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RUNTIME_FILENAME = "runtime.json"
CONTROL_FILENAME = "control.json"


class RuntimeStateStore:
    """Persist live orchestration state and safe external control requests."""

    def __init__(self, initiative: Path) -> None:
        self.directory = (initiative / ".loopai").resolve()
        self.path = self.directory / RUNTIME_FILENAME
        self.control_path = self.directory / CONTROL_FILENAME
        self.lock_path = self.directory / "active.lock"

    def start(self, **fields: Any) -> None:
        fields.setdefault("lifecycle", "running")
        fields.setdefault("phase", "initializing")
        fields.setdefault("pid", os.getpid())
        fields.setdefault("role", None)
        fields.setdefault("round", None)
        fields.setdefault("last_event", "orchestrator.started")
        fields.setdefault("agent_status", None)
        fields.setdefault("summary", None)
        fields.setdefault("cause", None)
        self.update(**fields)

    def update(self, **fields: Any) -> dict[str, Any]:
        current = self.read()
        current.setdefault("version", 1)
        current.update(fields)
        current["updated_at"] = _now()
        self._write_json(self.path, current)
        return current

    def read(self) -> dict[str, Any]:
        return self._read_json(self.path, {"version": 1})

    def request_stop(self, reason: str) -> dict[str, Any]:
        request = {
            "version": 1,
            "action": "stop",
            "reason": reason.strip() or "The Outer Agent requested a controlled stop.",
            "requested_at": _now(),
            "requested_by_pid": os.getpid(),
        }
        self._write_json(self.control_path, request)
        return request

    def stop_request(self) -> dict[str, Any] | None:
        payload = self._read_json(self.control_path, {})
        return payload if payload.get("action") == "stop" else None

    def clear_stop_request(self) -> None:
        self.control_path.unlink(missing_ok=True)

    @property
    def lock_exists(self) -> bool:
        return self.lock_path.exists()

    @property
    def active_pid(self) -> int | None:
        payload = self._read_json(self.lock_path, {})
        pid = payload.get("pid")
        return pid if isinstance(pid, int) and pid > 0 else None

    @property
    def is_active(self) -> bool:
        pid = self.active_pid
        return pid is not None and _process_exists(pid)

    @staticmethod
    def _read_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return dict(fallback)
        except json.JSONDecodeError as error:
            raise ValueError(f"Invalid LoopAI runtime file: {path}") from error
        if not isinstance(payload, dict):
            raise ValueError(f"LoopAI runtime file must contain an object: {path}")
        return payload

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
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
