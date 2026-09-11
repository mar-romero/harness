---
name: context-engineering
description: Build a minimal reproducible context pack for an agent without leaking secrets or flooding the context window.
---

# context-engineering

Use `python scripts/context_compiler.py`. Always include policy/task metadata, explicitly referenced files and highly relevant paths. Exclude secrets, generated output, VCS internals and oversized unrelated files. Record path, size, hash and inclusion reason; do not duplicate whole repositories into prompts.
