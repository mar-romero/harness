# Provider compatibility notes

Verification date: **2026-09-02**.

This harness keeps the portable policy, roles and skill bodies canonical and
limits provider files to adapters. Provider behavior still depends on the
installed CLI/version, organization policy, plan and permissions.

| Provider | Project agents | Canonical skills | Native gate bridge in this starter | Model policy |
|---|---|---|---|---|
| Codex | `.codex/agents/*.toml` | `.agents/skills/` | canonical gate CLI + Codex sandbox/permission layer | inherit |
| Claude Code | `.claude/agents/*.md` | generated `.claude/skills/*` wrappers -> `.agents/skills/*` | `.claude/settings.json` `PreToolUse` | inherit |
| Cursor | `.cursor/agents/*.md` | `.agents/skills/` | `.cursor/hooks.json` | inherit |
| Gemini CLI | `.gemini/agents/*.md` | `.agents/skills/` | `.gemini/settings.json` `BeforeTool` | inherit |
| OpenCode | `.opencode/agents/*.md` + primary orchestrator | `.agents/skills/` | native `.opencode/plugins/harness/index.ts` permission/shell bridge | live catalog + harness model router |
| GitHub Copilot CLI | `.github/agents/*.agent.md` | `.agents/skills/` | canonical gate CLI + provider permissions | inherit |

## Why Claude has wrappers

The skill body still lives once under `.agents/skills/<skill>/SKILL.md`.
Claude Code discovers project skills under `.claude/skills/`, so the compiler
generates a small wrapper that imports the canonical skill instead of copying
its body. `scripts/compile_harness.py --check` rejects wrapper drift.

## Privilege model

Strict inspection agents (`explorer`, `planner`, `reviewer`,
`security-reviewer`, `test-auditor`, `docs-researcher`) do not receive shell
capability where the provider adapter can express that restriction. `debugger`
and `verifier` are read-only with execution capability: they can reproduce or
verify behavior but are not given edit tools. `implementer` is the only writer.

Provider-native sandboxes and permission prompts remain in force. A harness
hook blocks unsafe operations but does not auto-authorize safe operations on
providers where an allow decision would bypass the provider permission UI.

## OpenCode-specific integration

The final build adds a local OpenCode plugin that refreshes the live enabled model catalog, writes a conservative runtime inventory, applies reviewed per-model overrides, injects the active task/progress context and passes edit/shell decisions through the canonical gates. `/harness-request` is the preferred multilingual entry point and `/harness-task` activates an already-normalized task.

## Known portability boundary

The generated files are syntax- and structure-checked locally, but this starter
cannot prove live discovery/invocation against every provider without those
CLIs being installed and authenticated. In particular, named custom-agent
availability can vary by CLI version/surface even when the configuration file
is valid. Run the provider's own agent/skill inspection command after cloning
into a real project.

## Authoritative references used for this revision

- OpenAI Codex agents/configuration: https://developers.openai.com/codex/
- Claude Code subagents, skills and hooks: https://code.claude.com/docs/
- Cursor subagents and hooks: https://cursor.com/docs/
- Gemini CLI subagents, skills and hooks: https://geminicli.com/docs/
- OpenCode agents and skills: https://opencode.ai/docs/
- GitHub Copilot CLI custom agents and skills: https://docs.github.com/en/copilot/
