# Model Selection — pr-creation

Choose model class via `harness/manifest.yaml` and `harness/models.json` routing.
Do not hardcode a model id; use class + reason.

## Decision table

| Context | Risk | Recommended class | Why | Fallback |
|---------|------|-------------------|-----|----------|
| R2/R3, narrative PR, need falsification | R2/R3 | `reasoning` (optimize `reasoning,falsification`, min `reasoning:high`) | Descriptive summary, risks, trade-offs | `coding` if no reasoning inventory |
| Code-heavy PR, large diff, needs accurate file refs | R1/R2 | `coding` (optimize `coding,tool-use`, min `reasoning:high`) | Precise change bullets from diff | `reasoning` |
| R0 trivial (docs, formatting, mechanical rename) | R0 | `fast` (optimize `latency,cost`, min `reasoning:medium`) | Low cost, sufficient | `reasoning` if nuance |
| No inventory (`model-inventory.json` missing) | any | `inherit` | Harness `scripts/model_router.py` returns inherit; respect it | session default |

## How to select at runtime

1. Read `harness/manifest.yaml` → `model_classes`.
2. Check `.harness/opencode/model-inventory.json` staleness (generated_at).
3. Run via harness: `python scripts/providers/opencode_activate_task.py tasks/TASK-xxx.json` exposes `model-selections.json` with per-agent class.
   - For PR creation outside a task: infer class from table above; document `selection reason: ...` in PR Risks or commit notes.
4. If using OpenCode plugin, the `reasoning` and `coding` classes map to `high` reasoning budget; `fast` to `medium`.

## Cost/latency guidance

- Default this skill to `reasoning` — PR quality dominates cost. Only downgrade to `fast` when `git diff --stat` shows ≤2 files and `R0` (docs/format).
- Do not use `fast` for R2+ (external contract, persistence, concurrency, important calculations) — see `manifest > risk_levels`.

## Example selection notes (put in PR body or progress ledger)

- `model_class: reasoning — R2 external-contract, needs structured narrative + risk articulation`
- `model_class: fast — R0 docs-only, 1 file, latency-optimized`
- `model_class: inherit — no inventory, session model inherited per harness`

## Integration with compile_harness

`scripts/compile_harness.py` does not select models; it only generates provider wrappers. Model choice stays at task routing time.
