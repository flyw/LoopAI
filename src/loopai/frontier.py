from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


_ROW = re.compile(
    r"^\|\s*\[(?P<label>[^]]+)]\((?P<path>[^)]+)\)\s*"
    r"\|\s*(?P<status>[^|]+?)\s*"
    r"\|\s*(?P<blocked>[^|]+?)\s*\|"
)
_TICKET_ID = re.compile(r"^\s*(?P<id>\d+)")
_BLOCKER_ID = re.compile(r"\b\d+\b")


@dataclass(frozen=True)
class TicketRecord:
    ticket_id: str
    path: Path
    status: str
    blockers: tuple[str, ...]
    index: int


@dataclass(frozen=True)
class Frontier:
    spec: Path
    execution_map: Path
    tickets: tuple[TicketRecord, ...]

    @classmethod
    def discover(cls, workspace: Path, spec: Path | None = None) -> Frontier:
        resolved_spec = _resolve_spec(workspace, spec)
        execution_map = resolved_spec.parent / "README.md"
        if not execution_map.is_file():
            raise ValueError(
                f"Execution map not found beside spec: {execution_map}. "
                "Expected the CropAI layout spec.md + README.md + issues/."
            )
        return cls.load(resolved_spec, execution_map)

    @classmethod
    def load(cls, spec: Path, execution_map: Path) -> Frontier:
        rows: list[TicketRecord] = []
        for line in execution_map.read_text(encoding="utf-8").splitlines():
            match = _ROW.match(line)
            if match is None:
                continue
            id_match = _TICKET_ID.match(match.group("label"))
            if id_match is None:
                continue
            ticket_id = id_match.group("id")
            raw_blockers = match.group("blocked").strip()
            blockers = (
                ()
                if raw_blockers.lower() in {"none", "n/a", "-"}
                else tuple(_BLOCKER_ID.findall(raw_blockers))
            )
            ticket_path = (execution_map.parent / match.group("path")).resolve()
            rows.append(
                TicketRecord(
                    ticket_id=ticket_id,
                    path=ticket_path,
                    status=match.group("status").strip().lower(),
                    blockers=blockers,
                    index=len(rows),
                )
            )

        if not rows:
            raise ValueError(f"No CropAI-style Ticket Index rows found in {execution_map}")
        frontier = cls(spec=spec.resolve(), execution_map=execution_map.resolve(), tickets=tuple(rows))
        frontier.validate()
        return frontier

    def validate(self) -> None:
        by_id: dict[str, TicketRecord] = {}
        for ticket in self.tickets:
            if ticket.ticket_id in by_id:
                raise ValueError(f"Duplicate ticket id {ticket.ticket_id} in {self.execution_map}")
            if not ticket.path.is_file():
                raise ValueError(f"Ticket file does not exist: {ticket.path}")
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
