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

## Legacy Provider-State Migration Model

`scripts/worktree_migration.py` migrates worktrees onto the shared
runtime/session model. The model:

- **worktree_id**: `harnesslib.worktree_identity()` derives a stable, opaque
  SHA-256 identity from Git's own checkout boundary (`--show-toplevel` plus the
  Git-common directory, `normcase`-normalized on Windows). It is never taken
  from cwd, a caller-supplied session id, or a binding's claimed value.
- **Overlays**: per-worktree active provider state lives only under
  `<worktree>/.harness/overlays/<worktree_id>/<provider>/` and is never
  cross-written from another checkout; foreign bindings are rejected by
  `harnesslib.read_provider_active`.
- **Git-common sharing**: durable state (`.harness/runs/`, evidence chains,
  receipts, locks, migration reports) lives under the Git common directory, so
  every linked worktree observes identical bytes.
- **Migration, rejection, quarantine**: legacy unscoped
  `.harness/<provider>/` state is migrated by strict copy-then-validate into
  the owning worktree's overlay archive (`migrated/`) with an immutable
  shared-runtime receipt, or rejected durably (`.rejected.json`, no mutation of
  legacy bytes). Migrated legacy bytes are archived only; a usable
  current-schema binding must be rebuilt through canonical provider activation.
  Quarantine is a human-gated operation, but provider active-state quarantine
  currently preserves the source and records `QUARANTINE_REJECTED` whenever
  atomic ownership of source removal cannot be proven (including Windows).
  Callers must treat that result as `cleared=false`; no source is silently
  deleted. Rollback restores only from verified backups; migration never
  deletes worktrees, branches, planning files, or task files.
- **Idempotency**: identical reruns are immutable no-ops (`already_migrated`);
  differing payloads for the same logical name fail closed as collisions.
- **CodeGraph**: out of scope for migration, but compatible: no install or
  integration step is required or performed here.
