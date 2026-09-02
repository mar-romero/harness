---
name: pr-creation
description: Create practical, descriptive pull requests with structured title/body, auto-collected context and optimal model routing.
---

# pr-creation

Practical PRs over plausible summaries. Auto-collects context, structures narrative for reviewers, and routes to the optimal model class.

## When to use

- Creating or updating a GitHub PR from local changes.
- Need descriptive title/body from git diff, commits, and task/issue context.
- Want consistent Summary/Changes/Testing/Risks/Checklist sections.

## Workflow

1. **Collect** — `git diff --stat`, `git log --oneline`, `git status`, task/issue file (`tasks/*.json` or `gh issue view`). Do not assume content; read refs only if needed.
2. **Classify** — Scope, risk (R0 trivial docs → R3 auth/secrets/destructive), and model class. See `references/model-selection.md`.
3. **Draft title** — Conventional Commits: `type(scope): concise intent` (e.g., `feat(harness): sync generated adapters`). Load `references/template.md` for rules.
4. **Build body** — Fill sections: Summary, Changes, Testing, Risks, Checklist, Links. Keep evidence-backed, no invented tests.
5. **Create** — `gh pr create --title "..." --body-file <rendered>` or `gh pr create --fill` then overwrite. For updates: `gh pr edit`.
6. **Verify** — `gh pr view --json url,state,title,body` and `git branch -vv`.

## Output template (summary)

```markdown
## Summary
One-line intent + motivation.

## Changes
- Bullet per logical change (files/behavior).

## Testing
- Commands run + results. Mark deterministic evidence.

## Risks
- Risk level + residual risks.

## Checklist
- [ ] Checks/compile passes, no secrets, links task/issue

## Links
- Closes # / Task: TASK-xxx
```

Full placeholders, validation rules, and examples: load `references/template.md` and `references/examples.md` only when drafting.

## Model routing

Use harness `harness/manifest.yaml` model_classes:
- `reasoning` for R2/R3 or descriptive narrative (default for this skill).
- `coding` when PR is code-heavy diff review.
- `fast` only for R0 trivial docs/renames.

Details: `references/model-selection.md`.

## Anti-patterns

- Do not copy raw diff into body; summarize.
- Do not hallucinate tests or coverage.
- Do not duplicate skill body into provider dirs; rely on `scripts/compile_harness.py` wrapper (`.claude/skills/pr-creation/SKILL.md`).

## References

Load on demand:
- `references/template.md` — full PR title/body template with placeholders
- `references/model-selection.md` — optimal model decision table
- `references/examples.md` — good vs. bad PR bodies
- `references/checklist.md` — pre-push verification checklist
