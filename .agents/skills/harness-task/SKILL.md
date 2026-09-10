---
name: harness-task
description: Execute an existing durable task through the Codex harness orchestrator and its routed evidence-backed lifecycle.
---

# Harness task

Use this skill when the user supplies an existing task JSON and asks to execute it.

Invoke the `harness-orchestrator` custom agent with the task path. It must first run:

`python scripts/providers/codex_activate_task.py <task-path>`

Then it must follow the route and `progress.json.current_step`, delegate only routed roles, use typed handoffs, honor adaptive TDD, run authoritative checks, and require the finish gate before reporting success.

If an integration point is not provider-neutral yet, stop at that control-plane failure and report it. Never replace a missing check, immutable task snapshot, or evidence artifact with a manual assertion.
