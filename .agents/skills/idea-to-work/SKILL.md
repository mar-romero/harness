---
name: idea-to-work
description: Turn a vague product idea or broad feature into a challenged, clarified and approval-ready project/epic/feature/spike/task structure before implementation.
---

# idea-to-work

Use this skill when the user expresses a product idea, broad feature, unclear goal, or asks to improve an idea before implementation. Do not force a multi-outcome idea into one executable task.

## 1. Classify the work unit

Choose the smallest truthful class:

- `QUESTION`: informational conversation; do not create planning artifacts.
- `SPIKE`: a material uncertainty prevents a credible implementation plan.
- `TASK`: one bounded, independently verifiable outcome.
- `FEATURE`: one user-visible capability that requires multiple tasks.
- `EPIC`: several related features or a cross-cutting capability.
- `PROJECT`: a new product or initiative containing multiple epics.

If unsure between two adjacent levels, choose the larger level until decomposition proves the smaller one is sufficient.

## 2. Discover the idea before decomposing it

Build a concise discovery dossier covering:

- target user / actor;
- problem being solved and current pain;
- desired outcome and observable success metrics;
- MVP scope and explicit non-goals;
- key workflows and business rules;
- data sources, integrations and ownership;
- security, privacy, credentials and compliance boundaries;
- operational constraints and failure modes;
- assumptions, unknowns and dependencies.

Repository evidence may answer technical questions. Do not ask the user for reversible implementation details that can be resolved from repository evidence.

## 3. Ask only material questions

Use `harness/product-discovery-policy.json` as the authority. Ask at most 3 questions in one turn and at most 2 clarification rounds unless the user explicitly asks for deeper discovery.

Ask only when the answer can materially change at least one of: product behavior, scope, architecture, risk, data contract, security/privacy boundary, acceptance criteria, success metric, or irreversible cost.

Prefer high-information questions. If a detail is reversible or safely inferable, state the assumption instead of blocking. If the user declines to answer, preserve the unknown and propose the safest reversible assumption.

## 4. Improve, do not merely transcribe

Before proposing work, challenge the idea constructively:

- identify hidden assumptions;
- identify missing actors or workflows;
- identify data that may not exist or may arrive late/incompletely;
- identify security/privacy and external-contract risks;
- identify likely failure/edge cases;
- propose a simpler MVP or vertical slice when it reduces uncertainty;
- propose how success will be measured;
- convert unresolved technical/product uncertainty into a `SPIKE` instead of pretending it is resolved.

Do not invent product decisions. Clearly distinguish user decisions, evidence-backed conclusions, and assumptions.

## 5. Decompose by outcomes and dependencies

Use this hierarchy:

`PROJECT → EPIC → FEATURE → TASK/SPIKE`

Tasks must be independently executable by the harness. Each task needs a canonical English description, acceptance criteria, dependencies, a size (`XS/S/M/L/XL`), and structured risk factors compatible with `tasks/TASK_TEMPLATE.json`.

Avoid horizontal decomposition that produces unusable intermediate states when a small vertical slice is possible. Prefer an end-to-end slice that can demonstrate value and expose integration risk early.

## 6. Approval boundary

Discovery may be persisted as `draft` or `ready_for_approval`, but executable tasks must not be materialized while blocking questions remain. Materialize executable `tasks/*.json` only after the user has approved the proposed direction or clearly instructed the harness to proceed.

Store the dossier under `planning/discovery/<DISCOVERY-ID>.json`, validate it with:

`python3 scripts/product_planning.py validate planning/discovery/<DISCOVERY-ID>.json`

After approval, set `status` to `approved` and run:

`python3 scripts/product_planning.py materialize planning/discovery/<DISCOVERY-ID>.json`

Derived task files intentionally omit `request.translation`: they are derived from an approved planning artifact rather than falsely claiming that each derived task was the user's original sentence. The materializer records provenance in each task's `origin` field.

## 7. Handoff to execution

If the user asked only to explore/refine the idea, stop after the improved plan. If the user explicitly said to start/implement/build after approving the plan, use `sprint-planning`, materialize the sprint, and activate the first unblocked task through the normal provider task activation flow.
