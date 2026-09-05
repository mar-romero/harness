---
name: adaptive-tdd
description: Apply adaptive test-driven development with fail-to-pass evidence, negative and boundary testing, small cycles, and independent test design when risk warrants it.
---

# adaptive-tdd

Use the TDD mode selected by the route; do not force test-first where the mode says it is not applicable.

For `tdd_required`, work in small RED -> GREEN -> REFACTOR cycles. Before production code changes for the behavior, add or select a focused test and execute it. RED is valid only when the test fails for the expected behavioral reason, not because of syntax, fixture, import, environment or unrelated failures. Record RED evidence. Implement the smallest coherent change, rerun the same test, record GREEN evidence, then refactor without changing behavior and run the relevant regression checks.

For `characterization_then_tdd`, first capture important existing behavior with passing characterization tests, then use RED -> GREEN -> REFACTOR for the requested change.

For `spike_then_tdd`, do not invent an uncertain external or technical contract. Resolve the blocking uncertainty first, preserve the result as a contract/research artifact, then design executable behavior and enter RED -> GREEN -> REFACTOR.

For `tdd_preferred`, use test-first when the behavior has a stable observable contract. A documented reason may justify test-after. For `test_after_allowed` or `not_applicable`, use the repository's normal validation strategy.

Always consider happy path, negative path, boundary values, invalid input, partial failure, repetition/retry and concurrency when applicable. Add only cases that can distinguish realistic defects.

The `test-designer` must derive behavior from acceptance criteria and repository/external evidence, not from the implementer's proposed patch. The implementer may write tests but must not weaken independently specified behavior to make a patch pass.

Do not optimize for coverage percentage. Prefer defect-detection value, fail-to-pass evidence, mutation adequacy when justified, and deterministic tests. Keep cycles small and uniform. Read `REFERENCE.md` when deeper guidance is required.
