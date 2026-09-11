#!/usr/bin/env python
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path, PurePosixPath

from harnesslib import ROOT, git, run_dir, safe_task_id, write_json_atomic


def lock(task):
    return ROOT / '.harness' / 'locks' / f'{task}.json'


def wt(task):
    return ROOT / '.worktrees' / task


def publish_artifact(task):
    return run_dir(task) / 'publish.json'


def ensure_git():
    r = git('rev-parse', '--is-inside-work-tree', check=False)
    if r.returncode != 0:
        raise SystemExit('not inside a git repository; initialize git before creating worktrees')


def _git_lines(*args, cwd=None):
    r = git(*args, cwd=cwd)
    return [line.strip().replace('\\', '/') for line in r.stdout.splitlines() if line.strip()]


def _root_branch():
    branch = git('branch', '--show-current').stdout.strip()
    if not branch:
        raise ValueError('canonical repository is detached; worktree publication requires a named integration branch')
    return branch


def _load_lock(task):
    p = lock(task)
    if not p.is_file():
        raise ValueError(f'writer lock missing: {p}')
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
    except Exception as exc:
        raise ValueError(f'writer lock is invalid: {exc}') from exc
    if data.get('task_id') != task:
        raise ValueError('writer lock task_id mismatch')
    return data


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
    return sorted({_normalize_surface_path(x) for x in files})


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
    path = wt(task)
    lk = lock(task)
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

    cmd = ['worktree', 'add', '-b', branch, str(path), base]
    if execute:
        path.parent.mkdir(parents=True, exist_ok=True)
        git(*cmd)
        write_json_atomic(
            lk,
            {
                'schema_version': 2,
                'task_id': task,
                'worktree': str(path),
                'branch': branch,
                'integration_branch': integration_branch,
                'base_commit': base_commit,
                'pid': os.getpid(),
            },
        )
    return {
        'execute': execute,
        'command': ['git', *cmd],
        'worktree': str(path),
        'branch': branch,
        'integration_branch': integration_branch,
        'base_commit': base_commit,
    }


def status(task):
    task = safe_task_id(task)
    path = wt(task)
    lk = lock(task)
    d = {'task_id': task, 'exists': path.exists(), 'lock': lk.exists()}
    if lk.exists():
        try:
            meta = json.loads(lk.read_text(encoding='utf-8'))
            for key in ('branch', 'integration_branch', 'base_commit'):
                if meta.get(key):
                    d[key] = meta[key]
        except Exception:
            d['lock_valid'] = False
    if path.exists():
        r = git('status', '--porcelain', cwd=path, check=False)
        d['dirty'] = bool(r.stdout.strip())
        d['status'] = r.stdout.splitlines()
    pub = publish_status(task)
    d['published'] = bool(pub.get('published'))
    if pub.get('reason'):
        d['publish_reason'] = pub['reason']
    return d


def remove(task, execute=False, force=False):
    task = safe_task_id(task)
    d = status(task)
    if d.get('dirty') and not force:
        raise SystemExit('worktree has uncommitted changes; refuse removal without --force')
    cmd = ['worktree', 'remove', str(wt(task))] + (['--force'] if force else [])
    if execute:
        if wt(task).exists():
            git(*cmd)
        lock(task).unlink(missing_ok=True)
    return {'execute': execute, 'command': ['git', *cmd]}


def _publication_preconditions(task):
    route = _load_route(task)
    if route.get('isolation') != 'worktree':
        raise ValueError('publish is only valid for routes with isolation=worktree')

    codex_active = ROOT / '.harness' / 'codex' / 'active-task.json'
    if codex_active.is_file():
        try:
            binding = json.loads(codex_active.read_text(encoding='utf-8'))
        except Exception as exc:
            raise ValueError(f'Codex active task binding is invalid: {exc}') from exc
        if binding.get('task_id') == task:
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
    r = git('diff', '--quiet', 'HEAD', '--', cwd=ROOT, check=False)
    return r.returncode != 0


def _root_untracked_overlap(changed):
    untracked = set(_git_lines('ls-files', '--others', '--exclude-standard', cwd=ROOT))
    return sorted(untracked & set(changed))


def _cleanup_published_worktree(task, meta):
    path = wt(task)
    branch = meta['branch']

    if path.exists():
        dirty = _dirty_files(path)
        if dirty:
            raise ValueError(
                'published worktree became dirty before cleanup: ' + ', '.join(dirty)
            )
        git('worktree', 'remove', str(path))

    lock(task).unlink(missing_ok=True)

    branches = set(_git_lines('branch', '--format=%(refname:short)', cwd=ROOT))
    if branch in branches:
        git('branch', '-d', branch, cwd=ROOT)


def _finalize_integrated_artifact(task, data):
    meta = dict(data)
    _cleanup_published_worktree(task, meta)
    meta['status'] = 'PASS'
    meta['cleaned_up'] = True
    meta['published_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
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
    current_branch = git('branch', '--show-current', cwd=ROOT, check=False)
    if current_branch.returncode != 0 or current_branch.stdout.strip() != data.get('integration_branch'):
        return {
            'task_id': task,
            'published': False,
            'reason': 'canonical repository is not on the recorded integration branch',
        }
    ancestor = git('merge-base', '--is-ancestor', commit, 'HEAD', cwd=ROOT, check=False)
    if ancestor.returncode != 0:
        return {
            'task_id': task,
            'published': False,
            'reason': 'published commit is not integrated into canonical HEAD',
        }
    if wt(task).exists() or lock(task).exists():
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
    path = wt(task)
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

    allowed = _authorized_surface(task)
    changed = _candidate_files(path, meta['base_commit'])
    _assert_authorized_surface(changed, allowed)
    if not changed:
        raise ValueError('worktree has no task changes to publish')

    root_branch = _root_branch()
    if root_branch != meta['integration_branch']:
        raise ValueError(
            f'canonical integration branch changed: expected {meta["integration_branch"]}, got {root_branch}'
        )

    root_head = git('rev-parse', 'HEAD', cwd=ROOT).stdout.strip()
    branch_head = git('rev-parse', 'HEAD', cwd=path).stdout.strip()
    if root_head not in {meta['base_commit'], branch_head}:
        raise ValueError(
            'canonical HEAD moved since worktree creation; publication requires explicit reconciliation'
        )

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
    if dirty:
        git('add', '-A', '--', *dirty, cwd=path)
        staged = _git_lines('diff', '--cached', '--no-renames', '--name-only', '--', cwd=path)
        _assert_authorized_surface(staged, allowed)
        if staged:
            git('commit', '-m', f'harness(task): publish {task}', cwd=path)

    candidate = git('rev-parse', 'HEAD', cwd=path).stdout.strip()
    committed = _committed_files(path, meta['base_commit'])
    _assert_authorized_surface(committed, allowed)
    if candidate == meta['base_commit'] or not committed:
        raise ValueError('publication produced no committed task change')

    # Re-check the integration base immediately before the fast-forward.
    root_head = git('rev-parse', 'HEAD', cwd=ROOT).stdout.strip()
    if root_head == meta['base_commit']:
        git('merge', '--ff-only', candidate, cwd=ROOT)
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
    else:
        out = publish(args.task, args.execute)
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
