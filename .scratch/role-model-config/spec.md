# Per-role model configuration

Automatically create and load `<workspace>/.loopai/config.toml` so Coordinator, Executor, and
Verifier can use different Codex models and reasoning efforts. Preserve project isolation and
existing CLI compatibility. Resolve configuration with this precedence:

```text
role CLI option > global CLI option > workspace TOML > built-in role default
```

Invalid TOML, unknown sections/keys, empty models, and unsupported reasoning efforts must fail with
an actionable error before any agent starts.
