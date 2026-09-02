# Audit report — Portable Agent Engineering Harness Final v4

Audit scope: consolidated v2 + v3 + OpenCode integration + requested upgrades
1, 3, 4, 5, 8, 9 and 10.

## Preserved invariants

- executable evidence outranks model claims;
- exactly one implementation writer owns a scoped worktree;
- writer cannot issue final review/verification verdicts;
- R0–R3 risk and human decision gates remain explicit;
- provider files remain generated adapters, not policy sources;
- untrusted content cannot authorize external side effects;
- evolution is proposal-only and cannot auto-edit/promote itself.

## New invariants

- non-English requests preserve the original and require a validated canonical
  English form before downstream use;
- structured risk flags outrank language-dependent keyword inference;
- authoritative specialist handoffs are JSON-schema contracts;
- retries and stalls are recorded in a durable progress ledger;
- R3 requires signed Ed25519 provenance evidence;
- reusable memory is only explicitly promoted from finish-gate-passing tasks;
- champion/challenger promotion always requires a human decision.

## What this audit can prove locally

It validates source generation/drift, unit behavior, deterministic evals,
OpenCode plugin syntax/contracts, cryptographic sign/verify with a temporary
Ed25519 key, runtime-eval adapter mechanics, routing equivalence for an EN/ES R3
case, typed handoffs, progress stall behavior, context token bounds and
champion/challenger safety behavior.

## Residual limitations

- Natural-language translation cannot be proven 100% semantically identical by
  deterministic software; the harness therefore preserves the original and
  blocks detectable ambiguity/invariant loss.
- Real provider/model quality still requires authenticated live runtime evals.
- The OpenCode plugin API is provider-owned and may evolve.
- OS/container sandboxing, narrow ACI tools and a full adversarial-security
  benchmark suite were not requested in this build and are not claimed.
- Ed25519 provenance depends on the operator protecting the private key outside
  the repository and establishing trust in the corresponding public key.
