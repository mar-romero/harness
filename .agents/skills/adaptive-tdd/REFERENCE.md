# Adaptive TDD Reference

## Modes

### `tdd_required`
Use for stable, executable behavior where regressions are costly or likely:
- bug fixes and regression fixes;
- calculations and business rules;
- validation/parsing/serialization;
- state machines and deterministic transformations;
- persistence invariants;
- authorization/security-sensitive rules;
- R2/R3 logic with a stable observable contract.

Required closure evidence: `red`, `green`. Independent `design` is required when the route selects `test-designer`.

### `tdd_preferred`
Use for ordinary R1 features with a clear contract. Test-first is preferred but not a finish gate. If test-after is chosen, record the reason in the implementation handoff.

### `characterization_then_tdd`
Use for legacy/brownfield/refactor work where existing behavior is insufficiently specified. First capture relevant current behavior with passing characterization tests; then add a focused failing test for the desired behavior.

Required closure evidence: `characterization`, `red`, `green`, plus independent `design` when routed.

### `spike_then_tdd`
Use when a production implementation depends on an unresolved external/technical contract. Research or experiment first. A test oracle based on an invented contract is worse than no test.

Required closure evidence: `contract`, `red`, `green`, plus independent `design`.

### `test_after_allowed`
Use for visual-only changes, mechanical configuration, generated adapters, and similar work where test-first offers little signal. Existing checks still apply.

### `not_applicable`
Use for pure research/spike/documentation tasks that do not produce executable behavior.

## RED validity

A RED observation is valid only when:
- the intended test command was executed;
- the command exits non-zero;
- the relevant test reached its behavioral assertion/oracle;
- the failure is the expected missing/wrong behavior;
- the failure is not caused by syntax, import, fixture, setup, dependency, infrastructure or unrelated test failures.

A test that is broken is not RED evidence.

## GREEN validity

GREEN is valid only when:
- the same behavioral test or stronger equivalent runs;
- the command exits zero;
- the implementation has not weakened/removed the oracle;
- relevant regression checks still pass.

## Independent test design

The test-designer is read-only and should produce:
- observable behavior;
- test oracle;
- minimal positive examples;
- negative and boundary examples;
- invariants;
- expected RED reason;
- environmental assumptions;
- explicitly unresolved contracts.

For R2/R3, prefer a model different from the implementer when a comparable alternative exists.

## Negative testing

Do not mechanically generate one test per category. Explicitly consider:
- malformed/invalid input;
- missing/empty values;
- extremes and numeric boundaries;
- permission denial;
- dependency timeout/failure;
- partial persistence failure;
- retry/duplicate delivery;
- ordering and concurrency;
- cancellation/resource cleanup.

Only materialize cases with meaningful defect-detection value.

## Mutation testing

Mutation testing is recommended for critical calculations, authorization, privacy, financial logic, parsers and subtle regression-prone code when supported by the repository.

Do not make mutation testing a universal gate. Use targeted mutants that represent realistic faults. A high line-coverage suite that cannot kill relevant mutants is weak.

## Small-cycle discipline

Prefer:
1. one behavior or invariant;
2. one focused test/oracle;
3. observe valid RED;
4. minimal implementation;
5. observe GREEN;
6. small refactor;
7. repeat.

Avoid writing a large test suite and a large patch before the first feedback cycle.

## Anti-patterns

Reject:
- tests written to mirror the implementation rather than the contract;
- weakening assertions after RED;
- deleting a failing case to reach GREEN;
- over-mocking the code under test;
- snapshot/golden tests with no meaningful oracle;
- nondeterministic sleeps where controllable time/events exist;
- declaring RED from an environment/setup failure;
- measuring success only through coverage percentage.
