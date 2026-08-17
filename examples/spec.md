# Example initiative

This example demonstrates a complete spec-driven frontier. The orchestrator discovers the
adjacent `issues/*.md` files, reads their ticket metadata, creates `.loopai/execution.json`, and
completes one ticket per invocation in dependency order. The adjacent `README.md` is documentation
only.

## Objective

Add a deterministic greeting function to the target repository.

## Preserved contracts

- Existing public behavior must remain unchanged.
- No dependency may be added.
