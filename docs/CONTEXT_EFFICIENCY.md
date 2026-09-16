# Context Efficiency

The harness keeps repository understanding outside the main model context whenever possible.

## Retrieval order

1. `scripts/context_compiler.py` applies a cheap deterministic retrieval gate.
2. Tiny explicit-file tasks skip structured retrieval.
3. Larger/uncertain/high-risk tasks use CodeGraph when `codegraph` is installed and `.codegraph/` is initialized.
4. If CodeGraph is unavailable, the harness falls back to its local dependency graph, a PageRank-style repo map, and bounded symbol snippets.
5. Agents use the provider-neutral `harness-aci` tool `repo_explore` before repeated search/read loops.
6. Whole-file reads are a last resort. Source already returned by `repo_explore` or the context pack is treated as read.

CodeGraph is optional. The harness does not download or execute an installer silently. The local `.codegraph/` index is excluded from context and ignored by Git.

## Optional CodeGraph setup

Install the external CLI using the vendor's supported installation method, then initialize the current repository:

```bash
codegraph init
```

You can inspect harness integration state with:

```bash
python scripts/codegraph_bridge.py status
```

The bridge invokes CodeGraph without a shell and keeps all repository paths rooted to the harness workspace.

## Context pack fields

`context.json` now contains:

- `retrieval`: why structured retrieval ran, which backend was used, and CodeGraph status.
- `structured_context.codegraph_explore`: bounded semantic source/call-path output when CodeGraph is active.
- `structured_context.repo_map`: a token-budgeted symbol map.
- `structured_context.symbol_snippets`: line-numbered fallback source snippets.
- `candidate_full_file_tokens`: estimated cost of reading every candidate file fully.
- `estimated_tokens`: estimated compact context actually intended for the model.
- `token_savings_estimate`: the difference between those estimates (diagnostic, not a billing guarantee).

## Design constraints

- Provider-neutral: Codex, Claude, Gemini, Cursor, OpenCode and Copilot continue to use the same ACI.
- Fail-soft: no CodeGraph binary/index means local retrieval, not task failure.
- Single-writer, gates, evidence and risk routing are unchanged.
- No exploratory transcript forwarding: downstream agents receive typed handoffs and durable artifacts.
