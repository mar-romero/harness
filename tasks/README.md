# Executable tasks

This folder contains durable task JSON files.

- `TASK-ID.json`: one task conforming to `harness/schema/task.schema.json`.
- `README.md`: defines the task input boundary.

Each task should include an ID, description, acceptance criteria, prospective
files, risk, and structured risk factors when applicable. Activation snapshots
the task, routes it, builds context, isolates the writer in a worktree, and
requires checks, review, verification, and evidence before closure.

Do not store runtime results, handoffs, or logs here; those belong under
`.harness/runs/<task-id>/`.
