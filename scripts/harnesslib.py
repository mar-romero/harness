from __future__ import annotations
from pathlib import Path
import hashlib, json, os, re, subprocess, sys, tempfile

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
    return runtime_root() / '.harness' / 'runs' / safe_task_id(task_id)

def sha256_file(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()

def write_json_atomic(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name+'.', dir=str(path.parent))
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(data,f,indent=2,ensure_ascii=False); f.write('\n')
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def git(*args, cwd=None, check=True):
    return subprocess.run(['git',*args], cwd=cwd or ROOT, text=True, capture_output=True, check=check)
