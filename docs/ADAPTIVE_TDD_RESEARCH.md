# Adaptive TDD: research basis

This overlay intentionally implements **adaptive**, not universal, TDD.

Key empirical findings informing the policy:

- Nagappan et al., *Realizing quality improvement through test driven development: results and experiences of four industrial teams* (Empirical Software Engineering, Microsoft/IBM): industrial teams reported lower defect density with an up-front development-time cost.
- Rafique & Mišić, *The Effects of Test-Driven Development on External Quality and Productivity: A Meta-Analysis* (IEEE TSE): aggregate evidence favors a small quality improvement while productivity effects vary substantially by context.
- Fucci et al., *A Dissection of Test-Driven Development: Does It Really Matter to Test-First or to Test-Last?* (IEEE TSE): cycle granularity and uniformity were strongly associated with outcomes, motivating small uniform loops rather than test-first dogma.
- Tosun et al., *An industry experiment on the effects of test-driven development on external quality and productivity*: task/context effects matter, especially for brownfield work, motivating characterization-first mode.
- TDD-Bench Verified (2024): generating useful fail-to-pass tests from real software issues remains difficult for LLMs, motivating independent test design and explicit RED validation.
- Meta, *Mutation-Guided LLM-based Test Generation at Meta* (2025): targeted mutation can produce useful hardening tests, motivating optional mutation guidance for high-risk logic.
- SWE-Mutation (2026): LLM-generated test suites can be superficial and fail to discriminate realistic faulty solutions, motivating mutation adequacy rather than coverage-only metrics.
- *Rethinking the Value of Agent-Generated Tests for LLM-Based Software Engineering Agents* (2026): simply increasing the volume of agent-written tests does not necessarily improve issue resolution, motivating selective TDD and strong oracles rather than test-count targets.

The policy therefore emphasizes:
1. valid fail-to-pass evidence;
2. independent test-oracle design for risky tasks;
3. negative/boundary analysis;
4. characterization-first for brownfield work;
5. spike-before-test when contracts are unknown;
6. small uniform cycles;
7. targeted mutation testing where its value justifies cost.
