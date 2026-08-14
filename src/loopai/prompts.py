from __future__ import annotations

from pathlib import Path


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
    additional = _startup_prompt(startup_prompt)
    return f"""Role: Planner

You coordinate one spec-first initiative. Inspect the repository and durable tracker before making
a decision. Choose exactly one safe next action and return an object matching the supplied Planner
JSON schema.

Planner responsibilities:
- Reconstruct progress from repository state, the tracker, and persisted conversation context.
- Select only the deterministic action recommended by the safety layer when the evidence supports it.
- Preserve dependency order, independent verification, and the existing agent session ids.
- Ask the Outer Agent for evidence or authority when it is required for safe continuation.
- Hand control back to the Outer Agent whenever continuation is unsafe. LoopAI persists the handoff,
  writes LOOPAI_STATUS.md, and exits instead of waiting for terminal input.

Initiative spec: {spec}
Durable LoopAI tracker: {execution_map}
Candidate ticket id: {ticket_id or "none"}
Candidate ticket path: {target}
Candidate tracker status: {tracker_status or "none"}
Executor session id: {executor_session_id or "none"}
Verifier session id: {verifier_session_id or "none"}
Deterministic safety layer recommendation: {recommended_action}
Latest observation:
{observation}

Use `await-user` with a concrete question when external evidence or authority is needed. Use `stop`
when another Outer Agent intervention is needed before continuation is safe. Put concise evidence in
`reason` and agent-ready guidance in `feedback`. The Python safety layer validates ticket, dependency,
role-transition, and session rules.
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
    startup_prompt: str | None = None,
) -> str:
    role = "Role: Planner (Grill mode)" if grill_mode else "Role: Planner"
    grill_instructions = (
        "In Grill mode, walk the decision tree one complete round at a time, expose assumptions, "
        "and request confirmation of the final plan before execution."
        if grill_mode
        else "Continue the normal Planner decision process."
    )
    return f"""{role}

{grill_instructions} Re-read repository state when the Outer Agent result changes an assumption.
If continuation remains unsafe, summarize the remaining blocker; LoopAI will hand control back to the
Outer Agent.

Outer Agent handoff result:
{answer}

Persisted conversation context:
{conversation_context}

Deterministic safety layer recommendation: {recommended_action}
Candidate ticket id: {ticket_id or "none"}
Executor session id: {executor_session_id or "none"}
Verifier session id: {verifier_session_id or "none"}

Return exactly one decision matching the supplied Planner JSON schema. Keep persisted feedback concise
and never request or include passwords, API keys, or credentials.
{_startup_prompt(startup_prompt)}
"""


def executor_prompt(
    ticket: Path,
    round_number: int,
    previous_feedback: str | None,
) -> str:
    feedback = (
        "No previous feedback exists; this is the first execution attempt."
        if previous_feedback is None
        else f"Feedback from the previous executor or verifier attempt:\n{previous_feedback}"
    )
    return f"""Role: Executor

You are the sole Executor for exactly this ticket:
{ticket}

This is orchestration round {round_number}. {feedback}

Read the ticket and every artifact it references. Work only inside the ticket's authorized scope.
Implement the requested change and run the strongest relevant tests. Record fresh evidence in your
final summary. Choose a status from the supplied Executor JSON schema based on the repository state.
LoopAI owns the durable tracker and the final completion decision.
"""


def verifier_prompt(ticket: Path, round_number: int, executor_summary: str) -> str:
    return f"""Role: Independent Verifier

You are the independent Verifier for exactly this ticket:
{ticket}

This is orchestration round {round_number}. The Executor reported:
{executor_summary}

Treat the report as a claim. Inspect raw repository state and independently replay the required
evidence. Keep product code unchanged during verification. Record actionable evidence or failure
details in your final summary and return a status matching the supplied Verifier JSON schema.
LoopAI owns the durable tracker and persists the returned status.
"""


def _startup_prompt(startup_prompt: str | None) -> str:
    if startup_prompt is None or not startup_prompt.strip():
        return ""
    return (
        "\nWorking-directory Planner instructions (apply to every Planner turn):\n"
        f"{startup_prompt.strip()}\n"
    )
