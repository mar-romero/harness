# Pre-PR Checklist — pr-creation

Run before `gh pr create`. Check off deterministically.

## 1. Compile / harness

- [ ] `python scripts/compile_harness.py` — ran if skill/role changed
- [ ] `python scripts/compile_harness.py --check` — `generated artifacts are in sync`

## 2. Secrets and scope

- [ ] `git diff origin/main...HEAD --stat` reviewed — no secrets/tokens/credentials
- [ ] `git diff origin/main...HEAD` scanned — no `.env`, private keys, or `sk-` strings
- [ ] Branch is not `main` — `git branch --show-current`

## 3. Title/body quality

- [ ] Title matches `type(scope): intent` (≤72 chars, imperative, no period)
- [ ] Body has all headings: Summary, Changes, Testing, Risks, Checklist, Links
- [ ] No `{placeholder}` left; empty sections say `None`
- [ ] Testing section marks `DETERMINISTIC` vs `INFERRED`

## 4. Links

- [ ] Task file `tasks/TASK-xxx.json` or issue `#N` linked and exists
- [ ] `Closes #N` used only if issue should auto-close

## 5. Create and verify

- [ ] `gh pr create --title "..." --body-file /tmp/pr-body.md` — captured URL
- [ ] `gh pr view --json url,state,title,body --jq .url` — URL matches expected repo
- [ ] If `gh` unauthenticated: record `INFERRED: pr not verified remotely` and push branch only

## Quick command bundle

```bash
git status --short
git diff --stat origin/main...HEAD
git log --oneline origin/main..HEAD -n 10
python scripts/compile_harness.py --check
# then create
gh pr create --title "type(scope): intent" --body-file /tmp/pr-body.md
gh pr view --json url,state,title
```
