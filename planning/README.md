# Planning

This folder contains product discovery and approved planning before executable
tasks are created.

- `discovery/`: original ideas, questions, assumptions, challenges, and MVP.
- `research/`: source-backed Research-RDD findings and contrary evidence.
- `domain/`: domain vocabulary, relationships, lifecycle, and invariants.
- `decisions/`: evidence-traced decisions, alternatives, and reversibility.
- `scenarios/`: observable scenarios bridging discovery into implementation.
- `projects/`: product or initiative records.
- `epics/`: multi-feature outcomes.
- `features/`: user-visible capabilities.
- `spikes/`: bounded uncertainty-reduction work.
- `sprints/`: bounded execution batches made from approved tasks.
- `examples/`: non-live reference dossiers.
- `README.md`: explains the planning boundary.

- `discovery/` — original idea, clarification, assumptions, challenges, MVP and proposed decomposition.
- `research/` — source-backed findings and contrary evidence for Research-RDD.
- `domain/` — domain vocabulary, relationships and invariants.
- `decisions/` — evidence-traced decisions, alternatives and reversibility.
- `scenarios/` — observable behavior scenarios that bridge discovery into BDD/TDD.
- `projects/` — product/initiative records.
- `epics/` — multi-feature outcomes.
- `features/` — user-visible capabilities.
- `spikes/` — bounded uncertainty-reduction work.
- `sprints/` — bounded execution batches made from approved tasks.
- `examples/` — non-live reference dossiers.

Executable units remain under `tasks/` and continue through the normal harness workflow.

A broad conversational idea should not become a task until the user has approved the direction and blocking product questions are resolved. `scripts/product_planning.py` validates and materializes approved discovery bundles.
