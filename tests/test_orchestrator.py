from __future__ import annotations

import tempfile
import unittest
from collections.abc import Awaitable, Callable
from pathlib import Path

from loopai.models import AgentResult, AgentRole, LoopConfig, StreamEvent
from loopai.orchestrator import InitiativeOrchestrator
from loopai.runner import AgentProcessError


class FakeRunner:
    def __init__(
        self,
        results: list[AgentResult],
        persist_completion: bool = True,
        coordinator_actions: list[str] | None = None,
        fail_coordinator_resume: bool = False,
        coordinator_question: str | None = "Which direction should LoopAI take?",
    ) -> None:
        self.results = iter(results)
        self.calls: list[dict[str, object]] = []
        self.persist_completion = persist_completion
        self.coordinator_actions = iter(coordinator_actions or [])
        self.fail_coordinator_resume = fail_coordinator_resume
        self.coordinator_question = coordinator_question

    async def run(
        self,
        *,
        role: AgentRole,
        ticket: Path,
        round_number: int,
        prompt: str,
        session_id: str | None,
        emit: Callable[[StreamEvent], Awaitable[None]],
    ) -> AgentResult:
        self.calls.append(
            {
                "role": role,
                "ticket": ticket,
                "round": round_number,
                "prompt": prompt,
                "session_id": session_id,
            }
        )
        await emit(
            StreamEvent(
                kind="agent.event",
                ticket=ticket,
                role=role,
                round_number=round_number,
                payload={"type": "test.event"},
            )
        )
        if role is AgentRole.COORDINATOR:
            if session_id is not None and self.fail_coordinator_resume:
                self.fail_coordinator_resume = False
                raise AgentProcessError("saved Coordinator session is unavailable")
            marker = "Deterministic safety layer recommendation: "
            recommendation = prompt.split(marker, 1)[1].splitlines()[0]
            action = next(self.coordinator_actions, recommendation)
            ticket_id = None
            ticket_marker = "Candidate ticket id: "
            raw_ticket_id = prompt.split(ticket_marker, 1)[1].splitlines()[0]
            if raw_ticket_id != "none":
                ticket_id = raw_ticket_id
            target_session = None
            if action == "resume-executor":
                value = prompt.split("Executor session id: ", 1)[1].splitlines()[0]
                target_session = None if value == "none" else value
            elif action == "resume-verifier":
                value = prompt.split("Verifier session id: ", 1)[1].splitlines()[0]
                target_session = None if value == "none" else value
            return AgentResult(
                role=role,
                status=action,
                summary=f"Coordinator chose {action}.",
                session_id="coord-1",
                final_output={
                    "action": action,
                    "ticket_id": ticket_id,
                    "session_id": target_session,
                    "reason": f"Coordinator chose {action}.",
                    "feedback": None,
                    "question": self.coordinator_question,
                    "recommended_answer": "Use the safe default.",
                },
            )
        result = next(self.results)
        if (
            self.persist_completion
            and role is AgentRole.VERIFIER
            and result.status == "completed"
        ):
            _mark_completed(ticket)
        return result


def result(role: AgentRole, status: str, summary: str, session: str) -> AgentResult:
    return AgentResult(
        role=role,
        status=status,
        summary=summary,
        session_id=session,
        final_output={"status": status, "summary": summary},
    )


def calls_for(runner: FakeRunner, role: AgentRole) -> list[dict[str, object]]:
    return [call for call in runner.calls if call["role"] is role]


def _mark_completed(ticket: Path) -> None:
    readme = ticket.parent.parent / "README.md"
    updated: list[str] = []
    for line in readme.read_text(encoding="utf-8").splitlines():
        if ticket.name in line and line.startswith("|"):
            columns = line.split("|")
            columns[2] = " completed "
            line = "|".join(columns)
        updated.append(line)
    readme.write_text("\n".join(updated) + "\n", encoding="utf-8")


def create_initiative(root: Path, statuses: tuple[str, str] = ("ready-for-agent", "blocked")) -> Path:
    plan = root / ".scratch" / "demo"
    issues = plan / "issues"
    issues.mkdir(parents=True)
    spec = plan / "spec.md"
    spec.write_text("# Demo spec\n", encoding="utf-8")
    (issues / "01-first.md").write_text("# 01 first\n", encoding="utf-8")
    (issues / "02-second.md").write_text("# 02 second\n", encoding="utf-8")
    (plan / "README.md").write_text(
        "# Frontier\n\n"
        "| Ticket | Status | Blocked by | Verification artifact |\n"
        "|---|---|---|---|\n"
        f"| [01 — first](issues/01-first.md) | {statuses[0]} | none | a.md |\n"
        f"| [02 — second](issues/02-second.md) | {statuses[1]} | 01 | b.md |\n",
        encoding="utf-8",
    )
    return spec


class InitiativeOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def collect(
        self, workspace: Path, runner: FakeRunner, spec: Path | None = None, max_rounds: int = 3
    ) -> list[StreamEvent]:
        config = LoopConfig(workspace=workspace, max_rounds=max_rounds)
        orchestrator = InitiativeOrchestrator(config, runner=runner)  # type: ignore[arg-type]
        return [event async for event in orchestrator.stream(spec)]

    async def test_discovers_spec_and_completes_all_tickets_in_dependency_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            create_initiative(workspace)
            runner = FakeRunner(
                [
                    result(AgentRole.EXECUTOR, "ready-for-verification", "one", "exec-1"),
                    result(AgentRole.VERIFIER, "completed", "one ok", "verify-1"),
                    result(AgentRole.EXECUTOR, "ready-for-verification", "two", "exec-2"),
                    result(AgentRole.VERIFIER, "completed", "two ok", "verify-2"),
                ]
            )

            events = await self.collect(workspace, runner)

        executor_tickets = [
            Path(call["ticket"]).name
            for call in runner.calls
            if call["role"] is AgentRole.EXECUTOR
        ]
        self.assertEqual(executor_tickets, ["01-first.md", "02-second.md"])
        self.assertEqual(events[0].kind, "initiative.started")
        self.assertEqual(events[-1].kind, "initiative.completed")
        self.assertEqual(events[-1].payload["status"], "completed")
        self.assertEqual(events[-1].payload["total"], 2)

    async def test_skips_completed_ticket_and_starts_new_frontier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            create_initiative(workspace, ("completed", "ready-for-agent"))
            runner = FakeRunner(
                [
                    result(AgentRole.EXECUTOR, "ready-for-verification", "two", "exec-2"),
                    result(AgentRole.VERIFIER, "completed", "two ok", "verify-2"),
                ]
            )

            events = await self.collect(workspace, runner)

        self.assertEqual(
            Path(calls_for(runner, AgentRole.EXECUTOR)[0]["ticket"]).name,
            "02-second.md",
        )
        self.assertEqual(events[-1].payload["status"], "completed")

    async def test_ready_for_verification_resumes_with_verifier_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            create_initiative(workspace, ("ready-for-verification", "blocked"))
            runner = FakeRunner(
                [
                    result(AgentRole.VERIFIER, "completed", "one ok", "verify-1"),
                    result(AgentRole.EXECUTOR, "blocked", "stop", "exec-2"),
                ]
            )

            events = await self.collect(workspace, runner)

        worker_calls = [
            call for call in runner.calls if call["role"] is not AgentRole.COORDINATOR
        ]
        self.assertEqual(worker_calls[0]["role"], AgentRole.VERIFIER)
        self.assertEqual(worker_calls[1]["role"], AgentRole.EXECUTOR)
        self.assertEqual(events[-1].payload["stopped_at_ticket"], "02")

    async def test_failed_verification_reacts_with_feedback_and_reuses_two_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            spec = create_initiative(workspace)
            runner = FakeRunner(
                [
                    result(AgentRole.EXECUTOR, "ready-for-verification", "patch", "exec-1"),
                    result(AgentRole.VERIFIER, "incomplete", "missing test", "verify-1"),
                    result(AgentRole.EXECUTOR, "ready-for-verification", "fixed", "exec-1"),
                    result(AgentRole.VERIFIER, "completed", "pass", "verify-1"),
                    result(AgentRole.EXECUTOR, "blocked", "stop", "exec-2"),
                ]
            )

            await self.collect(workspace, runner, spec)

        executor_calls = calls_for(runner, AgentRole.EXECUTOR)
        verifier_calls = calls_for(runner, AgentRole.VERIFIER)
        self.assertEqual(executor_calls[1]["session_id"], "exec-1")
        self.assertEqual(verifier_calls[1]["session_id"], "verify-1")
        self.assertIn("missing test", str(executor_calls[1]["prompt"]))

    async def test_incomplete_executor_retries_same_session_before_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            create_initiative(workspace)
            runner = FakeRunner(
                [
                    result(AgentRole.EXECUTOR, "incomplete", "ruff not run", "exec-1"),
                    result(
                        AgentRole.EXECUTOR,
                        "ready-for-verification",
                        "all checks pass",
                        "exec-1",
                    ),
                    result(AgentRole.VERIFIER, "completed", "verified", "verify-1"),
                    result(AgentRole.EXECUTOR, "blocked", "stop", "exec-2"),
                ]
            )

            events = await self.collect(workspace, runner)

        worker_calls = [
            call for call in runner.calls if call["role"] is not AgentRole.COORDINATOR
        ]
        self.assertEqual(
            [call["role"] for call in worker_calls[:3]],
            [AgentRole.EXECUTOR, AgentRole.EXECUTOR, AgentRole.VERIFIER],
        )
        self.assertEqual(worker_calls[1]["session_id"], "exec-1")
        self.assertIn("ruff not run", str(worker_calls[1]["prompt"]))
        self.assertEqual(events[-1].payload["stopped_at_ticket"], "02")

    async def test_ticket_failure_stops_before_downstream_ticket(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            create_initiative(workspace)
            runner = FakeRunner(
                [result(AgentRole.EXECUTOR, "blocked", "dependency missing", "exec-1")]
            )

            events = await self.collect(workspace, runner)

        self.assertEqual(len(calls_for(runner, AgentRole.EXECUTOR)), 1)
        self.assertEqual(len(calls_for(runner, AgentRole.VERIFIER)), 0)
        self.assertEqual(events[-1].kind, "initiative.completed")
        self.assertEqual(events[-1].payload["status"], "blocked")
        self.assertEqual(events[-1].payload["stopped_at_ticket"], "01")

    async def test_does_not_unlock_downstream_when_verifier_fails_to_persist_tracker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            create_initiative(workspace)
            runner = FakeRunner(
                [
                    result(AgentRole.EXECUTOR, "ready-for-verification", "one", "exec-1"),
                    result(AgentRole.VERIFIER, "completed", "claimed", "verify-1"),
                ],
                persist_completion=False,
            )

            events = await self.collect(workspace, runner)

        self.assertEqual(len(calls_for(runner, AgentRole.EXECUTOR)), 1)
        self.assertEqual(len(calls_for(runner, AgentRole.VERIFIER)), 1)
        self.assertEqual(events[-1].payload["status"], "blocked")
        self.assertIn("did not persist", str(events[-1].payload["summary"]))

    async def test_awaiting_ticket_stops_before_independent_ready_ticket(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            spec = create_initiative(
                workspace, ("awaiting-user-verification", "ready-for-agent")
            )
            readme = spec.parent / "README.md"
            readme.write_text(
                readme.read_text(encoding="utf-8").replace(
                    "| ready-for-agent | 01 | b.md |",
                    "| ready-for-agent | none | b.md |",
                ),
                encoding="utf-8",
            )
            runner = FakeRunner([])

            events = await self.collect(workspace, runner)

        self.assertEqual(calls_for(runner, AgentRole.EXECUTOR), [])
        self.assertEqual(calls_for(runner, AgentRole.VERIFIER), [])
        self.assertEqual(events[-1].payload["status"], "awaiting-user-input")
        self.assertEqual(events[-1].payload["stopped_at_ticket"], "01")

    async def test_reuses_one_coordinator_session_for_all_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            create_initiative(workspace)
            runner = FakeRunner(
                [
                    result(AgentRole.EXECUTOR, "ready-for-verification", "one", "exec-1"),
                    result(AgentRole.VERIFIER, "completed", "one ok", "verify-1"),
                    result(AgentRole.EXECUTOR, "blocked", "stop", "exec-2"),
                ]
            )

            await self.collect(workspace, runner)

        coordinator_calls = calls_for(runner, AgentRole.COORDINATOR)
        self.assertGreaterEqual(len(coordinator_calls), 3)
        self.assertIsNone(coordinator_calls[0]["session_id"])
        self.assertTrue(
            all(call["session_id"] == "coord-1" for call in coordinator_calls[1:])
        )

    async def test_startup_prompt_is_injected_once_per_valid_coordinator_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            create_initiative(workspace)
            runner = FakeRunner(
                [
                    result(AgentRole.EXECUTOR, "ready-for-verification", "one", "exec-1"),
                    result(AgentRole.VERIFIER, "completed", "one ok", "verify-1"),
                    result(AgentRole.EXECUTOR, "blocked", "stop", "exec-2"),
                ]
            )
            config = LoopConfig(
                workspace=workspace,
                coordinator_startup_prompt="请使用中文与用户交互。",
            )
            orchestrator = InitiativeOrchestrator(config, runner=runner)  # type: ignore[arg-type]

            events = [event async for event in orchestrator.stream()]

        del events
        coordinator_calls = calls_for(runner, AgentRole.COORDINATOR)
        prompts = [str(call["prompt"]) for call in coordinator_calls]
        self.assertEqual(
            sum("请使用中文与用户交互。" in prompt for prompt in prompts), 1
        )
        worker_prompts = [
            str(call["prompt"])
            for call in runner.calls
            if call["role"] is not AgentRole.COORDINATOR
        ]
        self.assertTrue(
            all("请使用中文与用户交互。" not in prompt for prompt in worker_prompts)
        )

    async def test_rejects_unsafe_coordinator_transition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            create_initiative(workspace)
            runner = FakeRunner(
                [], coordinator_actions=["complete-initiative"]
            )

            events = await self.collect(workspace, runner)

        self.assertEqual(calls_for(runner, AgentRole.EXECUTOR), [])
        self.assertEqual(events[-1].payload["status"], "blocked")
        self.assertIn("Rejected unsafe coordinator action", events[-1].payload["summary"])

    async def test_noninteractive_question_persists_and_stops_for_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            spec = create_initiative(workspace)
            runner = FakeRunner([], coordinator_actions=["ask-user"])

            events = await self.collect(workspace, runner, spec)
            state = (spec.parent / ".loopai" / "conversation.json").read_text(
                encoding="utf-8"
            )

        self.assertEqual(events[-1].payload["status"], "awaiting-user-input")
        self.assertTrue(any(event.kind == "user.input.required" for event in events))
        self.assertIn("Which direction should LoopAI take?", state)

    async def test_noninteractive_await_user_without_question_persists_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            spec = create_initiative(workspace)
            runner = FakeRunner(
                [],
                coordinator_actions=["await-user"],
                coordinator_question=None,
            )

            events = await self.collect(workspace, runner, spec)
            state = (spec.parent / ".loopai" / "conversation.json").read_text(
                encoding="utf-8"
            )

        self.assertEqual(events[-1].payload["status"], "awaiting-user-input")
        self.assertIn("Coordinator chose await-user", state)
        self.assertIn("How should LoopAI proceed?", state)

    async def test_interactive_await_user_resumes_same_coordinator(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            spec = create_initiative(workspace)
            answers = iter(["Authorize the ticket-scoped test files."])

            async def provide_input(request: dict[str, object]) -> str | None:
                self.assertIn("How should LoopAI proceed?", str(request["question"]))
                return next(answers, None)

            runner = FakeRunner(
                [result(AgentRole.EXECUTOR, "blocked", "stop", "exec-1")],
                coordinator_actions=["await-user", "start-executor"],
                coordinator_question=None,
            )
            orchestrator = InitiativeOrchestrator(
                LoopConfig(workspace=workspace),
                runner=runner,  # type: ignore[arg-type]
                input_provider=provide_input,
            )
            events = [event async for event in orchestrator.stream(spec)]

        coordinator_calls = calls_for(runner, AgentRole.COORDINATOR)
        self.assertIsNone(coordinator_calls[0]["session_id"])
        self.assertEqual(coordinator_calls[1]["session_id"], "coord-1")
        self.assertIn("Authorize the ticket-scoped", coordinator_calls[1]["prompt"])
        self.assertEqual(len(calls_for(runner, AgentRole.EXECUTOR)), 1)
        self.assertEqual(events[-1].payload["status"], "blocked")

    async def test_answer_resumes_same_coordinator_before_worker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            spec = create_initiative(workspace)
            first_runner = FakeRunner([], coordinator_actions=["ask-user"])
            await self.collect(workspace, first_runner, spec)

            answers = iter(["Use the recommended direction."])

            async def provide_input(request: dict[str, object]) -> str | None:
                del request
                return next(answers, None)

            runner = FakeRunner(
                [result(AgentRole.EXECUTOR, "blocked", "stop", "exec-1")]
            )
            config = LoopConfig(workspace=workspace)
            orchestrator = InitiativeOrchestrator(
                config, runner=runner, input_provider=provide_input  # type: ignore[arg-type]
            )
            events = [event async for event in orchestrator.stream(spec)]

        coordinator_calls = calls_for(runner, AgentRole.COORDINATOR)
        self.assertEqual(coordinator_calls[0]["session_id"], "coord-1")
        self.assertIn("Use the recommended direction", coordinator_calls[0]["prompt"])
        self.assertEqual(len(calls_for(runner, AgentRole.EXECUTOR)), 1)
        self.assertEqual(events[-1].payload["status"], "blocked")

    async def test_grill_mode_uses_grilling_skill_and_requires_final_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            spec = create_initiative(workspace)
            answers = iter(["yes", "Choose option A", "yes"])

            async def provide_input(request: dict[str, object]) -> str | None:
                del request
                return next(answers, None)

            runner = FakeRunner(
                [result(AgentRole.EXECUTOR, "blocked", "stop", "exec-1")],
                coordinator_actions=["enter-grill", "ask-user", "start-executor"],
            )
            orchestrator = InitiativeOrchestrator(
                LoopConfig(
                    workspace=workspace,
                    coordinator_startup_prompt="请使用中文与用户交互。",
                ),
                runner=runner,  # type: ignore[arg-type]
                input_provider=provide_input,
            )
            events = [event async for event in orchestrator.stream(spec)]

        coordinator_calls = calls_for(runner, AgentRole.COORDINATOR)
        self.assertTrue(
            str(coordinator_calls[1]["prompt"]).startswith(
                "$mattpocock-skills:grilling\n"
            )
        )
        input_events = [event for event in events if event.kind == "user.input.required"]
        self.assertEqual(len(input_events), 3)
        self.assertEqual(input_events[-1].payload["kind"], "grill-confirmation")

    async def test_stale_coordinator_session_is_replaced_with_persisted_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            spec = create_initiative(workspace)
            first_runner = FakeRunner([], coordinator_actions=["ask-user"])
            await self.collect(workspace, first_runner, spec)

            async def provide_input(request: dict[str, object]) -> str | None:
                del request
                return "Use option A"

            runner = FakeRunner(
                [result(AgentRole.EXECUTOR, "blocked", "stop", "exec-1")],
                fail_coordinator_resume=True,
            )
            orchestrator = InitiativeOrchestrator(
                LoopConfig(
                    workspace=workspace,
                    coordinator_startup_prompt="请使用中文与用户交互。",
                ),
                runner=runner,  # type: ignore[arg-type]
                input_provider=provide_input,
            )
            events = [event async for event in orchestrator.stream(spec)]

        self.assertTrue(
            any(event.kind == "coordinator.session.replaced" for event in events)
        )
        coordinator_calls = calls_for(runner, AgentRole.COORDINATOR)
        self.assertEqual(coordinator_calls[0]["session_id"], "coord-1")
        self.assertIsNone(coordinator_calls[1]["session_id"])
        self.assertIn("recent_answers", coordinator_calls[1]["prompt"])
        self.assertIn("请使用中文与用户交互。", coordinator_calls[1]["prompt"])


if __name__ == "__main__":
    unittest.main()
