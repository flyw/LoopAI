from __future__ import annotations

import json
import tempfile
import unittest
from collections.abc import Awaitable, Callable
from pathlib import Path

from loopai.models import AgentResult, AgentRole, LoopConfig, StreamEvent
from loopai.orchestrator import InitiativeOrchestrator
from loopai.runner import AgentProcessError
from loopai.runtime import RuntimeStateStore


class FakeRunner:
    def __init__(
        self,
        results: list[AgentResult],
        coordinator_actions: list[str] | None = None,
        fail_coordinator_resume: bool = False,
        coordinator_question: str | None = "Which direction should LoopAI take?",
    ) -> None:
        self.results = iter(results)
        self.calls: list[dict[str, object]] = []
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
        return result


class FailingExecutorRunner(FakeRunner):
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
        if role is AgentRole.EXECUTOR:
            self.calls.append(
                {
                    "role": role,
                    "ticket": ticket,
                    "round": round_number,
                    "prompt": prompt,
                    "session_id": session_id,
                }
            )
            raise AgentProcessError("executor unavailable")
        return await super().run(
            role=role,
            ticket=ticket,
            round_number=round_number,
            prompt=prompt,
            session_id=session_id,
            emit=emit,
        )


class StopAfterExecutorRunner(FakeRunner):
    def __init__(self, initiative: Path, results: list[AgentResult]) -> None:
        super().__init__(results)
        self.initiative = initiative

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
        if role is AgentRole.EXECUTOR:
            RuntimeStateStore(self.initiative).request_stop(
                "Verifier left the ticket scope."
            )
        return await super().run(
            role=role,
            ticket=ticket,
            round_number=round_number,
            prompt=prompt,
            session_id=session_id,
            emit=emit,
        )


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


def create_initiative(root: Path, statuses: tuple[str, str] = ("ready-for-agent", "blocked")) -> Path:
    plan = root / ".scratch" / "demo"
    issues = plan / "issues"
    issues.mkdir(parents=True)
    spec = plan / "spec.md"
    spec.write_text("# Demo spec\n", encoding="utf-8")
    (issues / "01-first.md").write_text(
        "# 01 first\n\n**Status:** "
        f"{statuses[0]}\n\n**Blocked by:** None\n",
        encoding="utf-8",
    )
    (issues / "02-second.md").write_text(
        "# 02 second\n\n**Status:** "
        f"{statuses[1]}\n\n**Blocked by:** 01\n",
        encoding="utf-8",
    )
    return spec


class InitiativeOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def collect(
        self, working_directory: Path, runner: FakeRunner, spec: Path | None = None, max_rounds: int = 3
    ) -> list[StreamEvent]:
        config = LoopConfig(working_directory=working_directory, max_rounds=max_rounds)
        orchestrator = InitiativeOrchestrator(config, runner=runner)  # type: ignore[arg-type]
        return [event async for event in orchestrator.stream(spec)]

    async def test_completes_one_ticket_per_invocation_and_resumes_next_ticket(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            create_initiative(working_directory)
            runner = FakeRunner(
                [
                    result(AgentRole.EXECUTOR, "ready-for-verification", "one", "exec-1"),
                    result(AgentRole.VERIFIER, "completed", "one ok", "verify-1"),
                    result(AgentRole.EXECUTOR, "ready-for-verification", "two", "exec-2"),
                    result(AgentRole.VERIFIER, "completed", "two ok", "verify-2"),
                ]
            )

            first_events = await self.collect(working_directory, runner)
            first_status = (working_directory / "LOOPAI_STATUS.md").read_text(
                encoding="utf-8"
            )
            second_events = await self.collect(working_directory, runner)
            second_status = (working_directory / "LOOPAI_STATUS.md").read_text(
                encoding="utf-8"
            )

        executor_tickets = [
            Path(call["ticket"]).name
            for call in runner.calls
            if call["role"] is AgentRole.EXECUTOR
        ]
        self.assertEqual(executor_tickets, ["01-first.md", "02-second.md"])
        self.assertEqual(first_events[0].kind, "initiative.started")
        self.assertEqual(first_events[-1].kind, "initiative.ticket-completed")
        self.assertEqual(first_events[-1].payload["status"], "ticket-completed")
        self.assertEqual(first_events[-1].payload["current_ticket_id"], "01")
        self.assertEqual(first_events[-1].payload["completed"], 1)
        self.assertIn("Status: `ticket-completed`", first_status)
        self.assertIn("Run LoopAI again", first_status)
        self.assertEqual(second_events[-1].kind, "initiative.completed")
        self.assertEqual(second_events[-1].payload["status"], "completed")
        self.assertEqual(second_events[-1].payload["total"], 2)
        self.assertIn("Status: `completed`", second_status)
        self.assertIn("The initiative completed successfully.", second_status)

    async def test_skips_completed_ticket_and_starts_new_frontier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            create_initiative(working_directory, ("completed", "ready-for-agent"))
            runner = FakeRunner(
                [
                    result(AgentRole.EXECUTOR, "ready-for-verification", "two", "exec-2"),
                    result(AgentRole.VERIFIER, "completed", "two ok", "verify-2"),
                ]
            )

            events = await self.collect(working_directory, runner)

        self.assertEqual(
            Path(calls_for(runner, AgentRole.EXECUTOR)[0]["ticket"]).name,
            "02-second.md",
        )
        self.assertEqual(events[-1].payload["status"], "completed")

    async def test_ready_for_verification_resumes_with_verifier_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            create_initiative(working_directory, ("ready-for-verification", "blocked"))
            runner = FakeRunner(
                [
                    result(AgentRole.VERIFIER, "completed", "one ok", "verify-1"),
                    result(AgentRole.EXECUTOR, "blocked", "stop", "exec-2"),
                ]
            )

            events = await self.collect(working_directory, runner)

        worker_calls = [
            call for call in runner.calls if call["role"] is not AgentRole.COORDINATOR
        ]
        self.assertEqual(worker_calls[0]["role"], AgentRole.VERIFIER)
        self.assertEqual(len(worker_calls), 1)
        self.assertEqual(events[-1].kind, "initiative.ticket-completed")
        self.assertEqual(events[-1].payload["current_ticket_id"], "01")
        self.assertEqual(events[-1].payload["status"], "ticket-completed")

    async def test_failed_verification_reacts_with_feedback_and_reuses_two_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            spec = create_initiative(working_directory)
            runner = FakeRunner(
                [
                    result(AgentRole.EXECUTOR, "ready-for-verification", "patch", "exec-1"),
                    result(AgentRole.VERIFIER, "incomplete", "missing test", "verify-1"),
                    result(AgentRole.EXECUTOR, "ready-for-verification", "fixed", "exec-1"),
                    result(AgentRole.VERIFIER, "completed", "pass", "verify-1"),
                    result(AgentRole.EXECUTOR, "blocked", "stop", "exec-2"),
                ]
            )

            await self.collect(working_directory, runner, spec)

        executor_calls = calls_for(runner, AgentRole.EXECUTOR)
        verifier_calls = calls_for(runner, AgentRole.VERIFIER)
        self.assertEqual(executor_calls[1]["session_id"], "exec-1")
        self.assertEqual(verifier_calls[1]["session_id"], "verify-1")
        self.assertIn("missing test", str(executor_calls[1]["prompt"]))
        self.assertEqual({Path(call["ticket"]).name for call in executor_calls}, {"01-first.md"})

    async def test_incomplete_executor_retries_same_session_before_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            create_initiative(working_directory)
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

            events = await self.collect(working_directory, runner)

        worker_calls = [
            call for call in runner.calls if call["role"] is not AgentRole.COORDINATOR
        ]
        self.assertEqual(
            [call["role"] for call in worker_calls[:3]],
            [AgentRole.EXECUTOR, AgentRole.EXECUTOR, AgentRole.VERIFIER],
        )
        self.assertEqual(worker_calls[1]["session_id"], "exec-1")
        self.assertIn("ruff not run", str(worker_calls[1]["prompt"]))
        self.assertEqual(events[-1].kind, "initiative.ticket-completed")
        self.assertEqual(events[-1].payload["current_ticket_id"], "01")
        self.assertEqual(events[-1].payload["status"], "ticket-completed")

    async def test_ticket_failure_stops_before_downstream_ticket(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            create_initiative(working_directory)
            runner = FakeRunner(
                [result(AgentRole.EXECUTOR, "blocked", "dependency missing", "exec-1")]
            )

            events = await self.collect(working_directory, runner)

        self.assertEqual(len(calls_for(runner, AgentRole.EXECUTOR)), 1)
        self.assertEqual(len(calls_for(runner, AgentRole.VERIFIER)), 0)
        self.assertEqual(events[-1].kind, "initiative.handoff")
        self.assertEqual(events[-1].payload["status"], "handoff")
        self.assertEqual(events[-1].payload["cause"], "blocked")
        self.assertEqual(events[-1].payload["current_ticket_id"], "01")

    async def test_operator_stop_persists_correctable_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            spec = create_initiative(working_directory)
            runner = StopAfterExecutorRunner(
                spec.parent,
                [result(AgentRole.EXECUTOR, "ready-for-verification", "patch", "exec-1")],
            )

            events = await self.collect(working_directory, runner, spec)
            status = (working_directory / "LOOPAI_STATUS.md").read_text(
                encoding="utf-8"
            )
            runtime = json.loads(
                (spec.parent / ".loopai" / "runtime.json").read_text(encoding="utf-8")
            )

        self.assertEqual(events[-1].kind, "initiative.handoff")
        self.assertEqual(events[-1].payload["cause"], "operator-stop")
        self.assertIn("Verifier left the ticket scope", events[-1].payload["summary"])
        self.assertIn("corrected guidance", status)
        self.assertEqual(runtime["lifecycle"], "stopped")
        self.assertFalse((spec.parent / ".loopai" / "control.json").exists())

    async def test_operator_stop_resumes_with_corrected_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            spec = create_initiative(working_directory)
            first_runner = StopAfterExecutorRunner(
                spec.parent,
                [result(AgentRole.EXECUTOR, "ready-for-verification", "patch", "exec-1")],
            )
            await self.collect(working_directory, first_runner, spec)

            async def provide_input(request: dict[str, object]) -> str | None:
                del request
                return "Re-read the ticket scope and resume verification."

            second_runner = FakeRunner(
                [result(AgentRole.VERIFIER, "completed", "verified", "verify-1")]
            )
            orchestrator = InitiativeOrchestrator(
                LoopConfig(working_directory=working_directory),
                runner=second_runner,  # type: ignore[arg-type]
                input_provider=provide_input,
            )
            events = [event async for event in orchestrator.stream(spec)]

        coordinator_calls = calls_for(second_runner, AgentRole.COORDINATOR)
        self.assertIn("Re-read the ticket scope", str(coordinator_calls[0]["prompt"]))
        self.assertEqual(len(calls_for(second_runner, AgentRole.EXECUTOR)), 0)
        self.assertEqual(events[-1].kind, "initiative.ticket-completed")

    async def test_agent_process_error_is_summarized_and_handed_off(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            create_initiative(working_directory)
            runner = FailingExecutorRunner([], coordinator_actions=["start-executor", "stop"])

            events = await self.collect(working_directory, runner)
            status = (working_directory / "LOOPAI_STATUS.md").read_text(
                encoding="utf-8"
            )

        self.assertEqual(events[-1].kind, "initiative.handoff")
        self.assertEqual(events[-1].payload["status"], "handoff")
        self.assertEqual(events[-1].payload["cause"], "blocked")
        coordinator_calls = calls_for(runner, AgentRole.COORDINATOR)
        self.assertIn("Executor agent failed", str(coordinator_calls[-1]["prompt"]))
        self.assertIn("loopai --answer", status)

    async def test_verifier_completion_is_persisted_by_loopai(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            spec = create_initiative(working_directory, ("ready-for-agent", "completed"))
            runner = FakeRunner(
                [
                    result(AgentRole.EXECUTOR, "ready-for-verification", "one", "exec-1"),
                    result(AgentRole.VERIFIER, "completed", "claimed", "verify-1"),
                ],
            )

            events = await self.collect(working_directory, runner)

            tracker = spec.parent / ".loopai" / "execution.json"
            payload = json.loads(tracker.read_text(encoding="utf-8"))
        self.assertEqual(payload["tickets"][0]["status"], "completed")
        self.assertEqual(events[-1].payload["status"], "completed")

    async def test_awaiting_ticket_stops_before_independent_ready_ticket(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            spec = create_initiative(
                working_directory, ("awaiting-user-verification", "ready-for-agent")
            )
            second_ticket = spec.parent / "issues" / "02-second.md"
            second_ticket.write_text(
                second_ticket.read_text(encoding="utf-8").replace(
                    "**Blocked by:** 01", "**Blocked by:** None"
                ),
                encoding="utf-8",
            )
            runner = FakeRunner([])

            events = await self.collect(working_directory, runner)

        self.assertEqual(calls_for(runner, AgentRole.EXECUTOR), [])
        self.assertEqual(calls_for(runner, AgentRole.VERIFIER), [])
        self.assertEqual(events[-1].payload["status"], "handoff")
        self.assertEqual(events[-1].payload["cause"], "awaiting-user-input")
        self.assertEqual(events[-1].payload["current_ticket_id"], "01")

    async def test_reuses_one_coordinator_session_for_all_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            create_initiative(working_directory)
            runner = FakeRunner(
                [
                    result(AgentRole.EXECUTOR, "ready-for-verification", "one", "exec-1"),
                    result(AgentRole.VERIFIER, "completed", "one ok", "verify-1"),
                    result(AgentRole.EXECUTOR, "blocked", "stop", "exec-2"),
                ]
            )

            await self.collect(working_directory, runner)

        coordinator_calls = calls_for(runner, AgentRole.COORDINATOR)
        self.assertEqual(len(coordinator_calls), 2)
        self.assertIsNone(coordinator_calls[0]["session_id"])
        self.assertTrue(
            all(call["session_id"] == "coord-1" for call in coordinator_calls[1:])
        )

    async def test_startup_prompt_is_injected_on_every_coordinator_turn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            create_initiative(working_directory)
            runner = FakeRunner(
                [
                    result(AgentRole.EXECUTOR, "ready-for-verification", "one", "exec-1"),
                    result(AgentRole.VERIFIER, "completed", "one ok", "verify-1"),
                    result(AgentRole.EXECUTOR, "blocked", "stop", "exec-2"),
                ]
            )
            config = LoopConfig(
                working_directory=working_directory,
                coordinator_startup_prompt="Use concise English.",
            )
            orchestrator = InitiativeOrchestrator(config, runner=runner)  # type: ignore[arg-type]

            events = [event async for event in orchestrator.stream()]

        del events
        coordinator_calls = calls_for(runner, AgentRole.COORDINATOR)
        prompts = [str(call["prompt"]) for call in coordinator_calls]
        self.assertEqual(
            sum("Use concise English." in prompt for prompt in prompts),
            len(prompts),
        )
        worker_prompts = [
            str(call["prompt"])
            for call in runner.calls
            if call["role"] is not AgentRole.COORDINATOR
        ]
        self.assertTrue(
            all("Use concise English." not in prompt for prompt in worker_prompts)
        )

    async def test_rejects_unsafe_coordinator_transition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            create_initiative(working_directory)
            runner = FakeRunner(
                [], coordinator_actions=["complete-initiative"]
            )

            events = await self.collect(working_directory, runner)

        self.assertEqual(calls_for(runner, AgentRole.EXECUTOR), [])
        self.assertEqual(events[-1].payload["status"], "handoff")
        self.assertEqual(events[-1].payload["cause"], "blocked")
        self.assertIn("Rejected unsafe coordinator action", events[-1].payload["summary"])

    async def test_noninteractive_question_persists_and_stops_for_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            spec = create_initiative(working_directory)
            runner = FakeRunner([], coordinator_actions=["ask-user"])

            events = await self.collect(working_directory, runner, spec)
            state = (spec.parent / ".loopai" / "conversation.json").read_text(
                encoding="utf-8"
            )
            conversation = json.loads(state)
            handoff = (working_directory / "LOOPAI_STATUS.md").read_text(
                encoding="utf-8"
            )

        self.assertEqual(events[-1].payload["status"], "handoff")
        self.assertEqual(events[-1].payload["cause"], "awaiting-user-input")
        self.assertTrue(any(event.kind == "user.input.required" for event in events))
        self.assertIn("Which direction should LoopAI take?", state)
        self.assertEqual(conversation["pending"]["kind"], "handoff")
        self.assertEqual(conversation["pending"]["original_kind"], "ask-user")
        self.assertIn("Status: `handoff`", handoff)
        self.assertIn("Cause: `awaiting-user-input`", handoff)
        self.assertIn("Coordinator chose ask-user.", handoff)
        self.assertIn("loopai --answer", handoff)

    async def test_input_provider_runs_after_required_event_is_delivered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            spec = create_initiative(working_directory)
            runner = FakeRunner([], coordinator_actions=["ask-user"])
            delivered: list[str] = []

            async def provide_input(request: dict[str, object]) -> str | None:
                del request
                self.assertIn("initiative.started", delivered)
                self.assertIn("user.input.required", delivered)
                return None

            orchestrator = InitiativeOrchestrator(
                LoopConfig(working_directory=working_directory),
                runner=runner,  # type: ignore[arg-type]
                input_provider=provide_input,
            )
            async for event in orchestrator.stream(spec):
                delivered.append(event.kind)

    async def test_noninteractive_await_user_without_question_persists_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            spec = create_initiative(working_directory)
            runner = FakeRunner(
                [],
                coordinator_actions=["await-user"],
                coordinator_question=None,
            )

            events = await self.collect(working_directory, runner, spec)
            state = (spec.parent / ".loopai" / "conversation.json").read_text(
                encoding="utf-8"
            )

        self.assertEqual(events[-1].payload["status"], "handoff")
        self.assertEqual(events[-1].payload["cause"], "awaiting-user-input")
        self.assertIn("Coordinator chose await-user", state)
        self.assertIn("How should LoopAI proceed?", state)

    async def test_interactive_await_user_resumes_same_coordinator(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            spec = create_initiative(working_directory)
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
                LoopConfig(working_directory=working_directory),
                runner=runner,  # type: ignore[arg-type]
                input_provider=provide_input,
            )
            events = [event async for event in orchestrator.stream(spec)]

        coordinator_calls = calls_for(runner, AgentRole.COORDINATOR)
        self.assertIsNone(coordinator_calls[0]["session_id"])
        self.assertEqual(coordinator_calls[1]["session_id"], "coord-1")
        self.assertIn("Authorize the ticket-scoped", coordinator_calls[1]["prompt"])
        self.assertEqual(len(calls_for(runner, AgentRole.EXECUTOR)), 1)
        self.assertEqual(events[-1].payload["status"], "handoff")
        self.assertEqual(events[-1].payload["cause"], "blocked")

    async def test_answer_resumes_same_coordinator_before_worker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            spec = create_initiative(working_directory)
            first_runner = FakeRunner([], coordinator_actions=["ask-user"])
            await self.collect(working_directory, first_runner, spec)

            answers = iter(["Use the recommended direction."])

            async def provide_input(request: dict[str, object]) -> str | None:
                del request
                return next(answers, None)

            runner = FakeRunner(
                [result(AgentRole.EXECUTOR, "blocked", "stop", "exec-1")]
            )
            config = LoopConfig(working_directory=working_directory)
            orchestrator = InitiativeOrchestrator(
                config, runner=runner, input_provider=provide_input  # type: ignore[arg-type]
            )
            events = [event async for event in orchestrator.stream(spec)]

        coordinator_calls = calls_for(runner, AgentRole.COORDINATOR)
        self.assertEqual(coordinator_calls[0]["session_id"], "coord-1")
        self.assertIn("Use the recommended direction", coordinator_calls[0]["prompt"])
        self.assertEqual(len(calls_for(runner, AgentRole.EXECUTOR)), 1)
        self.assertEqual(events[-1].payload["status"], "handoff")
        self.assertEqual(events[-1].payload["cause"], "blocked")

    async def test_grill_mode_uses_built_in_instructions_and_requires_final_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            spec = create_initiative(working_directory)
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
                    working_directory=working_directory,
                    coordinator_startup_prompt="Use concise English.",
                ),
                runner=runner,  # type: ignore[arg-type]
                input_provider=provide_input,
            )
            events = [event async for event in orchestrator.stream(spec)]

        coordinator_calls = calls_for(runner, AgentRole.COORDINATOR)
        self.assertTrue(
            str(coordinator_calls[1]["prompt"]).startswith(
                "Role: Planner (Grill mode)\n"
            )
        )
        input_events = [event for event in events if event.kind == "user.input.required"]
        self.assertEqual(len(input_events), 3)
        self.assertEqual(input_events[-1].payload["kind"], "grill-confirmation")

    async def test_stale_coordinator_session_is_replaced_with_persisted_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            spec = create_initiative(working_directory)
            first_runner = FakeRunner([], coordinator_actions=["ask-user"])
            await self.collect(working_directory, first_runner, spec)

            async def provide_input(request: dict[str, object]) -> str | None:
                del request
                return "Use option A"

            runner = FakeRunner(
                [result(AgentRole.EXECUTOR, "blocked", "stop", "exec-1")],
                fail_coordinator_resume=True,
            )
            orchestrator = InitiativeOrchestrator(
                LoopConfig(
                    working_directory=working_directory,
                    coordinator_startup_prompt="Use concise English.",
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
        self.assertIn("Use concise English.", coordinator_calls[1]["prompt"])


if __name__ == "__main__":
    unittest.main()
