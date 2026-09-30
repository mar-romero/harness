# Cursor adapter

This folder adapts the harness to Cursor.

- `agents/`: generated role adapters.
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
- There is no Cursor-specific activation script. Bind a task through the
  provider-neutral binding, which `task_checks.py`, `receipt_review.py` and
  `attest.py` accept:
  `python scripts/subscription_bridge.py activate tasks/<TASK>.json --providers cursor`.
- Generated subagents use `model: inherit`, so every role runs on the chat
  model. Reviewer/verifier independence must be recorded as weak unless the role
  runs through `subscription_bridge.py run` with a different model.
