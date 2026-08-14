from pathlib import Path
from unittest import TestCase

from loopai.prompts import (
    coordinator_prompt,
    coordinator_response_prompt,
    executor_prompt,
    verifier_prompt,
)


class AgentPromptTests(TestCase):
    def test_executor_prompt_is_self_contained(self) -> None:
        prompt = executor_prompt(Path("ticket.md"), 1, None)

        self.assertTrue(prompt.startswith("Role: Executor\n"))
        self.assertIn("Implement the requested change", prompt)

    def test_verifier_prompt_is_self_contained(self) -> None:
        prompt = verifier_prompt(Path("ticket.md"), 1, "executor handoff")

        self.assertTrue(prompt.startswith("Role: Independent Verifier\n"))
        self.assertIn("Treat the report as a claim", prompt)

    def test_coordinator_prompt_is_self_contained(self) -> None:
        prompt = coordinator_prompt(
            spec=Path("spec.md"),
            execution_map=Path(".loopai/execution.json"),
            ticket=Path("ticket.md"),
            ticket_id="01",
            tracker_status="ready-for-agent",
            recommended_action="start-executor",
            observation="No prior agent result.",
            executor_session_id=None,
            verifier_session_id=None,
        )

        self.assertTrue(prompt.startswith("Role: Planner\n"))
        self.assertIn("start-executor", prompt)
        self.assertIn("ready-for-agent", prompt)

    def test_grill_followup_uses_built_in_grill_instructions(self) -> None:
        prompt = coordinator_response_prompt(
            "yes",
            "{}",
            grill_mode=True,
            recommended_action="start-executor",
            ticket_id="01",
            executor_session_id=None,
            verifier_session_id=None,
        )

        self.assertTrue(prompt.startswith("Role: Planner (Grill mode)\n"))
        self.assertIn("Candidate ticket id: 01", prompt)

    def test_coordinator_response_includes_working_directory_instructions(self) -> None:
        prompt = coordinator_response_prompt(
            "yes",
            "{}",
            grill_mode=False,
            recommended_action="start-executor",
            ticket_id="01",
            executor_session_id=None,
            verifier_session_id=None,
            startup_prompt="Use concise questions.",
        )

        self.assertIn("Use concise questions.", prompt)
        self.assertIn("apply to every Planner turn", prompt)

    def test_coordinator_startup_prompt_follows_fixed_instructions(self) -> None:
        prompt = coordinator_prompt(
            spec=Path("spec.md"),
            execution_map=Path(".loopai/execution.json"),
            ticket=Path("ticket.md"),
            ticket_id="01",
            tracker_status="ready-for-agent",
            recommended_action="start-executor",
            observation="Inspect state.",
            executor_session_id=None,
            verifier_session_id=None,
            startup_prompt="Use concise English.",
        )

        self.assertTrue(prompt.startswith("Role: Planner\n"))
        self.assertGreater(
            prompt.index("Use concise English."),
            prompt.index("safety layer"),
        )
