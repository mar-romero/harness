# Design and architecture

Prefer simple modules with explicit responsibilities and stable boundaries.
Introduce abstractions only after concrete duplication/variation demonstrates a
shared contract. Keep side effects at boundaries. Favor reversible migrations,
compatibility windows and explicit invariants over cleverness. For concurrency,
define ownership, ordering, idempotency and retry semantics before coding.
