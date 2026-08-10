from pathlib import Path
from unittest import TestCase

from loopai.prompts import (
    coordinator_prompt,
    coordinator_response_prompt,
    executor_prompt,
    verifier_prompt,
)


class AgentPromptTests(TestCase):
    def test_executor_starts_with_explicit_executor_skill_invocation(self) -> None:
        prompt = executor_prompt(Path("ticket.md"), 1, None)

        self.assertTrue(prompt.startswith("$flyw:agent-ticket-executor\n"))
        self.assertNotIn("$flyw:agent-ticket-verifier", prompt)

    def test_verifier_starts_with_explicit_verifier_skill_invocation(self) -> None:
        prompt = verifier_prompt(Path("ticket.md"), 1, "executor handoff")

        self.assertTrue(prompt.startswith("$flyw:agent-ticket-verifier\n"))
        self.assertNotIn("$flyw:agent-ticket-executor", prompt)

    def test_coordinator_starts_with_explicit_orchestrator_skill_invocation(self) -> None:
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

        self.assertTrue(prompt.startswith("$flyw:agent-initiative-orchestrator\n"))
        self.assertIn("start-executor", prompt)
        self.assertIn("ready-for-agent", prompt)

    def test_grill_followup_explicitly_invokes_grilling_skill(self) -> None:
        prompt = coordinator_response_prompt(
            "yes",
            "{}",
            grill_mode=True,
            recommended_action="start-executor",
            ticket_id="01",
            executor_session_id=None,
            verifier_session_id=None,
        )

        self.assertTrue(prompt.startswith("$mattpocock-skills:grilling\n"))
        self.assertIn("Candidate ticket id: 01", prompt)

    def test_coordinator_response_includes_workspace_instructions(self) -> None:
        prompt = coordinator_response_prompt(
            "yes",
            "{}",
            grill_mode=False,
            recommended_action="start-executor",
            ticket_id="01",
            executor_session_id=None,
            verifier_session_id=None,
            startup_prompt="请使用中文提问。",
        )

        self.assertIn("请使用中文提问。", prompt)
        self.assertIn("apply to every Coordinator turn", prompt)

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
            startup_prompt="请使用中文与用户交互。",
        )

        self.assertTrue(prompt.startswith("$flyw:agent-initiative-orchestrator\n"))
        self.assertGreater(
            prompt.index("请使用中文与用户交互。"),
            prompt.index("Python safety layer"),
        )
