# Security Policy

## Scope

LoopAI starts local Codex CLI processes that can read, modify, and execute commands in the configured
project directory. Treat it as automation with write access to that directory.

Do not run LoopAI on an untrusted repository while sensitive credentials are available to the child
process environment.

## Reporting a vulnerability

Please do not disclose an unpatched vulnerability in a public issue. Use the repository's private
security reporting channel when one is configured, or contact the maintainers before publication.

Include the affected version, operating system, reproduction steps, expected behavior, and any
relevant logs with secrets removed.
