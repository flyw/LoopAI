from __future__ import annotations

import tempfile
import unittest
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import patch

from loopai.mcp_server import create_server, run_loopai_once
from loopai.models import StreamEvent


class McpServerTests(unittest.IsolatedAsyncioTestCase):
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

        self.assertEqual([tool.name for tool in listed.tools], ["loopai_run"])
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
