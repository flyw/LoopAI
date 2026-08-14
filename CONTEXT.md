# LoopAI orchestration context

This context defines the language used when LoopAI coordinates work between an outer agent and its
specialized agents.

## Coordination

**Initiative**:
A bounded unit of work described by one specification and its ordered tickets.
_Avoid_: Project, workspace

**Working directory**:
The directory in which the outer agent starts LoopAI and where the initiative is expected to live.
_Avoid_: Workspace, project root

**Planner**:
The Coordinator responsible for inspecting the initiative, making the next safe decision, and
summarizing why progress can or cannot continue.
_Avoid_: Manager, controller

**Outer Agent**:
The agent that invokes LoopAI and owns decisions or external work that LoopAI cannot perform
itself.
_Avoid_: User, parent process

**Handoff**:
A durable pause in which LoopAI records the Planner's current understanding and gives control to
the Outer Agent for an external decision, repair, authorization, or investigation.
_Avoid_: Pause, input loop, failure

**Resume invocation**:
A new LoopAI process started by the Outer Agent after a handoff, carrying the Outer Agent's result
back to the Planner.
_Avoid_: Continue command, answer prompt
