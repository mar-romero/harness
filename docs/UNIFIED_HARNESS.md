# Unified Multi-Provider Harness

## Purpose

This build makes the harness itself the primary application. You can open the neutral terminal, describe a software task in natural language, and let the deterministic control plane select canonical agents, skills, context, provider/model, checks, review, verification, security gates and human approvals.

The provider CLIs remain official runtimes authenticated by their own browser/OAuth/account flows. The harness does not scrape cookies or convert subscription credentials into a generic HTTP API.

## Architecture

```text
User -> Neutral terminal chat
          |
          v
  deterministic control plane
          |
  +-------+-----------------------------+
  | task/risk routing                    |
  | Research-RDD / spec workflow         |
  | adaptive TDD                         |
  | progressive agent budget             |
  | CodeGraph + context compiler          |
  | canonical skills                     |
  | ACI + hook bus                       |
  | model/provider routing               |
  | typed handoffs / progress ledger     |
  | Receipt-RDD / review / verification  |
  | security + human gates               |
  +-------------------+------------------+
                      |
        +-------------+-------------+-------------+
        |             |             |             |
      Codex         Claude       Copilot        Cursor
        |             |             |             |
      Gemini          Grok      (official subscription CLIs)
```

## What remains canonical

Provider changes do not redefine the engineering workflow. These remain owned by the harness:

- canonical agents in `.agents/roles/`;
- canonical skills in `.agents/skills/`;
- risk routing R0-R3;
- adaptive TDD and fail-to-pass evidence;
- Research-Driven Development and durable research artifacts;
- Receipt-Driven Development and frozen-candidate review receipts;
- specification/acceptance planning;
- single-writer worktree isolation;
- deterministic checks;
- review, test audit, verification and security review;
- human gates for critical work;
- ACI and CodeGraph/context retrieval;
- typed handoffs and progress ledger;
- verified memory, evals and champion/challenger infrastructure.

## Canonical skills in subscription runs

Subscription runtimes no longer depend only on provider-native skill discovery. `scripts/skill_compiler.py` loads the actual canonical `SKILL.md` files required by the role/task and embeds a bounded skill pack in the runtime prompt.

The skill compiler uses:

- the role's skills from `harness/manifest.yaml`;
- routed task skills;
- `harness/skill-runtime-policy.json`;
- a fixed runtime character/token budget.

Provider-native skills remain supplemental. They must not override canonical roles, gates or handoff contracts.

## CodeGraph and token-efficient context

The context layer includes:

- selective retrieval (skip expensive retrieval for trivially localized work);
- optional CodeGraph exploration;
- built-in symbol/dependency fallback;
- repo-map ranking under a fixed token budget;
- symbol snippets instead of whole-file loading;
- `.codegraph/` exclusion from context;
- content-addressed `repo_read_range` cache within one model execution;
- condensed prior handoffs rather than forwarding transcripts.

`repo_read_range` returns a cache hit instead of re-sending an exact unchanged range already delivered in the same runtime session. A model may request `force=true` if the content is genuinely no longer available in its current context.

Full typed handoffs remain authoritative on disk. `context_condenser.py` only creates a bounded working-memory representation for the next model.

## ACI

`harness-aci` is the common narrow tool surface for repository exploration and deterministic checks:

- `repo_explore`
- `repo_search`
- `repo_read_range`
- `repo_symbol`
- `repo_callers`
- `repo_dependencies`
- `git_status`
- `git_diff`
- `tests_run`
- `lint_run`
- `diagnostics_get`

Provider project configurations expose the same MCP server where supported. The runtime prompt explicitly prefers ACI over repeated raw read/search loops.

ACI does not expose arbitrary source writes. Source editing remains provider-native and restricted to the canonical `implementer` worktree.

## Hook model

There are two layers:

1. Provider-native hooks generated/configured for providers that support them.
2. `scripts/hook_bus.py`, the provider-neutral authority for subscription runtime lifecycle and ACI auditing.

The canonical hook bus:

- verifies the writer is in `.worktrees/<task>` before execution;
- audits ACI tool calls;
- preserves read-only mutation detection;
- inspects every writer-changed path after a run;
- applies the canonical protected/secret path policy;
- fails closed before publication if a writer touched a prohibited path.

The hook bus does not claim to intercept every opaque internal tool call made inside every vendor CLI. Native hooks/permissions remain defense in depth, while worktree isolation and pre-publication validation provide the common enforcement boundary.

## Subscription Bridge

Supported runtimes:

- OpenAI Codex CLI
- Anthropic Claude Code
- GitHub Copilot CLI
- Cursor CLI
- xAI Grok Build
- Google Gemini CLI

`doctor` performs runtime/version/capability probing without reading credential stores. Direct provider API environment variables are stripped from child processes by default so a configured API key does not silently change the intended subscription-authenticated path.

Model IDs are namespaced, for example:

```text
codex/<model>
claude/sonnet
copilot/<model>
cursor/auto
grok/<model>
gemini/auto
```

The existing model router can choose different providers for different roles while keeping vendor/family metadata for reviewer/verifier independence.

## Provider parity improvements

The bridge applies current provider capabilities when available:

- Claude: native `--effort`, plan/read-only mode, canonical ACI MCP allowlist.
- Cursor: `--mode=ask` for read-only roles plus Git fingerprint verification.
- Copilot: explicit `harness-aci` MCP permission with write/shell restrictions by role.
- Grok: headless permission rules plus MCP tool allowance and reasoning effort.
- Gemini: plan mode for read-only roles and trusted local harness ACI configuration.
- Codex: `codex exec`, model reasoning effort and read-only/workspace-write sandbox split.

`provider_capabilities.py` probes installed CLI help surfaces so optional features can be detected instead of assuming all installed versions expose the same flags.

## Important parity limitation

This design preserves the selected model and the harness engineering workflow, but it does **not** promise 100% identity with each provider's full interactive product. A provider may have its own proprietary subagents, memory, web, UI, hooks or context management that are intentionally restricted or not used when it is acting as a worker inside the harness.

The intended trade is:

```text
less provider-specific orchestration
+ one canonical cross-provider engineering system
+ independent reviewers/verifiers
+ deterministic gates
+ portable context/evidence
```

## Neutral terminal

Daily use starts from:

```bash
./harness-chat
```

Windows:

```bat
harness.cmd
```

Then type a task:

```text
harness> Fix the refresh-token race, add regression tests, and do not change the public API.
```

The neutral orchestrator:

1. creates a durable task;
2. routes initial risk/agents/TDD;
3. runs Explorer;
4. updates the localized file surface and re-routes if needed;
5. lets Planner refine acceptance criteria;
6. creates the writer worktree;
7. selects provider/model per role;
8. executes canonical roles through subscription runtimes;
9. validates typed handoffs;
10. runs deterministic checks;
11. performs verification assessment/Receipt-RDD;
12. runs review/test audit/verifier/security stages required by risk;
13. performs impact verification;
14. asks for explicit human approval when policy requires it;
15. publishes locally and closes only when the finish gate passes.

Runtime/quota/auth failures may fail over to another eligible provider. Integrity or security failures do not get hidden by failover.

## First installation

From the extracted all-in-one bundle:

```bash
python install.py /path/to/harness-main --doctor
```

To also install and initialize optional CodeGraph:

```bash
python install.py /path/to/harness-main --install-codegraph --init-codegraph --doctor
```

To open the neutral terminal immediately after installation:

```bash
python install.py /path/to/harness-main --doctor --launch
```

The installer accepts the original uploaded harness and known intermediate patch states, backs up replaced files, regenerates provider adapters, runs deterministic evals and unit tests, and preserves existing `.harness/runs` state while validating.

## Authentication

Install only the official provider CLIs you intend to use. Then launch the neutral chat. The first-run auth wizard invokes each CLI's official login flow. The harness does not read or export the resulting credentials.

Use `:providers` and `:auth` in the neutral chat to inspect/retry provider setup.

## Diagnostics

Optional direct diagnostics remain available:

```bash
python scripts/subscription_bridge.py doctor
python scripts/aci.py list-tools
python scripts/codegraph_bridge.py status
```

Normal daily use does not require manually driving individual workflow steps.

## Validation contract

A successful all-in-one installation validates:

- provider adapters are generated and in sync;
- harness structural checks pass;
- deterministic harness evals pass;
- full Python unit suite passes;
- no user runtime history is discarded during installer validation.

## Parallel post-implementation gates

The neutral orchestrator can fan out `REVIEW`, `TEST_AUDIT`, and `VERIFY` against the same frozen candidate, then fan their validated outputs back in and commit them in canonical order. Provider concurrency is bounded by `harness/parallel-policy.json`; the default permits two concurrent Claude processes and two concurrent Codex/Copilot/Gemini processes while keeping a global cap of four. Security review remains after impact verification by design.

## Minimum-sufficient model selection

The default routing strategy is `minimum_sufficient`: do not buy capability the current role does not need. Hard model/risk floors and dynamic task/role targets are computed first; among candidates that clear them, the router minimizes capability surplus and subscription-quota/latency burden. Independent-review rules still take precedence where required. R3 blocks if no candidate meets the dynamic sufficiency floor.

See `docs/PARALLEL_MINIMUM_ROUTING.md` for the exact policy and audit fields.
