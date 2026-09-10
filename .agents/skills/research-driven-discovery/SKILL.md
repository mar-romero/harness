---
name: research-driven-discovery
description: Adaptively research ambiguous or expensive-to-reverse product work before executable tasks are materialized.
---

# Research-driven discovery

Use this skill only when `harness/research-policy.json` resolves the discovery to `research` or `full`. Do not turn a bounded, well-understood task into a research project.

## Sequence

1. **Research** — gather authoritative evidence relevant to the actual decision. Record source, claim, uncertainty, contrary evidence, and what decision it changes.
2. **Discover** — identify stakeholders, desired outcomes, tensions, assumptions, and blocking unknowns. Invert major assumptions before they harden into architecture.
3. **Model** — in `full` mode, use `domain-modeling` to establish vocabulary, relationships, and invariants.
4. **Decide** — record alternatives, evidence, rationale, consequences, and reversibility. Decisions must be refutable rather than post-hoc justification.
5. **Architect** — describe module boundaries and provenance from user need → evidence → decision → design.
6. **Scenarios** — write observable behavior scenarios that can later become acceptance tests/BDD/TDD inputs.

Persist only evidence-backed material. Unknowns remain explicit. Research artifacts do not authorize implementation; approved product discovery must still materialize bounded tasks through the existing planning control plane.

Use `python scripts/research_discovery.py status <planning/discovery/...json>` to determine whether the required Research-RDD artifacts are complete.
