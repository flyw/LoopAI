import os
import tempfile
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from loopai.models import AgentRole, LoopConfig, StreamEvent
from loopai.runner import AgentProcessError, CodexRunner, _agent_message_text
from loopai.cli import build_parser, config_from_args


class CodexRunnerTests(TestCase):
    def setUp(self) -> None:
        self.working_directory = Path.cwd()
        self.runner = CodexRunner(LoopConfig(working_directory=self.working_directory))

    def test_new_session_command_sets_effort_working_directory_and_automatic_approval(self) -> None:
        command = self.runner.build_command(AgentRole.EXECUTOR)

        self.assertEqual(command[:2], ["codex", "exec"])
        self.assertNotIn("--model", command)
        self.assertIn('model_reasoning_effort="medium"', command)
        self.assertIn("--approve-for-me", command)
        self.assertNotIn("--sandbox", command)
        self.assertIn(str(self.working_directory.resolve()), command)
        self.assertEqual(command[-1], "-")

    def test_resume_command_reuses_exact_agent_session(self) -> None:
        command = self.runner.build_command(AgentRole.VERIFIER, "session-123")

        self.assertEqual(command[:3], ["codex", "exec", "resume"])
        self.assertEqual(command[-2:], ["session-123", "-"])
        self.assertNotIn("--approve-for-me", command)
        self.assertNotIn("--cd", command)

    def test_coordinator_command_selects_coordinator_schema(self) -> None:
        command = self.runner.build_command(AgentRole.COORDINATOR)

        schema = command[command.index("--output-schema") + 1]
        self.assertTrue(schema.endswith("schemas/coordinator.json"))

    def test_each_role_uses_codex_default_model_and_configured_effort(self) -> None:
        for role in AgentRole:
            command = self.runner.build_command(role)
            self.assertNotIn("--model", command)
            self.assertIn('model_reasoning_effort="medium"', command)

    def test_explicit_model_is_passed_to_codex(self) -> None:
        runner = CodexRunner(
            LoopConfig(working_directory=self.working_directory, model="public-model")
        )

        command = runner.build_command(AgentRole.EXECUTOR)

        self.assertEqual(command[command.index("--model") + 1], "public-model")

    def test_automatic_approval_can_be_disabled(self) -> None:
        runner = CodexRunner(
            LoopConfig(working_directory=self.working_directory, automatic_approval=False)
        )

        self.assertNotIn("--approve-for-me", runner.build_command(AgentRole.EXECUTOR))

    def test_role_setting_overrides_global_setting(self) -> None:
        runner = CodexRunner(
            LoopConfig(
                working_directory=self.working_directory,
                model="global-model",
                reasoning_effort="low",
                coordinator_model="coordinator-model",
                coordinator_reasoning_effort="max",
            )
        )

        coordinator = runner.build_command(AgentRole.COORDINATOR)
        executor = runner.build_command(AgentRole.EXECUTOR)
        self.assertIn("coordinator-model", coordinator)
        self.assertIn('model_reasoning_effort="max"', coordinator)
        self.assertIn("global-model", executor)
        self.assertIn('model_reasoning_effort="low"', executor)

    def test_extracts_only_completed_agent_messages(self) -> None:
        self.assertEqual(
            _agent_message_text(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": '{"status":"completed"}'},
                }
            ),
            '{"status":"completed"}',
        )
        self.assertIsNone(_agent_message_text({"type": "turn.completed"}))

    def test_human_readable_output_is_default_and_json_is_opt_in(self) -> None:
        defaults = build_parser().parse_args([])
        json_output = build_parser().parse_args(["--json"])

        self.assertFalse(defaults.json)
        self.assertTrue(json_output.json)
        self.assertTrue(defaults.automatic_approval)
        self.assertFalse(
            build_parser().parse_args(["--no-automatic-approval"]).automatic_approval
        )
        self.assertEqual(build_parser().parse_args(["--answer", "yes"]).answer, ["yes"])

    def test_cli_role_override_beats_global_and_working_directory_toml(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            args = build_parser().parse_args(
                [
                    "--model",
                    "global-model",
                    "--coordinator-model",
                    "role-model",
                ]
            )

            with patch("loopai.cli.Path.cwd", return_value=Path(directory)):
                config = config_from_args(args)

            self.assertEqual(config.model_for(AgentRole.COORDINATOR), "role-model")
            self.assertEqual(config.model_for(AgentRole.EXECUTOR), "global-model")
            self.assertTrue((Path(directory) / ".loopai" / "config.toml").is_file())


class CodexRunnerProcessTests(IsolatedAsyncioTestCase):
    async def test_missing_codex_cli_has_an_actionable_error(self) -> None:
        runner = CodexRunner(
            LoopConfig(working_directory=Path.cwd(), codex_binary="missing-codex-for-test")
        )

        async def emit(event: StreamEvent) -> None:
            del event

        with self.assertRaisesRegex(AgentProcessError, "Codex CLI executable not found"):
            await runner.run(
                role=AgentRole.EXECUTOR,
                ticket=Path("examples/issues/01-greeting.md").resolve(),
                round_number=1,
                prompt="test prompt",
                session_id=None,
                emit=emit,
            )

    async def test_streams_jsonl_and_returns_structured_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fake_codex = Path(directory) / "fake-codex"
            fake_codex.write_text(
                """#!/usr/bin/env python3
import json
import sys

assert sys.stdin.read()
print(json.dumps({"type": "thread.started", "thread_id": "thread-42"}), flush=True)
print(json.dumps({"type": "turn.started"}), flush=True)
print(json.dumps({"type": "item.completed", "item": {"type": "command_execution", "output": "x" * 100000}}), flush=True)
print(json.dumps({
    "type": "item.completed",
    "item": {
        "type": "agent_message",
        "text": json.dumps({"status": "ready-for-verification", "summary": "fresh evidence"}),
    },
}), flush=True)
""",
                encoding="utf-8",
            )
            os.chmod(fake_codex, 0o755)
            runner = CodexRunner(
                LoopConfig(working_directory=Path.cwd(), codex_binary=str(fake_codex))
            )
            events: list[StreamEvent] = []

            async def emit(event: StreamEvent) -> None:
                events.append(event)

            result = await runner.run(
                role=AgentRole.EXECUTOR,
                ticket=Path("examples/issues/01-greeting.md").resolve(),
                round_number=1,
                prompt="test prompt",
                session_id=None,
                emit=emit,
            )

        self.assertEqual(result.session_id, "thread-42")
        self.assertEqual(result.status, "ready-for-verification")
        self.assertEqual(result.summary, "fresh evidence")
        self.assertEqual(len(events), 4)
        self.assertTrue(all(event.kind == "agent.event" for event in events))
