from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .configuration import load_working_directory_config
from .models import LoopConfig
from .orchestrator import InitiativeOrchestrator
from .version import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="loopai",
        description=(
            "Complete every ticket in a spec frontier with coordinator, executor, "
            "and verifier agents from the current working directory."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--spec",
        type=Path,
        help=(
            "Initiative spec.md relative to the current working directory; "
            "auto-discovered when there is exactly one."
        ),
    )
    parser.add_argument("--model", help="Override the model for all three roles.")
    parser.add_argument(
        "--reasoning-effort", help="Override reasoning effort for all three roles."
    )
    for role in ("coordinator", "executor", "verifier"):
        parser.add_argument(f"--{role}-model")
        parser.add_argument(f"--{role}-reasoning-effort")
    parser.add_argument("--max-rounds", type=int, default=3)
    parser.add_argument("--max-questions", type=int, default=20)
    parser.add_argument("--codex-binary", default="codex")
    parser.add_argument(
        "--automatic-approval",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Allow Codex's automatic approval reviewer for non-interactive execution "
            "(default: enabled)."
        ),
    )
    parser.add_argument(
        "--answer",
        action="append",
        default=[],
        help="Return an outer-agent result to a pending Planner handoff; repeat if needed.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the complete event stream as JSONL instead of readable progress.",
    )
    return parser


async def async_main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = config_from_args(args)
    scripted_answers = list(args.answer)
    consumed_answers = 0

    async def input_provider(request: dict[str, object]) -> str | None:
        del request
        nonlocal consumed_answers
        if consumed_answers >= len(scripted_answers):
            return None
        answer = scripted_answers[consumed_answers]
        consumed_answers += 1
        return answer

    orchestrator = InitiativeOrchestrator(
        config,
        input_provider=input_provider if scripted_answers else None,
    )
    final_status: str | None = None
    async for event in orchestrator.stream(args.spec):
        if event.kind in {"initiative.completed", "initiative.handoff"}:
            final_status = str(event.payload["status"])
        if args.json:
            print(json.dumps(event.as_dict(), ensure_ascii=False), flush=True)
        else:
            _print_pretty(event.as_dict())
    if consumed_answers != len(scripted_answers):
        raise ValueError(
            "A --answer was supplied without a pending Planner handoff; "
            "start LoopAI from the initiative's current state and inspect LOOPAI_STATUS.md."
        )
    return 0 if final_status == "completed" else 1


def config_from_args(args: argparse.Namespace) -> LoopConfig:
    working_directory = Path.cwd().expanduser().resolve()
    role_settings = load_working_directory_config(working_directory)
    return LoopConfig(
        working_directory=working_directory,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        coordinator_model=(
            args.coordinator_model
            or (None if args.model else role_settings["coordinator"].model)
        ),
        coordinator_reasoning_effort=(
            args.coordinator_reasoning_effort
            or (
                None
                if args.reasoning_effort
                else role_settings["coordinator"].reasoning_effort
            )
        ),
        coordinator_startup_prompt=role_settings["coordinator"].startup_prompt,
        executor_model=(
            args.executor_model or (None if args.model else role_settings["executor"].model)
        ),
        executor_reasoning_effort=(
            args.executor_reasoning_effort
            or (
                None if args.reasoning_effort else role_settings["executor"].reasoning_effort
            )
        ),
        verifier_model=(
            args.verifier_model or (None if args.model else role_settings["verifier"].model)
        ),
        verifier_reasoning_effort=(
            args.verifier_reasoning_effort
            or (
                None if args.reasoning_effort else role_settings["verifier"].reasoning_effort
            )
        ),
        max_rounds=args.max_rounds,
        max_questions=args.max_questions,
        codex_binary=args.codex_binary,
        automatic_approval=args.automatic_approval,
    )


def _print_pretty(event: dict[str, object]) -> None:
    kind = event["kind"]
    role = event.get("role") or "orchestrator"
    round_number = event.get("round")
    payload = event.get("payload")
    prefix = f"[{role}]"
    if round_number is not None:
        prefix += f"[round {round_number}]"
    if kind == "agent.event" and isinstance(payload, dict):
        item = payload.get("item")
        if isinstance(item, dict) and item.get("type") == "agent_message":
            print(f"{prefix} {item.get('text', '')}", flush=True)
    elif kind in {
        "initiative.started",
        "initiative.completed",
        "initiative.handoff",
        "ticket.started",
        "agent.completed",
        "ticket.completed",
        "agent.stderr",
        "user.input.required",
    }:
        print(f"{prefix} {kind}: {payload}", flush=True)


def main() -> None:
    try:
        raise SystemExit(asyncio.run(async_main()))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as error:
        print(f"loopai: {error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
