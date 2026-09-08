---
name: harness-request
description: Convert a user request into one durable Codex harness task, preserving the original request and activating the Codex binding.
---

# Harness request

Use this skill when the user asks to start a meaningful repository change from conversational input.

Preserve the request verbatim. For a non-English request, create canonical English, a back-translation, and an `EXACT_INTENT` attestation with `scripts/request_normalizer.py`; stop if any material ambiguity remains.

Create or update one task JSON under `tasks/` with explicit acceptance criteria and structured `risk_factors`. Then invoke the `harness-orchestrator` custom agent with that task. The orchestrator must activate it through:

`python3 scripts/providers/codex_activate_task.py <task-path>`

Do not begin implementation, delegate specialists, or claim completion before activation succeeds. The original request remains the semantic reference; downstream work uses its canonical English form.
