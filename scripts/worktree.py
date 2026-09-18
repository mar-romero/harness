#!/usr/bin/env python
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path, PurePosixPath
import shutil

from harnesslib import (
    ROOT, git, load_json, read_provider_active, resolved_git_identity, run_dir,
    runtime_root, safe_task_id, scope_expansion_digest, secure_path, write_json_atomic,
)


def _norm_path(path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _same_path(left, right) -> bool:
    return _norm_path(left) == _norm_path(right)


def _safe_worktree(task: str) -> Path:
    return secure_path(wt(task), _common_repo_root())


def _safe_lock(task: str) -> Path:
    return secure_path(lock(task), _common_repo_root())


def _common_repo_root() -> Path:
    return runtime_root()


def lock(task):
    return _common_repo_root() / '.harness' / 'locks' / f'{safe_task_id(task)}.json'


def wt(task):
    return _common_repo_root() / '.worktrees' / safe_task_id(task)


def publish_artifact(task):
    return run_dir(task) / 'publish.json'


def ensure_git():
    r = git('rev-parse', '--is-inside-work-tree', check=False)
    if r.returncode != 0:
        raise SystemExit('not inside a git repository; initialize git before creating worktrees')


def _git_lines(*args, cwd=None):
    r = git(*args, cwd=cwd)
    return [line.strip().replace('\\', '/') for line in r.stdout.splitlines() if line.strip()]

def _worktree_records():
    result = git(
        "worktree",
        "list",
        "--porcelain",
        cwd=_common_repo_root(),
    )

    records = []
    current = None
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            if current is not None:
                records.append(current)
            current = {"worktree": Path(line[len("worktree "):].strip())}
            continue
        if current is None:
            continue
        if line.startswith("HEAD "):
            current["HEAD"] = line[len("HEAD "):].strip()
        elif line.startswith("branch "):
            current["branch"] = line[len("branch "):].strip()
        elif line == "detached":
            current["detached"] = True
    if current is not None:
        records.append(current)
    return records


def _worktree_record(path: Path):
    target = _norm_path(path)
    for record in _worktree_records():
        if _norm_path(record["worktree"]) == target:
            return record
    return None


def _worktree_registered(path: Path) -> bool:
    return _worktree_record(path) is not None


def _expected_branch(task: str) -> str:
    return f'agent/{task}'


def _short_branch(ref: str | None) -> str:
    value = str(ref or '')
    prefix = 'refs/heads/'
    if value.startswith(prefix):
        return value[len(prefix):]
    return value


def _validate_ref_name(name: str, label: str) -> None:
    if not isinstance(name, str) or not name:
        raise ValueError(f'writer lock lacks {label}')
    result = git('check-ref-format', f'refs/heads/{name}', cwd=_common_repo_root(), check=False)
    if result.returncode != 0:
        raise ValueError(f'writer lock {label} is not a valid local branch name')


def _resolve_commit(commit: str, label: str, cwd: Path) -> str:
    if not isinstance(commit, str) or not commit:
        raise ValueError(f'writer lock lacks {label}')
    result = git('rev-parse', '--verify', f'{commit}^{{commit}}', cwd=cwd, check=False)
    if result.returncode != 0:
        raise ValueError(f'writer lock {label} is not a commit')
    resolved = result.stdout.strip()
    if resolved != commit:
        raise ValueError(f'writer lock {label} must be the full commit id')
    return resolved


def _validate_lock_data(task: str, data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError('writer lock is not a JSON object')
    if data.get('schema_version') != 3:
        raise ValueError('writer lock schema_version mismatch')
    if data.get('task_id') != task:
        raise ValueError('writer lock task_id mismatch')

    path = _safe_worktree(task)
    branch = _expected_branch(task)
    if not data.get('worktree') or not _same_path(data.get('worktree'), path):
        raise ValueError('writer lock worktree path mismatch')
    if data.get('branch') != branch:
        raise ValueError('writer lock branch mismatch')

    identity = resolved_git_identity(path)
    for key, message in (
        ('worktree_root', 'writer lock Git worktree root mismatch'),
        ('git_common_dir', 'writer lock Git common directory mismatch'),
        ('git_dir', 'writer lock Git directory mismatch'),
        ('worktree_id', 'writer lock worktree identity mismatch'),
    ):
        if data.get(key) != identity[key]:
            raise ValueError(message)

    _validate_ref_name(data.get('integration_branch'), 'integration_branch')
    base_commit = _resolve_commit(data.get('base_commit'), 'base_commit', _common_repo_root())

    record = _worktree_record(path)
    if record is None:
        raise ValueError('assigned worktree is not registered')
    if _short_branch(record.get('branch')) != branch:
        raise ValueError('registered worktree branch mismatch')
    if not path.is_dir():
        raise ValueError('assigned worktree path is missing')

    current_branch = git('branch', '--show-current', cwd=path, check=False)
    if current_branch.returncode != 0 or current_branch.stdout.strip() != branch:
        raise ValueError('assigned worktree checked-out branch mismatch')

    integration_ref = git(
        'rev-parse',
        '--verify',
        f'refs/heads/{data["integration_branch"]}^{{commit}}',
        cwd=_common_repo_root(),
        check=False,
    )
    if integration_ref.returncode != 0:
        raise ValueError('writer lock integration_branch is not a local branch')

    ancestor = git('merge-base', '--is-ancestor', base_commit, 'HEAD', cwd=path, check=False)
    if ancestor.returncode != 0:
        raise ValueError('writer lock base_commit is not an ancestor of the worktree HEAD')
    return data


def _root_branch():
    branch = git('branch', '--show-current').stdout.strip()
    if not branch:
        raise ValueError('canonical repository is detached; worktree publication requires a named integration branch')
    return branch


def _load_lock(task):
    p = _safe_lock(task)
    if not p.is_file():
        raise ValueError(f'writer lock missing: {p}')
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
    except Exception as exc:
        raise ValueError(f'writer lock is invalid: {exc}') from exc
    return _validate_lock_data(task, data)


def _write_lock_exclusive(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, ensure_ascii=False) + '\n'
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = None
    try:
        fd = os.open(str(path), flags, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            fd = None
            handle.write(payload)
    except FileExistsError as exc:
        raise ValueError(f'writer lock already exists: {path}') from exc
    except Exception:
        if fd is not None:
            os.close(fd)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def _recoverable_provenance(branch: str) -> tuple[str, str]:
    result = git(
        'reflog',
        'show',
        '--format=%H%x00%gs',
        f'refs/heads/{branch}',
        cwd=_common_repo_root(),
        check=False,
    )
    if result.returncode != 0:
        raise ValueError('recoverable branch reflog is absent')
    rows = [line for line in result.stdout.splitlines() if line.strip()]
    if not rows:
        raise ValueError('recoverable branch reflog is expired or empty')

    # `git reflog show` walks entries newest-first and does not support
    # `--reverse`.  The oldest entry is the branch-creation provenance.
    first = rows[-1]
    try:
        base_commit, subject = first.split('\x00', 1)
    except ValueError as exc:
        raise ValueError('recoverable branch reflog is malformed') from exc

    prefix = 'branch: Created from '
    if not subject.startswith(prefix):
        raise ValueError('recoverable branch creation provenance is absent')
    source = subject[len(prefix):].strip()
    if not source or source == 'HEAD' or source.startswith('HEAD@'):
        raise ValueError('recoverable branch creation provenance is ambiguous')
    if source.startswith('refs/heads/'):
        integration_branch = source[len('refs/heads/'):]
    else:
        integration_branch = source

    _validate_ref_name(integration_branch, 'integration_branch')
    local_ref = git(
        'rev-parse',
        '--verify',
        f'refs/heads/{integration_branch}^{{commit}}',
        cwd=_common_repo_root(),
        check=False,
    )
    if local_ref.returncode != 0:
        raise ValueError('recoverable integration branch is not local')
    # Creation always records a second, explicit branch reset to the requested
    # base.  Keeping the named integration branch in the creation entry and
    # the requested base in a separate Git reflog entry makes recovery
    # unambiguous even when ``create --base`` names another local branch, a
    # remote ref, or a commit object.
    if len(rows) == 1:
        # Git omits a no-op forced update from the reflog. In that case the
        # creation commit is necessarily the requested base as well.
        base_commit = _resolve_commit(base_commit, 'base_commit', _common_repo_root())
    else:
        reset_commit, reset_subject = rows[-2].split('\x00', 1)
        if reset_subject.startswith('branch: Reset to '):
            base_commit = _resolve_commit(reset_commit, 'base_commit', _common_repo_root())
        else:
            # Compatibility for existing official worktrees created before the
            # explicit-reset protocol: their oldest creation entry remains the
            # only authoritative base, followed by ordinary development commits.
            base_commit = _resolve_commit(base_commit, 'base_commit', _common_repo_root())
    return integration_branch, base_commit


def _lock_metadata(task: str, path: Path, branch: str, integration_branch: str, base_commit: str) -> dict:
    return {
        'schema_version': 3,
        'task_id': task,
        'worktree': str(path),
        'branch': branch,
        'integration_branch': integration_branch,
        'base_commit': base_commit,
        **resolved_git_identity(path),
        'pid': os.getpid(),
    }


def _acquire_lock(task: str, data: dict) -> None:
    _validate_lock_data(task, data)
    _write_lock_exclusive(_safe_lock(task), data)


def _load_route(task):
    p = run_dir(task) / 'route.json'
    if not p.is_file():
        raise ValueError('route.json missing')
    data = json.loads(p.read_text(encoding='utf-8'))
    if data.get('task_id') != task:
        raise ValueError('route.json task_id mismatch')
    return data


def _load_progress(task):
    p = run_dir(task) / 'progress.json'
    if not p.is_file():
        raise ValueError('progress.json missing')
    data = json.loads(p.read_text(encoding='utf-8'))
    if data.get('task_id') != task:
        raise ValueError('progress.json task_id mismatch')
    return data


def _normalize_surface_path(raw):
    value = str(raw or '').replace('\\', '/').strip()
    p = PurePosixPath(value)
    if not value or p.is_absolute() or '..' in p.parts or '.' == value:
        raise ValueError(f'invalid task file surface path: {raw!r}')
    return p.as_posix()


def _load_task_snapshot(task):
    snapshot = run_dir(task) / 'task.json'
    if snapshot.is_file():
        data = json.loads(snapshot.read_text(encoding='utf-8'))
        if data.get('id') != task:
            raise ValueError('task snapshot id mismatch')
        return data

    # Compatibility fallback for runs activated before task snapshots existed.
    matches = []
    tasks_dir = ROOT / 'tasks'
    if tasks_dir.is_dir():
        for p in tasks_dir.glob('*.json'):
            try:
                data = json.loads(p.read_text(encoding='utf-8'))
            except Exception:
                continue
            if data.get('id') == task:
                matches.append(data)
    if len(matches) != 1:
        raise ValueError(
            f'authoritative task snapshot missing and expected exactly one tasks/*.json definition for {task}'
        )
    return matches[0]


def _authorized_surface(task):
    data = _load_task_snapshot(task)
    files = data.get('files')
    if not isinstance(files, list) or not files:
        raise ValueError('task.files must be a non-empty authorized publication surface')
    expansion_path = run_dir(task) / 'scope-expansion.json'
    if expansion_path.is_file():
        expansion = json.loads(expansion_path.read_text(encoding='utf-8'))
        if (
            expansion.get('schema_version') != 2
            or expansion.get('task_id') != task
            or expansion.get('status') != 'APPROVED'
            or not isinstance(expansion.get('expanded_files'), list)
        ):
            raise ValueError('scope-expansion.json is not an approved task-surface expansion')
        scope_expansion_digest(task)
        current = _current_candidate_subject_hash(task)
        if expansion.get('candidate_subject_hash') != current:
            raise ValueError('scope-expansion.json is bound to a different candidate')
        if expansion.get('base_commit') != _load_lock(task).get('base_commit'):
            raise ValueError('scope-expansion.json base commit does not match writer lock')
        expanded = [_normalize_surface_path(x) for x in expansion['expanded_files']]
        changed = [_normalize_surface_path(x) for x in expansion.get('changed_files', [])]
        policy = load_json('harness/policies/risk-policy.json')
        evolution = load_json('harness/evolution-policy.json')
        protected = {
            str(item).replace('\\', '/').strip().lower().rstrip('/')
            for item in [*policy.get('protected_paths', []), *evolution.get('protected_paths', [])]
        }
        for path in expanded:
            path_key = path.lower().rstrip('/')
            if any(path_key == item or path_key.startswith(item + '/') for item in protected) or any(__import__('fnmatch').fnmatch(path, pattern) or __import__('fnmatch').fnmatch(Path(path).name, pattern) for pattern in policy.get('secret_path_patterns', [])):
                raise ValueError(f'scope expansion contains protected or secret path: {path}')
        files = list(files) + expanded
        expansion['_changed_files'] = changed
        expansion['_expanded_files'] = expanded
    return sorted({_normalize_surface_path(x) for x in files})


def _current_candidate_subject_hash(task: str) -> str:
    from receipt_review import candidate_snapshot
    return candidate_snapshot(task)['subject_hash']


def _assert_authorized_surface(changed, allowed):
    changed_set = {_normalize_surface_path(x) for x in changed}
    allowed_set = {_normalize_surface_path(x) for x in allowed}
    extra = sorted(changed_set - allowed_set)
    if extra:
        raise ValueError(
            'worktree contains changes outside task.files publication surface: '
            + ', '.join(extra)
        )
    return sorted(changed_set)


def _assert_scope_expansion_covers_changes(task: str, changed: list[str]) -> None:
    expansion_path = run_dir(task) / 'scope-expansion.json'
    if not expansion_path.is_file():
        return
    expansion = json.loads(expansion_path.read_text(encoding='utf-8'))
    original = {
        _normalize_surface_path(path)
        for path in (_load_task_snapshot(task).get('files') or [])
    }
    actual_expanded = set(_normalize_surface_path(path) for path in changed) - original
    claimed = {
        _normalize_surface_path(path)
        for path in (expansion.get('changed_files') or [])
    }
    missing = sorted(actual_expanded - claimed)
    if missing:
        raise ValueError('scope-expansion.json does not include changed authorized files: ' + ', '.join(missing))


def _dirty_files(path):
    tracked = _git_lines('diff', '--no-renames', '--name-only', 'HEAD', '--', cwd=path)
    untracked = _git_lines('ls-files', '--others', '--exclude-standard', cwd=path)
    return sorted(set(tracked + untracked))


def _committed_files(path, base_commit):
    return sorted(set(_git_lines('diff', '--no-renames', '--name-only', f'{base_commit}..HEAD', '--', cwd=path)))


def _candidate_files(path, base_commit):
    return sorted(set(_committed_files(path, base_commit) + _dirty_files(path)))


def create(task, base='HEAD', execute=False):
    task = safe_task_id(task)
    ensure_git()
    path = _safe_worktree(task)
    lk = _safe_lock(task)
    if lk.exists():
        raise SystemExit(f'writer lock already exists: {lk}')
    if path.exists():
        raise SystemExit(f'worktree path already exists: {path}')

    branch = f'agent/{task}'
    integration_branch = _root_branch()
    base_commit = git('rev-parse', base).stdout.strip()
    root_head = git('rev-parse', 'HEAD').stdout.strip()
    if base == 'HEAD' and base_commit != root_head:
        raise SystemExit('HEAD changed while resolving worktree base')

    # Create the branch from the named integration branch, then explicitly
    # reset it to the requested base before registering its worktree.  Those
    # two immutable reflog entries preserve both pieces of provenance needed
    # after a failed atomic lock claim: the integration target and exact base.
    # The HEAD guard above proves this preserves the default checkout.
    branch_cmd = ['branch', '--create-reflog', branch, integration_branch]
    reset_cmd = ['branch', '--force', branch, base]
    cmd = ['worktree', 'add', str(path), branch]
    if execute:
        path.parent.mkdir(parents=True, exist_ok=True)
        git(*branch_cmd)
        git(*reset_cmd)
        git(*cmd)
        data = _lock_metadata(task, path, branch, integration_branch, base_commit)
        try:
            _acquire_lock(task, data)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    return {
        'execute': execute,
        'command': ['git', *cmd],
        'branch_command': ['git', *branch_cmd],
        'base_command': ['git', *reset_cmd],
        'worktree': str(path),
        'branch': branch,
        'integration_branch': integration_branch,
        'base_commit': base_commit,
    }


def status(task):
    task = safe_task_id(task)
    try:
        path = _safe_worktree(task)
        lk = _safe_lock(task)
    except ValueError as exc:
        return {'task_id': task, 'exists': False, 'lock': False, 'lock_valid': False, 'lock_reason': str(exc)}
    d = {'task_id': task, 'exists': path.is_dir(), 'lock': False}
    try:
        meta = _load_lock(task)
        d['lock'] = True
        d['lock_valid'] = True
        for key in ('branch', 'integration_branch', 'base_commit'):
            if meta.get(key):
                d[key] = meta[key]
    except ValueError as exc:
        d['lock_valid'] = False
        d['lock_reason'] = str(exc)
    if path.is_dir():
        r = git('status', '--porcelain', cwd=path, check=False)
        d['dirty'] = bool(r.stdout.strip())
        d['status'] = r.stdout.splitlines()
    pub = publish_status(task)
    d['published'] = bool(pub.get('published'))
    if pub.get('reason'):
        d['publish_reason'] = pub['reason']
    return d


def recover(task, execute=False):
    task = safe_task_id(task)
    ensure_git()

    path = _safe_worktree(task)
    lk = _safe_lock(task)
    if lk.exists():
        raise SystemExit(f'writer lock already exists: {lk}')
    if not path.is_dir():
        raise SystemExit(f'assigned worktree path missing: {path}')

    record = _worktree_record(path)
    if record is None:
        raise SystemExit(f'assigned worktree is not registered: {path}')

    branch = _expected_branch(task)
    if _short_branch(record.get('branch')) != branch:
        raise SystemExit(
            f'assigned worktree branch mismatch: expected {branch}, got {_short_branch(record.get("branch"))}'
        )

    current_branch = git('branch', '--show-current', cwd=path, check=False)
    if current_branch.returncode != 0 or current_branch.stdout.strip() != branch:
        raise SystemExit('assigned worktree checked-out branch mismatch')

    dirty = _dirty_files(path)
    if dirty:
        raise SystemExit(
            'assigned worktree is dirty; recovery requires a clean worktree: '
            + ', '.join(dirty)
        )

    try:
        integration_branch, base_commit = _recoverable_provenance(branch)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    ancestor = git('merge-base', '--is-ancestor', base_commit, 'HEAD', cwd=path, check=False)
    if ancestor.returncode != 0:
        raise SystemExit('recoverable base_commit is not an ancestor of the worktree HEAD')

    data = _lock_metadata(task, path, branch, integration_branch, base_commit)
    if execute:
        try:
            _acquire_lock(task, data)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc

    return {
        'execute': execute,
        'worktree': str(path),
        'branch': branch,
        'integration_branch': integration_branch,
        'base_commit': base_commit,
    }


def remove(task, execute=False, force=False):
    task = safe_task_id(task)
    d = status(task)
    if d.get('dirty') and not force:
        raise SystemExit('worktree has uncommitted changes; refuse removal without --force')
    path = _safe_worktree(task)
    cmd = ['worktree', 'remove', str(path)] + (['--force'] if force else [])
    if execute:
        if path.exists():
            git(*cmd)
        _safe_lock(task).unlink(missing_ok=True)
    return {'execute': execute, 'command': ['git', *cmd]}


def _publication_preconditions(task):
    route = _load_route(task)
    if route.get('isolation') != 'worktree':
        raise ValueError('publish is only valid for routes with isolation=worktree')

    # Publication is initiated from the primary checkout, but the binding that
    # matters belongs to the task's linked checkout.  Never inspect a global
    # direct-path legacy binding as a substitute.
    binding_root = _safe_worktree(task) if _safe_worktree(task).is_dir() else ROOT
    binding = read_provider_active('codex', binding_root)
    if binding is not None and binding.get('task_id') == task:
        raise ValueError(
            'Codex task binding is still active; clear the task-scoped model overlay before publication'
        )

    progress = _load_progress(task)
    if progress.get('current_step') != 'CLOSE' or progress.get('state') != 'RUNNING':
        raise ValueError(
            f'worktree publish requires RUNNING/CLOSE; '
            f'current_step={progress.get("current_step")}, state={progress.get("state")}'
        )

    from gate import finish_decision
    decision = finish_decision(task, route.get('risk'), require_publication=False)
    if not decision.get('allow'):
        raise ValueError(
            'worktree publish blocked by finish gate: '
            + json.dumps(decision, ensure_ascii=False, sort_keys=True)
        )
    return route, progress


def _root_has_tracked_changes():
    r = git('diff', '--quiet', 'HEAD', '--', cwd=_common_repo_root(), check=False)
    return r.returncode != 0


def _root_untracked_overlap(changed):
    untracked = set(_git_lines('ls-files', '--others', '--exclude-standard', cwd=_common_repo_root()))
    return sorted(untracked & set(changed))


def _cleanup_published_worktree(task, meta):
    path = _safe_worktree(task)
    branch = meta["branch"]
    cleanup_warning = None

    registered = _worktree_registered(path)

    if registered:
        if path.exists():
            dirty = _dirty_files(path)
            if dirty:
                raise ValueError(
                    "published worktree became dirty before cleanup: "
                    + ", ".join(dirty)
                )

        try:
            git("worktree", "remove", str(path), cwd=_common_repo_root())
        except Exception as exc:
            # On Windows Git can successfully unregister the worktree and
            # still return a non-zero exit code because physical directory
            # cleanup failed. Only tolerate the error if the Git registration
            # is actually gone.
            if _worktree_registered(path):
                raise

            cleanup_warning = (
                "git worktree remove reported failure after unregistering "
                f"the worktree: {exc}"
            )

    # An unregistered residual directory is no longer Git authority/state.
    # Remove it best-effort; failure here must not invalidate an already
    # integrated publication.
    if not _worktree_registered(path) and path.exists():
        try:
            shutil.rmtree(path)
        except OSError as exc:
            residue_warning = (
                f"residual worktree directory could not be removed: {exc}"
            )
            cleanup_warning = (
                f"{cleanup_warning}; {residue_warning}"
                if cleanup_warning
                else residue_warning
            )

    _safe_lock(task).unlink(missing_ok=True)

    branches = set(
        _git_lines("branch", "--format=%(refname:short)", cwd=_common_repo_root())
    )
    if branch in branches:
        git("branch", "-d", branch, cwd=_common_repo_root())

    return {
        "cleaned_up": (
            not _worktree_registered(path)
            and not path.exists()
        ),
        "cleanup_warning": cleanup_warning,
    }

def _finalize_integrated_artifact(task, data):
    meta = dict(data)
    cleanup = _cleanup_published_worktree(task, meta)

    meta["status"] = "PASS"
    meta["cleaned_up"] = cleanup["cleaned_up"]

    if cleanup["cleanup_warning"]:
        meta["cleanup_warning"] = cleanup["cleanup_warning"]
    else:
        meta.pop("cleanup_warning", None)

    meta["published_at"] = dt.datetime.now(dt.timezone.utc).isoformat()

    write_json_atomic(publish_artifact(task), meta)
    return meta

def publish_status(task):
    task = safe_task_id(task)
    p = publish_artifact(task)
    if not p.is_file():
        return {'task_id': task, 'published': False, 'reason': 'publish.json missing'}
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
    except Exception as exc:
        return {'task_id': task, 'published': False, 'reason': f'publish.json invalid: {exc}'}
    if data.get('task_id') != task or data.get('status') != 'PASS':
        return {'task_id': task, 'published': False, 'reason': 'publish artifact is not PASS for this task'}
    if not data.get('cleaned_up'):
        return {'task_id': task, 'published': False, 'reason': 'published worktree cleanup not complete'}
    commit = data.get('commit')
    if not commit:
        return {'task_id': task, 'published': False, 'reason': 'publish commit missing'}
    current_branch = git('branch', '--show-current', cwd=_common_repo_root(), check=False)
    if current_branch.returncode != 0 or current_branch.stdout.strip() != data.get('integration_branch'):
        return {
            'task_id': task,
            'published': False,
            'reason': 'canonical repository is not on the recorded integration branch',
        }
    ancestor = git('merge-base', '--is-ancestor', commit, 'HEAD', cwd=_common_repo_root(), check=False)
    if ancestor.returncode != 0:
        return {
            'task_id': task,
            'published': False,
            'reason': 'published commit is not integrated into canonical HEAD',
        }
    if _safe_worktree(task).exists() or _safe_lock(task).exists():
        return {
            'task_id': task,
            'published': False,
            'reason': 'published worktree or writer lock still exists',
        }
    return {
        'task_id': task,
        'published': True,
        'commit': commit,
        'integration_branch': data.get('integration_branch'),
        'changed_files': data.get('changed_files', []),
    }


def publish(task, execute=False):
    task = safe_task_id(task)
    ensure_git()

    existing = publish_artifact(task)
    if existing.is_file():
        data = json.loads(existing.read_text(encoding='utf-8'))
        if data.get('status') == 'PASS':
            checked = publish_status(task)
            if not checked.get('published'):
                raise ValueError(checked.get('reason') or 'existing publication is invalid')
            return data
        if data.get('status') == 'INTEGRATED':
            if not execute:
                return data
            commit = data.get('commit')
            if git('rev-parse', 'HEAD', cwd=ROOT).stdout.strip() != commit:
                raise ValueError('cannot resume publication: canonical HEAD no longer equals integrated commit')
            return _finalize_integrated_artifact(task, data)

    _publication_preconditions(task)
    meta = _load_lock(task)
    path = _safe_worktree(task)
    if not path.is_dir():
        raise ValueError('assigned task worktree does not exist')

    for key in ('branch', 'integration_branch', 'base_commit'):
        if not meta.get(key):
            raise ValueError(
                f'writer lock lacks {key}; recreate the task worktree with the current harness before publishing'
            )

    current_branch = git('branch', '--show-current', cwd=path).stdout.strip()
    if current_branch != meta['branch']:
        raise ValueError(
            f'assigned worktree branch mismatch: expected {meta["branch"]}, got {current_branch}'
        )

    root_branch = _root_branch()
    if root_branch != meta['integration_branch']:
        raise ValueError(
            f'canonical integration branch changed: expected {meta["integration_branch"]}, got {root_branch}'
        )

    branch_head = git('rev-parse', 'HEAD', cwd=path).stdout.strip()
    integrated = git('merge-base', '--is-ancestor', 'HEAD', branch_head, cwd=ROOT, check=False)
    if integrated.returncode != 0:
        raise ValueError(
            'canonical HEAD is not incorporated into the task branch; publication requires explicit reconciliation'
        )

    # The lock base proves creation provenance.  Publication instead compares
    # against the current integration tip already incorporated in this branch,
    # so an explicit upstream merge is not misclassified as task-owned work.
    root_head = git('rev-parse', 'HEAD', cwd=_common_repo_root()).stdout.strip()
    allowed = _authorized_surface(task)
    changed = _candidate_files(path, root_head)
    _assert_authorized_surface(changed, allowed)
    _assert_scope_expansion_covers_changes(task, changed)
    if not changed:
        raise ValueError('worktree has no task changes to publish')

    if _root_has_tracked_changes():
        raise ValueError('canonical repository has tracked local changes; commit or clean them before publication')

    overlap = _root_untracked_overlap(changed)
    if overlap:
        raise ValueError(
            'canonical repository has untracked files overlapping the task publication surface: '
            + ', '.join(overlap)
        )

    plan = {
        'schema_version': 1,
        'task_id': task,
        'status': 'READY',
        'worktree': str(path),
        'branch': meta['branch'],
        'integration_branch': meta['integration_branch'],
        'base_commit': meta['base_commit'],
        'changed_files': changed,
    }
    if not execute:
        return plan

    dirty = _dirty_files(path)
    _assert_authorized_surface(dirty, allowed)
    _assert_scope_expansion_covers_changes(task, dirty)
    if dirty:
        git('add', '-A', '--', *dirty, cwd=path)
        staged = _git_lines('diff', '--cached', '--no-renames', '--name-only', '--', cwd=path)
        _assert_authorized_surface(staged, allowed)
        if staged:
            git('commit', '-m', f'harness(task): publish {task}', cwd=path)

    candidate = git('rev-parse', 'HEAD', cwd=path).stdout.strip()
    integration_head = git('rev-parse', 'HEAD', cwd=_common_repo_root()).stdout.strip()
    committed = _committed_files(path, integration_head)
    _assert_authorized_surface(committed, allowed)
    if candidate == integration_head or not committed:
        raise ValueError('publication produced no committed task change')

    # Re-check the integration base immediately before the fast-forward.
    root_head = git('rev-parse', 'HEAD', cwd=_common_repo_root()).stdout.strip()
    if root_head == integration_head:
        git('merge', '--ff-only', candidate, cwd=_common_repo_root())
    elif root_head != candidate:
        raise ValueError('canonical HEAD moved before fast-forward integration')

    integrated = {
        'schema_version': 1,
        'task_id': task,
        'status': 'INTEGRATED',
        'worktree': str(path),
        'branch': meta['branch'],
        'integration_branch': meta['integration_branch'],
        'base_commit': meta['base_commit'],
        'commit': candidate,
        'changed_files': committed,
    }
    write_json_atomic(publish_artifact(task), integrated)
    return _finalize_integrated_artifact(task, integrated)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('create')
    p.add_argument('task')
    p.add_argument('--base', default='HEAD')
    p.add_argument('--execute', action='store_true')

    p = sub.add_parser('status')
    p.add_argument('task')

    p = sub.add_parser('remove')
    p.add_argument('task')
    p.add_argument('--execute', action='store_true')
    p.add_argument('--force', action='store_true')

    p = sub.add_parser('recover')
    p.add_argument('task')
    p.add_argument('--execute', action='store_true')

    p = sub.add_parser('adopt')
    p.add_argument('task')
    p.add_argument('--execute', action='store_true')

    p = sub.add_parser('publish')
    p.add_argument('task')
    p.add_argument('--execute', action='store_true')

    args = ap.parse_args()
    if args.cmd == 'create':
        out = create(args.task, args.base, args.execute)
    elif args.cmd == 'status':
        out = status(args.task)
    elif args.cmd == 'remove':
        out = remove(args.task, args.execute, args.force)
    elif args.cmd in {'recover', 'adopt'}:
        out = recover(args.task, args.execute)
    else:
        out = publish(args.task, args.execute)
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
