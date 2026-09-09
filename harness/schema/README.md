# Schemas

This folder contains JSON contracts for task, evidence, routing, planning, review, and evaluation data.

- `aci-result.schema.json`: ACI response envelope.
- `evidence.schema.json`: append-only evidence record.
- `evolution-experiment.schema.json`: champion/challenger experiment.
- `evolution-proposal.schema.json`: proposal-only harness change.
- `evolution-run.schema.json`: recorded evolution run.
- `model-inventory.schema.json`: provider model inventory.
- `model-selection.schema.json`: per-role model selection.
- `receipt.schema.json`: candidate review receipt.
- `receipt-assessment.schema.json`: Receipt-RDD route assessment.
- `research-rdd-artifact.schema.json`: Research-RDD artifact.
- `runtime-eval-result.schema.json`: runtime evaluation result.
- `task.schema.json`: executable task input.
- `handoffs/`: typed contracts for role handoffs.
- `planning/`: typed contracts for discovery, work items, and sprints.
- `benchmark/`: typed contracts for benchmark cases and oracles.
- `README.md`: explains the schema boundary.

Schemas are contracts, not task data or runtime logs.
