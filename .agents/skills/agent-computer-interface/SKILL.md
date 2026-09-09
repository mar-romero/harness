---
name: agent-computer-interface
description: Use the narrow typed Harness ACI for token-efficient repository inspection, git state and deterministic checks before falling back to raw shell commands.
---

# Agent Computer Interface

Prefer the `harness-aci` MCP tools over generic shell commands whenever a matching operation exists. The ACI returns bounded structured output, respects repository/secret exclusions and does not accept arbitrary shell commands.

Use these tools first:

- `repo_explore` **before** recursive search/read loops for architecture, flow tracing, localization or unfamiliar areas. It uses CodeGraph when an index is available and otherwise returns a bounded local symbol/repo-map fallback. Treat source returned by `repo_explore` as already read; do not reopen the same file just to reproduce its content.
- `repo_search` instead of `grep`, `rg` or recursive `find` for a precise text lookup not answered by `repo_explore`.
- `repo_read_range` instead of `cat`, `sed` or whole-file reads when a missing detail requires source outside the compact exploration result.
- `repo_symbol` for narrow lexical declaration discovery.
- `repo_callers` for bounded lexical reference candidates when semantic CodeGraph data is unavailable; do not claim semantic call-graph certainty from this fallback.
- `repo_dependencies` for import/dependency extraction.
- `git_status` and `git_diff` instead of shelling out to Git for inspection.
- `tests_run`, `lint_run` and `diagnostics_get` only with named profiles from `harness/aci-policy.json`.

Token discipline:

1. Ask `repo_explore` one focused question rather than issuing several broad searches.
2. Reuse returned source, paths and call-flow evidence instead of re-reading it.
3. Prefer symbol/range reads over complete files. Exact unchanged `repo_read_range` requests are content-addressed within one runtime execution; on a cache hit reuse the prior content instead of requesting it again. Use `force=true` only when the content is genuinely no longer available in the current model context.
4. Read a whole file only when the task genuinely depends on file-global invariants that bounded source cannot establish.
5. Return compact typed handoffs; never forward exploratory transcripts to the next agent.

Raw shell remains an escape hatch only when no ACI operation can express the required action. Record why the escape hatch was needed when it materially affects evidence or reproducibility.

The ACI is read/inspect/check oriented. Source writes remain provider-native and subject to the existing single-writer permissions and harness gates; do not bypass those controls through MCP.
