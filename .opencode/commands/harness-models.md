---
description: Inspect OpenCode runtime model inventory and identify models that still need reviewed capability profiles.
agent: harness-orchestrator
---

Run `python scripts/providers/opencode_inventory.py` and summarize the current OpenCode model inventory. Do not invent capability scores. If important models are unprofiled, explain that `harness/opencode-model-overrides.json` is the reviewed source for reasoning, coding, reliability and latency scores.
