---
name: progressive-agent-budget
description: Start with the smallest safe agent team and deterministically escalate support agents only when risk, impact, uncertainty or failed progress justifies them.
---

# progressive-agent-budget

Use the smallest safe team for the current stage. More agents are not automatically better.

Never remove mandatory lifecycle controls required by risk. The implementer remains the single writer. Required reviewer, test-auditor, verifier, security-reviewer, human gate and TDD test-designer obligations remain intact.

Progressive budgeting applies mainly to support work:
- additional planning;
- debugging;
- extra repository localization;
- external-contract research.

Use `scripts/agent_budget.py` as the authoritative runtime budget state. At initialization it separates:
- current pre-implementation agents;
- mandatory gate agents;
- deferred support agents;
- escalation history.

Escalate when deterministic signals justify it: high/critical impact, low localization confidence, unresolved external contract, blocked work, failed implementation/checks, or repeated failures. Do not add agents ceremonially.

When the budget escalates, delegate only newly activated support agents relevant to the signal, then continue the existing task. Do not restart the entire workflow unless the progress policy says to replan.

Read `REFERENCE.md` for the escalation matrix.
