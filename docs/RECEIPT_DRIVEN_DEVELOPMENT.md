# Receipt-Driven Development overlay

Receipt-RDD is opt-in and is **off by default**. It binds review evidence to the exact candidate bytes (plus Git file mode and base commit) so a change after review invalidates the receipt.

## Switch

```bash
python3 scripts/receipt_review.py mode status
python3 scripts/receipt_review.py mode enable
python3 scripts/receipt_review.py mode disable
```

## Verification assessment

After deterministic task checks and before review delegation, the orchestrator runs:

```bash
python3 scripts/receipt_review.py prepare TASK-001 --apply
python3 scripts/orchestrator.py reconcile TASK-001
```

When Receipt-RDD is off:

- `passive`: structural readback only beyond the existing harness gates;
- `medium`: separate verifier only for a small/low-effort implementer;
- `high` or unassessable: independent verifier is added;
- existing R2/R3 verifier requirements are never removed.

When Receipt-RDD is on, R1 normally uses the independent reviewer receipt as the independent check. Existing R2/R3 verification remains mandatory.

## Consent: once per provider session

The permission to run candidate reviews is granted once for the current Codex/OpenCode session:

```bash
python3 scripts/receipt_review.py consent status TASK-001
python3 scripts/receipt_review.py consent grant TASK-001
```

The provider session ID is captured locally. A new provider session requires a new consent grant. **Consent is session-scoped; receipts are still candidate-scoped.** Every candidate gets its own freeze, review and receipt.

## Candidate receipt

The orchestrator freezes the candidate before review:

```bash
python3 scripts/receipt_review.py freeze TASK-001
```

A PASS reviewer handoff is then bound to that frozen subject. The orchestrator integration issues the receipt automatically during the authoritative REVIEW commit.

The final harness gate calls Receipt-RDD validation. If any candidate byte or tracked Git mode changed after the freeze/review, closure fails and the candidate must be reassessed/reviewed.
