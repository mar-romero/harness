# Benchmarks

Public case descriptors live under `benchmarks/cases/`.

For a real holdout, keep hidden tests outside the project, for example:

```text
~/harness-benchmark-private/
└── BENCH-MY-CASE/
    ├── oracle.json
    └── hidden_test.py
```

The committed `benchmarks/examples/cpa-zero/demo-private/` is only a format demo and is not secret.
