---
description: Execute one durable harness task through routing, context, model selection, delegation, review and finish gates.
agent: harness-orchestrator
---

Execute the harness task at `$ARGUMENTS`.

First run `python3 scripts/providers/opencode_activate_task.py $ARGUMENTS`. Read the generated route, context pack and model selections. Follow the routed risk workflow exactly, delegate only the required specialist agents, record evidence, and run the finish gate before claiming completion.
