from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from loopai.conversation import ConversationStore, InitiativeAlreadyRunningError


class ConversationStoreTests(unittest.TestCase):
    def test_state_is_isolated_per_initiative_and_persists_answers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            first = workspace / "one"
            second = workspace / "two"
            first.mkdir()
            second.mkdir()
            store = ConversationStore(first, workspace)
            store.open()
            store.require_input(question="Choose", recommended_answer="A", kind="ask-user")
            store.record_answer("A")
            store.set_session("coord-1")
            store.close()

            reopened = ConversationStore(first, workspace)
            reopened.open()
            other = ConversationStore(second, workspace)
            other.open()
            try:
                self.assertEqual(reopened.coordinator_session_id, "coord-1")
                self.assertIn('"answer": "A"', reopened.context())
                self.assertNotEqual(reopened.directory, other.directory)
            finally:
                reopened.close()
                other.close()

    def test_live_lock_rejects_second_process_for_same_initiative(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            initiative = workspace / "one"
            initiative.mkdir()
            first = ConversationStore(initiative, workspace)
            first.open()
            try:
                with self.assertRaises(InitiativeAlreadyRunningError):
                    ConversationStore(initiative, workspace).open()
            finally:
                first.close()


if __name__ == "__main__":
    unittest.main()
