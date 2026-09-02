# Evals

The default suite is deliberately deterministic and credential-free:

1. **behavior fixtures** exercise router and command-gate decisions;
2. **agent contract evals** cover every canonical role, including single-writer,
   read-only and no-delegation invariants;
3. **skill contract evals** cover every canonical skill's portable Agent Skills
   structure and reject provider-agent paths inside canonical skill bodies.

Run:

```bash
python3 scripts/run_evals.py
```

These contract evals catch harness regressions, but they do not measure model
quality. For release qualification, run the same task corpus through each
installed provider and collect task success, defect introduction, review
precision/recall, token/cost/latency and human-intervention metrics. Those
provider-backed benchmarks are intentionally not faked by this starter.
