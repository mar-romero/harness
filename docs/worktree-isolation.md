# Worktree Isolation

Task worktrees are owned through `scripts/worktree.py`. The official workflow
keeps the worktree registration and writer lock bound to the same task id,
branch, worktree path, integration branch, and base commit.

## Normal Creation

Create a task worktree with:

```bash
python scripts/worktree.py create TASK-ID --execute
```

This first creates `agent/TASK-ID` from the named integration branch, records
an explicit reset to the requested `--base` (when it differs), then registers
`.worktrees/TASK-ID` and writes an exclusive writer lock under the common
repository `.harness/locks/` directory. Those Git reflog entries preserve both
the integration target and exact base for recovery. Linked worktrees share that
common lock namespace, so status checks from the main checkout and from
isolated worktrees see the same ownership state.

## Status Inspection

Inspect ownership with:

```bash
python scripts/worktree.py status TASK-ID
```

`lock: true` means the lock was parsed and validated against the registered
worktree, checked-out `agent/TASK-ID` branch, local integration branch, and base
commit ancestry. Missing, malformed, mismatched, or stale lock metadata is
reported as `lock: false` with `lock_reason`.

## Recovery

Use recovery only for an already registered, clean task worktree whose branch is
exactly `agent/TASK-ID`:

```bash
python scripts/worktree.py recover TASK-ID --execute
```

Recovery reads local Git reflog provenance for the task branch. It adopts the
worktree only when the branch creation entry identifies a local integration
branch and its base commit can be verified; for an explicit divergent `--base`,
the recorded branch-reset entry supplies that exact base. It refuses absent,
expired, malformed, ambiguous, mismatched, dirty, or unregistered worktrees.

## Lock Safety

Do not manually fabricate, edit, move, or overwrite writer locks. Create and
recovery both use exclusive lock creation; if another lock already exists, the
command fails without replacing it. A failed post-worktree lock claim leaves the
registered worktree in place for explicit inspection or recovery.
