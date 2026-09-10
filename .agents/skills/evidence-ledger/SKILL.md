---
name: evidence-ledger
description: Record machine-readable claims, checks, artifacts and verdicts in the task evidence ledger.
---

# evidence-ledger

Initialize with `python scripts/evidence.py init TASK-ID`. Add evidence with type, claim and exact command/result or source artifact. Do not edit historical entries to change outcomes; append superseding evidence. Completion gates consume the ledger.
