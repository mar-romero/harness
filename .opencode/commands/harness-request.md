---
description: Convert a user request in any language into the canonical English harness task without losing intent, then activate it.
agent: harness-orchestrator
---

Treat `$ARGUMENTS` as the original user request. Preserve it verbatim. If it is not English, translate it into canonical English with exactly the same intended scope, constraints, negations, literals, paths, code and numbers; produce a back-translation and compare it to the original. If any material ambiguity exists, stop and ask the user instead of guessing.

Create/update a task JSON under `tasks/` with `request.original_text`, `request.original_language`, `request.canonical_english`, translation metadata, acceptance criteria and explicit structured `risk_factors`. Validate the translation using `python3 scripts/request_normalizer.py ... --equivalence EXACT_INTENT`, then activate the normalized task with `python3 scripts/providers/opencode_activate_task.py <task-path>`.

All downstream subagents must receive and work from the canonical English request while the original remains the immutable semantic reference.
