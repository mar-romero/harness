# Change Impact Analysis Reference

## Research-driven objective

Repository-level tasks fail when an apparently local change has non-local consequences. This skill operationalizes:
- repository graph guidance;
- incremental dependency awareness;
- change may-impact analysis;
- targeted verification of affected nodes.

It complements, rather than replaces, `context_graph.py`, ACI symbol/caller tools, tests and independent verification.

## Graph semantics

For a seed file `A`:

- `dependencies`: files that `A` imports/depends on.
- `dependents`: files that depend on/import `A`.
- `related_tests`: tests connected by dependency edges, reverse edges or test-file affinity.
- `affected`: bounded transitive union used for inspection and check selection.

A file appearing in `affected` does **not** mean it must be edited.

## Seed selection

Priority:
1. explicit `task.files`;
2. high-scoring code files from the bounded context pack;
3. relevant tests only as secondary seeds.

Policy/config files that are always included in context must not become seeds merely because they are always present.

If seed confidence is low, the orchestrator should escalate repository localization before implementation.

## Severity

Impact severity uses risk plus graph signals such as:
- number of seeds;
- fan-in (reverse dependents);
- fan-out;
- transitive impact size;
- critical-path names such as auth, security, payment, migration, schema or persistence.

Severity is a routing/support signal, not a correctness verdict.

## Pre-change workflow

1. Build the context pack.
2. Run `impact_analysis.py plan`.
3. Inspect direct dependents and related tests.
4. For high/critical impact, involve the planner before implementation.
5. If a critical edge looks ambiguous, confirm it with ACI symbol/caller/dependency tools.

## Post-change workflow

1. Read actual changed files from git diff.
2. Compare with the pre-change predicted set.
3. Run `impact_analysis.py verify`.
4. Unexpected files force re-analysis.
5. For R2/R3, the verifier may explicitly approve an expanded impact surface with a reason.

## Anti-patterns

Do not:
- edit every node in the impact set;
- treat lexical/file-name affinity as proof of a dependency;
- let the graph override observed test/runtime evidence;
- silently accept new files outside the planned impact surface;
- feed the whole repository to the model merely because impact is broad.
