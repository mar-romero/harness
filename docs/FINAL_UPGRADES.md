# Final research-driven upgrades

> Historical upgrade record. For current operational commands, provider
> boundaries, and portable-installation status, use `README.md` and `docs/`.

## 1 — Language/risk boundary

The original request is never discarded. `request.canonical_english` is the
agent-facing representation. The deterministic validator preserves code blocks,
URLs, paths, identifiers, numbers, quoted literals, negation and strict
constraints; non-English requests require back-translation and an exact-intent
attestation. Structured risk flags ensure safety classification does not depend
on translation or vocabulary.

## 3 — Runtime reliability

`scripts/runtime_eval.py` performs repeated provider rollouts through a generic
adapter contract. It records both average success and strict all-trials success
(`pass_power_k`) together with cost/latency/safety metrics.

## 4 — Typed handoffs

Authoritative specialist communication is schema-bounded, size-bounded and
persisted under the task run. Free-form discussion can exist, but it is not an
authoritative workflow artifact.

## 5 — Progress ledger

`scripts/orchestrator.py` persists the current step, attempts, failures and next
action. Two identical failures at a step cause a stall/replan recommendation;
security review never auto-retries into approval.

## 8 — Signed evidence

The original hash chain remains. Ed25519 provenance adds cryptographic
attestation tied to the evidence head and candidate/control-plane hashes. R3
requires this attestation.

## 9 — Context and memory

Context selection adds dependency/test-neighborhood signals and token estimates.
Memory is local, explicit and promotion-gated: only a task that can already pass
its finish gate can be promoted, and a human approver is recorded.

## 10 — Champion/challenger

Candidate harness variants are evaluated independently against the same cases,
including holdouts. Safety regressions force KEEP_CHAMPION. Improvements only
become proposals for human promotion; the engine cannot apply repository edits.

## Deliberately not added in this iteration

The previously researched **#2 OS/container sandbox**, **#6 narrow typed ACI tool
surface**, and **#7 continuous adversarial-security suite** are not claimed as
implemented here because the user selected only 1, 3, 4, 5, 8, 9 and 10.
