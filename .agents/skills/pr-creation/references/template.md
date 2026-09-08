# PR Template — pr-creation

Load this file only when drafting a PR title/body. Keep placeholders explicit; fail closed if context missing.

## Title template

```
<type>(<scope>): <concise intent, imperative, <=72 chars>
```

- type: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `perf`, `ci`, `revert`
- scope: area affected (`harness`, `agents`, `skills`, `api`, etc.) — lowercase, no spaces
- intent: what changes for the user/reviewer, not how

Validation:
- No trailing period.
- No `WIP`, no generic `update` alone.
- If task exists: include `TASK-xxx` in body, not title.

## Body template

Render to a temp file then pass via `gh pr create --body-file`.

```markdown
## Summary
{1-3 sentences: problem, solution, why now. Link motivation to task/issue.}

## Changes
- {logical change 1} (`path/to/file:lines` or `git diff --stat` summary)
- {logical change 2}
- {notable side effects / migrations / compat notes or `None`}

## Testing
- [ ] `python scripts/compile_harness.py --check` — {pass/fail, output}
- [ ] `pytest -q` / targeted tests — {which tests, result}
- [ ] Manual verification — {steps, evidence}
- Deterministic evidence: {commands + excerpt or `INFERRED: reason`}

## Risks
- Level: R0|R1|R2|R3 — {justification}
- Residual risks: {list or `None`}
- Rollback: {revert commit / feature flag / `N/A`}

## Checklist
- [ ] Title follows Conventional Commits
- [ ] No secrets/tokens in diff (`git diff origin/main...HEAD` checked)
- [ ] Linked task/issue: {Closes #N / TASK-xxx}
- [ ] Docs updated if behavior changed
- [ ] `gh pr view` verified after creation

## Links
- Task: {TASK-xxx or `N/A`}
- Issue: {Closes #N or `N/A`}
- Diff stat: `git diff --stat origin/main...HEAD` → {paste summary}

### Placeholders guide

- Replace `{...}` — do not leave placeholders.
- If no task/issue: use `N/A` and note in Risks.
- For empty sections: write `None` rather than deleting heading (keeps reviewer scan stable).

## Context collection commands

Run before drafting:

```bash
git status --short
git diff --stat origin/main...HEAD
git log --oneline origin/main..HEAD
gh issue view <num> --json title,body,labels    # if issue-driven
cat tasks/TASK-xxx.json                         # if task-driven
python scripts/compile_harness.py --check       # harness staleness
```

Map `git diff --stat` to Changes bullets; map `git log` to intent; do not paste full diff.

## Anti-hallucination

- Only list tests actually run.
- Mark `DETERMINISTIC` vs `INFERRED` per AGENTS.md evidence contract.
- If `gh` not authenticated: note `INFERRED: gh auth missing, URL not verified` instead of fabricating URL.
```

