# Coordinator user startup prompt

Allow a workspace owner to define an optional multiline `[coordinator].startup_prompt` in
`.loopai/config.toml`. Append it after LoopAI's fixed Coordinator instructions only when creating a
new Coordinator session, including stale-session replacement. Never inject it into Executor or
Verifier prompts, and never repeat it while resuming a valid Coordinator session.
