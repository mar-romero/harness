# Neutral Terminal Chat

`harness-chat` turns the harness into the primary user interface. Provider CLIs
(Codex, Claude Code, Copilot CLI, Cursor CLI, Grok Build, Gemini CLI) become
subscription-authenticated worker runtimes behind the deterministic harness
control plane.

## Mental model

```text
You
 |
 v
Neutral terminal chat
 |
 v
Deterministic harness orchestrator
 |-- task/risk routing
 |-- context compiler + ACI/CodeGraph
 |-- model routing
 |-- typed handoffs
 |-- worktree/single-writer
 |-- checks/review/verification/security/human gates
 |
 +--> Explorer     -> provider/model A
 +--> Planner      -> provider/model B
 +--> Implementer  -> provider/model C
 +--> Reviewer     -> provider/model D
 +--> Verifier     -> provider/model E
```

The provider is not the workflow authority. A provider receives one canonical
role prompt and returns one typed handoff. The next role receives the validated
handoff plus compact current context, not the previous provider's conversation.

## Start

Linux/macOS:

```bash
./harness-chat
```

Windows cmd:

```bat
harness.cmd
```

Or directly:

```bash
python scripts/harness_chat.py
```

On first launch the chat detects installed official CLIs and offers to open their
official login flows. The harness does not read browser cookies, credential
stores, or private OAuth tokens. Direct model API-key environment variables are
removed from provider child processes by the Subscription Bridge unless the
low-level bridge is explicitly overridden.

## Normal use

At the prompt, type a task:

```text
harness> Corregí el refresh token race, agregá tests y no cambies la API pública.
```

No task JSON is required. The app:

1. creates a local task under `.harness/chat/tasks/`;
2. routes risk/agents/TDD;
3. builds compact repository context;
4. selects provider/model/reasoning effort per role;
5. runs Explorer and uses its validated `relevant_files` to localize the task;
6. refreshes route/context/model selections if localization changes risk;
7. uses Planner acceptance criteria as the authoritative task criteria;
8. creates the writer worktree automatically;
9. runs the Implementer as the only writer;
10. runs deterministic checks;
11. prepares receipt/verification policy;
12. runs review, test audit, verifier, impact verification and security review as routed;
13. asks the human only for explicit policy gates/consent;
14. locally publishes the isolated worktree and closes only if the finish gate passes.

## Cross-provider example

The model router can produce a run such as:

```text
EXPLORE      claude/sonnet
PLAN         claude/opus
TEST_DESIGN  copilot/gpt-5.4
IMPLEMENT    codex/gpt-5.3-codex
REVIEW       gemini/auto
VERIFY       copilot/claude-sonnet-4.6
```

This does not create Claude-specific or Codex-specific workflow agents. The
canonical agents remain `.agents/roles/*.md`; provider CLIs are runtimes.

## Runtime failover

If a selected CLI fails because its runtime is unavailable, unauthenticated,
rate-limited, or quota-limited, the neutral runner can select another eligible
provider from the same normalized subscription inventory. A read-only integrity
violation is **not** treated as availability failure and is not hidden by
failover.

## Context behavior

The runner never copies the full transcript from one provider into the next.
It passes:

- current task snapshot;
- compact context/compiler output;
- CodeGraph/repo-map/symbol snippets when installed;
- bounded typed handoffs;
- bounded recent support-agent findings.

This preserves provider independence and reduces context duplication.

## Human interaction

Human approval is never synthesized. The terminal asks explicitly when:

- the routed task reaches an R3 `HUMAN_GATE`;
- Receipt-RDD needs session-scoped review consent.

Security-review failure is not automatically retried/bypassed.

## Optional chat commands

The normal interface is plain task text. These commands are only operational
controls:

- `:auth` — re-open official provider login flows
- `:providers` — show installed/auth state
- `:status` — show the active task and routed model selections
- `:help` — help
- `:quit` — exit without losing progress

On restart, unfinished task state is detected and can be resumed.
