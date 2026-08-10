# Await-user interactive routing

Any Coordinator decision that requires user input must enter the persisted input loop instead of
ending an interactive LoopAI process. Treat `ask-user`, `enter-grill`, and `await-user` as interactive
actions. A TTY reads an answer and resumes the same Coordinator session; non-interactive and JSONL
runs persist the request, emit `user.input.required`, and return `awaiting-user-input` for later
`--answer` recovery. Only `/cancel`, `stop`, or an unrecoverable error ends an interactive wait.
