from __future__ import annotations
from pathlib import Path, PurePosixPath
import fnmatch, hashlib, json, os, re, subprocess, sys, tempfile
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parents[1]

def load_json(path):
    return json.loads((ROOT / path).read_text(encoding='utf-8'))

def load_manifest():
    # JSON is valid YAML 1.2; keeping the manifest JSON-compatible avoids runtime deps.
    return load_json('harness/manifest.yaml')

def safe_task_id(value: str) -> str:
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{1,63}', value or ''):
        raise ValueError('task id must match [A-Za-z0-9][A-Za-z0-9._-]{1,63}')
    return value

def runtime_root() -> Path:
    """Return the checkout-independent root for durable harness artifacts."""
    fixture = os.environ.get('HARNESS_FIXTURE_RUNTIME_ROOT')
    if os.environ.get('HARNESS_FIXTURE_SEAM') == '1' and fixture:
        candidate = Path(fixture)
        if not candidate.is_absolute():
            raise ValueError('fixture runtime root must be absolute')
        candidate = candidate.resolve()
        if candidate != ROOT.resolve() or not candidate.is_dir():
            raise ValueError('fixture runtime root is invalid')
        return candidate
    try:
        result = git('rev-parse', '--git-common-dir', cwd=ROOT, check=False)
    except OSError as exc:
        raise ValueError('common git directory unavailable') from exc
    if result.returncode != 0:
        raise ValueError('common git directory unavailable')
    raw = result.stdout.strip()
    if not raw:
        raise ValueError('git common directory is empty')
    common = Path(raw)
    if not common.is_absolute():
        common = ROOT / common
    common = common.resolve()
    if not common.is_dir() or not (common / 'HEAD').is_file() or not (common / 'objects').is_dir():
        raise ValueError('git common directory is invalid')
    return common.parent

def run_dir(task_id: str) -> Path:
    root = runtime_root()
    return _contained(root / '.harness' / 'runs' / safe_task_id(task_id), root)


_PROVIDER = re.compile(r'[a-z][a-z0-9-]{0,31}')
_OVERLAY_SCHEMA = 1


def _contained(path: Path, base: Path) -> Path:
    """Resolve ``path`` and reject values escaping its declared base."""
    _reject_reparse_components(path)
    resolved = Path(str(path.resolve()).removeprefix('\\\\?\\'))
    base_resolved = Path(str(base.resolve()).removeprefix('\\\\?\\'))
    try:
        resolved.relative_to(base_resolved)
    except ValueError as exc:
        raise ValueError('runtime path escapes its declared base') from exc
    return resolved


def secure_path(path: Path, base: Path) -> Path:
    """Return a contained path only when no component is a reparse point."""
    absolute = Path(os.path.abspath(str(path)))
    _reject_reparse_components(absolute)
    resolved = absolute.resolve()
    if os.path.normcase(str(resolved)) != os.path.normcase(str(absolute)):
        raise ValueError('path resolves through a symlink or reparse point')
    try:
        resolved.relative_to(base.resolve())
    except ValueError as exc:
        raise ValueError('path escapes its declared base') from exc
    return resolved


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    if os.name != 'nt':
        return False
    try:
        attributes = getattr(path.lstat(), 'st_file_attributes', 0)
    except OSError:
        return False
    return bool(attributes & getattr(__import__('stat'), 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))


def _reject_reparse_components(path: Path) -> None:
    """Reject symlink/junction components before resolving a write target."""
    current = Path(path)
    while True:
        if (current.exists() or current.is_symlink()) and _is_reparse_point(current):
            raise ValueError(f'path contains a symlink or reparse point: {current}')
        parent = current.parent
        if parent == current:
            return
        current = parent


def _worktree_metadata(root: Path | None = None) -> tuple[Path, Path]:
    """Return validated checkout and common Git directory metadata.

    Overlay identity is deliberately based on Git's own worktree boundary, never
    on cwd or a caller supplied session id.  This also keeps a moved checkout
    stable as long as Git continues to identify the same checkout path.
    """
    candidate = (root or ROOT).resolve()
    try:
        top_result = git('rev-parse', '--show-toplevel', cwd=candidate, check=False)
        common_result = git('rev-parse', '--git-common-dir', cwd=candidate, check=False)
    except OSError as exc:
        raise ValueError('worktree Git metadata unavailable') from exc
    if top_result.returncode or common_result.returncode:
        raise ValueError('worktree Git metadata unavailable')
    top_raw, common_raw = top_result.stdout.strip(), common_result.stdout.strip()
    if not top_raw or not common_raw:
        raise ValueError('worktree Git metadata is empty')
    top = Path(top_raw).resolve()
    common = Path(common_raw)
    if not common.is_absolute():
        common = candidate / common
    common = common.resolve()
    if top != candidate or not top.is_dir():
        raise ValueError('worktree root does not match Git checkout')
    if not common.is_dir() or not (common / 'HEAD').is_file() or not (common / 'objects').is_dir():
        raise ValueError('worktree Git common directory is invalid')
    return top, common


def resolved_git_identity(root: Path | None = None) -> dict:
    """Resolve Git's authoritative checkout/common-dir identity for a path."""
    top, common = _worktree_metadata(root)
    candidate = (root or ROOT).resolve()
    git_dir_result = git('rev-parse', '--git-dir', cwd=candidate, check=False)
    head_result = git('rev-parse', 'HEAD', cwd=candidate, check=False)
    if git_dir_result.returncode or head_result.returncode:
        raise ValueError('worktree Git identity unavailable')
    git_dir = Path(git_dir_result.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = candidate / git_dir
    git_dir = git_dir.resolve()
    if not git_dir.is_dir():
        raise ValueError('worktree Git directory is invalid')
    return {
        'worktree_root': str(top),
        'git_common_dir': str(common),
        'git_dir': str(git_dir),
        'head': head_result.stdout.strip(),
        'worktree_id': worktree_identity(top)['worktree_id'],
    }


def worktree_identity(root: Path | None = None) -> dict:
    """Return a stable, opaque, validated worktree identity marker."""
    top, common = _worktree_metadata(root)
    # normcase is a no-op on POSIX and gives Windows case-insensitive paths one
    # canonical identity without accepting a path supplied by an active binding.
    material = '\0'.join((os.path.normcase(str(top)), os.path.normcase(str(common))))
    return {
        'schema_version': _OVERLAY_SCHEMA,
        'worktree_id': hashlib.sha256(material.encode('utf-8')).hexdigest(),
    }


def provider_overlay_dir(provider: str, root: Path | None = None) -> Path:
    """Return the current checkout's provider-owned overlay directory."""
    if not isinstance(provider, str) or not _PROVIDER.fullmatch(provider):
        raise ValueError('provider name is invalid')
    top, _ = _worktree_metadata(root)
    owner = worktree_identity(top)
    return _contained(top / '.harness' / 'overlays' / owner['worktree_id'] / provider, top)


def provider_active_path(provider: str, root: Path | None = None) -> Path:
    return provider_overlay_dir(provider, root) / 'active-task.json'


def provider_session_path(provider: str, root: Path | None = None) -> Path:
    return provider_overlay_dir(provider, root) / 'session.json'


def provider_audit_path(provider: str, root: Path | None = None) -> Path:
    return provider_overlay_dir(provider, root) / 'permission-audit.jsonl'


def provider_catalog_path(provider: str, root: Path | None = None) -> Path:
    return provider_overlay_dir(provider, root) / 'catalog-snapshot.json'


def provider_inventory_path(provider: str, root: Path | None = None) -> Path:
    return provider_overlay_dir(provider, root) / 'model-inventory.json'


def provider_enriched_inventory_path(provider: str, root: Path | None = None) -> Path:
    """Return the provider-local scored inventory used for activation."""
    return provider_overlay_dir(provider, root) / 'enriched-inventory.json'


def provider_model_selections_path(provider: str, root: Path | None = None) -> Path:
    return provider_overlay_dir(provider, root) / 'model-selections.json'


def provider_inventory_binding_path(provider: str, root: Path | None = None) -> Path:
    """Return the only inventory path a provider selection may bind to."""
    if provider == 'subscriptions':
        return provider_inventory_path(provider, root)
    if provider in {'codex', 'opencode'}:
        return provider_enriched_inventory_path(provider, root)
    raise ValueError('unsupported provider name')


def _legacy_provider_paths(provider: str, root: Path | None = None) -> list[Path]:
    """Direct provider paths are legacy and must never become a fallback."""
    if not isinstance(provider, str) or not _PROVIDER.fullmatch(provider):
        raise ValueError('provider name is invalid')
    top, _ = _worktree_metadata(root)
    roots = [top, runtime_root()]
    unique_roots = []
    for candidate in roots:
        candidate = candidate.resolve()
        if candidate not in unique_roots:
            unique_roots.append(candidate)
    return [base / '.harness' / provider / name for base in unique_roots for name in (
        'active-task.json', 'session.json', 'permission-audit.jsonl',
        'catalog-snapshot.json', 'model-inventory.json',
        'enriched-inventory.json', 'model-selections.json',
    )]


def reject_legacy_provider_state(provider: str, root: Path | None = None) -> None:
    legacy = [path for path in _legacy_provider_paths(provider, root) if path.exists()]
    if legacy:
        names = ', '.join(path.name for path in legacy)
        raise ValueError(
            f'legacy unscoped {provider} state detected ({names}); '
            'remove or migrate it with an operator-approved recovery before continuing'
        )


def validate_provider_active(provider: str, active: dict, root: Path | None = None) -> dict:
    """Validate a local binding before a consumer can trust or mutate it."""
    if not isinstance(active, dict):
        raise ValueError('provider active binding must be an object')
    expected = worktree_identity(root)
    marker = active.get('overlay')
    if active.get('schema_version') != 3:
        raise ValueError('provider active binding schema mismatch')
    if active.get('provider') != provider:
        raise ValueError('provider active binding provider mismatch')
    if not isinstance(marker, dict) or marker.get('schema_version') != _OVERLAY_SCHEMA:
        raise ValueError('provider active binding has no valid overlay marker')
    if marker.get('worktree_id') != expected['worktree_id']:
        raise ValueError('provider active binding belongs to another worktree')
    task_id = safe_task_id(active.get('task_id', ''))
    top, _ = _worktree_metadata(root)
    task_path = active.get('task_path')
    expected_task_path = (top / 'tasks' / f'{task_id}.json').resolve()
    if not isinstance(task_path, str) or Path(task_path).is_absolute() or '..' in Path(task_path).parts:
        raise ValueError('provider active binding task_path is invalid')
    if _contained(top / task_path, top) != expected_task_path:
        raise ValueError('provider active binding task_path does not match task_id')
    expected_shared = {
        'route_path': 'route.json',
        'context_path': 'context.json',
        'progress_path': 'progress.json',
        'task_snapshot_path': 'task.json',
        'impact_path': 'impact.json',
        'agent_budget_path': 'agent-budget.json',
    }
    for key in (*expected_shared, 'model_selections_path'):
        value = active.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f'provider active binding {key} is invalid')
        if key == 'model_selections_path':
            candidate = _contained(top / value, top)
            if candidate != provider_model_selections_path(provider, root):
                raise ValueError('provider model selections path does not belong to its overlay')
        else:
            expected = run_dir(task_id) / expected_shared[key]
            if runtime_reference(value) != expected:
                raise ValueError(f'provider active binding {key} does not match task_id')
    snapshot = json.loads(runtime_reference(active['task_snapshot_path']).read_text(encoding='utf-8'))
    if snapshot.get('id') != task_id:
        raise ValueError('provider active binding task snapshot does not match task_id')
    selections_path = provider_model_selections_path(provider, root)
    selection_ref = _contained(top / active['model_selections_path'], top)
    if selection_ref != selections_path:
        raise ValueError('provider model selections path does not belong to its overlay')
    if not selections_path.is_file():
        raise ValueError('provider model selections file is missing')
    selection_payload = validate_model_selections(provider, task_id, selections_path, root)
    selection_hash = sha256_file(selections_path)
    if active.get('model_selections_sha256') != selection_hash:
        raise ValueError('provider active binding model selections integrity mismatch')
    if active.get('selections') != selection_payload.get('selections'):
        raise ValueError('provider active binding selections mismatch')
    return active


def validate_model_selections(provider: str, task_id: str, path: Path | None = None,
                              root: Path | None = None) -> dict:
    """Validate task/provider/schema/path/inventory binding for a selection file."""
    task_id = safe_task_id(task_id)
    top, _ = _worktree_metadata(root)
    expected = provider_model_selections_path(provider, root)
    path = expected if path is None else Path(path)
    if _contained(path, top) != expected:
        raise ValueError('provider model selections path is not local to this worktree')
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError('provider model selections file is invalid') from exc
    if payload.get('schema_version') != 2:
        raise ValueError('provider model selections schema mismatch')
    if payload.get('task_id') != task_id:
        raise ValueError('provider model selections task mismatch')
    if payload.get('provider') != provider:
        raise ValueError('provider model selections provider mismatch')
    if not isinstance(payload.get('selections'), list):
        raise ValueError('provider model selections are not a list')
    inventory_ref = payload.get('inventory_path')
    inventory_hash = payload.get('inventory_sha256')
    expected_inventory = provider_inventory_binding_path(provider, root)
    if provider == 'subscriptions' and not inventory_ref:
        raise ValueError('subscription model selections require a local inventory')
    if inventory_ref is None:
        if inventory_hash is not None:
            raise ValueError('model selection inventory hash has no inventory path')
    else:
        if not isinstance(inventory_ref, str) or Path(inventory_ref).is_absolute() or '..' in Path(inventory_ref).parts:
            raise ValueError('model selection inventory path is invalid')
        inventory = _contained(top / inventory_ref, top)
        if inventory != expected_inventory:
            raise ValueError('model selection inventory path is not provider-local')
        if not inventory.is_file():
            raise ValueError('model selection inventory is missing')
        if not isinstance(inventory_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', inventory_hash):
            raise ValueError('model selection inventory hash is invalid')
        if sha256_file(inventory) != inventory_hash:
            raise ValueError('model selection inventory integrity mismatch')
        try:
            inventory_data = json.loads(inventory.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError('provider inventory is invalid') from exc
        expected_schema = 1 if provider == 'subscriptions' else 3
        if inventory_data.get('schema_version') != expected_schema:
            raise ValueError('provider inventory schema mismatch')
        if inventory_data.get('provider') != provider:
            raise ValueError('provider inventory provider mismatch')
        if not isinstance(inventory_data.get('models'), list):
            raise ValueError('provider inventory models are not a list')
    return payload


def scope_expansion_path(task: str) -> Path:
    return run_dir(safe_task_id(task)) / 'scope-expansion.json'


def scope_expansion_digest(task: str) -> str | None:
    """Validate and hash the immutable approved scope expansion, if present."""
    path = scope_expansion_path(task)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError('scope-expansion.json is invalid') from exc
    if data.get('schema_version') != 2 or data.get('task_id') != safe_task_id(task) or data.get('status') != 'APPROVED':
        raise ValueError('scope-expansion.json is not an approved immutable expansion')
    expanded = data.get('expanded_files')
    if not isinstance(expanded, list) or not expanded:
        raise ValueError('scope-expansion.json has no expanded files')
    if len(expanded) != len(set(expanded)):
        raise ValueError('scope-expansion.json contains duplicate files')
    policy = load_json('harness/policies/risk-policy.json')
    evolution = load_json('harness/evolution-policy.json')
    protected = {
        str(item).replace('\\', '/').strip().lower().rstrip('/')
        for item in [*policy.get('protected_paths', []), *evolution.get('protected_paths', [])]
    }
    secret_patterns = policy.get('secret_path_patterns', [])
    for raw in expanded:
        normalized = str(raw).replace('\\', '/').strip()
        if not normalized or PurePosixPath(normalized).is_absolute() or '..' in PurePosixPath(normalized).parts:
            raise ValueError('scope expansion contains an invalid path')
        normalized_key = normalized.lower().rstrip('/')
        if any(normalized_key == item or normalized_key.startswith(item + '/') for item in protected) or any(fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(Path(normalized).name, pattern) for pattern in secret_patterns):
            raise ValueError(f'scope expansion contains protected or secret path: {normalized}')
    claimed = data.get('document_sha256')
    body = dict(data)
    body.pop('document_sha256', None)
    expected = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
    if claimed != expected:
        raise ValueError('scope-expansion.json integrity mismatch')
    return sha256_file(path)


def read_provider_active(provider: str, root: Path | None = None) -> dict | None:
    """Read only this checkout's validated binding; legacy files are rejected."""
    reject_legacy_provider_state(provider, root)
    path = provider_active_path(provider, root)
    if not path.is_file():
        return None
    try:
        active = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f'{provider} active binding is invalid') from exc
    return validate_provider_active(provider, active, root)


def assert_overlay_writable(provider: str, root: Path | None = None) -> Path:
    """Reject legacy or foreign state before lifecycle code writes its overlay."""
    reject_legacy_provider_state(provider, root)
    path = provider_active_path(provider, root)
    if path.is_file():
        read_provider_active(provider, root)
    return path


def runtime_reference(path: str) -> Path:
    """Resolve a durable artifact reference stored relative to Git-common runtime."""
    if not isinstance(path, str) or not path or Path(path).is_absolute():
        raise ValueError('runtime artifact reference is invalid')
    # Reject Windows absolute and drive-relative forms even when running POSIX.
    if re.match(r'^[A-Za-z]:', path) or path.startswith(('\\\\', '//')):
        raise ValueError('runtime artifact reference is invalid')
    return _contained(runtime_root() / path, runtime_root())

def sha256_file(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()

def write_json_atomic(path: Path, data):
    _reject_reparse_components(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name+'.', dir=str(path.parent))
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(data,f,indent=2,ensure_ascii=False); f.write('\n')
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def write_json_immutable(path: Path, data):
    """Create a durable shared artifact or reject a changed existing value."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with _artifact_lock(path):
        _reject_reparse_components(path)
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f'immutable artifact is unreadable: {path}') from exc
            if existing != data:
                raise ValueError(f'immutable artifact differs; refusing overwrite: {path}')
            return path
        write_json_exclusive(path, data)
        return path


@contextmanager
def _artifact_lock(path: Path):
    """Serialize immutable shared-artifact creation across linked worktrees."""
    lock_path = path.parent / '.artifact-write.lock'
    with lock_path.open('a+b') as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b'\0')
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    import time
                    time.sleep(0.05)
            unlock = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            unlock = lambda: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        try:
            yield
        finally:
            unlock()


def write_json_exclusive(path: Path, data):
    """Create one JSON artifact without replacing an existing file."""
    _reject_reparse_components(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, 'O_BINARY'):
        flags |= os.O_BINARY
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

def git(*args, cwd=None, check=True):
    return subprocess.run(
        ['git', *args], cwd=cwd or ROOT, text=True, encoding='utf-8',
        errors='replace', capture_output=True, check=check,
    )
