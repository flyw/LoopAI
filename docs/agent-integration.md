# Outer-agent integration

This document defines the machine-facing contract for an agent that launches the `loopai` CLI.
The CLI is a single-turn worker: it runs until the initiative completes or until the Planner creates
a handoff for the outer agent.

## Launch

Launch LoopAI with the target project as the process working directory:

```text
cwd = /absolute/path/to/project
argv = ["loopai", "--json", "--spec", "spec.md"]
```

The `--spec` path is resolved from `cwd` and must remain inside it. Omit it when there is exactly one
`spec.md` under `cwd`.

LoopAI never waits for terminal input. It accepts an external response only through one or more
`--answer` arguments on the next invocation.

## Event stream

Read stdout line by line. Each line is a JSON object:

```json
{
  "schema_version": 1,
  "kind": "initiative.handoff",
  "ticket": "/absolute/path/to/project/initiative/issues/02-follow-up.md",
  "role": "coordinator",
  "round": null,
  "payload": {
    "status": "handoff",
    "cause": "awaiting-user-input",
    "completed": 1,
    "total": 2,
    "current_ticket_id": "02",
    "summary": "The Planner needs external evidence.",
    "status_file": "/absolute/path/to/project/LOOPAI_STATUS.md"
  }
}
```

Treat unknown event kinds as forward-compatible progress events. The terminal event is either
`initiative.completed` or `initiative.handoff`.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | All tickets completed and persisted. |
| `1` | A safe handoff was persisted. Read the status file and decide the next action. |
| `2` | LoopAI could not initialize or encountered a runtime/configuration error. |

Exit code `1` is expected control flow. The outer agent should not retry it blindly.

## Handoff loop

1. Launch LoopAI.
2. If the exit code is `0`, continue the outer workflow.
3. If the exit code is `1`, read `LOOPAI_STATUS.md` and the referenced tracker/ticket files.
4. Perform the external action or repository repair requested by the Planner.
5. Launch LoopAI again with the result:

   ```bash
   loopai --json --answer "The external action is complete. Please continue."
   ```

6. Repeat until the terminal event is `initiative.completed` or the outer workflow decides to stop.

The answer is persisted in `.loopai/conversation.json`. Keep it concise and exclude secrets.

## Shell wrapper example

```bash
set +e
loopai --json --spec spec.md
status=$?
set -e

case "$status" in
  0)
    echo "LoopAI completed the initiative"
    ;;
  1)
    echo "LoopAI handed control to the outer agent" >&2
    sed -n '1,240p' LOOPAI_STATUS.md
    ;;
  *)
    echo "LoopAI failed with status $status" >&2
    exit "$status"
    ;;
esac
```

An Agent Host that already has a shell tool can use this contract without MCP.
