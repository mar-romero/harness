---
name: context-engineering
description: Build a minimal reproducible context pack for an agent without leaking secrets or flooding the context window.
---

# context-engineering

Use `python3 scripts/context_compiler.py`. The compiler applies selective retrieval: trivial explicit-file work skips graph retrieval overhead, while uncertain/high-impact work uses CodeGraph when available and a local graph/symbol fallback otherwise.

Consume context in this order:

1. policy/task metadata already provided by the harness;
2. `structured_context.codegraph_explore` when present;
3. `structured_context.repo_map` and `structured_context.symbol_snippets`;
4. `repo_explore` for one missing semantic question;
5. `repo_read_range` for a missing source range;
6. whole-file reads only as a last resort.

Treat source already present in structured context as read. Do not reproduce it through grep/read loops. The `files` array is a **candidate surface**, not an instruction to load every file completely.

Always exclude secrets, generated output, VCS internals, `.codegraph/` index data and oversized unrelated files. Preserve path, size, hash and inclusion reason for reproducibility. Keep explorer/verifier transcripts isolated; downstream agents receive typed handoffs and durable artifacts, not full prior conversations.
