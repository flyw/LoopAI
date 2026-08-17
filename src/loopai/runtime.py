from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RUNTIME_FILENAME = "runtime.json"
CONTROL_FILENAME = "control.json"
WORKER_LOCK_FILENAME = "worker.lock"
WORKER_REQUEST_FILENAME = "worker-request.json"
WORKER_LOG_FILENAME = "worker.log"


class RuntimeStateStore:
    """Persist live orchestration state and safe external control requests."""

    def __init__(self, initiative: Path) -> None:
        self.directory = (initiative / ".loopai").resolve()
        self.path = self.directory / RUNTIME_FILENAME
        self.control_path = self.directory / CONTROL_FILENAME
        self.lock_path = self.directory / "active.lock"
        self.worker_lock_path = self.directory / WORKER_LOCK_FILENAME
        self.worker_request_path = self.directory / WORKER_REQUEST_FILENAME
        self.worker_log_path = self.directory / WORKER_LOG_FILENAME

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
        fields.setdefault("worker_pid", os.getpid())
        self.update(**fields)

    def update(self, **fields: Any) -> dict[str, Any]:
        current = self.read()
        current.setdefault("version", 1)
        current.update(fields)
        now = _now()
        current["updated_at"] = now
        current["heartbeat_at"] = now
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

    def clear_stop_request(self, expected: dict[str, Any] | None = None) -> None:
        if expected is not None and self.stop_request() != expected:
            return
        self.control_path.unlink(missing_ok=True)

    def reserve_worker(self) -> dict[str, Any] | None:
        """Atomically reserve the single background Worker slot.

        A live lock owner means another synchronous or background turn is already
        using the initiative. A dead owner is stale and may be reclaimed before
        retrying the atomic create.
        """

        self.directory.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                descriptor = os.open(
                    self.worker_lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError:
                payload = self.worker_lock()
                owner = _worker_lock_pid(payload)
                if owner is None:
                    # A partially written or malformed reservation is safer to
                    # treat as active than to race another launcher.
                    return None
                if _process_exists(owner):
                    return None
                self.worker_lock_path.unlink(missing_ok=True)
                continue

            payload = {
                "version": 1,
                "state": "starting",
                "pid": os.getpid(),
                "worker_pid": None,
                "reserved_at": _now(),
            }
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(payload, stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
            except BaseException:
                self.worker_lock_path.unlink(missing_ok=True)
                raise
            return payload

    def worker_lock(self) -> dict[str, Any]:
        return self._read_json(
            self.worker_lock_path,
            {"version": 1, "state": "starting", "pid": None, "worker_pid": None},
        )

    @property
    def worker_pid(self) -> int | None:
        payload = self.worker_lock()
        worker_pid = payload.get("worker_pid")
        if isinstance(worker_pid, int) and worker_pid > 0:
            return worker_pid
        if payload.get("state") == "running":
            pid = payload.get("pid")
            if isinstance(pid, int) and pid > 0:
                return pid
        return None

    @property
    def worker_is_active(self) -> bool:
        if not self.worker_lock_path.exists():
            return False
        owner = _worker_lock_pid(self.worker_lock())
        # An empty reservation is still active until its launcher either starts
        # the Worker or explicitly releases the reservation.
        return owner is None or _process_exists(owner)

    def claim_worker(self, pid: int | None = None) -> dict[str, Any]:
        """Transfer a launcher reservation to the detached Worker process."""

        if not self.worker_lock_path.exists():
            raise RuntimeError(f"Worker reservation does not exist: {self.worker_lock_path}")
        worker_pid = pid or os.getpid()
        payload = self.worker_lock()
        payload.update(
            {
                "state": "running",
                "pid": worker_pid,
                "worker_pid": worker_pid,
                "started_at": payload.get("reserved_at", _now()),
            }
        )
        self._write_json(self.worker_lock_path, payload)
        return payload

    def assign_worker(self, pid: int) -> bool:
        """Record a spawned child's PID without recreating a released lock."""

        if not self.worker_lock_path.exists():
            return False
        payload = self.worker_lock()
        current = payload.get("worker_pid")
        if isinstance(current, int) and current > 0 and current != pid:
            return False
        payload.update(
            {
                "state": payload.get("state", "starting"),
                "pid": pid,
                "worker_pid": pid,
                "started_at": payload.get("reserved_at", _now()),
            }
        )
        self._write_json(self.worker_lock_path, payload)
        return True

    def release_worker(self, owner_pid: int | None = None) -> None:
        """Release the Worker reservation only if this process still owns it."""

        if not self.worker_lock_path.exists():
            return
        payload = self.worker_lock()
        expected = owner_pid or os.getpid()
        owner = _worker_lock_pid(payload)
        if owner == expected:
            self.worker_lock_path.unlink(missing_ok=True)

    def write_worker_request(self, *, spec: str, answer: str | None) -> None:
        self._write_json(
            self.worker_request_path,
            {
                "version": 1,
                "spec": spec,
                "answer": answer,
                "created_at": _now(),
            },
        )

    def read_worker_request(self) -> dict[str, Any]:
        return self._read_json(self.worker_request_path, {})

    def clear_worker_request(self) -> None:
        self.worker_request_path.unlink(missing_ok=True)

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


def _worker_lock_pid(payload: dict[str, Any]) -> int | None:
    worker_pid = payload.get("worker_pid")
    if isinstance(worker_pid, int) and worker_pid > 0:
        return worker_pid
    pid = payload.get("pid")
    return pid if isinstance(pid, int) and pid > 0 else None
