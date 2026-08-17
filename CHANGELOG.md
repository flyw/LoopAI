# Changelog

All notable changes to LoopAI will be documented here.

## [0.2.0] - Unreleased

- Added a self-contained public CLI prompt set.
- Added version reporting and a stable event schema version.
- Added configurable automatic approval for Codex child sessions.
- Added an optional MCP stdio adapter through the `mcp` extra.
- Restored explicit per-role model configuration while keeping Coordinator startup prompts optional.
- Changed execution to stop after one ticket completes or hands off; the next invocation advances
  to the next dependency-ready ticket.
- Added MCP runtime inspection through `loopai_status` and controlled stopping through `loopai_stop`.
- Added optional detached MCP Worker execution through `loopai_run(wait=false)` with an atomic
  single-instance `.loopai/worker.lock`, isolated Worker logs, heartbeat state, and strict resume
  checks. Synchronous `wait=true` remains the default.
- Rewrote the public README in English.
