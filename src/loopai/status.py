from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STATUS_FILENAME = "LOOPAI_STATUS.md"


class StatusFile:
    """Atomically publishes the latest handoff or completion snapshot."""

    def __init__(self, working_directory: Path) -> None:
        self.working_directory = working_directory.expanduser().resolve()
        self.path = self.working_directory / STATUS_FILENAME

    def write(
        self,
        *,
        status: str,
        cause: str | None,
        spec: Path,
        execution_map: Path,
        completed: int,
        total: int,
        current_ticket_id: str | None,
        current_ticket_path: Path | None,
        summary: str,
        pending: dict[str, Any] | None,
        waiting_ticket_ids: list[str] | None = None,
        error: str | None = None,
    ) -> None:
        lines = [
            "# LoopAI status",
            "",
            f"- Status: `{status}`",
            f"- Cause: `{cause or 'none'}`",
            f"- Updated at: `{_now()}`",
            f"- Working directory: `{self.working_directory}`",
            f"- Initiative spec: `{spec}`",
            f"- Execution tracker: `{execution_map}`",
            f"- Progress: `{completed}/{total}` tickets completed",
            f"- Current ticket: `{current_ticket_id or 'none'}`",
        ]
        if current_ticket_path is not None:
            lines.append(f"- Current ticket path: `{current_ticket_path}`")
        if waiting_ticket_ids:
            lines.append(
                "- Waiting ticket ids: "
                + ", ".join(f"`{ticket_id}`" for ticket_id in waiting_ticket_ids)
            )
        if error:
            lines.append(f"- Runtime error: `{error}`")

        lines.extend(["", "## Planner summary", "", summary.strip() or "No summary was provided."])

        if pending is not None:
            question = pending.get("question")
            recommended = pending.get("recommended_answer")
            lines.extend(["", "## Pending handoff", ""])
            lines.append(
                str(question).strip()
                if isinstance(question, str) and question.strip()
                else "The Outer Agent must decide how LoopAI should proceed."
            )
            if isinstance(recommended, str) and recommended.strip():
                lines.extend(["", f"Recommended response: {recommended.strip()}"])

        if status == "handoff":
            lines.extend(
                [
                    "",
                    "## Outer Agent action",
                    "",
                    "Inspect or repair the repository as needed, then invoke LoopAI again "
                    "with the result.",
                    "",
                    "```bash",
                    'loopai --answer "外层 Agent 已完成处理，请继续执行"',
                    "```",
                ]
            )
        else:
            lines.extend(["", "## Result", "", "The initiative completed successfully."])

        self._write_atomic("\n".join(lines) + "\n")

    def _write_atomic(self, content: str) -> None:
        self.working_directory.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(self.path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
