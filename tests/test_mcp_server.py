from __future__ import annotations

import tempfile
import subprocess
import sys
import unittest
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import patch

from loopai.conversation import ConversationStore
from loopai.mcp_server import (
    create_server,
    get_loopai_status,
    request_loopai_stop,
    run_loopai,
    run_loopai_once,
)
from loopai.models import StreamEvent
from loopai.runtime import RuntimeStateStore
from loopai.worker import run_worker


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
        self.assertIsInstance(result["worker_pid"], int)
        self.assertIsNotNone(result["heartbeat_at"])

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
        self.assertEqual(result["status"], "stop_requested")
        self.assertEqual(result["lifecycle"], "stop_requested")
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

    async def test_background_run_returns_after_starting_one_worker(self) -> None:
        class FakeProcess:
            pid = 43210

        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            spec = create_initiative(working_directory)
            with patch("loopai.mcp_server.Path.cwd", return_value=working_directory):
                with patch(
                    "loopai.mcp_server.subprocess.Popen", return_value=FakeProcess()
                ) as popen:
                    result = await run_loopai(spec=str(spec), wait=False)

            runtime = RuntimeStateStore(spec.parent)
            try:
                self.assertEqual(result["event"], "initiative.accepted")
                self.assertEqual(result["status"], "starting")
                self.assertEqual(result["worker_pid"], 43210)
                command = popen.call_args.args[0]
                self.assertEqual(command[:3], [
                    sys.executable,
                    "-m",
                    "loopai.worker",
                ])
                self.assertEqual(popen.call_args.kwargs["cwd"], str(working_directory))
                self.assertIs(popen.call_args.kwargs["stdin"], subprocess.DEVNULL)
                self.assertIs(popen.call_args.kwargs["stderr"], subprocess.STDOUT)
                self.assertTrue(popen.call_args.kwargs["start_new_session"])
                self.assertTrue(runtime.worker_lock_path.exists())
                self.assertTrue(runtime.worker_log_path.exists())
            finally:
                runtime.release_worker(owner_pid=43210)

    async def test_background_run_rejects_a_second_worker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            spec = create_initiative(working_directory)
            runtime = RuntimeStateStore(spec.parent)
            runtime.reserve_worker()
            runtime.claim_worker()
            try:
                with patch("loopai.mcp_server.Path.cwd", return_value=working_directory):
                    with patch("loopai.mcp_server.subprocess.Popen") as popen:
                        result = await run_loopai(spec=str(spec), wait=False)
                self.assertEqual(result["event"], "initiative.already-running")
                self.assertEqual(result["status"], "running")
                popen.assert_not_called()
            finally:
                runtime.release_worker()

    def test_stale_worker_lock_is_reclaimed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            initiative = Path(directory) / ".scratch" / "demo"
            initiative.mkdir(parents=True)
            runtime = RuntimeStateStore(initiative)
            runtime.worker_lock_path.parent.mkdir(parents=True, exist_ok=True)
            runtime.worker_lock_path.write_text(
                '{"version": 1, "state": "running", "pid": 999999999, '
                '"worker_pid": 999999999}\n',
                encoding="utf-8",
            )

            self.assertIsNotNone(runtime.reserve_worker())
            runtime.release_worker()

    async def test_worker_calls_core_and_releases_its_lock(self) -> None:
        async def fake_run_loopai_once(
            *, spec: str | None = None, answer: str | None = None
        ) -> dict[str, object]:
            return {
                "event": "initiative.ticket-completed",
                "status": "ticket-completed",
                "spec": spec,
                "answer": answer,
            }

        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            spec = create_initiative(working_directory)
            runtime = RuntimeStateStore(spec.parent)
            runtime.reserve_worker()
            runtime.write_worker_request(spec=str(spec), answer=None)

            with patch("loopai.worker.run_loopai_once", fake_run_loopai_once):
                exit_code = await run_worker(
                    initiative=spec.parent,
                    request_file=runtime.worker_request_path,
                )

            self.assertEqual(exit_code, 0)
            self.assertFalse(runtime.worker_lock_path.exists())
            self.assertEqual(
                runtime.read()["last_result"]["status"], "ticket-completed"
            )

    async def test_resume_requires_answer_when_handoff_is_pending(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            spec = create_initiative(working_directory)
            conversation = ConversationStore(spec.parent, working_directory)
            conversation.open()
            conversation.mark_handoff(cause="blocked", summary="Need guidance.")
            conversation.close()

            with patch("loopai.mcp_server.Path.cwd", return_value=working_directory):
                with patch("loopai.mcp_server.subprocess.Popen") as popen:
                    result = await run_loopai(spec=str(spec), wait=False)

            self.assertEqual(result["event"], "initiative.error")
            self.assertEqual(result["cause"], "answer-required")
            popen.assert_not_called()

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
                    spec = create_initiative(Path(directory))
                    async with Client(create_server()) as client:
                        listed = await client.list_tools()
                        result = await client.call_tool(
                            "loopai_run", {"spec": str(spec)}
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
