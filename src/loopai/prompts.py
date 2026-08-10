from __future__ import annotations

from pathlib import Path


COORDINATOR_SKILL = "$flyw:agent-initiative-orchestrator"
EXECUTOR_SKILL = "$flyw:agent-ticket-executor"
VERIFIER_SKILL = "$flyw:agent-ticket-verifier"
GRILLING_SKILL = "$mattpocock-skills:grilling"


def coordinator_prompt(
    *,
    spec: Path,
    execution_map: Path,
    ticket: Path | None,
    ticket_id: str | None,
    tracker_status: str | None,
    recommended_action: str,
    observation: str,
    executor_session_id: str | None,
    verifier_session_id: str | None,
    startup_prompt: str | None = None,
) -> str:
    target = "none" if ticket is None else str(ticket)
    additional = (
        ""
        if startup_prompt is None or not startup_prompt.strip()
        else (
            "\nAdditional user startup instructions for this Coordinator session:\n"
            f"{startup_prompt.strip()}\n"
        )
    )
    return f"""{COORDINATOR_SKILL}

Load and follow the explicitly invoked initiative orchestrator skill before deciding.

Inspect the current repository and durable tracker rather than assuming work starts at the first
ticket. Choose exactly one safe next action and match the supplied JSON schema.

Initiative spec: {spec}
Execution map: {execution_map}
Candidate ticket id: {ticket_id or "none"}
Candidate ticket path: {target}
Candidate tracker status: {tracker_status or "none"}
Executor session id: {executor_session_id or "none"}
Verifier session id: {verifier_session_id or "none"}
Deterministic safety layer recommendation: {recommended_action}
Latest observation:
{observation}

Use repository facts to assess the recommendation. Select `await-user` with a concrete `question`
when external evidence or authority could unblock progress; this enters a resumable input loop.
Select `stop` only when another user answer cannot make continuation safe. The Python safety layer
will reject actions that violate the dependency, role, or completion gates. Put concise evidence in
`reason` and agent-ready guidance in `feedback`.
{additional}
"""


def coordinator_response_prompt(
    answer: str,
    conversation_context: str,
    *,
    grill_mode: bool,
    recommended_action: str,
    ticket_id: str | None,
    executor_session_id: str | None,
    verifier_session_id: str | None,
) -> str:
    skill = GRILLING_SKILL if grill_mode else COORDINATOR_SKILL
    return f"""{skill}

Continue the same Coordinator decision process with the user's answer below. Re-read repository
state when the answer changes an assumption. In grill mode, recompute the decision-tree frontier,
ask the next complete round when branches remain, and request explicit confirmation of the final
plan before returning an execution action.

User answer:
{answer}

Persisted conversation context:
{conversation_context}

Deterministic safety layer recommendation: {recommended_action}
Candidate ticket id: {ticket_id or "none"}
Executor session id: {executor_session_id or "none"}
Verifier session id: {verifier_session_id or "none"}

Return exactly one decision matching the supplied Coordinator JSON schema. Never include secrets
in persisted feedback or request passwords, API keys, or credentials.
"""


def executor_prompt(
    ticket: Path,
    round_number: int,
    previous_feedback: str | None,
) -> str:
    feedback = (
        "No previous feedback exists; this is the first execution attempt."
        if previous_feedback is None
        else (
            "Feedback from the previous incomplete executor or verifier attempt:\n"
            f"{previous_feedback}"
        )
    )
    return f"""{EXECUTOR_SKILL}

Load and follow the explicitly invoked executor skill before taking any ticket action.

You are the sole executor agent for exactly this ticket:
{ticket}

This is orchestration round {round_number}. {feedback}

Read the ticket and every artifact it references. Work only inside the ticket's authorized
scope. Implement and test the ticket, update only executor-owned handoff artifacts, and do not
self-declare the ticket completed. Your final response must match the supplied JSON schema.
Set `status` mechanically according to the skill. Include concise fresh evidence in `summary`.
"""


def verifier_prompt(ticket: Path, round_number: int, executor_summary: str) -> str:
    return f"""{VERIFIER_SKILL}

Load and follow the explicitly invoked verifier skill before taking any verification action.

You are the sole independent verifier agent for exactly this ticket:
{ticket}

This is orchestration round {round_number}. The executor reported:
{executor_summary}

Treat that report only as a claim. Inspect raw repository state and independently replay the
required evidence. Do not modify product code. Update only verifier-owned artifacts and the
ticket/execution-map state allowed by the skill. Your final response must match the supplied JSON
schema. Set `status` mechanically according to the skill and put actionable evidence or failure
details in `summary`.
"""
