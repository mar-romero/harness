# Benchmarks

Public benchmark case descriptors belong under `benchmarks/cases/`. Private
oracles and hidden tests must remain outside this repository; committed examples
are format demonstrations, not secret evaluation data.

```text
private-benchmark-root/
└── BENCH-EXAMPLE/
    ├── oracle.json
    └── hidden_test.py
```

Validate, run, or render a benchmark through the checked-in interface:

```bash
python scripts/harness_benchmark.py --help
python scripts/harness_benchmark.py validate <suite.json>
python scripts/harness_benchmark.py run <suite.json> --provider codex
python scripts/harness_benchmark.py report <report.json>
```

Benchmark runs use temporary workspaces. They do not prove general provider
quality, and their metrics should be interpreted with the provider/model,
repetition count, private-oracle status, and environment recorded alongside the
result.
