---
name: task-router
description: Classify task type, risk, required capabilities, agents and skills using the deterministic harness routing contract.
---

# task-router

Use `python scripts/task_router.py <task.json>`. Treat the output as the default execution graph. If confidence is low, contradictory, or R3, escalate. Do not downgrade risk to avoid review. Explicit task metadata overrides keyword heuristics only within policy.
