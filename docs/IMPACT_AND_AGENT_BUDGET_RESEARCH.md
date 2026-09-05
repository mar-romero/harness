# Research basis — Change Impact Graph + Progressive Agent Budget

This overlay implements two ideas only.

## 1. Change Impact Graph

### CodePlan
Bairi et al., **CodePlan: Repository-level Coding using LLMs and Planning** (2023) frame repository-level coding as a planning problem and combine incremental dependency analysis with change may-impact analysis and adaptive planning. The harness overlay borrows the engineering principle, not the implementation: compute a bounded dependency/reverse-dependency surface before editing and compare the real diff against it afterwards.

### RepoGraph
Ouyang et al., **RepoGraph: Enhancing AI Software Engineering with Repository-level Code Graph** (2024) show that repository-level graph guidance can improve software-engineering agent performance across multiple systems. The overlay therefore promotes the existing harness `context_graph.py` from a context bonus into an explicit impact artifact used by planning and verification.

### SWE-agent / ACI
Yang et al., **SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering** (2024) show that agent-computer interface design materially affects software-engineering performance. Therefore graph edges that matter for a risky decision should be confirmable through the harness ACI (`repo_symbol`, `repo_callers`, `repo_dependencies`) rather than trusting an approximate static graph blindly.

## 3. Progressive Agent Budget

### Agentless
Xia et al., **Agentless: Demystifying LLM-based Software Engineering Agents** (2024) demonstrate that a simple localization → repair → validation pipeline can be highly competitive, highlighting that orchestration complexity is not automatically beneficial.

The harness response is not to remove specialization. Instead it preserves mandatory safety/quality gates while delaying optional support agents until a deterministic signal justifies them.

## Design consequences

- Mandatory risk gates never disappear.
- The single-writer rule remains.
- Planner/debugger/additional localization/research are progressive support capacity.
- High/critical impact activates planning early.
- Failed or blocked progress activates debugging/planning.
- Runtime budget state is durable under `.harness/runs/<task>/agent-budget.json`.
- The objective is lower coordination overhead without reducing evidence-backed closure.
