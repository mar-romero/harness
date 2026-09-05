# Conversational product discovery

This overlay adds a product-planning boundary in front of the existing executable task workflow.

## User experience in OpenCode

A broad message such as:

> Quiero una app que analice mis anuncios de Meta y me ayude a mejorar ROAS, CPA y CTR.

should not immediately become one giant implementation task. The orchestrator uses `idea-to-work` to:

1. classify the request as project/epic/feature/task/spike;
2. identify missing product decisions and risky assumptions;
3. ask only high-information questions;
4. propose a refined problem statement, metrics, MVP and non-goals;
5. challenge the idea with failure modes and a simpler vertical slice;
6. decompose approved work into independently executable tasks;
7. optionally create a bounded sprint through `sprint-planning`;
8. activate the first unblocked task only when the user has explicitly asked to start.

## Why approval exists

The harness may resolve reversible engineering details from repository evidence, but it must not silently invent material product decisions. Draft planning artifacts can exist before approval; executable task files are emitted only when the discovery dossier has `status: approved` and no blocking questions.

## Durable layout

```text
planning/
  discovery/   # source dossier; original request + clarification + challenge
  projects/
  epics/
  features/
  spikes/
  sprints/

tasks/         # existing executable harness units
```

## Materializer

Validate:

```bash
python3 scripts/product_planning.py validate planning/discovery/DISC-XYZ.json
```

Materialize after approval:

```bash
python3 scripts/product_planning.py materialize planning/discovery/DISC-XYZ.json
```

The script is bounded to repository-local planning/task artifacts and refuses to overwrite a different existing task unless `--force-tasks` is explicit.

## Derived-task language semantics

A project idea may be written in Spanish while the derived tasks are canonical English engineering statements. A derived task is not a literal translation of the original product sentence. Therefore the generated task records do not fabricate `EXACT_INTENT` translation attestations. They retain provenance through `origin.discovery_id` and point back to the discovery dossier where the original request is preserved.
