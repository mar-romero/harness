# Historical harness audit record

> This is a bounded historical audit record, not a current operating guide.
> Current commands and support boundaries are documented in `README.md` and
> `docs/INSTALLATION.md`.

## Recorded scope

The audit covered the consolidated core, model routing, provider integration,
and the named upgrades for language normalization, runtime evaluation, typed
handoffs, progress tracking, signed provenance, context/memory, and
champion/challenger evaluation.

## Preserved invariants

- Executable evidence outranks model assertions.
- One implementation writer owns a scoped worktree.
- The writer cannot issue its own final review or verification verdict.
- Risk levels and human decision gates remain explicit.
- Canonical roles and skills are the source; provider adapters are generated.
- Untrusted input cannot authorize external side effects.
- Evolution can propose but cannot auto-apply a change.

## Verification boundary

Local checks can validate generated-file drift, deterministic tests, typed
handoff schemas, evidence-chain mechanics, and configured policy. They cannot
prove semantic equivalence of natural-language translation, authenticated live
provider behavior, provider API stability, account permissions, or protection
of a signing key outside the repository.

For the current validation path, use:

```bash
python scripts/compile_harness.py --check
python -m unittest discover -s tests -p "test_*.py" -v
python scripts/run_evals.py
```
