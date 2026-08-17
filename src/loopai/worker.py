"""Detached single-initiative Worker used by the optional MCP adapter."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from .mcp_server import run_loopai_once
from .runtime import RuntimeStateStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="loopai-worker")
    parser.add_argument("--initiative", type=Path, required=True)
    parser.add_argument("--request-file", type=Path, required=True)
    return parser


async def run_worker(*, initiative: Path, request_file: Path) -> int:
    runtime = RuntimeStateStore(initiative)
    claimed = False
    heartbeat_task: asyncio.Task[None] | None = None
    try:
        runtime.claim_worker()
        claimed = True
        heartbeat_task = asyncio.create_task(_heartbeat(runtime))
        request = runtime.read_worker_request()
        if request_file.resolve() != runtime.worker_request_path.resolve():
            request = _read_request_file(request_file)
        spec = request.get("spec")
        answer = request.get("answer")
        if not isinstance(spec, str) or not spec.strip():
            raise ValueError("Worker request did not contain a non-empty spec.")
        if answer is not None and not isinstance(answer, str):
            raise ValueError("Worker request answer must be a string or null.")

        result = await run_loopai_once(spec=spec, answer=answer)
        runtime.update(last_result=result)
        if result.get("status") == "error":
            snapshot = runtime.read()
            if snapshot.get("lifecycle") in {None, "starting", "running"}:
                runtime.update(
                    lifecycle="error",
                    phase="error",
                    last_event="worker.error",
                    cause=result.get("cause"),
                    summary=result.get("error"),
                )
            return 1
        return 0
    except asyncio.CancelledError:
        raise
    except BaseException as error:
        runtime.update(
            lifecycle="error",
            phase="error",
            last_event="worker.error",
            cause="worker-error",
            summary=str(error),
        )
        print(f"loopai-worker: {error}", file=sys.stderr)
        return 1
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
        if claimed:
            runtime.clear_worker_request()
            runtime.release_worker()


async def _heartbeat(runtime: RuntimeStateStore) -> None:
    while True:
        await asyncio.sleep(5)
        try:
            runtime.update()
        except Exception:
            # The orchestration result remains authoritative if the status file
            # is temporarily unavailable; the next phase update will retry it.
            continue


def _read_request_file(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Worker request must contain an object: {path}")
    return payload


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(
        asyncio.run(
            run_worker(
                initiative=args.initiative.expanduser().resolve(),
                request_file=args.request_file.expanduser().resolve(),
            )
        )
    )


if __name__ == "__main__":
    main()
