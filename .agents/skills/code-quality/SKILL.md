---
name: code-quality
description: Apply pragmatic, language-agnostic code-quality standards for maintainability, robustness, safe evolution and evidence-backed review without expanding scope.
---

# code-quality

Optimize for correctness first, then clarity, cohesion, safe evolution and operational robustness. Follow repository-local conventions and tooling; do not impose generic style over established project standards.

Keep changes small and purposeful. Prefer simple control flow, explicit boundaries and intention-revealing names. Keep modules and functions cohesive; split them when they mix unrelated reasons to change, hide important invariants or become difficult to test or review.

Use DRY, SOLID and design patterns only when they solve demonstrated duplication, coupling, volatility or substitutability problems. Do not create abstractions, interfaces, factories, layers or dependencies speculatively. Prefer duplication over the wrong abstraction when the shared behavior is not yet stable.

Validate untrusted or boundary inputs. Make error behavior explicit, preserve useful causal context and never silently swallow failures. Define ownership and lifetime for files, connections, locks, transactions and other resources.

For external calls, define timeouts and bounded retry behavior where appropriate; make retryable operations idempotent or explicitly non-retryable. For persistence and concurrency, preserve invariants under partial failure, races and retries. Treat migrations and contract changes as compatibility work, not local refactors.

Add observability where operators need to distinguish success, degraded behavior and failure, but never log secrets or unnecessary sensitive data. Avoid obvious unbounded work, N+1 access patterns and accidental quadratic behavior; optimize further only with evidence.

Tests should protect changed behavior and important failure modes. Do not replace `test-strategy`; use it for broader test design. Reviewers should report actionable quality defects with concrete impact and location, and must separate correctness/reliability risks from cosmetic preferences.

Do not refactor unrelated code merely to satisfy this skill. When deeper guidance is needed, read `REFERENCE.md`.
