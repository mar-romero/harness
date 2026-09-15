#!/usr/bin/env python
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from harnesslib import ROOT, load_json, safe_task_id, write_json_atomic, run_dir
from context_graph import build_graph_document, neighborhood, _excluded, _integer, _relative_path
from memory import search as search_memory
from symbol_index import index_file
from snippet_extractor import extract_snippet

def excluded(rel,policy):
    return _excluded(rel.as_posix(), policy)
def tokens(text): return {t for t in re.findall(r'[a-zA-Z0-9_\-]{3,}',text.lower()) if t not in {'this','that','with','from','into','para','como','esta','este'}}
def estimate_tokens(size): return max(1, (size + 3) // 4)


TEXT_EXTENSIONS = frozenset('py js ts tsx jsx go rs java kt cs rb php swift md json yaml yml toml sh sql graphql proto html css scss jsonl'.split())


def context_policy():
    """Validate compiler limits before either graph or candidate content is read.

    Version 3 accepts these additive ranking options; defaults preserve older
    version 2/3 policies. Required status, never score, grants budget exceptions.
    """
    policy = dict(load_json('harness/context-policy.json'))
    if type(policy.get('version')) is not int or policy['version'] not in (2, 3):
        raise ValueError('unsupported context policy version')
    for key, default, minimum, maximum in (
        ('max_files', 40, 0, 10000), ('max_file_bytes', 120000, 1, 1000000),
        ('max_total_bytes', 600000, 0, 64000000),
        ('max_total_tokens_estimate', 120000, 0, 1000000000),
        ('graph_neighbor_bonus', 350, 0, 500), ('path_token_bonus', 20, 0, 20),
        ('max_path_token_matches', 5, 0, 5), ('memory_bonus', 60, 0, 100),
        ('related_test_bonus', 40, 0, 100), ('max_memory_items', 5, 0, 100),
        ('max_memory_text_chars', 4096, 0, 64000),
    ):
        policy[key] = _integer(policy.get(key, default), key, minimum, maximum)
    for key in ('always_include', 'exclude_dirs', 'exclude_globs', 'text_extensions'):
        values = policy.get(key)
        if not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values):
            raise ValueError(f'{key} must be a list of nonempty strings')
    for key in ('always_include', 'exclude_dirs'):
        if any(not _relative_path(v.rstrip('/') if key == 'exclude_dirs' else v) for v in policy[key]):
            raise ValueError(f'{key} must contain repository-relative POSIX paths')
    if any(not _relative_path(v) for v in policy['exclude_globs']):
        raise ValueError('exclude_globs must contain safe relative patterns')
    if any(v not in {'.' + ext for ext in TEXT_EXTENSIONS} for v in policy['text_extensions']):
        raise ValueError('unsupported text extension')
    return policy


def admitted(path, root, policy):
    """Reject links (including Windows reparse points) on every path component."""
    try:
        rel = path.relative_to(root)
        if not _relative_path(rel.as_posix()) or excluded(rel, policy):
            return False
        for component in (path, *path.parents):
            if component == root:
                break
            metadata = component.lstat()
            if (stat.S_ISLNK(metadata.st_mode) or
                    getattr(metadata, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                return False
        return path.resolve().is_relative_to(root)
    except (OSError, ValueError, RuntimeError):
        return False


def candidate_files(root, policy, explicit):
    files = {}
    for current, directories, names in os.walk(root, followlinks=False):
        parent = Path(current)
        directories[:] = sorted(name for name in directories if admitted(parent / name, root, policy))
        for name in sorted(names):
            path = parent / name
            rel = path.relative_to(root).as_posix()
            if not admitted(path, root, policy):
                continue
            if (path.suffix.lower() not in policy['text_extensions'] and
                    rel not in policy['always_include'] and rel not in explicit):
                continue
            try:
                metadata = path.stat(follow_symlinks=False)
            except OSError:
                continue
            if not stat.S_ISREG(metadata.st_mode):
                continue
            # Preserve the legacy distinction: only explicit files bypass this cap.
            if metadata.st_size > policy['max_file_bytes'] and rel not in explicit:
                continue
            files[rel] = metadata
    return files


def fingerprint(metadata):
    # On Python 3.12 Windows, stat and fstat can disagree about ctime semantics.
    # Identity, size and mtime are comparable on both APIs; ctime adds protection
    # on POSIX where it consistently denotes the last metadata change.
    return (metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_size,
            metadata.st_mtime_ns, metadata.st_ctime_ns if os.name != 'nt' else None)


def stable_hash(path, root, policy, metadata):
    """Hash one admitted snapshot; omit disappearing, replaced or changing files."""
    try:
        if not admitted(path, root, policy):
            return None
        with path.open('rb', buffering=0) as stream:
            if (fingerprint(os.fstat(stream.fileno())) != fingerprint(metadata)
                    or not admitted(path, root, policy)):
                return None
            digest = hashlib.sha256()
            remaining = metadata.st_size
            while remaining:
                raw = stream.read(min(65536, remaining))
                if not raw:
                    return None
                digest.update(raw)
                remaining -= len(raw)
            if fingerprint(os.fstat(stream.fileno())) != fingerprint(metadata):
                return None
        if (not admitted(path, root, policy)
                or fingerprint(path.stat(follow_symlinks=False)) != fingerprint(metadata)):
            return None
        return digest.hexdigest()
    except OSError:
        return None


def memory_terms(memories, policy):
    """Bound text per record; malformed text never supplies paths or authority."""
    result = set()
    for memory in memories:
        if not isinstance(memory, dict):
            continue
        remaining = policy['max_memory_text_chars']
        for key in ('title', 'summary', 'lessons', 'tags'):
            value = memory.get(key)
            values = value if isinstance(value, list) else [value]
            for text in values[:100]:
                if isinstance(text, str) and remaining:
                    result.update(tokens(text[:remaining]))
                    remaining -= min(len(text), remaining)
    return result

def is_test_path(value: str) -> bool:
    p = Path(value)
    parts = [part.lower() for part in p.parts]
    stem = p.stem.lower()

    return (
        any(part in {"test", "tests", "spec", "specs"} for part in parts[:-1])
        or stem.startswith(("test_", "spec_"))
        or stem.endswith(("_test", "_spec"))
    )

def build(task,route=None):
    policy = context_policy()
    task_id = safe_task_id(task['id'])
    request = task.get('request') or {}
    query = request.get('canonical_english') or task.get('description', '')
    wanted = tokens(query) | set(map(str.lower, task.get('tags', [])))
    explicit = {value for value in task.get('files', []) if _relative_path(value)}
    root = ROOT.resolve()
    document = build_graph_document()
    files = candidate_files(root, policy, explicit)
    explicit &= files.keys()
    adjacency = {}
    for edge in document['edges']:
        if edge['source'] in files and edge['target'] in files:
            adjacency.setdefault(edge['source'], set()).add(edge['target'])
    neighbors = set(neighborhood(adjacency, explicit, policy.get('graph_neighbor_depth', 2)))
    relevant = explicit | neighbors
    related = set()
    for edge in document['edges']:
        if edge['kind'] == 'test_affinity' and {edge['source'], edge['target']} <= relevant:
            related.update(path for path in (edge['source'], edge['target']) if is_test_path(path))
    try:
        memories = search_memory(query, policy['max_memory_items']) if policy['max_memory_items'] else []
    except (TypeError, AttributeError, ValueError):
        # The memory reader may encounter malformed legacy records. Retrieval is optional.
        memories = []
    memories = memories[:policy['max_memory_items']] if isinstance(memories, list) else []
    remembered = memory_terms(memories, policy)
    candidates = []
    for path, metadata in files.items():
        score = 0
        reasons = []
        # Required tiers remain above all ordinary relevance contributions.
        if path in policy['always_include']:
            score = 1000
            reasons.append('policy')
            if path in explicit:
                reasons.append('explicit')
        elif path in explicit:
            score = 900
            reasons.append('explicit')
        else:
            for applies, bonus, reason in (
                (path in neighbors, policy['graph_neighbor_bonus'], 'dependency-graph-neighbor'),
                (path in related, policy['related_test_bonus'], 'related-test'),
                (bool(remembered & tokens(path)), policy['memory_bonus'], 'memory'),
            ):
                if applies and bonus:
                    score += bonus
                    reasons.append(reason)
            overlap = sorted(wanted & tokens(path))[:policy['max_path_token_matches']]
            if overlap and policy['path_token_bonus']:
                score += policy['path_token_bonus'] * len(overlap)
                reasons.append('path-token:' + ','.join(overlap))
        if score:
            candidates.append((score, path, metadata, reasons))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    selected = []
    total = total_tokens = 0
    for score, path, metadata, reasons in candidates:
        size = metadata.st_size
        estimate = estimate_tokens(size)
        if len(selected) >= policy['max_files']:
            break
        required = path in explicit or path in policy['always_include']
        if not required and (total + size > policy['max_total_bytes'] or
                             total_tokens + estimate > policy['max_total_tokens_estimate']):
            continue
        digest = stable_hash(root / path, root, policy, metadata)
        if digest is None:
            continue
        selected.append({'path': path, 'bytes': size, 'estimated_tokens': estimate,
                         'sha256': digest, 'reason': reasons, 'score': score})
        total += size
        total_tokens += estimate
    snippets = []
    # Symbol retrieval is additive and fail-closed: the complete-file records above
    # remain the safe fallback for malformed/unsupported/unsafe sources.
    for record in selected:
        if not record['path'].endswith('.py'):
            continue
        path = root / record['path']
        try:
            source = path.read_text(encoding='utf-8')
            symbols = index_file(path)
        except (OSError, UnicodeError):
            continue
        terms = wanted | tokens(record['path'])
        chosen = [item for item in symbols if terms & tokens(item['name'])]
        if not chosen and (record['path'] in explicit or record['path'] in policy['always_include']):
            chosen = symbols[:1]
        for symbol in sorted(chosen, key=lambda item: (item['start_line'], item['name'])):
            extracted = extract_snippet(source, symbol['name'])
            raw = extracted['text'].encode('utf-8')
            estimate = estimate_tokens(len(raw))
            if total_tokens + estimate > policy['max_total_tokens_estimate']:
                break
            snippets.append({
                'path': record['path'], 'symbol': symbol['name'],
                'start_line': extracted['start_line'], 'end_line': extracted['end_line'],
                'sha256': extracted['sha256'], 'estimated_tokens': estimate,
                'reason': 'symbol' if not extracted['fallback'] else 'fallback',
                'fallback': bool(extracted['fallback']),
            })
            total_tokens += estimate
    return {'task_id': task_id, 'policy_version': policy['version'], 'route': route or {},
            'files': selected, 'memory': memories, 'graph_neighbors': sorted(neighbors),
            'graph_backend': document['backend'], 'total_bytes': total, 'estimated_tokens': total_tokens,
            'snippets': snippets,
            'limits': {'files': policy['max_files'], 'bytes': policy['max_total_bytes'],
                       'estimated_tokens': policy['max_total_tokens_estimate']}}
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('task'); ap.add_argument('--route'); ap.add_argument('--output'); a=ap.parse_args(); task=json.loads(Path(a.task).read_text(encoding='utf-8')); route=json.loads(Path(a.route).read_text()) if a.route else None; out=build(task,route); dest=Path(a.output) if a.output else run_dir(task['id'])/'context.json'; dest=dest if dest.is_absolute() else ROOT/dest; write_json_atomic(dest,out); print(json.dumps(out,indent=2,ensure_ascii=False))
if __name__=='__main__': main()
