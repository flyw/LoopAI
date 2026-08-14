# Contributing to LoopAI

Thanks for helping improve LoopAI.

## Before opening a pull request

1. Read the public behavior in [README.md](README.md) and the relevant ADRs in `docs/adr/`.
2. Keep changes within the current working-directory and outer-agent handoff model.
3. Add or update tests for behavior changes.
4. Run the complete test suite:

   ```bash
   PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
     python3 -m unittest discover -s tests -v
   ```

5. Check the diff for generated state, credentials, and unrelated changes.

## Design expectations

- Keep the CLI non-interactive.
- Treat `LOOPAI_STATUS.md` and `initiative.handoff` as public integration contracts.
- Preserve dependency ordering and independent verification.
- Keep MCP support as an adapter around the core orchestrator.
- Prefer small, testable changes with explicit failure behavior.

## Pull requests

Describe the user-visible behavior, the machine-facing contract, and the evidence used to verify
the change. If a change modifies the CLI, JSON event schema, persisted state, or MCP tool shape,
call that out explicitly.
