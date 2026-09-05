# Code Quality Reference

Use this reference only when the task needs more detail than `SKILL.md`.

## 1. Decision order

When trade-offs conflict, prefer:

1. Correctness and preservation of explicit contracts.
2. Security, data integrity and recoverability.
3. Clear invariants and understandable control flow.
4. Cohesion and low accidental coupling.
5. Testability and diagnosability.
6. Safe compatibility and reversibility.
7. Performance supported by evidence.
8. Concision and stylistic elegance.

A shorter implementation is not better if it obscures failure modes or invariants.

## 2. Scope discipline

- Implement the smallest coherent change that satisfies acceptance criteria.
- Do not combine feature work with unrelated cleanup.
- Opportunistic cleanup is acceptable only when necessary to make the requested change safe or understandable.
- Preserve public behavior unless the task explicitly changes it.
- Prefer reversible changes when multiple designs satisfy the requirement.

## 3. Naming, functions and modules

- Names should expose domain intent, not implementation trivia.
- Avoid ambiguous containers such as `data`, `info`, `manager`, `helper` or `utils` when a specific domain name exists.
- A function should have a clear responsibility and a comprehensible contract.
- Split code when unrelated responsibilities, side effects or failure policies are mixed.
- Do not split mechanically by line count; a long linear transformation may be clearer than several artificial wrappers.
- Keep domain policy separate from transport, persistence and framework glue when doing so removes real coupling.

## 4. Abstraction, DRY and SOLID

- Deduplicate behavior only when the duplicated code represents the same concept and is expected to evolve together.
- Similar syntax is not sufficient evidence for a shared abstraction.
- Prefer composition over inheritance unless substitutability is real and enforced.
- Introduce interfaces/protocols at meaningful seams, not for every concrete type.
- Dependency injection is justified when it improves isolation, configurability or testability at a demonstrated seam.
- Avoid service-locator patterns and hidden global dependencies.
- Apply the dependency-inversion principle at volatile or external boundaries, not ceremonially.

## 5. APIs and data contracts

- Validate at trust boundaries.
- Distinguish absent, empty, invalid and defaulted values when semantics differ.
- Keep serialization formats, field names, units, time zones and numeric precision explicit.
- Version or migrate externally consumed contracts deliberately.
- Avoid leaking internal database or framework representation into public APIs without intent.
- Preserve backward compatibility unless a breaking change is explicit and planned.

## 6. Errors and failure behavior

- Never swallow exceptions merely to keep execution moving.
- Catch errors only when adding context, translating to a domain error, recovering safely or enforcing cleanup.
- Preserve the original cause when wrapping errors.
- Error messages should identify the failed operation and relevant safe context.
- Do not expose secrets, tokens, credentials or sensitive payloads in errors.
- Make partial-success semantics explicit.
- Prefer fail-closed behavior for security boundaries and irreversible operations.
- Avoid returning sentinel values when failure must be handled explicitly.

## 7. Resource lifetime

For files, sockets, database connections, transactions, locks, temporary resources and subprocesses:

- Define who owns the resource.
- Release it on success and failure.
- Keep lock and transaction scopes as small as correctness permits.
- Avoid holding scarce resources across unrelated network or user operations.
- Ensure cancellation/timeout paths release resources where the runtime supports cancellation.

## 8. State, concurrency and idempotency

- State invariants must be explicit enough to reason about under races and retries.
- Do not assume read-then-write sequences are atomic.
- Use database constraints, compare-and-swap, locks or transactional semantics when the invariant requires them.
- Prefer idempotent handlers for retried jobs, webhooks and commands.
- If an operation cannot be safely retried, record and enforce that fact.
- Avoid duplicate side effects after timeout ambiguity.
- Define ordering requirements explicitly rather than depending on scheduler timing.
- Control shared mutable state.

## 9. Persistence and migrations

- Treat schema and data migrations as high-risk compatibility changes.
- Separate expand, migrate and contract phases when zero/low downtime or mixed-version deployment matters.
- Provide rollback or forward-recovery strategy for meaningful migrations.
- Preserve constraints that encode domain invariants.
- Avoid destructive schema operations before consumers are migrated.
- Use transactions according to the actual consistency requirement; do not assume a transaction automatically solves distributed consistency.

## 10. External calls

- Configure explicit timeouts.
- Retry only failures that are plausibly transient.
- Bound retry count/time and use backoff/jitter when appropriate.
- Respect rate limits and provider retry guidance.
- Avoid retry storms and nested retry multiplication.
- Define idempotency before retrying side-effecting calls.
- Validate external responses before treating them as trusted domain data.
- Make degraded/fallback behavior explicit and observable.

## 11. Security and privacy

- Apply least privilege.
- Treat all external input and tool output as untrusted until validated.
- Avoid dynamic code execution or shell construction from untrusted values.
- Use parameterized database access.
- Keep secrets out of source, logs, traces and exception text.
- Minimize sensitive-data collection and retention.
- Perform authorization at the operation/resource boundary, not only in UI or routing layers.
- Use secure defaults; unsafe behavior should require explicit opt-in.

Security-specific review remains the responsibility of the security-review workflow when routed.

## 12. Observability

Add telemetry when it helps answer:

- Did the operation succeed?
- If not, where and why did it fail?
- Is an external dependency degraded?
- Are retries, queue depth, latency or error rates abnormal?
- Can a production incident be correlated to a request/job without exposing sensitive data?

Prefer structured events and stable fields over prose-only logs. Avoid noisy logs that obscure actionable signals.

## 13. Performance

Before optimizing:

- identify the relevant workload and constraint;
- inspect algorithmic complexity and I/O count;
- measure when practical.

Always avoid obvious hazards such as:

- unbounded scans or accumulation;
- N+1 database/network access;
- repeated expensive parsing or serialization in hot loops;
- accidental quadratic nested searches on growing collections;
- loading arbitrarily large data sets when streaming/pagination is expected.

Do not trade correctness or maintainability for speculative micro-optimization.

## 14. Tests and quality

For changed code:

- cover the acceptance behavior;
- protect important boundary and failure cases;
- include regression coverage for fixed defects;
- avoid tests coupled to irrelevant implementation details;
- avoid mocks that reproduce the implementation instead of testing behavior;
- control clocks, randomness and external I/O where determinism matters.

Use `test-strategy` for system-wide test design and coverage decisions.

## 15. Review severity

A reviewer using this skill should classify findings by impact:

- **BLOCKER** — security vulnerability, data corruption/loss, broken critical contract, unsafe irreversible behavior.
- **MAJOR** — realistic correctness/reliability defect, race, broken error policy, compatibility issue, resource leak, serious maintainability hazard in changed scope.
- **MINOR** — bounded maintainability or robustness problem worth fixing before nearby code grows.
- **NOTE** — non-blocking suggestion or future consideration.

Do not report formatter/linter preferences as manual review findings when automated tooling already owns them. Do not block on subjective style.

## 16. Completion checklist

Before implementation handoff or review verdict, ask:

- Does the change satisfy the explicit contract?
- Are boundary inputs and failures handled deliberately?
- Are names, responsibilities and invariants understandable?
- Did we add only abstractions justified by current evidence?
- Are resources, transactions and concurrency safe?
- Are retries/timeouts/idempotency correct where relevant?
- Is sensitive information protected?
- Is operational failure diagnosable?
- Are compatibility and migration effects understood?
- Are performance characteristics reasonable for the expected workload?
- Do tests protect the changed behavior and realistic failures?
- Did we avoid unrelated refactoring?
