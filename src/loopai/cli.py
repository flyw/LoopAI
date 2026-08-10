from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .configuration import load_workspace_config
from .models import LoopConfig
from .orchestrator import InitiativeOrchestrator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="loopai",
        description=(
            "Complete every ticket in a spec frontier with coordinator, executor, "
            "and verifier agents."
        ),
    )
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument(
        "--spec",
        type=Path,
        help="Initiative spec.md; auto-discovered when the workspace has exactly one.",
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
        "--answer",
        action="append",
        default=[],
        help="Answer a pending Coordinator question; repeat for scripted answers.",
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
    scripted_answers = iter(args.answer)

    async def input_provider(request: dict[str, object]) -> str | None:
        try:
            return next(scripted_answers)
        except StopIteration:
            pass
        if args.json or not sys.stdin.isatty():
            return None
        question = str(request.get("question") or "Coordinator needs your input.")
        recommended = request.get("recommended_answer")
        suffix = f"\nRecommended: {recommended}" if recommended else ""
        return await asyncio.to_thread(
            input,
            f"\n{question}{suffix}\n"
            "Commands: /status, /back, /cancel\n"
            "loopai> ",
        )

    orchestrator = InitiativeOrchestrator(config, input_provider=input_provider)
    final_status: str | None = None
    async for event in orchestrator.stream(args.spec):
        if event.kind == "initiative.completed":
            final_status = str(event.payload["status"])
        if args.json:
            print(json.dumps(event.as_dict(), ensure_ascii=False), flush=True)
        else:
            _print_pretty(event.as_dict())
    return 0 if final_status == "completed" else 1


def config_from_args(args: argparse.Namespace) -> LoopConfig:
    workspace = args.workspace.expanduser().resolve()
    role_settings = load_workspace_config(workspace)
    return LoopConfig(
        workspace=workspace,
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
        "ticket.started",
        "agent.completed",
        "ticket.completed",
        "agent.stderr",
        "user.input.required",
        "user.input.status",
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
