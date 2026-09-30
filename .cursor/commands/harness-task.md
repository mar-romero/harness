# Harness task

Run the durable harness task named after this command (a path under `tasks/`, or a task ID) as the primary Cursor orchestrator.

1. Read and follow `.cursor/rules/harness-orchestrator.mdc` for the whole lifecycle.
2. Record the Task tool model IDs available in this chat with `python scripts/providers/cursor_activate_task.py --host-models <ids>`.
3. Activate with `python scripts/providers/cursor_activate_task.py <task-path>` and use its `delegation` plan to pick `subagent_type` and `model` for every stage.
4. Follow `progress.json.current_step` until the finish gate allows closure, and report the exact blocker otherwise.

If no task file exists yet, first turn the request into one with the `harness-request` skill (or `idea-to-work` for a broad product idea) and stop for approval when it has blocking questions.
