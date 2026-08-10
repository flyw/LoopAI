from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from loopai.frontier import Frontier
from test_orchestrator import create_initiative


class FrontierTests(unittest.TestCase):
    def test_discovers_ticket_metadata_and_blockers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            spec = create_initiative(workspace)

            frontier = Frontier.discover(workspace, spec)

        self.assertEqual([ticket.ticket_id for ticket in frontier.tickets], ["01", "02"])
        self.assertEqual(frontier.tickets[1].blockers, ("01",))
        self.assertEqual(frontier.next_ticket().ticket_id, "01")
        self.assertEqual(frontier.next_ticket({"01"}).ticket_id, "02")

    def test_creates_tracker_without_readme(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            spec = create_initiative(workspace)

            frontier = Frontier.discover(workspace, spec)
            tracker = spec.parent / ".loopai" / "execution.json"

            self.assertFalse((spec.parent / "README.md").exists())
            self.assertEqual(frontier.execution_map, tracker.resolve())
            payload = json.loads(tracker.read_text(encoding="utf-8"))

        self.assertEqual(payload["tickets"][0]["ticket_id"], "01")
        self.assertEqual(payload["tickets"][1]["blocked_by"], ["01"])

    def test_auto_discovers_the_only_spec(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            spec = create_initiative(workspace)

            frontier = Frontier.discover(workspace)

        self.assertEqual(frontier.spec, spec.resolve())

    def test_rejects_multiple_implicit_specs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            create_initiative(workspace)
            other = workspace / "other"
            other.mkdir()
            (other / "spec.md").write_text("# Other\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Multiple spec.md"):
                Frontier.discover(workspace)

    def test_rejects_unknown_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            spec = create_initiative(workspace)
            first_ticket = spec.parent / "issues" / "01-first.md"
            first_ticket.write_text(
                first_ticket.read_text(encoding="utf-8").replace(
                    "**Blocked by:** None", "**Blocked by:** 99"
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "unknown blockers: 99"):
                Frontier.discover(workspace, spec)


if __name__ == "__main__":
    unittest.main()
