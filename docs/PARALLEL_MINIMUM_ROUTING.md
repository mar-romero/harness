# Parallel gates + minimum-sufficient model routing

## Goal

This layer changes two runtime behaviors without changing the canonical harness lifecycle, agents, skills, TDD/RDD, Receipt-RDD, evidence chain, worktree isolation, or finish gates.

1. Independent post-implementation gates can execute concurrently against one frozen candidate.
2. Model routing chooses the **least-resource model that is sufficient** for the task/role/risk instead of choosing the highest-scoring model by default.

## Parallel execution

After implementation and deterministic checks/verification assessment, the routed stages `REVIEW`, `TEST_AUDIT`, and `VERIFY` form a parallel batch when at least two are present:

```text
frozen candidate
      |
      +--> reviewer      -> provider/model A
      +--> test-auditor  -> provider/model B
      +--> verifier      -> provider/model C
      |
      v
fan-in
      |
commit typed handoffs in canonical order
```

All workers receive the same candidate subject hash and independently evaluate the candidate. They do not consume sibling-review transcripts.

The workers are **read-only harness roles**. The single writer remains `implementer` in `.worktrees/<TASK>`.

### Why commit after fan-in

Parallel outputs are pending, not authoritative. After all workers finish, the harness verifies that the candidate subject hash is unchanged and commits results in normal workflow order:

```text
REVIEW -> TEST_AUDIT -> VERIFY
```

If an earlier gate fails, later pending outputs are not reused after repair. A new candidate must be reviewed again. This preserves Receipt-RDD/candidate binding.

`SECURITY_REVIEW` intentionally remains after `IMPACT_VERIFY`. This avoids spending a security-model invocation before the actual changed surface has passed impact verification.

## Concurrency policy

`harness/parallel-policy.json` is the authority.

Defaults:

```text
global max workers: 4
Claude: 2
Codex: 2
Copilot: 2
Gemini: 2
Cursor: 1
Grok: 1
```

These values are local scheduler limits, not promises that a subscription plan will always allow that concurrency. If a provider rate-limits or exhausts quota, the existing runtime failover path can choose another eligible provider.

Two different harness roles may therefore run through Claude at the same time, for example:

```text
reviewer     -> Claude Sonnet
test-auditor -> Claude Sonnet
verifier     -> Codex
```

They are separate official-CLI processes/sessions. They are not required to be provider-native subagents.

## Minimum-sufficient routing

`harness/models.json` now uses:

```json
"selection": {
  "strategy": "minimum_sufficient"
}
```

For each canonical role the router computes:

- hard model-class floors;
- risk floors;
- dynamic task/role targets for reasoning, coding, tool use, and reliability;
- a sufficiency floor equal to the hard floor or 90% of the dynamic target, whichever is higher.

The 90% factor is intentional because the 0..5 capability catalog is an estimated routing prior, not a calibrated physical measurement. Hard R2/R3 safety requirements remain exact.

Candidates below the sufficiency floor are not considered "cheap enough"; they are insufficient.

Among sufficient candidates the router minimizes **resource burden**:

```text
60% capability surplus
25% subscription-quota penalty
10% latency penalty
 5% local-evidence penalty
```

So a model with exactly enough capability is preferred over a frontier model that adds unnecessary capability for that stage.

Independence rules still override pure savings when required. Reviewer/test-auditor/verifier can be pushed to another model/family/vendor so that the system does not save quota by having the implementer effectively review itself.

### Reasoning effort

Reasoning effort is also minimum-sufficient. The task/role/risk pressure chooses a required tier. If the runtime does not expose that exact tier, the router now prefers the nearest **higher** tier before falling back lower.

Example:

```text
required: high
runtime offers: medium, xhigh
selected: xhigh
```

not `medium`.

## Risk behavior

For R0-R2, if no model meets every dynamic target, the router can use the least-resource model that still clears the canonical hard capability floors and marks the selection `sufficiency_degraded=true`.

For R3, no dynamic-sufficient candidate is fail-closed: selection blocks rather than silently under-provisioning a critical stage.

## Observability

Every model selection now records:

- `selection_strategy`;
- `sufficiency_floor`;
- `sufficiency_degraded`;
- normal quality score;
- `resource_burden` and its components;
- reasoning effort;
- independence decision.

Parallel batches are recorded under:

```text
.harness/runs/<TASK>/parallel-batches/
```

with the candidate subject hash and provider/model result for each gate.
