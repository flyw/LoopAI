from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Optional
from pathlib import Path

from .conversation import ConversationStore
from .frontier import Frontier, TicketRecord
from .models import AgentRole, LoopConfig, StreamEvent, TicketResult
from .prompts import (
    coordinator_prompt,
    coordinator_response_prompt,
    executor_prompt,
    verifier_prompt,
)
from .runner import AgentProcessError, CodexRunner
from .runtime import RuntimeStateStore
from .status import StatusFile

_DONE = object()
InputProvider = Callable[[dict[str, Any]], Awaitable[Optional[str]]]
_OPERATOR_STOP_QUESTION = (
    "LoopAI was stopped by the Outer Agent. Review the current repository and provide "
    "corrected guidance before resuming."
)


class InitiativeOrchestrator:
    """Completes at most one ticket per invocation of the orchestration loop."""

    def __init__(
        self,
        config: LoopConfig,
        runner: CodexRunner | None = None,
        input_provider: InputProvider | None = None,
    ) -> None:
        self.config = config
        self.runner = runner or CodexRunner(config)
        self.input_provider = input_provider
        self._conversation: ConversationStore | None = None
        self._runtime: RuntimeStateStore | None = None

    async def stream(self, spec: Path | None = None) -> AsyncIterator[StreamEvent]:
        queue: asyncio.Queue[StreamEvent | object] = asyncio.Queue()
        failure: BaseException | None = None

        async def emit(event: StreamEvent) -> None:
            await queue.put(event)
            # Let the stream consumer render queued progress before a worker-side
            # input provider writes its prompt directly to the terminal.
            await asyncio.sleep(0)

        async def work() -> None:
            nonlocal failure
            try:
                await self._run_initiative(spec, emit)
            except BaseException as error:
                if self._runtime is not None and not isinstance(
                    error, asyncio.CancelledError
                ):
                    self._runtime.update(
                        lifecycle="error",
                        phase="error",
                        last_event="orchestrator.error",
                        summary=str(error),
                    )
                failure = error
            finally:
                if self._conversation is not None:
                    self._conversation.close()
                    self._conversation = None
                self._runtime = None
                await queue.put(_DONE)

        task = asyncio.create_task(work())
        try:
            while True:
                event = await queue.get()
                if event is _DONE:
                    break
                assert isinstance(event, StreamEvent)
                yield event
            await task
            if failure is not None:
                raise failure
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def _run_initiative(
        self,
        spec: Path | None,
        emit: Callable[[StreamEvent], Awaitable[None]],
    ) -> None:
        frontier = Frontier.discover(self.config.working_directory, spec)
        conversation = ConversationStore(
            frontier.spec.parent, self.config.working_directory
        )
        conversation.open()
        self._conversation = conversation
        runtime = RuntimeStateStore(frontier.spec.parent)
        runtime.start(
            spec=str(frontier.spec),
            execution_map=str(frontier.execution_map),
            current_ticket_id=None,
            current_ticket_path=None,
            completed=len(frontier.completed_ids),
            total=len(frontier.tickets),
        )
        self._runtime = runtime
        status_file = StatusFile(self.config.working_directory)
        coordinator_session = conversation.coordinator_session_id
        coordinator_turn = 0
        latest_observation = "No agent has run in this process. Inspect durable repository state."

        async def coordinate(
            recommendation: str,
            record: TicketRecord | None,
            observation: str,
            executor_session_id: str | None = None,
            verifier_session_id: str | None = None,
        ) -> tuple[str, str, str | None]:
            nonlocal coordinator_session, coordinator_turn
            conversation.set_ticket(record.ticket_id if record is not None else None)
            subject = record.path if record is not None else frontier.spec
            prompt = coordinator_prompt(
                spec=frontier.spec,
                execution_map=frontier.execution_map,
                ticket=record.path if record is not None else None,
                ticket_id=record.ticket_id if record is not None else None,
                tracker_status=record.status if record is not None else None,
                recommended_action=recommendation,
                observation=(
                    f"{observation}\nPersisted conversation state: "
                    f"{conversation.context()}"
                ),
                executor_session_id=executor_session_id,
                verifier_session_id=verifier_session_id,
                startup_prompt=self.config.coordinator_startup_prompt,
            )

            pending = conversation.pending
            if pending is not None:
                answer = await self._request_input(emit, subject, pending)
                if answer is None:
                    previous_summary = pending.get("planner_summary")
                    return (
                        "awaiting-user-input",
                        previous_summary.strip()
                        if isinstance(previous_summary, str) and previous_summary.strip()
                        else "The Planner is waiting for the Outer Agent's handoff response.",
                        None,
                    )
                pending_kind = pending.get("original_kind", pending.get("kind"))
                if pending_kind == "grill-confirmation" and _is_affirmative(answer):
                    conversation.set_mode("normal")
                if pending_kind == "enter-grill" and not _is_affirmative(answer):
                    conversation.set_mode("normal")
                    answer = (
                        "The user declined grill mode. Continue autonomously or ask one "
                        f"focused question. User response: {answer}"
                    )
                conversation.record_answer(answer)
                prompt = coordinator_response_prompt(
                    answer,
                    conversation.context(),
                    grill_mode=conversation.mode == "grill",
                    recommended_action=recommendation,
                    ticket_id=record.ticket_id if record is not None else None,
                    executor_session_id=executor_session_id,
                    verifier_session_id=verifier_session_id,
                    startup_prompt=self.config.coordinator_startup_prompt,
                )

            for _ in range(self.config.max_questions + 1):
                coordinator_turn += 1
                runtime.update(
                    lifecycle="running",
                    phase="coordinator",
                    role=AgentRole.COORDINATOR.value,
                    round=coordinator_turn,
                    current_ticket_id=(record.ticket_id if record is not None else None),
                    current_ticket_path=(
                        str(record.path) if record is not None else None
                    ),
                    last_event="coordinator.started",
                    summary=observation,
                )
                try:
                    decision = await self.runner.run(
                        role=AgentRole.COORDINATOR,
                        ticket=subject,
                        round_number=coordinator_turn,
                        prompt=prompt,
                        session_id=coordinator_session,
                        emit=emit,
                    )
                except AgentProcessError as error:
                    if coordinator_session is None:
                        return (
                            "stop",
                            f"Coordinator agent failed before producing a decision: {error}",
                            None,
                        )
                    stale_session = coordinator_session
                    coordinator_session = None
                    conversation.set_session(None)
                    await emit(
                        StreamEvent(
                            kind="coordinator.session.replaced",
                            ticket=subject,
                            role=AgentRole.COORDINATOR,
                            round_number=coordinator_turn,
                            payload={"stale_session_id": stale_session},
                        )
                    )
                    recovery_prompt = coordinator_prompt(
                        spec=frontier.spec,
                        execution_map=frontier.execution_map,
                        ticket=record.path if record is not None else None,
                        ticket_id=record.ticket_id if record is not None else None,
                        tracker_status=record.status if record is not None else None,
                        recommended_action=recommendation,
                        observation=(
                            "The saved Coordinator session was unavailable. Reconstruct the "
                            f"decision from repository state and this history: {conversation.context()}"
                        ),
                        executor_session_id=executor_session_id,
                        verifier_session_id=verifier_session_id,
                        startup_prompt=self.config.coordinator_startup_prompt,
                    )
                    try:
                        decision = await self.runner.run(
                            role=AgentRole.COORDINATOR,
                            ticket=subject,
                            round_number=coordinator_turn,
                            prompt=recovery_prompt,
                            session_id=None,
                            emit=emit,
                        )
                    except AgentProcessError as recovery_error:
                        return (
                            "stop",
                            "Coordinator agent failed during session recovery: "
                            f"{recovery_error}",
                            None,
                        )
                coordinator_session = decision.session_id
                conversation.set_session(coordinator_session)
                runtime.update(
                    lifecycle="running",
                    phase="coordinator",
                    role=AgentRole.COORDINATOR.value,
                    round=coordinator_turn,
                    last_event="agent.completed",
                    agent_status=decision.status,
                    summary=decision.summary,
                )
                await emit(
                StreamEvent(
                    kind="agent.completed",
                    ticket=subject,
                    role=AgentRole.COORDINATOR,
                    round_number=coordinator_turn,
                    payload={"status": decision.status, "summary": decision.summary},
                )
                )

                if decision.status not in {"ask-user", "enter-grill", "await-user"}:
                    if conversation.mode != "grill":
                        break
                    request = conversation.require_input(
                        question=(
                            "Grill interview reached a proposed final decision. Confirm this "
                            f"plan before execution:\n\n{decision.summary}"
                        ),
                        recommended_answer="yes",
                        kind="grill-confirmation",
                    )
                    answer = await self._request_input(emit, subject, request)
                    if answer is None:
                        return "awaiting-user-input", decision.summary, None
                    conversation.record_answer(answer)
                    if _is_affirmative(answer):
                        conversation.set_mode("normal")
                        break
                    prompt = coordinator_response_prompt(
                        "The user did not confirm the proposed plan. Reopen the decision tree. "
                        f"User response: {answer}",
                        conversation.context(),
                        grill_mode=True,
                        recommended_action=recommendation,
                        ticket_id=record.ticket_id if record is not None else None,
                        executor_session_id=executor_session_id,
                        verifier_session_id=verifier_session_id,
                        startup_prompt=self.config.coordinator_startup_prompt,
                    )
                    continue
                raw_question = decision.final_output.get("question")
                question = (
                    raw_question.strip()
                    if isinstance(raw_question, str) and raw_question.strip()
                    else _fallback_question(decision.summary, decision.final_output.get("feedback"))
                )
                if decision.status == "enter-grill":
                    conversation.set_mode("grill")
                    question = (
                        "Coordinator recommends entering grill mode for a multi-round "
                        f"decision interview. Continue?\n\n{question}"
                    )
                recommended = decision.final_output.get("recommended_answer")
                request = conversation.require_input(
                    question=question,
                    recommended_answer=(recommended if isinstance(recommended, str) else None),
                    kind=decision.status,
                )
                answer = await self._request_input(emit, subject, request)
                if answer is None:
                    return (
                        "awaiting-user-input",
                        decision.summary,
                        None,
                    )
                if decision.status == "enter-grill" and not _is_affirmative(answer):
                    conversation.set_mode("normal")
                    answer = (
                        "The user declined grill mode. Continue autonomously or ask one "
                        f"focused question. User response: {answer}"
                    )
                conversation.record_answer(answer)
                prompt = coordinator_response_prompt(
                    answer,
                    conversation.context(),
                    grill_mode=conversation.mode == "grill",
                    recommended_action=recommendation,
                    ticket_id=record.ticket_id if record is not None else None,
                    executor_session_id=executor_session_id,
                    verifier_session_id=verifier_session_id,
                    startup_prompt=self.config.coordinator_startup_prompt,
                )
            else:
                return "stop", "Coordinator exceeded the maximum user-question rounds.", None

            allowed = {recommendation, "stop", "await-user"}
            raw = decision.final_output
            expected_ticket_id = record.ticket_id if record is not None else None
            if decision.status not in allowed:
                return (
                    "stop",
                    f"Rejected unsafe coordinator action {decision.status!r}; "
                    f"allowed actions were {sorted(allowed)}.",
                    None,
                )
            if raw.get("ticket_id") != expected_ticket_id:
                return (
                    "stop",
                    "Rejected coordinator decision for a ticket other than the current "
                    f"dependency-ready target {expected_ticket_id!r}.",
                    None,
                )
            expected_session = None
            if decision.status == "resume-executor":
                expected_session = executor_session_id
            elif decision.status == "resume-verifier":
                expected_session = verifier_session_id
            if decision.status.startswith("resume-") and raw.get("session_id") != expected_session:
                return (
                    "stop",
                    "Rejected coordinator decision with a mismatched agent session id.",
                    None,
                )
            if decision.status.startswith("start-") and raw.get("session_id") is not None:
                return (
                    "stop",
                    "Rejected coordinator start decision that supplied an existing session id.",
                    None,
                )
            feedback = raw.get("feedback")
            return (
                decision.status,
                decision.summary,
                feedback if isinstance(feedback, str) and feedback.strip() else None,
            )
        await emit(
            StreamEvent(
                kind="initiative.started",
                payload={
                    "spec": str(frontier.spec),
                    "execution_map": str(frontier.execution_map),
                    "ticket_count": len(frontier.tickets),
                },
            )
        )
        runtime.update(
            lifecycle="running",
            phase="selecting-ticket",
            last_event="initiative.started",
            completed=len(frontier.completed_ids),
            total=len(frontier.tickets),
        )

        async def publish_handoff(
            *,
            cause: str,
            summary: str,
            current_ticket_id: str | None = None,
            waiting_ticket_ids: list[str] | None = None,
            error: str | None = None,
            question: str | None = None,
        ) -> None:
            current_frontier = Frontier.load(frontier.spec, frontier.execution_map)
            selected_ticket_id = current_ticket_id or conversation.state.get(
                "current_ticket_id"
            )
            selected_ticket = next(
                (
                    item
                    for item in current_frontier.tickets
                    if item.ticket_id == selected_ticket_id
                ),
                None,
            )
            pending = conversation.mark_handoff(
                cause=cause,
                summary=summary,
                question=question,
            )
            runtime.update(
                lifecycle="handoff",
                phase="handoff",
                role=AgentRole.COORDINATOR.value,
                current_ticket_id=(
                    selected_ticket.ticket_id if selected_ticket is not None else None
                ),
                current_ticket_path=(
                    str(selected_ticket.path) if selected_ticket is not None else None
                ),
                completed=len(current_frontier.completed_ids),
                total=len(current_frontier.tickets),
                last_event="initiative.handoff",
                cause=cause,
                summary=summary,
            )
            if cause == "operator-stop":
                runtime.clear_stop_request()
            status_file.write(
                status="handoff",
                cause=cause,
                spec=current_frontier.spec,
                execution_map=current_frontier.execution_map,
                completed=len(current_frontier.completed_ids),
                total=len(current_frontier.tickets),
                current_ticket_id=(
                    selected_ticket.ticket_id if selected_ticket is not None else None
                ),
                current_ticket_path=(
                    selected_ticket.path if selected_ticket is not None else None
                ),
                summary=summary,
                pending=pending,
                waiting_ticket_ids=waiting_ticket_ids,
                error=error,
            )
            await emit(
                StreamEvent(
                    kind="initiative.handoff",
                    ticket=selected_ticket.path if selected_ticket is not None else None,
                    role=AgentRole.COORDINATOR,
                    payload={
                        "status": "handoff",
                        "cause": cause,
                        "completed": len(current_frontier.completed_ids),
                        "total": len(current_frontier.tickets),
                        "current_ticket_id": (
                            selected_ticket.ticket_id
                            if selected_ticket is not None
                            else None
                        ),
                        "summary": summary,
                        "pending": pending,
                        "status_file": str(status_file.path),
                    },
                )
            )

        async def publish_completed(summary: str) -> None:
            current_frontier = Frontier.load(frontier.spec, frontier.execution_map)
            conversation.mark_completed()
            runtime.update(
                lifecycle="completed",
                phase="completed",
                role=AgentRole.COORDINATOR.value,
                current_ticket_id=None,
                current_ticket_path=None,
                completed=len(current_frontier.completed_ids),
                total=len(current_frontier.tickets),
                last_event="initiative.completed",
                summary=summary,
            )
            status_file.write(
                status="completed",
                cause=None,
                spec=current_frontier.spec,
                execution_map=current_frontier.execution_map,
                completed=len(current_frontier.completed_ids),
                total=len(current_frontier.tickets),
                current_ticket_id=None,
                current_ticket_path=None,
                summary=summary,
                pending=None,
            )
            await emit(
                StreamEvent(
                    kind="initiative.completed",
                    payload={
                        "status": "completed",
                        "completed": len(current_frontier.completed_ids),
                        "total": len(current_frontier.tickets),
                        "summary": summary,
                        "status_file": str(status_file.path),
                    },
                )
            )

        async def publish_ticket_completed(
            ticket: TicketRecord, summary: str
        ) -> None:
            current_frontier = Frontier.load(frontier.spec, frontier.execution_map)
            conversation.mark_ticket_completed(ticket.ticket_id)
            runtime.update(
                lifecycle="ticket-completed",
                phase="ticket-completed",
                role=AgentRole.COORDINATOR.value,
                current_ticket_id=ticket.ticket_id,
                current_ticket_path=str(ticket.path),
                completed=len(current_frontier.completed_ids),
                total=len(current_frontier.tickets),
                last_event="initiative.ticket-completed",
                summary=summary,
            )
            status_file.write(
                status="ticket-completed",
                cause="ticket-completed",
                spec=current_frontier.spec,
                execution_map=current_frontier.execution_map,
                completed=len(current_frontier.completed_ids),
                total=len(current_frontier.tickets),
                current_ticket_id=ticket.ticket_id,
                current_ticket_path=ticket.path,
                summary=summary,
                pending=None,
            )
            await emit(
                StreamEvent(
                    kind="initiative.ticket-completed",
                    ticket=ticket.path,
                    role=AgentRole.COORDINATOR,
                    payload={
                        "status": "ticket-completed",
                        "cause": "ticket-completed",
                        "completed": len(current_frontier.completed_ids),
                        "total": len(current_frontier.tickets),
                        "current_ticket_id": ticket.ticket_id,
                        "summary": summary,
                        "status_file": str(status_file.path),
                    },
                )
            )

        while True:
            stop_request = runtime.stop_request()
            if stop_request is not None:
                await publish_handoff(
                    cause="operator-stop",
                    summary=_operator_stop_summary(stop_request),
                    current_ticket_id=conversation.state.get("current_ticket_id"),
                    question=_OPERATOR_STOP_QUESTION,
                )
                return
            frontier = Frontier.load(frontier.spec, frontier.execution_map)
            effective_completed = frontier.completed_ids
            if len(effective_completed) == len(frontier.tickets):
                action, reason, _ = await coordinate(
                    "complete-initiative", None, latest_observation
                )
                if action == "complete-initiative":
                    await publish_completed(reason)
                else:
                    await publish_handoff(
                        cause=self._status_for_action(action),
                        summary=reason,
                    )
                return

            ticket = frontier.next_ticket()
            if ticket is None:
                waiting = [
                    item.ticket_id
                    for item in frontier.tickets
                    if item.ticket_id not in effective_completed
                    and item.status == "awaiting-user-verification"
                ]
                recommendation = "await-user" if waiting else "stop"
                action, reason, _ = await coordinate(
                    recommendation, None, latest_observation
                )
                await publish_handoff(
                    cause=self._status_for_action(action),
                    summary=reason,
                    waiting_ticket_ids=waiting,
                )
                return

            if ticket.status == "awaiting-user-verification":
                action, reason, _ = await coordinate(
                    "await-user", ticket, latest_observation
                )
                await publish_handoff(
                    cause=self._status_for_action(action),
                    summary=reason,
                    current_ticket_id=ticket.ticket_id,
                )
                return

            first_action = (
                "start-verifier"
                if ticket.status == "ready-for-verification"
                else "start-executor"
            )
            action, reason, _ = await coordinate(
                first_action, ticket, latest_observation
            )
            if action != first_action:
                await publish_handoff(
                    cause=self._status_for_action(action),
                    summary=reason,
                    current_ticket_id=ticket.ticket_id,
                )
                return

            result = await self._run_ticket(
                frontier, ticket, runtime, emit, coordinate, action
            )
            latest_observation = (
                f"Ticket {ticket.ticket_id} ended with status {result.status}: {result.summary}"
            )
            if result.status != "completed":
                await publish_handoff(
                    cause=result.status,
                    summary=result.summary,
                    current_ticket_id=ticket.ticket_id,
                    question=(
                        _OPERATOR_STOP_QUESTION
                        if result.status == "operator-stop"
                        else None
                    ),
                )
                return
            refreshed = Frontier.load(frontier.spec, frontier.execution_map)
            persisted = next(
                item for item in refreshed.tickets if item.ticket_id == ticket.ticket_id
            )
            if persisted.status != "completed":
                await publish_handoff(
                    cause="tracker-persistence-error",
                    summary=(
                        "Verifier returned completed but the durable tracker did not "
                        "persist the ticket as completed."
                    ),
                    current_ticket_id=ticket.ticket_id,
                )
                return
            if len(refreshed.completed_ids) < len(refreshed.tickets):
                await publish_ticket_completed(persisted, result.summary)
                return
            # The final ticket may complete the initiative. Preserve the existing
            # initiative-level completion decision, but never start another ticket
            # in this invocation.

    async def _run_ticket(
        self,
        frontier: Frontier,
        record: TicketRecord,
        runtime: RuntimeStateStore,
        emit: Callable[[StreamEvent], Awaitable[None]],
        coordinate: Callable[
            [str, TicketRecord | None, str, str | None, str | None],
            Awaitable[tuple[str, str, str | None]],
        ],
        next_action: str,
    ) -> TicketResult:
        ticket = record.path
        executor_session: str | None = None
        verifier_session: str | None = None
        previous_feedback: str | None = None

        await emit(
            StreamEvent(
                kind="ticket.started",
                ticket=ticket,
                payload={"ticket_id": record.ticket_id, "tracker_status": record.status},
            )
        )
        for round_number in range(1, self.config.max_rounds + 1):
            stop_result = await self._stop_result_if_requested(
                runtime,
                emit,
                ticket,
                round_number,
                executor_session,
                verifier_session,
            )
            if stop_result is not None:
                return stop_result
            if not (round_number == 1 and record.status == "ready-for-verification"):
                runtime.update(
                    lifecycle="running",
                    phase="executor",
                    role=AgentRole.EXECUTOR.value,
                    round=round_number,
                    current_ticket_id=record.ticket_id,
                    current_ticket_path=str(ticket),
                    last_event="executor.started",
                )
                try:
                    executor = await self.runner.run(
                        role=AgentRole.EXECUTOR,
                        ticket=ticket,
                        round_number=round_number,
                        prompt=executor_prompt(ticket, round_number, previous_feedback),
                        session_id=executor_session,
                        emit=emit,
                    )
                except AgentProcessError as error:
                    next_action, decision_reason, _ = await coordinate(
                        "stop",
                        record,
                        f"Executor agent failed before completing its turn: {error}",
                        executor_session,
                        verifier_session,
                    )
                    return await self._decision_stop(
                        emit,
                        ticket,
                        round_number,
                        executor_session,
                        verifier_session,
                        next_action,
                        decision_reason,
                    )
                executor_session = executor.session_id
                _persist_worker_status(frontier, record.ticket_id, executor.status)
                runtime.update(
                    lifecycle="running",
                    phase="executor",
                    role=AgentRole.EXECUTOR.value,
                    round=round_number,
                    last_event="agent.completed",
                    agent_status=executor.status,
                    summary=executor.summary,
                )
                await emit(
                    StreamEvent(
                        kind="agent.completed",
                        ticket=ticket,
                        role=AgentRole.EXECUTOR,
                        round_number=round_number,
                        payload={"status": executor.status, "summary": executor.summary},
                    )
                )

                stop_result = await self._stop_result_if_requested(
                    runtime,
                    emit,
                    ticket,
                    round_number,
                    executor_session,
                    verifier_session,
                )
                if stop_result is not None:
                    return stop_result
                if executor.status == "incomplete":
                    previous_feedback = executor.summary
                    next_action, decision_reason, decision_feedback = await coordinate(
                        "resume-executor",
                        record,
                        f"Executor returned incomplete: {executor.summary}",
                        executor_session,
                        verifier_session,
                    )
                    if next_action != "resume-executor":
                        return await self._decision_stop(
                            emit, ticket, round_number, executor_session, verifier_session,
                            next_action, decision_reason,
                        )
                    previous_feedback = decision_feedback or (
                        f"Executor reported incomplete: {executor.summary}. "
                        f"Coordinator: {decision_reason}"
                    )
                    continue
                if executor.status not in {
                    "ready-for-verification",
                    "awaiting-user-verification",
                }:
                    recommendation = (
                        "await-user"
                        if executor.status == "awaiting-user-verification"
                        else "stop"
                    )
                    next_action, decision_reason, _ = await coordinate(
                        recommendation,
                        record,
                        f"Executor returned {executor.status}: {executor.summary}",
                        executor_session,
                        verifier_session,
                    )
                    return await self._decision_stop(
                        emit, ticket, round_number, executor_session, verifier_session,
                        next_action, decision_reason,
                    )
                executor_summary = executor.summary
            else:
                executor_summary = "Tracker already marks this ticket ready-for-verification."

            verifier_recommendation = (
                "resume-verifier" if verifier_session is not None else "start-verifier"
            )
            if not (
                round_number == 1
                and record.status == "ready-for-verification"
                and next_action == "start-verifier"
            ):
                next_action, decision_reason, _ = await coordinate(
                    verifier_recommendation,
                    record,
                    f"Executor handoff for independent verification: {executor_summary}",
                    executor_session,
                    verifier_session,
                )
            else:
                decision_reason = "Coordinator selected verification from durable tracker state."
            if next_action != verifier_recommendation:
                return await self._decision_stop(
                    emit, ticket, round_number, executor_session, verifier_session,
                    next_action, decision_reason,
                )

            stop_result = await self._stop_result_if_requested(
                runtime,
                emit,
                ticket,
                round_number,
                executor_session,
                verifier_session,
            )
            if stop_result is not None:
                return stop_result
            runtime.update(
                lifecycle="running",
                phase="verifier",
                role=AgentRole.VERIFIER.value,
                round=round_number,
                current_ticket_id=record.ticket_id,
                current_ticket_path=str(ticket),
                last_event="verifier.started",
            )
            try:
                verifier = await self.runner.run(
                    role=AgentRole.VERIFIER,
                    ticket=ticket,
                    round_number=round_number,
                    prompt=verifier_prompt(ticket, round_number, executor_summary),
                    session_id=verifier_session,
                    emit=emit,
                )
            except AgentProcessError as error:
                next_action, decision_reason, _ = await coordinate(
                    "stop",
                    record,
                    f"Verifier agent failed before completing its turn: {error}",
                    executor_session,
                    verifier_session,
                )
                return await self._decision_stop(
                    emit,
                    ticket,
                    round_number,
                    executor_session,
                    verifier_session,
                    next_action,
                    decision_reason,
                )
            verifier_session = verifier.session_id
            _persist_worker_status(frontier, record.ticket_id, verifier.status)
            runtime.update(
                lifecycle="running",
                phase="verifier",
                role=AgentRole.VERIFIER.value,
                round=round_number,
                last_event="agent.completed",
                agent_status=verifier.status,
                summary=verifier.summary,
            )
            await emit(
                StreamEvent(
                    kind="agent.completed",
                    ticket=ticket,
                    role=AgentRole.VERIFIER,
                    round_number=round_number,
                    payload={"status": verifier.status, "summary": verifier.summary},
                )
            )

            stop_result = await self._stop_result_if_requested(
                runtime,
                emit,
                ticket,
                round_number,
                executor_session,
                verifier_session,
            )
            if stop_result is not None:
                return stop_result
            if verifier.status != "incomplete":
                if verifier.status != "completed":
                    recommendation = (
                        "await-user"
                        if verifier.status == "awaiting-user-verification"
                        else "stop"
                    )
                    next_action, decision_reason, _ = await coordinate(
                        recommendation,
                        record,
                        f"Verifier returned {verifier.status}: {verifier.summary}",
                        executor_session,
                        verifier_session,
                    )
                    return await self._decision_stop(
                        emit, ticket, round_number, executor_session, verifier_session,
                        next_action, decision_reason,
                    )
                result = TicketResult(
                    ticket=ticket,
                    status=verifier.status,
                    rounds=round_number,
                    executor_session_id=executor_session,
                    verifier_session_id=verifier_session,
                    summary=verifier.summary,
                )
                await self._finish(emit, result)
                return result
            previous_feedback = verifier.summary
            next_action, decision_reason, decision_feedback = await coordinate(
                "resume-executor",
                record,
                f"Verifier returned incomplete: {verifier.summary}",
                executor_session,
                verifier_session,
            )
            if next_action != "resume-executor":
                return await self._decision_stop(
                    emit, ticket, round_number, executor_session, verifier_session,
                    next_action, decision_reason,
                )
            previous_feedback = decision_feedback or (
                f"Verifier reported incomplete: {verifier.summary}. "
                f"Coordinator: {decision_reason}"
            )

        result = TicketResult(
            ticket=ticket,
            status="max-rounds-exceeded",
            rounds=self.config.max_rounds,
            executor_session_id=executor_session,
            verifier_session_id=verifier_session,
            summary=previous_feedback or "Execution or verification did not complete.",
        )
        await self._finish(emit, result)
        return result

    async def _stop_result_if_requested(
        self,
        runtime: RuntimeStateStore,
        emit: Callable[[StreamEvent], Awaitable[None]],
        ticket: Path,
        round_number: int,
        executor_session: str | None,
        verifier_session: str | None,
    ) -> TicketResult | None:
        request = runtime.stop_request()
        if request is None:
            return None
        return await self._decision_stop(
            emit,
            ticket,
            round_number,
            executor_session,
            verifier_session,
            "operator-stop",
            _operator_stop_summary(request),
        )

    @classmethod
    async def _decision_stop(
        cls,
        emit: Callable[[StreamEvent], Awaitable[None]],
        ticket: Path,
        round_number: int,
        executor_session: str | None,
        verifier_session: str | None,
        action: str,
        reason: str,
    ) -> TicketResult:
        result = TicketResult(
            ticket=ticket,
            status=cls._status_for_action(action),
            rounds=round_number,
            executor_session_id=executor_session,
            verifier_session_id=verifier_session,
            summary=reason,
        )
        await cls._finish(emit, result)
        return result

    async def _request_input(
        self,
        emit: Callable[[StreamEvent], Awaitable[None]],
        subject: Path,
        request: dict[str, Any],
    ) -> str | None:
        payload = {
            **request,
            "warning": "Do not enter passwords, API keys, or other secrets.",
        }
        if self._runtime is not None:
            self._runtime.update(
                lifecycle="running",
                phase="waiting-input",
                role=AgentRole.COORDINATOR.value,
                last_event="user.input.required",
                summary=str(request.get("question") or "Waiting for Outer Agent input."),
            )
        await emit(
            StreamEvent(
                kind="user.input.required",
                ticket=subject,
                role=AgentRole.COORDINATOR,
                payload=payload,
            )
        )
        if self.input_provider is None:
            return None
        return await self.input_provider(payload)

    @staticmethod
    def _status_for_action(action: str) -> str:
        if action == "await-user":
            return "awaiting-user-verification"
        if action == "awaiting-user-input":
            return "awaiting-user-input"
        if action == "operator-stop":
            return "operator-stop"
        return "blocked"

    @staticmethod
    async def _finish(
        emit: Callable[[StreamEvent], Awaitable[None]], result: TicketResult
    ) -> None:
        await emit(
            StreamEvent(
                kind="ticket.completed",
                ticket=result.ticket,
                payload={
                    "status": result.status,
                    "rounds": result.rounds,
                    "executor_session_id": result.executor_session_id,
                    "verifier_session_id": result.verifier_session_id,
                    "summary": result.summary,
                },
            )
        )


def _persist_worker_status(frontier: Frontier, ticket_id: str, status: str) -> None:
    persisted_status = {
        "incomplete": "in-progress",
        "ready-for-verification": "ready-for-verification",
        "awaiting-user-verification": "awaiting-user-verification",
        "completed": "completed",
        "blocked": "blocked",
    }.get(status)
    if persisted_status is not None:
        frontier.set_status(ticket_id, persisted_status)


def _is_affirmative(answer: str) -> bool:
    return answer.strip().lower() in {"y", "yes", "确认", "好的", "好", "同意", "继续"}


def _fallback_question(reason: str, feedback: object) -> str:
    guidance = feedback.strip() if isinstance(feedback, str) and feedback.strip() else None
    details = f"{reason}\n\nRequested guidance:\n{guidance}" if guidance else reason
    return f"{details}\n\nHow should LoopAI proceed?"


def _operator_stop_summary(request: dict[str, Any]) -> str:
    reason = request.get("reason")
    if isinstance(reason, str) and reason.strip():
        return f"Controlled stop requested by the Outer Agent: {reason.strip()}"
    return "Controlled stop requested by the Outer Agent."
