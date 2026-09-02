# Harness architecture

## Control plane

`harness/manifest.yaml` is the canonical machine-readable control plane. Role
prompts live once in `.agents/roles/`; Agent Skills live once in
`.agents/skills/`. `scripts/compile_harness.py` generates provider adapters.

## Execution plane

1. `task-intake` creates a task.
2. `task_router.py` determines risk, capabilities, agents and skills.
3. `context_compiler.py` creates a bounded context pack without secrets.
4. `worktree.py` isolates the single writer.
5. `gate.py` blocks unsafe tool actions and enforces completion requirements.
6. `evidence.py` appends tamper-evident, SHA-256-chained claims and checks with independent actor constraints.
7. independent review/verification consumes the frozen candidate and evidence.

## Eight implemented upgrades

1. Canonical manifest and generated provider adapters.
2. Deterministic task router.
3. Executable hooks and risk gates.
4. Cross-role deterministic eval framework.
5. Git worktree isolation with writer locks.
6. Bounded context compiler.
7. Capability-based agents, including debugger and verifier specialists.
8. Append-only evidence ledger and finish gate.
