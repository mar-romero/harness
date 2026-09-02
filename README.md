# Portable Agent Engineering Harness — Final v4

This is the **consolidated project**: v2 core + v3 model routing/evolution + the
OpenCode native overlay + the requested research-driven upgrades **1, 3, 4, 5,
8, 9 and 10**. Use this repository as the current version; the previous ZIPs do
not need to be layered on top of it.

## Core workflow preserved

`REQUEST → TASK → ROUTE → RISK → CONTEXT → IMPLEMENT → CHECKS → REVIEW → VERIFY → CLOSE`

Language normalization happens inside REQUEST→TASK, and the progress ledger
controls the existing stages rather than replacing them.

## Providers

- OpenAI Codex
- Claude Code
- Cursor
- Gemini CLI
- OpenCode (native plugin, permissions, model catalog and task activation)
- GitHub Copilot CLI

Provider adapters are generated from the same canonical roles. The harness is
provider-portable; provider runtime behavior must still be tested with the
actual authenticated CLI/environment.

## Consolidated capabilities

### Existing v2/v3/OpenCode layers

- canonical manifest and generated provider adapters;
- deterministic task/risk routing;
- executable command/path/finish gates;
- 9 capability-based agents and 28 skills;
- one writer per isolated Git worktree;
- bounded context packs;
- hash-chained evidence ledger;
- dynamic capability-based model routing;
- proposal-only evolution engine;
- OpenCode orchestrator, native plugin gates and live model inventory.

### Research-driven additions in this final build

1. **Canonical English request boundary + structured multilingual risk.** The
   original prompt is immutable; downstream agents use `canonical_english`.
   Deterministic invariants, a back-translation and an `EXACT_INTENT`
   attestation are required for non-English prompts. Natural-language
   translation cannot be mathematically guaranteed to be 100% identical, so
   ambiguity fails closed rather than being silently accepted.
3. **Runtime Eval Lab.** Repeated real-provider rollouts record success,
   `pass_power_k`, tokens, cost, latency, unsafe attempts and human intervention.
4. **Typed handoffs.** Explorer/planner/implementer/reviewer/verifier outputs
   validate against JSON contracts before becoming authoritative handoffs.
5. **Progress Ledger.** Durable orchestration state detects repeated failures,
   stalls and when to return to debugger/planner instead of looping.
8. **Signed provenance.** R3 closure requires an Ed25519 attestation over the
   candidate/control-plane/evidence state; private signing keys must live outside
   the repository.
9. **Context + Memory v2.** Context uses token estimates and repository graph
   neighbors; reusable memory can only be explicitly promoted from a task whose
   finish gate already passes.
10. **Champion/challenger evolution.** Runtime-eval runs can be compared on
   holdout cases with bootstrap confidence estimates and hard safety constraints.
   Promotion is always human-reviewed and never auto-applied.

## Spanish → canonical English

The safest daily path is to let the primary agent/orchestrator create the task.
In OpenCode use:

```text
/harness-request <tu pedido en español>
```

The harness preserves the Spanish text, creates the English canonical request,
back-translates it, validates protected literals/negation/constraints and then
routes the English task. If exact intent is uncertain, it must ask instead of
guessing.

Manual normalization is also available:

```bash
python3 scripts/request_normalizer.py tasks/TASK-001.json \
  --language es \
  --english "Exact English rendering produced by the agent" \
  --back-translation "Traducción inversa para verificar significado" \
  --equivalence EXACT_INTENT
```

For security-sensitive work, also populate `risk_factors`; structured flags take
precedence over language-dependent keyword inference.

## Standard task activation

```bash
python3 scripts/compile_harness.py
bash scripts/check-harness.sh
python3 scripts/task_router.py tasks/TASK-001.json
python3 scripts/context_compiler.py tasks/TASK-001.json \
  --route .harness/runs/TASK-001/route.json
python3 scripts/orchestrator.py init TASK-001 \
  --route .harness/runs/TASK-001/route.json
python3 scripts/evidence.py init TASK-001
```

OpenCode performs those runtime bindings through:

```text
/harness-task tasks/TASK-001.json
```

## Typed handoffs

Validate/save a specialist result before the next stage consumes it:

```bash
python3 scripts/handoff.py validate planner /tmp/plan.json
python3 scripts/handoff.py save planner /tmp/plan.json
```

Schemas live under `harness/schema/handoffs/`.

## Progress / replanning

```bash
python3 scripts/orchestrator.py status TASK-001
python3 scripts/orchestrator.py record TASK-001 --status PASS
python3 scripts/orchestrator.py record TASK-001 --status FAIL --note "same test still fails"
```

A repeated failed action becomes `STALLED` and returns an explicit debugger /
planner / implementer recommendation instead of treating retries as progress.

## Runtime Eval Lab

Provider-specific adapters intentionally remain external-command adapters so
volatile CLI syntax is not hard-coded into the canonical harness.

```bash
python3 scripts/runtime_eval.py \
  --suite harness/runtime-evals/cases/example.json \
  --adapter python3 path/to/your-provider-adapter.py \
  --trials 5 --provider opencode --model provider/model --agent implementer
```

Results are written under `.harness/runtime-evals/runs/` and can feed model
selection and evolution experiments.

## R3 signed attestation

Generate an Ed25519 key **outside the repository**:

```bash
openssl genpkey -algorithm ED25519 -out ~/.config/harness-attest.pem
openssl pkey -in ~/.config/harness-attest.pem -pubout \
  -out ~/.config/harness-attest.pub.pem
```

After all pre-attestation R3 evidence exists:

```bash
python3 scripts/attest.py create TASK-001 --risk R3 \
  --private-key ~/.config/harness-attest.pem
python3 scripts/attest.py verify .harness/runs/TASK-001/attestation.json \
  --public-key ~/.config/harness-attest.pub.pem
```

The attestation records the prior evidence-chain head, Git state where available,
manifest/context/route/model-selection hashes and runtime version. R3 finish now
requires deterministic `attestation` evidence in addition to security review and
human approval.

## Verified memory

A task cannot become reusable memory until its own risk-aware finish gate passes:

```bash
python3 scripts/memory.py promote TASK-001 \
  --title "Idempotent invoice migration" \
  --summary "Verified approach and constraints" \
  --lesson "Keep the migration resumable" \
  --approved-by "human-reviewer"
```

The context compiler retrieves only approved local memories by relevance.

## Champion / challenger

Run the same holdout suite against the current champion and an isolated
challenger, then compare:

```bash
python3 scripts/evolution_experiment.py \
  --champion .harness/runtime-evals/runs/CHAMPION.json \
  --challenger .harness/runtime-evals/runs/CHALLENGER.json
```

Possible decisions are `PROMOTE_FOR_HUMAN_REVIEW`, `KEEP_CHAMPION`,
`INCONCLUSIVE`, `INSUFFICIENT_DATA` or `INCOMPARABLE`. There is no auto-apply
code path.

## Verify the harness

```bash
python3 scripts/compile_harness.py --check
bash scripts/check-harness.sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 scripts/run_evals.py
```

See `docs/RELEASE_LINEAGE.md`, `docs/FINAL_UPGRADES.md`, `AUDIT.md` and
`AUDIT_RESULTS.txt` for scope and residual limitations.
