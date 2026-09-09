# Harness configuration

This folder contains the declarative control plane for the harness.

- `manifest.yaml`: canonical workflow, roles, skills, providers, and capabilities.
- `models.json`: model classes, risk floors, scoring, effort, and independence.
- `opencode-model-overrides.json`: reviewed OpenCode capability overrides.
- `*-policy.json`: stable routing, risk, context, TDD, evidence, and security rules.
- `policies/`: specialized policies such as dangerous-action risk rules.
- `schema/`: JSON contracts for tasks, handoffs, evidence, and results.
- `model-providers/`: Codex and OpenCode discovery configuration.
- `hooks/`: declarative lifecycle hook events.
- `runtime-evals/`: optional runtime-evaluation configuration.
- `attestation/`: reserved canonical boundary for attestation-related metadata.
- `memory/`: reserved canonical boundary for memory-related metadata.
- `README.md`: explains this configuration boundary.

Store stable policy and schema here; do not store tasks, runtime results, secrets,
tokens, or private keys.
