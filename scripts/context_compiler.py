#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import json
import math
import re
from pathlib import Path

from harnesslib import ROOT, load_json, safe_task_id, sha256_file, write_json_atomic, run_dir
from context_graph import build_graph, neighborhood
from memory import search as search_memory
from codegraph_bridge import explore as codegraph_explore, status as codegraph_status
from symbol_context import build_repo_map, build_snippets, estimate_tokens as estimate_text_tokens


def excluded(rel, policy):
    s = rel.as_posix()
    if any(s == d or s.startswith(d.rstrip('/') + '/') for d in policy['exclude_dirs']):
        return True
    return any(fnmatch.fnmatch(rel.name, g) or fnmatch.fnmatch(s, g) for g in policy['exclude_globs'])


def tokens(text):
    return {
        t for t in re.findall(r'[a-zA-Z0-9_\-]{3,}', text.lower())
        if t not in {'this', 'that', 'with', 'from', 'into', 'para', 'como', 'esta', 'este'}
    }


def estimate_tokens(size):
    return max(1, math.ceil(size / 4))


def is_test_path(value: str) -> bool:
    p = Path(value)
    parts = [part.lower() for part in p.parts]
    stem = p.stem.lower()
    return (
        any(part in {'test', 'tests', 'spec', 'specs'} for part in parts[:-1])
        or stem.startswith(('test_', 'spec_'))
        or stem.endswith(('_test', '_spec'))
    )


def _query(task: dict) -> str:
    req = task.get('request') or {}
    return str(req.get('canonical_english') or req.get('original_text') or task.get('description', '') or '')


def _selective_retrieval_needed(task: dict, route: dict | None, explicit: set[str], policy: dict) -> tuple[bool, list[str]]:
    """Cheap deterministic gate inspired by selective RAG.

    Do not pay retrieval overhead for a tiny, explicit, local task. Prefer richer
    graph/symbol retrieval when localization is uncertain or the impact/risk is larger.
    """
    reasons: list[str] = []
    risk = str((route or {}).get('risk') or task.get('risk') or 'R1')
    if risk in set(policy.get('structured_retrieval_risks', ['R2', 'R3'])):
        reasons.append(f'risk:{risk}')
    if not explicit:
        reasons.append('no-explicit-files')
    if len(explicit) > int(policy.get('direct_file_count_threshold', 1)):
        reasons.append('multiple-explicit-files')
    q = _query(task)
    if len(tokens(q)) >= int(policy.get('query_token_retrieval_threshold', 10)):
        reasons.append('broad-query')
    large_threshold = int(policy.get('direct_file_token_threshold', 3500))
    for rel in sorted(explicit):
        path = ROOT / rel
        if path.is_file() and estimate_tokens(path.stat().st_size) > large_threshold:
            reasons.append('large-explicit-file')
            break
    tags = task.get('tags') or []
    if len(tags) >= int(policy.get('tag_retrieval_threshold', 4)):
        reasons.append('many-tags')
    return bool(reasons), reasons


def _codegraph_context(task: dict, policy: dict, needed: bool) -> dict:
    backend = str(policy.get('graph_backend', 'auto')).lower()
    if not needed or backend in {'off', 'builtin'}:
        return {
            'attempted': False,
            'backend': 'builtin' if backend != 'off' else 'off',
            'status': 'skipped',
            'text': '',
            'paths': [],
            'estimated_tokens': 0,
        }
    status = codegraph_status()
    if backend == 'codegraph' and status.get('status') != 'ready':
        return {
            'attempted': True,
            'backend': 'builtin',
            'status': 'fallback',
            'reason': status.get('reason') or status.get('status'),
            'codegraph_status': status,
            'text': '',
            'paths': [],
            'estimated_tokens': 0,
        }
    if backend == 'auto' and status.get('status') != 'ready':
        return {
            'attempted': True,
            'backend': 'builtin',
            'status': 'fallback',
            'reason': status.get('reason') or status.get('status'),
            'codegraph_status': status,
            'text': '',
            'paths': [],
            'estimated_tokens': 0,
        }
    max_tokens = int(policy.get('codegraph_max_tokens', 7000))
    result = codegraph_explore(
        _query(task),
        max_files=int(policy.get('codegraph_max_files', 8)),
        max_chars=max_tokens * 4,
    )
    text = str(result.get('text') or result.get('stdout') or '') if result.get('ok') else ''
    if not result.get('ok'):
        return {
            'attempted': True,
            'backend': 'builtin',
            'status': 'fallback',
            'reason': result.get('reason') or result.get('stderr') or result.get('status'),
            'codegraph_status': status,
            'text': '',
            'paths': [],
            'estimated_tokens': 0,
        }
    return {
        'attempted': True,
        'backend': 'codegraph',
        'status': 'ready',
        'truncated': bool(result.get('truncated')),
        'paths': list(result.get('paths') or []),
        'text': text,
        'estimated_tokens': estimate_text_tokens(text) if text else 0,
        'codegraph_status': status,
    }


def _memory_token_estimate(memories: list[dict]) -> int:
    try:
        text = json.dumps(memories, ensure_ascii=False, separators=(',', ':'))
    except TypeError:
        text = str(memories)
    return estimate_text_tokens(text) if text else 0


def build(task, route=None):
    p = load_json('harness/context-policy.json')
    task_id = safe_task_id(task['id'])
    query = _query(task)
    wanted = tokens(query) | set(map(str.lower, task.get('tags', [])))
    explicit = {Path(x).as_posix() for x in task.get('files', [])}

    graph = build_graph()
    neighbors = set(neighborhood(graph, explicit, int(p.get('graph_neighbor_depth', 2)))) if explicit else set()
    retrieval_needed, retrieval_reasons = _selective_retrieval_needed(task, route, explicit, p)
    cg = _codegraph_context(task, p, retrieval_needed)
    codegraph_paths = set(cg.get('paths') or [])

    candidates = []
    for path in ROOT.rglob('*'):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if excluded(rel, p):
            continue
        if path.suffix.lower() not in p['text_extensions'] and rel.as_posix() not in p['always_include'] and rel.as_posix() not in explicit:
            continue
        size = path.stat().st_size
        if size > p['max_file_bytes'] and rel.as_posix() not in explicit:
            continue
        score = 0
        reasons = []
        r = rel.as_posix()
        low = r.lower()
        if r in p['always_include']:
            score += 1000
            reasons.append('policy')
        if r in explicit:
            score += 900
            reasons.append('explicit')
        if r in codegraph_paths:
            score += int(p.get('codegraph_file_bonus', 500))
            reasons.append('codegraph')
        if r in neighbors:
            score += int(p.get('graph_neighbor_bonus', 350))
            reasons.append('dependency-graph-neighbor')
        overlap = wanted & tokens(low)
        if overlap:
            score += 20 * len(overlap)
            reasons.append('path-token:' + ','.join(sorted(overlap)[:5]))
        if is_test_path(r):
            score += 5
        if score:
            candidates.append((score, size, r, reasons))

    candidates.sort(key=lambda x: (-x[0], x[2]))
    selected = []
    total = 0
    candidate_full_tokens = 0
    selected_scores: dict[str, float] = {}
    for score, size, r, reasons in candidates:
        est = estimate_tokens(size)
        if len(selected) >= p['max_files']:
            break
        if total + size > p['max_total_bytes'] and score < 900:
            continue
        if candidate_full_tokens + est > p.get('max_total_tokens_estimate', 10**9) and score < 900:
            continue
        path = ROOT / r
        selected.append({
            'path': r,
            'bytes': size,
            'estimated_tokens': est,
            'sha256': sha256_file(path),
            'reason': reasons,
            'score': score,
            'load_policy': 'range-or-symbol-first' if path.suffix.lower() in set(p.get('code_extensions', [])) else 'metadata-or-bounded-read',
        })
        selected_scores[r] = float(score)
        total += size
        candidate_full_tokens += est

    selected_paths = [row['path'] for row in selected]
    repo_map = {'text': '', 'estimated_tokens': 0, 'files': [], 'symbol_count': 0}
    snippets = {'snippets': [], 'estimated_tokens': 0, 'files': []}
    if retrieval_needed and cg.get('backend') != 'off':
        repo_map = build_repo_map(
            selected_paths,
            query,
            explicit,
            graph,
            token_budget=int(p.get('repo_map_tokens', 1600)),
            selected_scores=selected_scores,
        )
        # CodeGraph explore already returns verbatim symbol source. Avoid duplicating
        # that source in the context pack; built-in snippets are the offline fallback.
        if cg.get('backend') != 'codegraph':
            snippets = build_snippets(
                selected_paths,
                query,
                explicit,
                graph,
                token_budget=int(p.get('symbol_snippet_tokens', 6500)),
                max_symbols_per_file=int(p.get('max_symbols_per_file', 3)),
                max_lines_per_symbol=int(p.get('max_lines_per_symbol', 80)),
                selected_scores=selected_scores,
            )

    memories = search_memory(query, int(p.get('max_memory_items', 5)))
    structured_tokens = int(cg.get('estimated_tokens') or 0) + int(repo_map.get('estimated_tokens') or 0) + int(snippets.get('estimated_tokens') or 0)
    effective_tokens = structured_tokens + _memory_token_estimate(memories)

    return {
        'task_id': task_id,
        'policy_version': p['version'],
        'route': route or {},
        'files': selected,
        'memory': memories,
        'graph_neighbors': sorted(neighbors),
        'retrieval': {
            'needed': retrieval_needed,
            'reasons': retrieval_reasons,
            'requested_backend': p.get('graph_backend', 'auto'),
            'backend': cg.get('backend'),
            'status': cg.get('status'),
            'codegraph': {k: v for k, v in cg.items() if k not in {'text'}},
        },
        'structured_context': {
            'usage': 'Prefer this compact source/map first. Use repo_explore or repo_read_range only for missing details; do not reopen whole files already represented here.',
            'codegraph_explore': cg.get('text') or '',
            'repo_map': repo_map,
            'symbol_snippets': snippets.get('snippets') or [],
            'estimated_tokens': structured_tokens,
        },
        'total_bytes': total,
        # Effective pack estimate, not the cost of reading every candidate file in full.
        'estimated_tokens': effective_tokens,
        'candidate_full_file_tokens': candidate_full_tokens,
        'token_savings_estimate': max(0, candidate_full_tokens - effective_tokens),
        'limits': {
            'files': p['max_files'],
            'bytes': p['max_total_bytes'],
            'candidate_full_file_tokens': p.get('max_total_tokens_estimate'),
            'estimated_tokens': p.get('compact_context_max_tokens'),
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('task')
    ap.add_argument('--route')
    ap.add_argument('--output')
    a = ap.parse_args()
    task = json.loads(Path(a.task).read_text(encoding='utf-8'))
    route = json.loads(Path(a.route).read_text()) if a.route else None
    out = build(task, route)
    dest = Path(a.output) if a.output else run_dir(task['id']) / 'context.json'
    dest = dest if dest.is_absolute() else ROOT / dest
    write_json_atomic(dest, out)
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
