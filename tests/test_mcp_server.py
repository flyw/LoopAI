from __future__ import annotations

import tempfile
import unittest
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import patch

from loopai.conversation import ConversationStore
from loopai.mcp_server import (
    create_server,
    get_loopai_status,
    request_loopai_stop,
    run_loopai_once,
)
from loopai.models import StreamEvent
from loopai.runtime import RuntimeStateStore


def create_initiative(root: Path) -> Path:
    plan = root / ".scratch" / "demo"
    issues = plan / "issues"
    issues.mkdir(parents=True)
    spec = plan / "spec.md"
    spec.write_text("# Demo spec\n", encoding="utf-8")
    (issues / "01-first.md").write_text(
        "# 01 first\n\n**Status:** ready-for-agent\n\n**Blocked by:** None\n",
        encoding="utf-8",
    )
    (issues / "02-second.md").write_text(
        "# 02 second\n\n**Status:** blocked\n\n**Blocked by:** 01\n",
        encoding="utf-8",
    )
    return spec


class McpServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_loopai_status_reports_live_runtime_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            spec = create_initiative(working_directory)
            conversation = ConversationStore(spec.parent, working_directory)
            conversation.open()
            runtime = RuntimeStateStore(spec.parent)
            runtime.start(
                spec=str(spec.resolve()),
                execution_map=str((spec.parent / ".loopai" / "execution.json").resolve()),
                current_ticket_id="01",
                current_ticket_path=str((spec.parent / "issues" / "01-first.md").resolve()),
                phase="verifier",
                role="verifier",
                round=2,
                last_event="verifier.started",
            )
            try:
                with patch("loopai.mcp_server.Path.cwd", return_value=working_directory):
                    result = await get_loopai_status(spec=str(spec))
            finally:
                conversation.close()

        self.assertEqual(result["event"], "initiative.status")
        self.assertEqual(result["status"], "running")
        self.assertTrue(result["active"])
        self.assertEqual(result["phase"], "verifier")
        self.assertEqual(result["current_ticket_id"], "01")
        self.assertEqual(result["round"], 2)

    async def test_loopai_stop_requests_safe_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            spec = create_initiative(working_directory)
            conversation = ConversationStore(spec.parent, working_directory)
            conversation.open()
            runtime = RuntimeStateStore(spec.parent)
            runtime.start(spec=str(spec.resolve()), phase="executor")
            try:
                with patch("loopai.mcp_server.Path.cwd", return_value=working_directory):
                    result = await request_loopai_stop(
                        spec=str(spec), reason="Verifier left the ticket scope."
                    )
                request = runtime.stop_request()
            finally:
                runtime.clear_stop_request()
                conversation.close()

        self.assertEqual(result["event"], "initiative.stop-requested")
        self.assertEqual(result["status"], "stop-requested")
        self.assertEqual(result["stop_reason"], "Verifier left the ticket scope.")
        self.assertIsNotNone(request)

    async def test_run_loopai_once_returns_ticket_completion_boundary(self) -> None:
        class FakeOrchestrator:
            def __init__(self, config: object, input_provider: object = None) -> None:
                del config, input_provider

            async def stream(self, spec: Path | None = None):
                del spec
                yield StreamEvent(
                    kind="initiative.ticket-completed",
                    payload={
                        "status": "ticket-completed",
                        "cause": "ticket-completed",
                        "completed": 1,
                        "total": 2,
                        "current_ticket_id": "01",
                        "summary": "The first ticket passed verification.",
                        "status_file": "/tmp/project/LOOPAI_STATUS.md",
                    },
                )

        with tempfile.TemporaryDirectory() as directory:
            with patch("loopai.mcp_server.Path.cwd", return_value=Path(directory)):
                with patch("loopai.mcp_server.InitiativeOrchestrator", FakeOrchestrator):
                    result = await run_loopai_once(spec="examples/spec.md")

        self.assertEqual(result["event"], "initiative.ticket-completed")
        self.assertEqual(result["status"], "ticket-completed")
        self.assertEqual(result["current_ticket_id"], "01")
        self.assertIn("next dependency-ready ticket", result["next_action"])

    async def test_run_loopai_once_returns_compact_handoff_result(self) -> None:
        class FakeOrchestrator:
            def __init__(self, config: object, input_provider: object = None) -> None:
                del config, input_provider

            async def stream(self, spec: Path | None = None):
                del spec
                yield StreamEvent(
                    kind="initiative.handoff",
                    payload={
                        "status": "handoff",
                        "cause": "blocked",
                        "completed": 1,
                        "total": 2,
                        "current_ticket_id": "02",
                        "summary": "External evidence is required.",
                        "status_file": "/tmp/project/LOOPAI_STATUS.md",
                        "pending": {"kind": "handoff"},
                    },
                )

        with tempfile.TemporaryDirectory() as directory:
            with patch("loopai.mcp_server.Path.cwd", return_value=Path(directory)):
                with patch("loopai.mcp_server.InitiativeOrchestrator", FakeOrchestrator):
                    result = await run_loopai_once(spec="examples/spec.md")

        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["status"], "handoff")
        self.assertEqual(result["cause"], "blocked")
        self.assertEqual(result["current_ticket_id"], "02")
        self.assertIn("loopai_run", result["next_action"])

    async def test_mcp_protocol_exposes_and_calls_loopai_run(self) -> None:
        if find_spec("mcp") is None:
            self.skipTest("MCP extra is not installed")

        from mcp import Client

        class FakeOrchestrator:
            def __init__(self, config: object, input_provider: object = None) -> None:
                del config, input_provider

            async def stream(self, spec: Path | None = None):
                del spec
                yield StreamEvent(
                    kind="initiative.handoff",
                    payload={
                        "status": "handoff",
                        "cause": "blocked",
                        "completed": 0,
                        "total": 1,
                        "current_ticket_id": "01",
                        "summary": "External action is required.",
                    },
                )

        with tempfile.TemporaryDirectory() as directory:
            with patch("loopai.mcp_server.Path.cwd", return_value=Path(directory)):
                with patch("loopai.mcp_server.InitiativeOrchestrator", FakeOrchestrator):
                    async with Client(create_server()) as client:
                        listed = await client.list_tools()
                        result = await client.call_tool(
                            "loopai_run", {"spec": "spec.md"}
                        )

        self.assertEqual(
            [tool.name for tool in listed.tools],
            ["loopai_run", "loopai_status", "loopai_stop"],
        )
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["status"], "handoff")
        self.assertEqual(result.structured_content["schema_version"], 1)

    def test_mcp_dependency_is_optional_for_cli_users(self) -> None:
        if find_spec("mcp") is None:
            with self.assertRaisesRegex(RuntimeError, "MCP support is optional"):
                create_server()
        else:
            self.assertIsNotNone(create_server())


if __name__ == "__main__":
    unittest.main()
