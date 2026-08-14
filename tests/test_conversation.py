from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from loopai.conversation import ConversationStore, InitiativeAlreadyRunningError


class ConversationStoreTests(unittest.TestCase):
    def test_state_is_isolated_per_initiative_and_persists_answers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            first = working_directory / "one"
            second = working_directory / "two"
            first.mkdir()
            second.mkdir()
            store = ConversationStore(first, working_directory)
            store.open()
            store.require_input(question="Choose", recommended_answer="A", kind="ask-user")
            store.record_answer("A")
            pending = store.mark_handoff(cause="blocked", summary="Need external repair.")
            store.set_session("coord-1")
            store.close()

            reopened = ConversationStore(first, working_directory)
            reopened.open()
            other = ConversationStore(second, working_directory)
            other.open()
            try:
                self.assertEqual(reopened.coordinator_session_id, "coord-1")
                self.assertIn('"answer": "A"', reopened.context())
                self.assertEqual(pending["kind"], "handoff")
                self.assertEqual(reopened.state["status"], "handoff")
                self.assertNotEqual(reopened.directory, other.directory)
            finally:
                reopened.close()
                other.close()

    def test_live_lock_rejects_second_process_for_same_initiative(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            initiative = working_directory / "one"
            initiative.mkdir()
            first = ConversationStore(initiative, working_directory)
            first.open()
            try:
                with self.assertRaises(InitiativeAlreadyRunningError):
                    ConversationStore(initiative, working_directory).open()
            finally:
                first.close()


if __name__ == "__main__":
    unittest.main()
