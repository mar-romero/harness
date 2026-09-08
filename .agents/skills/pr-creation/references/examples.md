# Examples — pr-creation

## Good PR title

- `feat(harness): add pr-creation skill with templated body and model routing`
- `fix(compile): regenerate adapters for new skill wrappers`
- `chore(docs): note pr-creation skill in README skill index`

Bad titles:
- `update` (no scope/type)
- `WIP: pr stuff` (WIP, vague)
- `feat: fixed bug.` (period, vague)

## Good PR body (R1 feature)

```markdown
## Summary
Adds portable skill `pr-creation` that structures PR titles/bodies and routes to optimal model class. Motivation: prior PR flow was too simple (missing context collection and risk sections).

## Changes
- New skill `.agents/skills/pr-creation/SKILL.md` (concise, progressive disclosure)
- References: `template.md`, `model-selection.md`, `examples.md`, `checklist.md`
- Provider wrapper generated via `scripts/compile_harness.py` → `.claude/skills/pr-creation/SKILL.md`

## Testing
- [x] `python scripts/compile_harness.py --check` — generated artifacts are in sync
- [x] `python scripts/compile_harness.py` — generated 84 provider artifacts
- Deterministic evidence: captured stdout in task ledger

## Risks
- Level: R1 (new docs, no runtime). Residual: none. Rollback: revert commit.

## Checklist
- [x] Title follows Conventional Commits
- [x] No secrets in diff
- [x] Linked task: TASK-20260902-pr-skill
- [x] `gh pr view` not run (no remote PR in this example)

## Links
- Task: TASK-20260902-pr-skill
- Diff stat: ` .agents/skills/pr-creation/SKILL.md | 72 +++++` 
```

## Good PR body (R0 trivial docs)

```markdown
## Summary
Fix typo in README skill index.

## Changes
- `README.md:42` — correct skill name

## Testing
- [x] `python scripts/compile_harness.py --check` — pass

## Risks
- Level: R0 — docs only, no behavior change.

## Checklist
- [x] Title: `docs(readme): fix pr-creation index typo`
```

## Bad PR body (anti-pattern)

```markdown
## Summary
Did stuff.

## Changes
<full git diff pasted>

## Testing
Tests pass.

## Risks
None.
```

Why bad: no motivation, raw diff (reviewer burden), unverified testing claim, missing risk level and checklist, no links.

## Rendering tip

Use `--body-file` to preserve markdown:

```bash
cat > /tmp/pr-body.md <<'EOF'
## Summary
...
EOF
gh pr create --title "feat(harness): add pr-creation skill" --body-file /tmp/pr-body.md
gh pr view --json url,title,body
```
