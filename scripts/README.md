# Operational scripts

This folder contains the executable control-plane implementation.

- `harnesslib.py`: shared repository, JSON, hashing, and Git utilities.
- `request_normalizer.py`: preserves the original request and creates canonical English.
- `task_router.py`: calculates risk, route, agents, skills, and TDD.
- `tdd_policy.py`: selects the adaptive TDD mode.
- `tdd_evidence.py`: validates append-only RED/GREEN evidence and explicitly supersedes invalid attempts.
- `research_discovery.py`: assesses Research-RDD and artifact readiness.
- `product_planning.py`: validates and materializes approved planning.
- `context_graph.py`: builds a bounded lexical dependency graph.
- `context_compiler.py`: builds the bounded task context pack.
- `impact_analysis.py`: plans and verifies change impact.
- `agent_budget.py`: selects and escalates supporting agents.
- `model_task_profile.py`: profiles task capability requirements.
- `model_router.py`: selects a model per routed agent.
- `openrouter_sync.py`: explicitly refreshes external scores and local inventories.
- `subscription_bridge.py`: builds a cross-provider subscription inventory, activates tasks, and runs routed roles through official authenticated coding CLIs.
- `subscription_runtime.py`: shell-free CLI adapters, API-key environment sanitization, output normalization, and read-only integrity checks.
- `orchestrator.py`: advances task stages and validates prerequisites.
- `worktree.py`: creates, publishes, and cleans isolated worktrees.
- `task_checks.py`: runs authorized deterministic task checks.
- `gate.py`: blocks unsafe actions and validates finish conditions.
- `handoff.py`: validates typed role handoffs.
- `evidence.py`: records append-only task evidence.
- `receipt_review.py`: prepares and validates Receipt-RDD reviews.
- `attest.py`: creates R3 signed provenance attestations.
- `memory.py`: promotes and searches approved task memory.
- `model_feedback.py`: records local model outcomes.
- `compile_harness.py`: generates provider adapters from canonical sources.
- `check_harness.py`: audits manifest, policies, adapters, and contracts.
- `codex_hook.py`: enforces Codex pre-tool safety decisions.
- `codex_context_hook.py`: injects active task context into Codex sessions.
- `aci.py`: command-line ACI client.
- `aci_core.py`: bounded repository and Git operation implementation.
- `aci_mcp.py`: JSON-RPC MCP server for ACI.
- `aci_mcp_worker.py`: persistent ACI worker used by the Windows-safe Node bridge.
- `aci_mcp_node.js`: Windows-safe Node-to-Python MCP bridge.
- `start_aci_mcp.ps1`: locates a stable Node runtime and starts the MCP bridge.

Codex Desktop starts `harness-aci` from the absolute checkout configured in
`.codex/config.toml`. If the repository is moved or cloned elsewhere, update
that server's `cwd` before using Desktop; its relative launcher argument is then
resolved from that configured checkout.
- `benchmark_variant.py`: creates disposable benchmark variants.
- `harness_benchmark.py`: executes benchmark suites.
- `eval_recorder.py`: records deterministic and runtime evaluation results.
- `run_evals.py`: executes harness evaluation cases.
- `runtime_eval.py`: runs repeated provider adapter evaluations.
- `evolution_engine.py`: proposes harness improvements from evaluation history.
- `evolution_experiment.py`: compares champion and challenger configurations.
- `check-harness.sh`: runs the structural audit, unit tests, and evaluations in CI.
- `run-evals.sh`: convenience wrapper for `run_evals.py`.
- `providers/`: provider-specific activation and inventory commands.
- `README.md`: explains the executable control-plane boundary.

Scripts read policy from `harness/`; they must not contain project-specific logic or secrets.

## Superseding invalid TDD evidence

Run `python scripts/tdd_evidence.py supersede <TASK> --reason "<failure/recovery explanation>"`
to preserve an invalid chain and select a fresh attempt. The operation uses the
same interprocess task lock as evidence appends. It leaves the failed file's bytes
untouched and atomically writes `tdd-attempts.json` in the shared task run directory,
recording each failed path, SHA-256, validation error, reason and timestamp.
The active chain is explicitly named under `tdd-attempts/`; repeated supersession
retains every previous failure. Valid chains cannot be superseded by this operation.

`add`, `summary`, the IMPLEMENT prerequisite and finish gates use only the selected
attempt. Required design/RED/GREEN evidence must be recorded afresh; no phase is
inherited. New records bind their task and attempt into the hash chain. Missing or
invalid selection, missing active evidence, or altered historical bytes fail closed.
An attempts directory without its selector also blocks legacy fallback, including
after an interrupted first selection. An orphaned attempt from interrupted publication
is never overwritten; explicit operator recovery is required. Supersession does not
modify progress or handoffs and does not itself authorize task completion.

## Neutral terminal orchestrator

- `scripts/harness_chat.py`: provider-neutral terminal chat. Plain text becomes a task and the harness drives the workflow automatically.
- `scripts/autonomous_orchestrator.py`: deterministic stage runner behind the chat; executes canonical roles through the Subscription Bridge and commits typed handoffs/control-plane gates.
