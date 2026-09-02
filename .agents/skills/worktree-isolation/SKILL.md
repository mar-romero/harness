---
name: worktree-isolation
description: Isolate concurrent implementation work using one Git worktree and writer lock per task.
---

# worktree-isolation

Use `python3 scripts/worktree.py create TASK-ID` to prepare an isolated worktree. Never assign two writers to the same worktree. Use `status` before removal and refuse destructive cleanup when uncommitted work exists unless explicitly forced by a human.
