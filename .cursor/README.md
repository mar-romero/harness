# Cursor adapter

This folder adapts the harness to Cursor.

- `agents/`: generated role adapters.
- `rules/harness-orchestrator.mdc`: generated orchestrator rule (agent-requested).
- `commands/harness-task.md`: generated `/harness-task` command that runs it.
- `hooks/`: gate bridge for commands and writes.
- `hooks.json`: Cursor hook event configuration.
- `mcp.json`: ACI MCP server configuration.
- `README.md`: explains this adapter boundary.

The source of changes is `.agents/` plus `harness/manifest.yaml`.

## Using Cursor as the host

- Hooks read the payload as UTF-8 bytes because Cursor on Windows prefixes it
  with a BOM. An unreadable payload is denied with an explicit reason.
- Enable `harness-aci` once under Customize → MCP; project servers do not load
  until approved. For the headless CLI run `cursor-agent mcp enable harness-aci`.
- Hooks also record the chat `conversation_id` as the Cursor provider session,
  so review consent (`receipt_review.py consent`) is scoped to the current chat.
- Run a task with `/harness-task tasks/<TASK>.json`. The orchestrator rule drives
  the lifecycle; Cursor is a native binding provider for `task_checks.py`,
  `receipt_review.py` and `attest.py`.

## Per-role models

Cursor has no model-list API, so the Task tool's model IDs are recorded as the
host catalog, scored from the local OpenRouter cache, and routed per role:

```
python scripts/providers/cursor_activate_task.py --host-models <id1,id2,...>
python scripts/openrouter_sync.py --provider cursor --cache-only
python scripts/providers/cursor_activate_task.py tasks/<TASK>.json
```

Activation prints a `delegation` plan; the orchestrator passes each role's
`model` to the Task tool. Selection is the least-resource model that meets the
role's capability target, and reviewers prefer a different model, family or
vendor from the implementer. Generated `agents/*.md` keep `model: inherit` so
they stay deterministic; the model is chosen per launch.

Model IDs such as `claude-opus-5-thinking-high` fix the reasoning effort, so each
ID is one inventory entry with a single effort. Models that OpenRouter does not
score (for example `composer-*`) keep unknown capabilities and are not selected
for roles with capability floors. R2/R3 floors require `reliability`, which comes
from OpenRouter endpoint health; refresh it with `python scripts/openrouter_sync.py --all`
(set `OPENROUTER_API_KEY` for benchmarks), then rebuild with `--provider cursor --cache-only`.
`python scripts/providers/cursor_activate_task.py --clear` removes the binding.

## CodeGraph

`python scripts/codegraph_bridge.py init` builds the local `.codegraph/` index
(ignored by Git). When `status` reports `ready`, the context compiler and the
ACI search use graph evidence; otherwise they fall back to lexical search.
