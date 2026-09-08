---
name: change-impact-analysis
description: Map likely repository-wide consequences of a change using dependency direction, reverse dependents, related tests and post-diff verification before claiming completion.
---

# change-impact-analysis

Treat repository changes as dependency-aware work, not isolated file edits.

Before implementation, identify the smallest credible set of seed files from explicit task files or the bounded context pack. Build a change-impact view that separates:
- dependencies the seed relies on;
- reverse dependents that may be affected by seed changes;
- related tests;
- transitive neighbors within the configured depth.

Use the deterministic `scripts/impact_analysis.py plan` artifact as the baseline. When the graph is uncertain, confirm critical relationships with ACI repository tools such as symbol, caller and dependency inspection rather than guessing.

The impact report is advisory for low-risk work and a verification obligation for higher-risk work. Do not edit every impacted file automatically: impact means "inspect/verify", not "must modify".

After implementation, compare the actual diff with the pre-change impact set. Unexpected changed files require re-analysis. For R2/R3, closure requires a passing impact verification or a verifier-reviewed expansion with a recorded reason.

Prefer targeted checks/tests associated with affected areas, then the route's required deterministic checks. Do not infer correctness from graph proximity alone.

Read `REFERENCE.md` when deeper guidance is required.
