# Canonical agent sources

This folder is the provider-neutral source of truth for agent behavior.

- `roles/`: canonical role instructions.
- `skills/`: reusable process instructions.
- `README.md`: explains this folder.

Provider adapters are generated from these files and `harness/manifest.yaml`.
Do not store tasks, runtime results, secrets, or provider-specific behavior here.
