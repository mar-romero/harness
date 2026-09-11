# Operational scripts

This folder contains the executable control-plane implementation.

- `harnesslib.py`: shared repository, JSON, hashing, and Git utilities.
- `request_normalizer.py`: preserves the original request and creates canonical English.
- `task_router.py`: calculates risk, route, agents, skills, and TDD.
- `tdd_policy.py`: selects the adaptive TDD mode.
- `tdd_evidence.py`: validates append-only RED/GREEN evidence.
- `research_discovery.py`: assesses Research-RDD and artifact readiness.
- `product_planning.py`: validates and materializes approved planning.
- `context_graph.py`: builds a bounded lexical dependency graph.
- `context_compiler.py`: builds the bounded task context pack.
- `impact_analysis.py`: plans and verifies change impact.
- `agent_budget.py`: selects and escalates supporting agents.
- `model_task_profile.py`: profiles task capability requirements.
- `model_router.py`: selects a model per routed agent.
- `openrouter_sync.py`: explicitly refreshes external scores and local inventories.
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
- `start_aci_mcp.ps1`: locates Node and starts the MCP bridge.
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
