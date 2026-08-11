# Repository Guidance

## Code Review Rules

### Session isolation

- Treat `(user_id, session_id)` as the isolation boundary for messages, summaries, and todos. Flag storage queries or tool-state access that omit either key because they can leak state across users or windows.
- Safe path: pass both identifiers through `ToolContext` and scope every persistence operation with both values.

### Agent loop and context integrity

- Preserve every `tool_use` / `tool_result` pair, including its call ID, across persistence, provider conversion, and context compaction. Flag changes that split or reorder a pair because the next model call may become invalid or use the wrong result.
- Safe path: compact only at complete-message boundaries and add provider-conversion regression tests when the neutral message format changes.

### Credentials and verification

- Never write API keys or provider credentials to SQLite, traces, fixtures, examples, or committed files. Read secrets from environment variables and redact sensitive tool arguments before production logging.
- Changes to the runtime loop, session storage, compaction, or provider adapters must include focused regression tests and keep the maximum-iteration termination path intact.
