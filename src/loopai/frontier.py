from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


_TICKET_FILE = re.compile(r"^(?P<id>\d+)(?:[-_ ].*)?\.md$")
_BLOCKER_ID = re.compile(r"\b\d+\b")
_TRACKER_VERSION = 1
_TRACKER_NAME = "execution.json"


@dataclass(frozen=True)
class TicketRecord:
    ticket_id: str
    path: Path
    status: str
    blockers: tuple[str, ...]
    index: int


@dataclass(frozen=True)
class _TicketDefinition:
    ticket_id: str
    path: Path
    status: str
    blockers: tuple[str, ...]


@dataclass(frozen=True)
class Frontier:
    """Validated ticket frontier backed by LoopAI's automatic JSON tracker."""

    spec: Path
    execution_map: Path
    tickets: tuple[TicketRecord, ...]

    @classmethod
    def discover(cls, workspace: Path, spec: Path | None = None) -> Frontier:
        resolved_spec = _resolve_spec(workspace, spec)
        execution_map = resolved_spec.parent / ".loopai" / _TRACKER_NAME
        _sync_execution_map(resolved_spec, execution_map)
        return cls.load(resolved_spec, execution_map)

    @classmethod
    def load(cls, spec: Path, execution_map: Path) -> Frontier:
        payload = _read_execution_map(execution_map)
        raw_tickets = payload.get("tickets")
        if not isinstance(raw_tickets, list):
            raise ValueError(f"LoopAI tracker must contain a tickets list: {execution_map}")

        rows: list[TicketRecord] = []
        for index, raw in enumerate(raw_tickets):
            if not isinstance(raw, dict):
                raise ValueError(
                    f"Invalid ticket entry {index} in LoopAI tracker: {execution_map}"
                )
            ticket_id = _required_string(raw, "ticket_id", execution_map)
            raw_path = _required_string(raw, "path", execution_map)
            ticket_path = (spec.parent / raw_path).resolve()
            if not ticket_path.is_file():
                raise ValueError(f"Ticket file does not exist: {ticket_path}")
            status = _required_string(raw, "status", execution_map).lower()
            raw_blockers = raw.get("blocked_by", [])
            if not isinstance(raw_blockers, list) or not all(
                isinstance(item, str) for item in raw_blockers
            ):
                raise ValueError(
                    f"Ticket {ticket_id} has invalid blocked_by data in {execution_map}"
                )
            rows.append(
                TicketRecord(
                    ticket_id=ticket_id,
                    path=ticket_path,
                    status=status,
                    blockers=tuple(raw_blockers),
                    index=index,
                )
            )

        if not rows:
            raise ValueError(f"No ticket files found for {spec}")
        frontier = cls(spec=spec.resolve(), execution_map=execution_map.resolve(), tickets=tuple(rows))
        frontier.validate()
        return frontier

    def validate(self) -> None:
        by_id: dict[str, TicketRecord] = {}
        for ticket in self.tickets:
            if ticket.ticket_id in by_id:
                raise ValueError(f"Duplicate ticket id {ticket.ticket_id} in {self.execution_map}")
            by_id[ticket.ticket_id] = ticket
        for ticket in self.tickets:
            missing = [blocker for blocker in ticket.blockers if blocker not in by_id]
            if missing:
                raise ValueError(
                    f"Ticket {ticket.ticket_id} has unknown blockers: {', '.join(missing)}"
                )
        _assert_acyclic(by_id)

    @property
    def completed_ids(self) -> set[str]:
        return {ticket.ticket_id for ticket in self.tickets if ticket.status == "completed"}

    def next_ticket(self, additional_completed: set[str] | None = None) -> TicketRecord | None:
        completed = self.completed_ids | (additional_completed or set())
        for ticket in self.tickets:
            if ticket.ticket_id in completed:
                continue
            if set(ticket.blockers) <= completed:
                return ticket
        return None

    def set_status(self, ticket_id: str, status: str) -> None:
        """Persist a worker result without requiring an agent to edit a tracker."""

        payload = _read_execution_map(self.execution_map)
        raw_tickets = payload.get("tickets")
        if not isinstance(raw_tickets, list):
            raise ValueError(f"LoopAI tracker must contain a tickets list: {self.execution_map}")
        for raw in raw_tickets:
            if isinstance(raw, dict) and raw.get("ticket_id") == ticket_id:
                raw["status"] = status
                _write_execution_map(self.execution_map, payload)
                return
        raise ValueError(f"Ticket {ticket_id} not found in {self.execution_map}")


def _sync_execution_map(spec: Path, execution_map: Path) -> None:
    definitions = _discover_ticket_definitions(spec.parent)
    previous: dict[str, dict[str, object]] = {}
    if execution_map.exists():
        payload = _read_execution_map(execution_map)
        raw_tickets = payload.get("tickets")
        if not isinstance(raw_tickets, list):
            raise ValueError(f"LoopAI tracker must contain a tickets list: {execution_map}")
        for raw in raw_tickets:
            if not isinstance(raw, dict):
                raise ValueError(f"Invalid ticket entry in LoopAI tracker: {execution_map}")
            ticket_id = raw.get("ticket_id")
            if not isinstance(ticket_id, str) or not ticket_id:
                raise ValueError(f"Ticket entry has no ticket_id in {execution_map}")
            if ticket_id in previous:
                raise ValueError(f"Duplicate ticket id {ticket_id} in {execution_map}")
            previous[ticket_id] = raw

    definition_ids = {definition.ticket_id for definition in definitions}
    removed = sorted(set(previous) - definition_ids)
    if removed:
        raise ValueError(
            "LoopAI tracker contains tickets whose files no longer exist: "
            f"{', '.join(removed)}. Remove {execution_map} to rebuild it."
        )

    tickets: list[dict[str, object]] = []
    for definition in definitions:
        old = previous.get(definition.ticket_id)
        status = definition.status
        if old is not None:
            old_status = old.get("status")
            if not isinstance(old_status, str) or not old_status.strip():
                raise ValueError(
                    f"Ticket {definition.ticket_id} has invalid status in {execution_map}"
                )
            status = old_status.strip().lower()
        tickets.append(
            {
                "ticket_id": definition.ticket_id,
                "path": str(definition.path.relative_to(spec.parent)),
                "status": status,
                "blocked_by": list(definition.blockers),
            }
        )

    updated = {"version": _TRACKER_VERSION, "tickets": tickets}
    if not execution_map.exists() or _read_execution_map(execution_map) != updated:
        _write_execution_map(execution_map, updated)


def _discover_ticket_definitions(initiative: Path) -> list[_TicketDefinition]:
    issue_directory = initiative / "issues"
    if not issue_directory.is_dir():
        raise ValueError(
            f"Ticket directory not found: {issue_directory}. "
            "Expected spec.md beside an issues/ directory."
        )
    ticket_files = sorted(issue_directory.glob("*.md"))
    if not ticket_files:
        raise ValueError(
            f"No ticket files found in {issue_directory}. "
            "Expected files such as issues/01-first.md."
        )

    definitions: list[_TicketDefinition] = []
    seen: set[str] = set()
    for ticket_path in ticket_files:
        match = _TICKET_FILE.fullmatch(ticket_path.name)
        if match is None:
            raise ValueError(
                f"Ticket file must start with a numeric id, for example 01-first.md: "
                f"{ticket_path}"
            )
        ticket_id = match.group("id")
        if ticket_id in seen:
            raise ValueError(f"Duplicate ticket id {ticket_id} in {issue_directory}")
        seen.add(ticket_id)
        text = ticket_path.read_text(encoding="utf-8")
        status = _metadata_value(text, "Status") or "ready-for-agent"
        blockers_value = _metadata_value(text, "Blocked by") or ""
        blockers = tuple(_BLOCKER_ID.findall(blockers_value))
        definitions.append(
            _TicketDefinition(
                ticket_id=ticket_id,
                path=ticket_path.resolve(),
                status=status.strip().lower(),
                blockers=blockers,
            )
        )
    definitions.sort(key=lambda definition: (int(definition.ticket_id), definition.path.name))
    return definitions


def _metadata_value(text: str, label: str) -> str | None:
    match = re.search(
        rf"^\s*\*\*{re.escape(label)}:\*\*\s*(?P<value>.*?)\s*$",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    return match.group("value") if match is not None else None


def _required_string(payload: dict[str, object], key: str, path: Path) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Tracker field {key!r} must be a non-empty string in {path}")
    return value.strip()


def _read_execution_map(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"LoopAI tracker does not exist: {path}") from None
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid LoopAI tracker {path}: {error}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"LoopAI tracker must contain an object: {path}")
    return payload


def _write_execution_map(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _resolve_spec(workspace: Path, spec: Path | None) -> Path:
    if spec is not None:
        candidate = spec.expanduser()
        if not candidate.is_absolute():
            candidate = workspace / candidate
        candidate = candidate.resolve()
        if not candidate.is_file():
            raise ValueError(f"Spec does not exist: {candidate}")
        return candidate

    candidates = sorted(
        path.resolve()
        for path in workspace.rglob("spec.md")
        if not any(part in {".git", ".venv", "node_modules"} for part in path.parts)
    )
    if not candidates:
        raise ValueError(f"No spec.md found under workspace: {workspace}")
    if len(candidates) > 1:
        choices = "\n".join(f"- {path}" for path in candidates)
        raise ValueError(f"Multiple spec.md files found; select one with --spec:\n{choices}")
    return candidates[0]


def _assert_acyclic(by_id: dict[str, TicketRecord]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(ticket_id: str) -> None:
        if ticket_id in visiting:
            raise ValueError(f"Dependency cycle detected at ticket {ticket_id}")
        if ticket_id in visited:
            return
        visiting.add(ticket_id)
        for blocker in by_id[ticket_id].blockers:
            visit(blocker)
        visiting.remove(ticket_id)
        visited.add(ticket_id)

    for ticket_id in by_id:
        visit(ticket_id)
