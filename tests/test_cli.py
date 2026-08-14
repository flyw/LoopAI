from __future__ import annotations

from collections.abc import AsyncIterator
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from loopai.cli import async_main, build_parser, config_from_args
from loopai.models import StreamEvent


class CliConfigurationTests(unittest.TestCase):
    def test_cli_uses_the_process_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            with patch("loopai.cli.Path.cwd", return_value=working_directory):
                args = build_parser().parse_args(["--answer", "done"])
                config = config_from_args(args)

        self.assertEqual(config.working_directory, working_directory.resolve())

    def test_old_workspace_flag_is_removed(self) -> None:
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["--workspace", "."])

    def test_answer_is_repeatable(self) -> None:
        args = build_parser().parse_args(["--answer", "one", "--answer", "two"])

        self.assertEqual(args.answer, ["one", "two"])


class CliRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_cli_never_reads_terminal_input(self) -> None:
        captured: dict[str, object] = {}

        class FakeOrchestrator:
            def __init__(self, config: object, input_provider: object = None) -> None:
                captured["config"] = config
                captured["input_provider"] = input_provider

            async def stream(self, spec: Path | None = None) -> AsyncIterator[StreamEvent]:
                del spec
                yield StreamEvent(
                    kind="initiative.handoff",
                    payload={"status": "handoff", "cause": "blocked"},
                )

        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            with patch("loopai.cli.Path.cwd", return_value=working_directory):
                with patch("loopai.cli.InitiativeOrchestrator", FakeOrchestrator):
                    with patch(
                        "builtins.input",
                        side_effect=AssertionError("CLI must not read terminal input"),
                    ):
                        status = await async_main([])

        self.assertEqual(status, 1)
        self.assertIsNone(captured["input_provider"])


if __name__ == "__main__":
    unittest.main()
