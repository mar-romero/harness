#!/usr/bin/env python
"""Migrate repository worktrees onto the shared runtime/session model.

Task HARNESS-WORKTREE-MIGRATION-001 (R3).  Design constraints:

- Durable state (runs, evidence, receipts, locks, migration reports) lives
  under the Git common directory so every linked worktree sees it.
- Per-worktree active provider state lives under
  ``<worktree>/.harness/overlays/<worktree_id>/<provider>/`` and is never
  cross-written from another checkout.
- Legacy unscoped ``.harness/<provider>/`` state is migrated by strict
  copy-then-validate into the owning worktree's overlay archive with an
  immutable shared-runtime receipt, or rejected durably with no mutation.
  Migrated legacy bytes are only archived; a usable current-schema binding
  must always be rebuilt through canonical provider activation, never
  fabricated here.
- Quarantine (removing the legacy source file) is destructive and therefore
  requires ``quarantine=True`` plus a recorded human gate token.
- Rollback only restores from verified backups; nothing is ever deleted.
- Identical reruns are immutable no-ops; differing payloads fail closed as
  collisions.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path

from harnesslib import (
    ROOT, _contained, _reject_reparse_components, load_json, read_provider_active,
    resolved_git_identity, run_dir, runtime_root, safe_task_id, secure_path,
    sha256_file, write_json_atomic, write_json_immutable,
    shared_migration_lock,
    worktree_identity,
)
import worktree

MIGRATION_SCHEMA = 1
RECEIPT_SCHEMA = 1
REJECTED_SCHEMA = 1
QUARANTINE_SCHEMA = 1

PROVIDERS = ('codex', 'opencode', 'subscriptions')
CLASSIFICATIONS = (
    'already_migrated', 'eligible', 'ambiguous', 'dirty', 'potentially_active',
    'legacy_rejected', 'unreadable', 'unregistered', 'collision',
)
# Lowest number wins when several conditions apply.
_PRIORITY = {
    'unreadable': 0, 'collision': 1, 'legacy_rejected': 2, 'ambiguous': 3,
    'dirty': 4, 'potentially_active': 5, 'unregistered': 6,
    'already_migrated': 7, 'eligible': 8,
}

_SECRET_CONTENT = re.compile(
    r'\b(api[_-]?key|secret|token|password|passwd|credential)\b', re.IGNORECASE
)

_MIGRATION_TASK = 'HARNESS-WORKTREE-MIGRATION-001'


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def migration_workdir() -> Path:
    """Shared (Git-common) directory holding migration reports and receipts."""
    return run_dir(_MIGRATION_TASK) / 'migration'


def _receipt_dir(worktree_id: str) -> Path:
    base = migration_workdir()
    return _contained(base / 'receipts' / worktree_id, base)


def _overlay_migrated_dir(provider: str, root: Path) -> Path:
    """Per-worktree overlay archive for validated migrated legacy bytes."""
    from harnesslib import provider_overlay_dir
    top = Path(resolved_git_identity(root)['worktree_root'])
    return secure_path(provider_overlay_dir(provider, top) / 'migrated', top)


def _git(*args, cwd) -> object:
    import subprocess
    return subprocess.run(
        ['git', *args], cwd=str(cwd), text=True, encoding='utf-8',
        errors='replace', capture_output=True, check=False,
    )


def _dirty_files(path: Path) -> list[str]:
    tracked = _git('diff', '--no-renames', '--name-only', 'HEAD', '--', cwd=path)
    untracked = _git('ls-files', '--others', '--exclude-standard', cwd=path)
    names = []
    if tracked.returncode == 0:
        names.extend(tracked.stdout.split())
    if untracked.returncode == 0:
        names.extend(untracked.stdout.split())
    return sorted(set(names))


def _norm(path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _worktree_records():
    from worktree import _worktree_records as records, _short_branch
    out = []
    for record in records():
        path = Path(record['worktree'])
        entry = {
            'path': str(path),
            'head': record.get('HEAD', ''),
            'branch': _short_branch(record.get('branch')),
            'detached': bool(record.get('detached')),
        }
        try:
            resolved_git_identity(path)
            entry['identity'] = resolved_git_identity(path)
        except (ValueError, OSError) as exc:
            entry['identity'] = None
            entry['unreadable_reason'] = str(exc)
        out.append((path, entry))
    return out


def _lock_snapshot(task: str) -> dict:
    """Read-only writer-lock snapshot; never mutates or assigns locks."""
    lk = worktree.lock(task)
    if not lk.is_file():
        return {'lock': False, 'lock_valid': True}
    try:
        data = worktree._load_lock(task)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {'lock': True, 'lock_valid': False, 'lock_reason': str(exc)}
    return {
        'lock': True,
        'lock_valid': True,
        'lock_task_id': data.get('task_id'),
        'lock_branch': data.get('branch'),
        'lock_pid': data.get('pid'),
        'lock_worktree_id': data.get('worktree_id'),
    }


def legacy_files(provider: str, root: Path) -> list[Path]:
    """Existing legacy unscoped provider files for ``root`` (read-only)."""
    return [p for p in _direct_legacy_provider_paths(provider, root)
            if _legacy_source_present(p)]


def _legacy_source_present(path: Path) -> bool:
    """Probe a legacy path only after rejecting reparse components."""
    try:
        _reject_reparse_components(path)
    except (OSError, ValueError):
        # Preserve the lexical candidate for explicit durable rejection.
        return True
    return path.is_file()


_LEGACY_NAMES = (
    'active-task.json', 'session.json', 'permission-audit.jsonl',
    'catalog-snapshot.json', 'model-inventory.json',
    'enriched-inventory.json', 'model-selections.json',
)


def _direct_legacy_provider_paths(provider: str, root: Path) -> list[Path]:
    """Return only legacy files physically owned by this checkout."""
    top = Path(resolved_git_identity(root)['worktree_root'])
    return [top / '.harness' / provider / name for name in _LEGACY_NAMES]


def _shared_legacy_provider_paths(provider: str) -> list[Path]:
    """Return shared-root legacy files without attributing them to a caller."""
    base = Path(runtime_root()).resolve()
    return [base / '.harness' / provider / name for name in _LEGACY_NAMES]


def _bound_shared_sources(provider: str, root: Path) -> list[Path]:
    """Select shared legacy files whose payload explicitly binds to ``root``."""
    top = Path(resolved_git_identity(root)['worktree_root'])
    wid = worktree_identity(top)['worktree_id']
    selected = []
    for source in _shared_legacy_provider_paths(provider):
        try:
            _reject_reparse_components(source)
        except (OSError, ValueError):
            selected.append(source)
            continue
        if not source.is_file():
            continue
        try:
            payload = json.loads(source.read_text(encoding='utf-8'))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            # Malformed shared state must enter the durable rejection path;
            # silently treating it as absent would lose the diagnostic.
            selected.append(source)
            continue
        if not isinstance(payload, dict):
            selected.append(source)
            continue
        try:
            bound = (
                payload.get('worktree_id') == wid and
                payload.get('worktree_root') and
                Path(payload['worktree_root']).resolve() == top
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            bound = False
        if bound:
            selected.append(source)
    return selected


def overlay_state(provider: str, root: Path) -> dict:
    """Summarize per-worktree overlay state without trusting any global path."""
    out = {'active_binding': None, 'files': []}
    from harnesslib import provider_active_path
    top = Path(resolved_git_identity(root)['worktree_root'])
    overlay = secure_path(provider_active_path(provider, root).parent, top)
    out['files'] = (
        sorted(p.name for p in overlay.iterdir() if p.is_file())
        if overlay.is_dir() else []
    )
    active = overlay / 'active-task.json'
    if active.is_file():
        try:
            read_provider_active(provider, root)
            out['active_binding'] = {
                'schema_version': 3, 'valid': True,
                'task_id': json.loads(active.read_text(encoding='utf-8')).get('task_id'),
            }
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            out['active_binding'] = {'valid': False, 'reason': str(exc)}
    return out


def _planning_tasks_hashes(root: Path) -> dict[str, str]:
    """Hash every untracked planning/ and tasks/ file for byte-preservation."""
    hashes = {}
    for top in ('planning', 'tasks'):
        base = root / top
        if not base.is_dir():
            continue
        others = _git('ls-files', '--others', '--exclude-standard', '--', top, cwd=root)
        if others.returncode != 0:
            continue
        for line in others.stdout.splitlines():
            rel = line.strip()
            if not rel:
                continue
            try:
                secure_path(root / rel, root)
                hashes[rel] = sha256_file(root / rel)
            except (ValueError, OSError):
                hashes[rel] = 'unreadable'
    return hashes


def _worktree_task(top: Path) -> str | None:
    """Return the task owner encoded by a registered worktree branch."""
    record = worktree._worktree_record(top)
    if not record:
        return None
    branch = str(record.get('branch') or '')
    prefix = 'refs/heads/agent/'
    if branch.startswith(prefix):
        try:
            return safe_task_id(branch[len(prefix):])
        except ValueError:
            return None
    return None


def _legacy_owner(provider: str, source: Path, top: Path) -> tuple[Path, str | None]:
    """Resolve the only possible owner of a legacy path.

    A direct provider file under the current checkout can be attributed to that
    checkout. A file under the shared runtime root is intentionally ownerless
    unless it carries an explicit binding; it must never be assigned to the
    caller merely because the caller can see it.
    """
    current = Path(resolved_git_identity(top)['worktree_root'])
    try:
        _reject_reparse_components(source)
    except (OSError, ValueError):
        return Path(runtime_root()).resolve(), None
    source_key = _norm(source)
    # Shared paths are considered first and remain ownerless unless their
    # payload carries a verifiable worktree binding.  Visibility from a
    # linked checkout is never ownership evidence.
    for candidate in _shared_legacy_provider_paths(provider):
        if _norm(candidate) != source_key:
            continue
        try:
            payload = json.loads(source.read_text(encoding='utf-8'))
            declared_root = payload.get('worktree_root')
            declared_wid = payload.get('worktree_id')
            if declared_root and declared_wid:
                owner = _resolve_worktree(declared_root)
                if worktree_identity(owner)['worktree_id'] == declared_wid:
                    return owner, declared_wid
        except (OSError, UnicodeDecodeError, json.JSONDecodeError,
                TypeError, AttributeError, ValueError):
            pass
        return Path(runtime_root()).resolve(), None
    for candidate in _direct_legacy_provider_paths(provider, current):
        if _norm(candidate) == source_key:
            return current, worktree_identity(current)['worktree_id']
    raise ValueError('legacy source is not an owned provider path')


def _rejection_reason(provider: str, source: Path, owner: Path | None = None,
                      owner_wid: str | None = None,
                      owner_task: str | None = None) -> str | None:
    """Return a durable rejection reason, or None when preconditions hold."""
    import fnmatch
    policy = load_json('harness/policies/risk-policy.json')
    patterns = policy.get('secret_path_patterns', [])
    if any(fnmatch.fnmatch(source.name, pattern) for pattern in patterns):
        return 'secret_like_path'
    try:
        _reject_reparse_components(source)
    except (OSError, ValueError) as exc:
        return f'reparse_source_rejected: {exc}'
    try:
        text = source.read_text(encoding='utf-8')
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        return f'unreadable: {exc}'
    if _SECRET_CONTENT.search(text):
        return 'secret_like_content'
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return f'malformed: {exc}'
    if not isinstance(payload, dict):
        return 'malformed: legacy state is not a JSON object'
    if payload.get('provider') != provider:
        return 'foreign: legacy provider field mismatch'
    legacy_task = payload.get('task_id')
    if not legacy_task:
        return 'ambiguous: legacy state has no task_id ownership'
    try:
        safe_task_id(str(legacy_task))
    except ValueError as exc:
        return f'ambiguous: unusable legacy task_id ({exc})'
    declared_root = payload.get('worktree_root')
    declared_wid = payload.get('worktree_id')
    if declared_root is not None:
        if owner is None:
            return 'ambiguous: legacy worktree owner is unavailable'
        try:
            if Path(declared_root).resolve() != owner.resolve():
                return 'foreign: legacy worktree_root does not match source owner'
        except (OSError, RuntimeError, TypeError, ValueError):
            return 'ambiguous: legacy worktree_root is unreadable'
    if declared_wid is not None and declared_wid != owner_wid:
        return 'foreign: legacy worktree_id does not match source owner'
    if owner_wid is None:
        if declared_wid is None or declared_root is None:
            return 'ambiguous: shared-runtime legacy state has no explicit worktree ownership'
    elif owner_task and str(legacy_task) != owner_task:
        return (
            'foreign: legacy task binding '
            f'{legacy_task!r} does not match the source worktree owner {owner_task!r}'
        )
    elif not owner_task and (declared_wid is None or declared_root is None):
        return 'ambiguous: source worktree has no task owner and legacy state lacks explicit ownership'
    return None


def _potentially_active(item: dict) -> bool:
    """A live valid overlay binding in a worktree locked by another task.

    Ownership is never inferred from untrusted PID fields; only a validated
    current-schema active binding counts as live-activity evidence.
    """
    lock = item.get('lock') or {}
    if not lock.get('lock'):
        return False
    for provider, state in item.get('providers', {}).items():
        binding = state.get('active_binding') or {}
        if binding.get('valid') and lock.get('lock_pid') != os.getpid():
            return True
    return False


def _rejections_for(item: dict) -> list[dict]:
    out = []
    path = Path(item['path'])
    owner = path if item.get('identity') else None
    owner_wid = item.get('worktree_id')
    owner_task = _worktree_task(path) if owner is not None else None
    for provider, state in item.get('providers', {}).items():
        for raw in state.get('legacy', []):
            reason = _rejection_reason(provider, Path(raw), owner, owner_wid, owner_task)
            if reason:
                out.append({'provider': provider, 'path': raw, 'reason': reason})
    return out


def _collisions_for(item: dict) -> list[dict]:
    """Legacy sources mutated after a receipt for the same logical name."""
    wid = item.get('worktree_id')
    if not wid:
        return []
    rdir = _receipt_dir(wid)
    out = []
    for provider, state in item.get('providers', {}).items():
        for raw in state.get('legacy', []):
            source = Path(raw)
            try:
                _reject_reparse_components(source)
                digest = sha256_file(source)
            except (OSError, ValueError):
                continue
            matched = []
            for p in sorted(rdir.glob(f'{provider}-{source.name}-*.json')):
                if p.name.endswith('.rejected.json') or '.collision.' in p.name:
                    continue
                try:
                    data = json.loads(p.read_text(encoding='utf-8'))
                except (OSError, json.JSONDecodeError):
                    continue
                if data.get('source_sha256') == digest:
                    matched.append(p.name)
            if matched:
                continue
            if any(p.name.startswith(f'{provider}-{source.name}-')
                   for p in rdir.glob(f'{provider}-*.json')
                   if not p.name.endswith('.rejected.json') and '.collision.' not in p.name):
                out.append({'provider': provider, 'path': raw, 'source_sha256': digest})
    return out


def _classify(item: dict) -> str:
    scores = ['eligible']
    if item.get('identity') is None:
        scores.append('unreadable')
    if item.get('unregistered'):
        scores.append('unregistered')
    if item.get('collisions'):
        scores.append('collision')
    if item.get('rejections'):
        scores.append('legacy_rejected')
    if item.get('ambiguous'):
        scores.append('ambiguous')
    if item.get('dirty'):
        scores.append('dirty')
    if item.get('potentially_active'):
        scores.append('potentially_active')
    if item.get('legacy'):
        scores.append('eligible')
    if any(((state or {}).get('active_binding') or {}).get('valid')
           for state in item.get('providers', {}).values()):
        scores.append('already_migrated')
    return min(scores, key=lambda name: _PRIORITY[name])


def _lock_for_path(path: Path) -> dict:
    task = _worktree_task(path)
    if task is None:
        return {'lock': False, 'lock_valid': True,
                'task': None,
                'note': 'registered worktree has no agent task owner'}
    lock = _lock_snapshot(task)
    lock['registered'] = worktree._worktree_record(path) is not None
    lock['task'] = task
    return lock


def inventory() -> dict:
    """Read-only classification of every registered and unregistered worktree."""
    common_root = runtime_root()
    worktrees_dir = common_root / '.worktrees'
    registered = _worktree_records()
    registered_paths = {_norm(p) for p, _ in registered}

    unregistered = []
    reparse_skips = []
    if worktrees_dir.is_dir():
        for path in sorted(worktrees_dir.iterdir()):
            if path.is_dir() and _norm(path) not in registered_paths:
                try:
                    secure_path(path, worktrees_dir)
                except (ValueError, OSError) as exc:
                    record = _write_inventory_skip_record(path, str(exc))
                    reparse_skips.append({
                        'path': str(path), 'reason': str(exc),
                        'mutation': 'none', 'record': str(record),
                    })
                    continue  # reparse/junk path is not a worktree safe to inspect
                try:
                    identity = resolved_git_identity(path)
                except (ValueError, OSError) as exc:
                    identity = None
                    reason = str(exc)
                else:
                    reason = 'directory is not registered with Git'
                unregistered.append({
                    'path': str(path),
                    'registered': False,
                    'identity': identity,
                    'dirty': _dirty_files(path),
                    'planning_tasks_untracked_hashes': _planning_tasks_hashes(path),
                    'classification': 'unregistered',
                    'reason': reason,
                })
                unregistered[-1]['inventory_record'] = str(
                    _write_inventory_record(unregistered[-1])
                )

    report_items = []
    for path, entry in registered:
        item = dict(entry)
        item['worktree_id'] = (entry.get('identity') or {}).get('worktree_id')
        item['lock'] = _lock_for_path(path) if item['worktree_id'] else None
        item['dirty'] = _dirty_files(path) if item['worktree_id'] else []
        item['planning_tasks_untracked_hashes'] = _planning_tasks_hashes(path)
        providers = {}
        for provider in PROVIDERS:
            legacy_paths = legacy_files(provider, path) if item['worktree_id'] else []
            if item['worktree_id']:
                legacy_paths += _bound_shared_sources(provider, path)
            legacy = [str(p) for p in dict.fromkeys(legacy_paths)]
            try:
                state = overlay_state(provider, path) if item['worktree_id'] else {}
            except (ValueError, OSError) as exc:
                state = {'active_binding': {'valid': False, 'reason': str(exc)}}
            providers[provider] = {'legacy': legacy, **state}
        item['providers'] = providers
        item['legacy'] = any(p.get('legacy') for p in providers.values())
        item['potentially_active'] = _potentially_active(item)
        item['collisions'] = _collisions_for(item)
        item['rejections'] = _rejections_for(item)
        for rejection in item['rejections']:
            try:
                record = _write_rejection_record(
                    item['worktree_id'], rejection['provider'],
                    Path(rejection['path']), _digest_for_record(Path(rejection['path'])),
                    rejection['reason'], path,
                )
                rejection['record'] = str(record)
            except (OSError, ValueError):
                rejection['record'] = None
        item['ambiguous'] = bool(item['lock']) and not item['lock'].get('lock_valid', True)
        item['classification'] = _classify(item)
        item['inventory_record'] = str(_write_inventory_record(item))
        report_items.append(item)

    untracked = _planning_tasks_hashes(ROOT)
    from harnesslib import _reject_reparse_components
    preserved = []
    for rel in sorted(untracked):
        if rel in ('unreadable',):
            continue
        if untracked[rel] == 'unreadable':
            continue
        candidate = ROOT / rel
        try:
            _reject_reparse_components(candidate)
            if candidate.is_file() and sha256_file(candidate) == untracked[rel]:
                preserved.append(rel)
        except (ValueError, OSError):
            continue
    shared_legacy = []
    for provider in PROVIDERS:
        for source in _shared_legacy_provider_paths(provider):
            if not _legacy_source_present(source):
                continue
            try:
                owner, owner_wid = _legacy_owner(provider, source, ROOT)
                reason = _rejection_reason(
                    provider, source, owner, owner_wid,
                    _worktree_task(owner) if owner_wid else None,
                )
                owner_path = str(owner) if owner_wid else None
            except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                owner_path = None
                owner_wid = None
                reason = f'ambiguous: {exc}'
            shared_legacy.append({
                'provider': provider,
                'path': str(source),
                'owner_worktree': owner_path,
                'owner_worktree_id': owner_wid,
                'reason': reason,
                'mutation': 'none',
            })
            if reason:
                try:
                    shared_legacy[-1]['record'] = str(
                        _write_shared_rejection_record(
                            provider, source, _digest_for_record(source), reason
                        )
                    )
                except (OSError, ValueError):
                    shared_legacy[-1]['record'] = None
    report = {
        'schema_version': MIGRATION_SCHEMA,
        'task_id': _MIGRATION_TASK,
        'generated_at': _now(),
        'common_root': str(common_root),
        'worktrees': report_items,
        'unregistered_worktrees': unregistered,
        'untracked_hashes': untracked,
        'untracked_hashes_by_worktree': {
            item['path']: item.get('planning_tasks_untracked_hashes', {})
            for item in report_items
        },
        'shared_legacy': shared_legacy,
        'reparse_skips': reparse_skips,
        'codegraph_note': (
            'CodeGraph external provider is out of scope; a future per-worktree '
            'index would live under provider overlays and is preserved as a hook.'
        ),
    }
    write_json_atomic(migration_workdir() / 'inventory.json', report)
    return report


def _receipt_key(provider: str, source: Path, digest: str) -> str:
    return f'{provider}-{source.name}-{digest[:16]}'


def _resolve_worktree(worktree_root) -> Path:
    try:
        candidate = Path(worktree_root)
    except (TypeError, ValueError) as exc:
        raise ValueError('worktree path must be a filesystem path') from exc
    if not candidate.is_dir():
        raise ValueError(f'worktree does not exist: {candidate}')
    try:
        resolved_git_identity(candidate)
    except (ValueError, OSError) as exc:
        raise ValueError(f'worktree Git identity unavailable: {exc}') from exc
    top = Path(resolved_git_identity(candidate)['worktree_root'])
    if not top.is_dir():
        raise ValueError('resolved worktree root is missing')
    if worktree._worktree_record(top) is None:
        raise ValueError('worktree is not registered with Git')
    return top


def _assert_registered_worktree(top: Path, wid: str) -> None:
    """Revalidate the checkout identity before and after sensitive operations."""
    record = worktree._worktree_record(top)
    if record is None:
        raise ValueError('worktree registration changed during migration')
    identity = resolved_git_identity(top)
    if identity.get('worktree_id') != wid:
        raise ValueError('worktree identity changed during migration')
    task = _worktree_task(top)
    if task is not None:
        lock = _lock_snapshot(task)
        if not lock.get('lock') or not lock.get('lock_valid'):
            raise ValueError('valid writer lock is required for task-owned migration')
        if lock.get('lock_worktree_id') != wid:
            raise ValueError('writer lock ownership changed during migration')


def _claim_migration_lock(worktree_id: str | None = None):
    """Serialize all legacy-source migrations across all checkouts.

    The anchor deliberately does not live inside the per-worktree receipts
    directory: ``_artifact_lock`` derives its lock file from the anchor's own
    parent, and receipt writers lock ``receipts/<wid>/.artifact-write.lock``.
    Nesting both on one lock file would self-deadlock the writer process.
    """
    return shared_migration_lock()


def _write_rejection_record(wid: str, provider: str, source: Path, digest: str,
                            reason: str, top: Path) -> Path:
    payload = {
        'schema_version': REJECTED_SCHEMA,
        'status': 'REJECTED',
        'task_id': _worktree_task(top) or safe_task_id(top.name),
        'provider': provider,
        'worktree_id': wid,
        'source': str(source),
        'source_sha256': digest,
        'reason': reason,
    }
    key = _receipt_key(provider, source, digest)
    return write_json_immutable(
        _receipt_dir(wid) / f'{key}.rejected.json', payload
    )


def _task_owner_for_worktree(top: Path) -> str:
    """Prefer the registered branch owner over a custom directory basename."""
    return _worktree_task(top) or safe_task_id(top.name)


def _record_collision(wid: str, provider: str, source: Path, digest: str) -> None:
    key = _receipt_key(provider, source, digest)
    write_json_immutable(_receipt_dir(wid) / f'{key}.collision.json', {
        'schema_version': MIGRATION_SCHEMA,
        'status': 'COLLISION',
        'provider': provider,
        'source': str(source),
        'source_sha256': digest,
        # The receipt is content-addressed by source identity and digest.
        # Do not include wall-clock data: an identical repeated collision must
        # be an immutable no-op rather than a false overwrite attempt.
        'note': 'differing bytes for an already migrated legacy source; fail closed',
    })


def _write_shared_rejection_record(provider: str, source: Path, digest: str,
                                   reason: str) -> Path:
    payload = {
        'schema_version': REJECTED_SCHEMA,
        'status': 'REJECTED',
        'task_id': _MIGRATION_TASK,
        'provider': provider,
        'source': str(source),
        'source_sha256': digest,
        'reason': reason,
        'ownership': 'unassigned shared runtime state',
    }
    return write_json_immutable(
        migration_workdir() / 'rejections' /
        f'shared-{_receipt_key(provider, source, digest)}.json', payload,
    )


def _write_inventory_skip_record(path: Path, reason: str) -> Path:
    payload = {
        'schema_version': MIGRATION_SCHEMA,
        'status': 'INVENTORY_SKIP',
        'task_id': _MIGRATION_TASK,
        'path': str(path),
        'reason': reason,
        'mutation': 'none',
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
    ).hexdigest()
    return write_json_immutable(
        migration_workdir() / 'rejections' /
        f'inventory-skip-{digest[:24]}.json',
        {**payload, 'record_sha256': digest},
    )


def _digest_for_record(source: Path) -> str:
    try:
        _reject_reparse_components(source)
        return sha256_file(source)
    except (OSError, ValueError) as exc:
        return f'unavailable-{type(exc).__name__}'


def _write_operation_record(provider: str, top: Path, wid: str,
                            sources: list[Path], results: list[dict]) -> Path:
    """Append an immutable, content-addressed record of every migration run."""
    status = 'NO_LEGACY_STATE' if not sources else 'COMPLETED'
    body = {
        'schema_version': MIGRATION_SCHEMA,
        'status': status,
        'task_id': _worktree_task(top) or safe_task_id(top.name),
        'provider': provider,
        'worktree_root': str(top),
        'worktree_id': wid,
        'sources': [str(path) for path in sources],
        'results': results,
    }
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(',', ':')).encode('utf-8')
    ).hexdigest()
    payload = {**body, 'operation_sha256': digest}
    return write_json_immutable(
        migration_workdir() / 'operations' / wid / f'{provider}-{digest[:24]}.json',
        payload,
    )


def _write_inventory_record(item: dict) -> Path:
    """Persist the read-only classification that preceded any migration."""
    body = {
        'schema_version': MIGRATION_SCHEMA,
        'status': 'INVENTORIED',
        'path': item['path'],
        'head': item.get('head'),
        'branch': item.get('branch'),
        'worktree_id': item.get('worktree_id'),
        'classification': item.get('classification'),
        'dirty': item.get('dirty', []),
        'providers': item.get('providers', {}),
        'rejections': item.get('rejections', []),
        'reason': item.get('reason'),
        'mutation': 'none',
    }
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(',', ':')).encode('utf-8')
    ).hexdigest()
    payload = {**body, 'inventory_sha256': digest}
    location = (migration_workdir() / 'operations' /
                (item.get('worktree_id') or 'unresolved'))
    return write_json_immutable(location / f'inventory-{digest[:24]}.json', payload)


def migrate_legacy(provider: str, worktree_root, quarantine: bool = False,
                   human_gate: str | None = None) -> dict:
    """Copy-then-validate legacy provider state into the worktree overlay."""
    if provider not in PROVIDERS:
        raise ValueError(f'unsupported provider: {provider!r}')
    top = _resolve_worktree(worktree_root)
    wid = worktree_identity(top)['worktree_id']
    _assert_registered_worktree(top, wid)
    legacy = [p for p in _direct_legacy_provider_paths(provider, top)
              if _legacy_source_present(p)]
    legacy.extend(_bound_shared_sources(provider, top))
    legacy = list(dict.fromkeys(legacy))
    with _claim_migration_lock(wid):
        _assert_registered_worktree(top, wid)
        if not legacy:
            operation = _write_operation_record(provider, top, wid, [], [])
            return {'provider': provider, 'worktree': str(top), 'worktree_id': wid,
                    'no_legacy_state': True, 'results': [],
                    'operation_record': str(operation)}
        results = []
        for source in legacy:
            owner, owner_wid = _legacy_owner(provider, source, top)
            reason = _rejection_reason(
                provider, source, owner, owner_wid,
                _worktree_task(owner) if owner_wid else None,
            )
            digest = _digest_for_record(source)
            if reason:
                shared_source = owner_wid is None and _norm(source) in {
                    _norm(candidate)
                    for candidate in _shared_legacy_provider_paths(provider)
                }
                path = (
                    _write_shared_rejection_record(provider, source, digest, reason)
                    if shared_source else
                    _write_rejection_record(wid, provider, source, digest, reason, top)
                )
                results.append({'source': str(source), 'rejected': reason,
                                'record': str(path)})
                continue
            # Idempotency / collision precheck under the migration lock.
            existing = [
                p for p in sorted(_receipt_dir(wid).glob(
                    f'{provider}-{source.name}-*.json'))
                if not p.name.endswith(('.rejected.json', '.quarantine-rejected.json'))
                and '.collision.' not in p.name
            ]
            matched = [p for p in existing
                       if _valid_migration_receipt(
                           p, provider, source, digest, top, wid)]
            stale = [p for p in existing if p not in matched]
            if stale:
                _record_collision(wid, provider, source, digest)
                results.append({'source': str(source), 'collision': True})
                continue
            if matched:
                # Identical rerun: source bytes already have a MIGRATED
                # receipt; the rerun is an immutable no-op.
                entry = {'source': str(source), 'already_migrated': True,
                         'receipt': str(matched[0])}
                if quarantine:
                    entry['quarantine'] = _quarantine_legacy_unlocked(
                        provider, top, source, digest, wid, human_gate)
                results.append(entry)
                continue
            results.append(_copy_validate_record(
                wid, provider, top, source, digest))
            _assert_registered_worktree(top, wid)
            if quarantine:
                results[-1]['quarantine'] = _quarantine_legacy_unlocked(
                    provider, top, source, digest, wid, human_gate)
    operation = _write_operation_record(provider, top, wid, legacy, results)
    return {'provider': provider, 'worktree': str(top), 'worktree_id': wid,
            'results': results, 'operation_record': str(operation)}


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def _valid_migration_receipt(path: Path, provider: str, source: Path,
                             digest: str, top: Path, wid: str) -> bool:
    """Accept an idempotent receipt only when its archive is intact and bound."""
    try:
        payload = _json(path)
        if not isinstance(payload, dict) or payload.get('status') != 'MIGRATED':
            return False
        if payload.get('schema_version') != RECEIPT_SCHEMA:
            return False
        if payload.get('provider') != provider or payload.get('worktree_id') != wid:
            return False
        if payload.get('source') != str(source) or payload.get('source_sha256') != digest:
            return False
        if payload.get('worktree_root') != str(top):
            return False
        expected_task = _worktree_task(top) or safe_task_id(top.name)
        if payload.get('task_id') != expected_task:
            return False
        if path.name != f'{_receipt_key(provider, source, digest)}.json':
            return False
        receipt_hash = payload.get('receipt_sha256')
        body = dict(payload)
        body.pop('receipt_sha256', None)
        expected_receipt_hash = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(',', ':')).encode('utf-8')
        ).hexdigest()
        if receipt_hash != expected_receipt_hash:
            return False
        dest = Path(payload.get('dest', ''))
        expected = _overlay_migrated_dir(provider, top) / source.name
        if os.path.normcase(str(dest)) != os.path.normcase(str(expected)):
            return False
        _reject_reparse_components(dest)
        return dest.is_file() and sha256_file(dest) == digest and payload.get('dest_sha256') == digest
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False


def _copy_validate_record(wid: str, provider: str, top: Path, source: Path,
                          digest: str) -> dict:
    _assert_registered_worktree(top, wid)
    _reject_reparse_components(source)
    source_bytes = source.read_bytes()
    if hashlib.sha256(source_bytes).hexdigest() != digest:
        raise ValueError('source changed before migration snapshot')
    dest_dir = _overlay_migrated_dir(provider, top)
    _reject_reparse_components(dest_dir)
    dest = secure_path(dest_dir / source.name, dest_dir)
    created = False
    created_identity = None
    if dest.is_file():
        if sha256_file(dest) != digest:
            raise ValueError(
                f'migration collision: overlay archive differs for {source.name}'
            )
    else:
        write_json_exclusive_bytes(dest, source_bytes)
        created = True
        created_stat = os.stat(dest)
        created_identity = (created_stat.st_dev, created_stat.st_ino)
    try:
        if sha256_file(dest) != digest:
            raise ValueError('copy-then-validate failed: destination digest mismatch')
        _reject_reparse_components(source)
        if sha256_file(source) != digest:
            raise ValueError('source changed during migration; destination is untrusted')
        _assert_registered_worktree(top, wid)
    except BaseException:
        if created:
            try:
                current = os.stat(dest)
                if (current.st_dev, current.st_ino) == created_identity:
                    dest.unlink()
            except OSError:
                pass
        raise
    receipt = {
        'schema_version': RECEIPT_SCHEMA,
        'status': 'MIGRATED',
        'task_id': _task_owner_for_worktree(top),
        'provider': provider,
        'worktree_root': str(top),
        'worktree_id': wid,
        'source': str(source),
        'source_sha256': digest,
        'dest': str(dest),
        'dest_sha256': sha256_file(dest),
        'migrated_at': _now(),
        'binding_note': (
            'archived legacy bytes only; rebuild the active binding through '
            'canonical provider activation'
        ),
    }
    receipt['receipt_sha256'] = hashlib.sha256(
        json.dumps(receipt, sort_keys=True, separators=(',', ':')).encode('utf-8')
    ).hexdigest()
    receipt_path = _receipt_dir(wid) / f'{_receipt_key(provider, source, digest)}.json'
    receipt_existed = receipt_path.exists()
    receipt_identity = None
    write_json_immutable(receipt_path, receipt)
    if not receipt_existed:
        receipt_stat = os.stat(receipt_path)
        receipt_identity = (receipt_stat.st_dev, receipt_stat.st_ino)
    try:
        _reject_reparse_components(source)
        if sha256_file(source) != digest:
            raise ValueError('source changed after migration receipt; destination is untrusted')
    except BaseException:
        if not receipt_existed:
            try:
                current = os.stat(receipt_path)
                if (current.st_dev, current.st_ino) == receipt_identity:
                    receipt_path.unlink()
            except OSError:
                pass
        if created:
            try:
                current = os.stat(dest)
                if (current.st_dev, current.st_ino) == created_identity:
                    dest.unlink()
            except OSError:
                pass
        raise
    return {'source': str(source), 'migrated_to': str(dest)}


def write_json_exclusive_bytes(path: Path, data: bytes) -> None:
    _reject_reparse(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    if hasattr(os, 'O_BINARY'):
        flags |= os.O_BINARY
    fd = os.open(path, flags, 0o600)
    created = os.fstat(fd)
    handed_to_file = False
    try:
        with os.fdopen(fd, 'wb') as handle:
            handed_to_file = True
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        if not handed_to_file:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            current = os.stat(path)
            if (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino):
                path.unlink()
        except OSError:
            pass
        raise


def _reject_reparse(path: Path) -> None:
    from harnesslib import _reject_reparse_components
    _reject_reparse_components(path)


def quarantine_legacy(provider: str, top: Path, source: Path, digest: str,
                      wid: str, human_gate: str | None) -> dict:
    """Destructively quarantine one source under the shared migration lock."""
    with _claim_migration_lock(wid):
        return _quarantine_legacy_unlocked(provider, top, source, digest, wid, human_gate)


def _quarantine_legacy_unlocked(provider: str, top: Path, source: Path, digest: str,
                                 wid: str, human_gate: str | None) -> dict:
    """Refuse destructive removal when atomic source ownership is unavailable.

    The migration remains reversible through the validated overlay archive.  A
    caller may request quarantine only to obtain a durable, explicit rejection;
    this task never unlinks a live provider file because a concurrent writer
    cannot be excluded portably on every supported filesystem.
    """
    if not human_gate:
        raise ValueError(
            'quarantine is destructive (R3); requires an explicit recorded human gate'
        )
    if len(str(human_gate)) < 8:
        raise ValueError('human gate token is too short to be a recorded approval')
    rdir = _receipt_dir(wid)
    path = rdir / f'{_receipt_key(provider, source, digest)}.quarantine-rejected.json'
    if any(candidate != path for candidate in rdir.glob(
            f'{provider}-{source.name}-*.quarantine-rejected.json')):
        raise ValueError(f'quarantine rejection receipt collision: {path}')
    record = {
        'schema_version': QUARANTINE_SCHEMA,
        'status': 'QUARANTINE_REJECTED',
        'task_id': _worktree_task(top) or safe_task_id(top.name),
        'provider': provider,
        'worktree_root': str(top),
        'worktree_id': wid,
        'source': str(source),
        'source_sha256': digest,
        'reason': 'atomic source ownership cannot be proven; source preserved',
        'human_gate': 'recorded',
    }
    if path.exists():
        _reject_reparse(path)
        try:
            previous = _json(path)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f'quarantine rejection receipt collision: {path}') from exc
        timestamp = previous.get('recorded_at') if isinstance(previous, dict) else None
        if not isinstance(timestamp, str) or not timestamp or previous != {
            **record, 'recorded_at': timestamp,
        }:
            raise ValueError(f'quarantine rejection receipt collision: {path}')
        manifest = path
    else:
        manifest = write_json_immutable(path, {**record, 'recorded_at': _now()})
    return {'rejected': 'atomic source ownership cannot be proven; source preserved',
            'record': str(manifest)}


def _receipt_dir_key_path(wid, provider, source, digest):
    return None


def rollback_receipt(receipt_path: Path) -> dict:
    """Restore a historical quarantine from a verified immutable byte snapshot."""
    path = Path(receipt_path)
    _reject_reparse(path)
    path = path.resolve()
    payload = json.loads(path.read_text(encoding='utf-8'))
    if payload.get('schema_version') != QUARANTINE_SCHEMA or payload.get('status') != 'QUARANTINED':
        raise ValueError('rollback requires a QUARANTINED manifest record')
    provider = payload.get('provider')
    if provider not in PROVIDERS:
        raise ValueError('rollback refused: invalid provider')
    wid = payload.get('worktree_id')
    if not isinstance(wid, str) or not re.fullmatch(r'[0-9a-f]{64}', wid):
        raise ValueError('rollback refused: invalid worktree identity')
    receipt_hash = payload.get('receipt_sha256')
    receipt_body = dict(payload)
    receipt_body.pop('receipt_sha256', None)
    expected_receipt_hash = hashlib.sha256(
        json.dumps(receipt_body, sort_keys=True, separators=(',', ':')).encode('utf-8')
    ).hexdigest()
    if receipt_hash != expected_receipt_hash:
        raise ValueError('rollback refused: manifest integrity mismatch')
    if payload.get('status') != 'QUARANTINED':
        raise ValueError('rollback refused: only historical QUARANTINED manifests are restorable')
    with _claim_migration_lock(wid):
        return _rollback_receipt_unlocked(path, payload, provider, wid)


def _rollback_receipt_unlocked(path: Path, payload: dict, provider: str,
                               wid: str) -> dict:
    receipt_dir = _receipt_dir(wid)
    if secure_path(path, receipt_dir) != path:
        raise ValueError('rollback refused: manifest is outside its receipt directory')
    top_value = payload.get('worktree_root')
    if not isinstance(top_value, str):
        raise ValueError('rollback refused: manifest lacks worktree provenance')
    top = _resolve_worktree(top_value)
    if worktree_identity(top)['worktree_id'] != wid:
        raise ValueError('rollback refused: worktree identity provenance mismatch')
    _assert_registered_worktree(top, wid)
    source = Path(payload.get('source', ''))
    _reject_reparse(source)
    if source.exists():
        raise ValueError('rollback refused: original source already exists; nothing to restore')
    allowed_sources = _direct_legacy_provider_paths(provider, top)
    if not any(os.path.normcase(str(source.resolve())) == os.path.normcase(str(candidate.resolve()))
               for candidate in allowed_sources):
        raise ValueError('rollback refused: source is not an owned legacy provider path')
    overlay = _overlay_migrated_dir(provider, top)
    dest = secure_path(overlay / source.name, overlay)
    if payload.get('overlay_copy') != str(dest):
        raise ValueError('rollback refused: overlay provenance mismatch')
    if not dest.is_file():
        raise ValueError('rollback refused: overlay copy is missing')
    if payload.get('overlay_copy_sha256') != sha256_file(dest):
        raise ValueError('rollback refused: overlay copy hash mismatch')
    backup_dir = secure_path(dest.parent / 'quarantine-backup', dest.parent)
    backup = secure_path(backup_dir / f'{source.name}.{payload.get("source_sha256", "")[:16]}', backup_dir)
    if payload.get('backup') != str(backup):
        raise ValueError('rollback refused: backup provenance mismatch')
    digest = payload.get('source_sha256')
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('rollback refused: invalid source hash')
    if not backup.is_file():
        raise ValueError('rollback refused: backup is missing')
    backup_bytes = backup.read_bytes()
    if hashlib.sha256(backup_bytes).hexdigest() != digest:
        raise ValueError('rollback refused: backup hash mismatch (concurrent change?)')
    if not payload.get('overlay_copy_sha256'):
        raise ValueError('rollback record lacks overlay copy hash metadata')
    _reject_reparse(source.parent)
    source.parent.mkdir(parents=True, exist_ok=True)
    write_json_exclusive_bytes(source, backup_bytes)
    if sha256_file(source) != digest:
        raise ValueError('rollback postcondition failed: restored digest mismatch')
    restored_rid = write_json_immutable(
        Path(str(path) + '.rollback.json'), {
            'schema_version': QUARANTINE_SCHEMA,
            'status': 'ROLLED_BACK',
            'manifest': str(path),
            'restored_source': str(source),
            'restored_sha256': sha256_file(source),
            'rollback_at': _now(),
        })
    return {'restored': str(source), 'record': str(restored_rid)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='cmd', required=True)

    sub.add_parser('inventory', help='read-only worktree inventory and classification')

    p = sub.add_parser('migrate')
    p.add_argument('provider', choices=PROVIDERS)
    p.add_argument('worktree')
    p.add_argument('--quarantine', action='store_true',
                   help='destructive: remove legacy source after migration')
    p.add_argument('--human-gate',
                   help='recorded human approval token required by --quarantine')

    p = sub.add_parser('rollback')
    p.add_argument('receipt', help='path to a .quarantine.json manifest')

    args = ap.parse_args()
    if args.cmd == 'inventory':
        out = inventory()
    elif args.cmd == 'migrate':
        if args.quarantine and not args.human_gate:
            ap.error('--quarantine requires --human-gate (recorded human approval)')
        out = migrate_legacy(args.provider, args.worktree,
                             quarantine=args.quarantine, human_gate=args.human_gate)
    else:
        out = rollback_receipt(args.receipt)
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
