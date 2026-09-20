#!/usr/bin/env python
from __future__ import annotations

import argparse
import fnmatch
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat

from codegraph_backend import try_build
from harnesslib import ROOT, load_json, run_dir, write_json_atomic

IMPORT_PATTERNS = [
    re.compile(r'^\s*from\s+([\w.]+)\s+import', re.M),
    re.compile(r'^\s*import\s+([\w.]+)', re.M),
    re.compile(r'from\s+["\']([^"\']+)["\']'),
    re.compile(r'require\(["\']([^"\']+)["\']\)'),
]
RELATIVE_FROM_PATTERN = re.compile(
    r'^\s*from\s+(\.+)\s+import\s+(\([^)]*\)|[^\r\n;]+)', re.M,
)
EXTS = ['.py', '.js', '.ts', '.tsx', '.jsx', '.go', '.rs', '.java', '.kt', '.cs', '.rb', '.php', '.swift']


def _relative_path(value):
    return (
        isinstance(value, str) and bool(value)
        and not any(char in value for char in ('\\', ':'))
        and not any(ord(char) < 32 or 127 <= ord(char) <= 159 or 0xD800 <= ord(char) <= 0xDFFF for char in value)
        and not value.startswith('/')
        and all(part not in ('', '.', '..') for part in value.split('/'))
    )


def _integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{name} must be an integer between {minimum} and {maximum}')
    return value


def _policy():
    policy = dict(load_json('harness/context-policy.json'))
    if type(policy.get('version')) is not int or policy['version'] not in (2, 3):
        raise ValueError('version must be a supported context policy version (2 or 3)')
    policy.setdefault('graph_backend', 'auto')
    if policy['graph_backend'] not in ('auto', 'lexical', 'codegraph'):
        raise ValueError('graph_backend must be auto, lexical, or codegraph')
    policy['graph_max_source_bytes'] = _integer(
        policy.get('graph_max_source_bytes', policy.get('max_file_bytes', 120000)),
        'graph_max_source_bytes', 1, 1000000,
    )
    for key, default, minimum, maximum in (
        ('graph_max_files', 1000, 1, 10000),
        ('graph_max_total_bytes', 8000000, 1, 64000000),
        ('graph_max_edges', 50000, 0, 100000),
    ):
        policy[key] = _integer(policy.get(key, default), key, minimum, maximum)
    policy['graph_neighbor_depth'] = _integer(policy.get('graph_neighbor_depth', 2), 'graph_neighbor_depth', 0, 32)
    policy['graph_max_results'] = _integer(policy.get('graph_max_results', 20), 'graph_max_results', 0, 10000)
    for key in ('exclude_dirs', 'exclude_globs'):
        values = policy.get(key)
        if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
            raise ValueError(f'{key} must be a list of nonempty strings')
        policy[key] = values
    if any(not _relative_path(value.rstrip('/')) for value in policy['exclude_dirs']):
        raise ValueError('exclude_dirs must contain repository-relative POSIX paths')
    return policy


def _excluded(rel, policy):
    path = rel.casefold()
    parts = path.split('/')
    for directory in policy['exclude_dirs']:
        directory = directory.rstrip('/').casefold()
        if ('/' not in directory and directory in parts) or path == directory or path.startswith(directory + '/'):
            return True
    return any(
        fnmatch.fnmatchcase(path, pattern.casefold()) or fnmatch.fnmatchcase(parts[-1], pattern.casefold())
        for pattern in policy['exclude_globs']
    )


def _allowed(path, root, policy):
    rel = path.relative_to(root).as_posix()
    if not _relative_path(rel) or _excluded(rel, policy):
        return False
    try:
        return not path.is_symlink() and path.resolve().is_relative_to(root)
    except (OSError, RuntimeError):
        return False


def _sources(policy):
    """Read a deterministic prefix within file-open and raw-byte budgets."""
    root = ROOT.resolve()
    limit = policy['graph_max_source_bytes']
    remaining_bytes = policy['graph_max_total_bytes']
    opened_files = 0
    sources = {}
    for current, directories, names in os.walk(root, followlinks=False):
        if opened_files >= policy['graph_max_files'] or remaining_bytes <= 0:
            break
        parent = Path(current)
        directories[:] = sorted(name for name in directories if _allowed(parent / name, root, policy))
        for name in sorted(names):
            if opened_files >= policy['graph_max_files'] or remaining_bytes <= 0:
                return dict(sorted(sources.items()))
            path = parent / name
            if path.suffix.lower() not in EXTS or not _allowed(path, root, policy):
                continue
            try:
                metadata = path.stat(follow_symlinks=False)
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
                    continue
                if metadata.st_size > remaining_bytes:
                    # Stop at this prefix; do not open later, smaller files.
                    return dict(sorted(sources.items()))
                opened_files += 1
                with path.open('rb', buffering=0) as stream:
                    opened = os.fstat(stream.fileno())
                    if (not stat.S_ISREG(opened.st_mode)
                            or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)):
                        continue
                    allowance = min(limit + 1, remaining_bytes)
                    # Failed reads retain this reservation because partial I/O
                    # is unknowable. Successful reads refund unused allowance.
                    remaining_bytes -= allowance
                    raw = stream.read(allowance)
                    remaining_bytes += allowance - len(raw)
                    final_size = os.fstat(stream.fileno()).st_size
                if len(raw) > limit or final_size != len(raw):
                    continue
            except OSError:
                continue
            sources[path.relative_to(root).as_posix()] = raw.decode('utf-8', errors='replace')
    return dict(sorted(sources.items()))


def candidates():
    return list(_sources(_policy()))


def resolve_import(src, target, known):
    """Resolve lexical candidates only to already-admitted repository paths."""
    source = PurePosixPath(src)
    options = []
    if source.suffix == '.py':
        if target.startswith('.'):
            level = len(target) - len(target.lstrip('.'))
            base = source.parent
            for _ in range(level - 1):
                if base == PurePosixPath('.'):
                    return None
                base = base.parent
            if base == PurePosixPath('.'):
                return None  # Relative imports cannot leave the top-level package.
            options.append((base / target[level:].replace('.', '/')).as_posix())
        else:
            module = target.replace('.', '/')
            options.extend([module, (source.parent / module).as_posix()])
    elif target.startswith('.'):
        options.append(posixpath.normpath(posixpath.join(source.parent.as_posix(), target)))
    else:
        options.extend([target, target.replace('.', '/'), (source.parent / target).as_posix()])
    for option in options:
        for candidate in [option, *(option + ext for ext in EXTS), option + '/__init__.py',
                          *(option + '/index' + ext for ext in ('.js', '.ts', '.tsx', '.jsx'))]:
            if candidate in known:
                return candidate
    return None


def _lexical_edges(sources, limit):
    if not limit:
        return []
    known = set(sources)
    edges = set()
    for path, text in sources.items():
        for pattern in IMPORT_PATTERNS:
            for match in pattern.finditer(text):
                target = resolve_import(path, match.group(1), known)
                if target and target != path:
                    edges.add((path, target, 'import'))
                    if len(edges) >= limit:
                        return sorted(edges)
        if PurePosixPath(path).suffix == '.py':
            # Dots alone name the package; imported names can name its modules.
            for match in RELATIVE_FROM_PATTERN.finditer(text):
                names = re.sub(r'#[^\r\n]*', '', match.group(2)).strip().strip('()')
                for name in names.split(','):
                    imported = re.fullmatch(r'\s*(\w+)(?:\s+as\s+\w+)?\s*', name)
                    if imported:
                        target = resolve_import(path, match.group(1) + imported.group(1), known)
                        if target and target != path:
                            edges.add((path, target, 'import'))
                            if len(edges) >= limit:
                                return sorted(edges)
    # Affinity is a naming heuristic, represented explicitly rather than as an import.
    stems = {}
    for path in sources:
        stems.setdefault(PurePosixPath(path).stem, []).append(path)
    for path in sources:
        value = PurePosixPath(path)
        if ('test' in value.parts or 'tests' in value.parts or value.stem.startswith('test_') or value.stem.endswith('_test')):
            stem = value.stem.removeprefix('test_').removesuffix('_test')
            for other in stems.get(stem, []):
                if other != path:
                    pair = {(path, other, 'test_affinity'), (other, path, 'test_affinity')}
                    if len(edges) + len(pair - edges) > limit:
                        return sorted(edges)
                    edges.update(pair)
                    if len(edges) >= limit:
                        return sorted(edges)
    return sorted(edges)


def build_graph_document(*, backend=None):
    """Return the stable, versioned graph artifact; paths never contain the root."""
    policy = _policy()
    sources = _sources(policy)
    requested = policy['graph_backend']

    def bounded_backend(admitted):
        raw = backend(admitted)
        if (type(raw) is dict and type(raw.get('edges')) is list
                and len(raw['edges']) > policy['graph_max_edges']):
            return None  # The adapter treats this as invalid_output before normalization.
        return raw

    edges, fallback = (None, None) if requested == 'lexical' else try_build(
        sources, bounded_backend if backend is not None else None,
    )
    selected = 'codegraph' if edges is not None else 'lexical'
    if edges is None:
        edges = _lexical_edges(sources, policy['graph_max_edges'])
    return {
        'schema_version': 1,
        'repository': {'root': '.', 'path_format': 'relative-posix'},
        'backend': {'requested': requested, 'selected': selected, 'fallback_reason': fallback},
        'nodes': [{'path': path} for path in sources],
        'edges': [{'source': src, 'target': target, 'kind': kind} for src, target, kind in edges],
        'counts': {'nodes': len(sources), 'edges': len(edges)},
        'limits': {'max_source_bytes': policy['graph_max_source_bytes'],
                   'max_files': policy['graph_max_files'], 'max_total_bytes': policy['graph_max_total_bytes'],
                   'max_edges': policy['graph_max_edges'],
                   'max_depth': policy['graph_neighbor_depth'], 'max_results': policy['graph_max_results']},
    }


def build_graph():
    """Keep the sorted adjacency contract used by context and impact consumers."""
    edges = {}
    for edge in build_graph_document()['edges']:
        edges.setdefault(edge['source'], set()).add(edge['target'])
    return {path: sorted(edges[path]) for path in sorted(edges)}


def neighborhood(graph, seeds, depth=None):
    """Return a policy-bounded breadth-first neighborhood, excluding seed paths."""
    policy = _policy()
    depth = policy['graph_neighbor_depth'] if depth is None else _integer(depth, 'depth', 0, 32)
    depth = min(depth, policy['graph_neighbor_depth'])
    limit = policy['graph_max_results']

    def allowed(path):
        return _relative_path(path) and not _excluded(path, policy)

    seed_set = {path for path in seeds if allowed(path)}
    if not seed_set or not depth or not limit:
        return []
    seen = set(seed_set)
    frontier = set(seed_set)
    forward = {}
    reverse = {}
    for source, targets in graph.items():
        if not allowed(source):
            continue
        for target in targets:
            if not allowed(target):
                continue
            forward.setdefault(source, set()).add(target)
            reverse.setdefault(target, set()).add(source)
    for _ in range(depth):
        following = set()
        for path in frontier:
            following.update(forward.get(path, []))
            following.update(reverse.get(path, []))
        following -= seen
        remaining = limit - len(seen - seed_set)
        frontier = set(sorted(following)[:remaining])
        seen |= frontier
        if not frontier or len(seen - seed_set) >= limit:
            break
    return sorted(seen - seed_set)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--task-id')
    parser.add_argument('--output')
    args = parser.parse_args()
    payload = build_graph_document()
    destination = Path(args.output) if args.output else (
        run_dir(args.task_id) / 'context-graph.json' if args.task_id else ROOT / '.harness/context-graph.json'
    )
    if not destination.is_absolute():
        destination = ROOT / destination
    write_json_atomic(destination, payload)
    print(json.dumps({'output': str(destination), **payload['counts']}, indent=2))


if __name__ == '__main__':
    main()
