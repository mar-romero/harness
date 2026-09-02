---
name: chained-work
description: Coordinate a sequence of dependent work units without losing handoff evidence or expanding scope.
---

# chained-work

Represent the chain explicitly: inputs, owner, outputs, evidence and next dependency. Each unit must be independently understandable and must not silently change upstream acceptance criteria. Stop the chain when an upstream unit is blocked or evidence is insufficient.
