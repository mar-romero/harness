# Progressive Agent Budget Reference

## Principle

Complex multi-agent systems add cost, latency and coordination failure modes. Use specialization where it has demonstrated value, but preserve a simple path for ordinary work.

## Roles

### Always/stage-mandatory
- `implementer`: execution writer.
- `test-designer`: when Adaptive TDD route requires independent test design.
- `reviewer`: required by R1+ route.
- `test-auditor`: required by R2+ route.
- `verifier`: required by R2+ route.
- `security-reviewer`: required by security/R3 route.
- human gate: required by R3.

These are not removed by progressive budgeting.

### Support agents
- `explorer`: initial localization/context support.
- `planner`: initial for R2/R3 or high-impact work; otherwise defer when possible.
- `debugger`: normally deferred until failure/reproduction is needed.
- `docs-researcher`: initial only when an external contract is materially unresolved; otherwise defer.

## Escalation signals

- `high_impact`: activate planner if available.
- `critical_impact`: activate planner and an additional localization pass.
- `low_localization_confidence`: activate explorer/planner.
- `external_uncertainty`: activate docs-researcher.
- `implementation_fail`: activate debugger; planner if repeated/high impact.
- `checks_fail`: activate debugger; planner after repeated failure.
- `review_fail`: activate planner when the finding changes assumptions or scope.
- `blocked`: activate planner plus the support specialist relevant to the block.
- `repeated_failure`: activate planner + debugger.

## Non-escalation examples

Do not add debugger merely because a task is a bug if a failing regression test already localizes the defect and the first implementation succeeds.

Do not add planner to a small R1 edit with low impact, clear acceptance criteria and successful first-pass checks.

Do not add docs-researcher merely because a file uses an SDK when no external contract uncertainty is involved.

## Budget state

Runtime state lives under:

`.harness/runs/<task>/agent-budget.json`

It records:
- available agents from the route;
- current support agents;
- mandatory gate agents;
- deferred support agents;
- signals seen;
- escalation events.

The state is runtime evidence and should not be committed.
