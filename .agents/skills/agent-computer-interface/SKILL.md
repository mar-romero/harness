---
name: agent-computer-interface
description: Use the narrow typed Harness ACI for repository inspection, git state and deterministic checks before falling back to raw shell commands.
---

# Agent Computer Interface

Prefer the `harness-aci` MCP tools over generic shell commands whenever a matching operation exists. The ACI returns bounded structured output, respects repository/secret exclusions and does not accept arbitrary shell commands.

Use these tools first:

- `repo_search` instead of `grep`, `rg` or recursive `find` for text discovery.
- `repo_read_range` instead of `cat`, `sed` or whole-file reads when a bounded range is enough.
- `repo_symbol` for lexical declaration discovery.
- `repo_callers` for bounded lexical reference candidates; do not claim semantic call-graph certainty from this tool.
- `repo_dependencies` for import/dependency extraction.
- `git_status` and `git_diff` instead of shelling out to Git for inspection.
- `tests_run`, `lint_run` and `diagnostics_get` only with named profiles from `harness/aci-policy.json`.

Raw shell remains an escape hatch only when no ACI operation can express the required action. Record why the escape hatch was needed when it materially affects evidence or reproducibility.

The ACI is read/inspect/check oriented. Source writes remain provider-native and subject to the existing single-writer permissions and harness gates; do not bypass those controls through MCP.
