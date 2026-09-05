---
name: sprint-planning
description: Build a bounded execution batch from approved tasks using dependencies, value, risk reduction, uncertainty and capacity instead of arbitrary ordering.
---

# sprint-planning

Use this skill only after there are approved, executable tasks. A sprint here is a bounded execution batch, not a promise of calendar duration.

## Goal first

Define one observable sprint goal. Do not create a sprint that is merely a bag of unrelated tasks. Prefer a vertical slice that produces a demonstrable capability or removes a major uncertainty.

## Readiness

Only schedule tasks whose blocking product questions are resolved. A task with unmet dependencies may appear in a later execution wave but must not be executed before those dependencies close.

## Capacity

Use complexity units from `harness/product-discovery-policy.json`:

- `XS = 1`
- `S = 2`
- `M = 3`
- `L = 5`
- `XL = 8`

Default sprint capacity is 12 units unless the user/repository supplies a different explicit capacity. Do not estimate hours unless the user explicitly asks for time estimates.

## Priority

Score candidate tasks on a 1–5 scale for:

- user/business value;
- dependency-unlock value;
- risk/uncertainty reduction;
- urgency;
- confidence/readiness.

Priority is:

`0.35*value + 0.25*dependency_unlock + 0.20*risk_reduction + 0.10*urgency + 0.10*confidence`

Use priority to choose among ready tasks; dependencies remain authoritative over the numerical score.

## Balance

Prefer:

1. one end-to-end vertical slice;
2. early spikes that unblock expensive decisions;
3. integration/data-contract risk before polish;
4. no more than one `XL` task in a default sprint;
5. a small amount of capacity slack rather than filling the sprint with low-value work.

## Output

Create a `proposed_sprint` in the discovery dossier with:

- id and goal;
- capacity units;
- ordered task IDs;
- execution waves;
- selection rationale;
- deferred task IDs.

Validate/materialize through `scripts/product_planning.py`. If the user has explicitly asked to begin execution, the orchestrator may activate the first unblocked task after materialization.
