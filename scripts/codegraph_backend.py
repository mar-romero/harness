"""Offline adapter seam; no provider is discovered, imported, installed, or invoked.

A trusted host may explicitly supply an in-process callable accepting the admitted
source mapping and returning the node/edge subset of context-graph v1. The callable
must be local and side-effect-free. No concrete provider is configured here.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType

LocalBackend = Callable[[Mapping[str, str]], object]


def _edges(raw, known):
    """Validate untrusted records against the already-filtered local inventory."""
    if type(raw) is not dict or set(raw) != {'nodes', 'edges'}:
        raise ValueError('invalid graph fields')
    nodes, edges = raw['nodes'], raw['edges']
    if type(nodes) is not list or len(nodes) != len(known):
        raise ValueError('invalid node inventory')
    paths = set()
    for node in nodes:
        if type(node) is not dict or set(node) != {'path'} or type(node['path']) is not str:
            raise ValueError('invalid node')
        paths.add(node['path'])
    if paths != known:
        raise ValueError('node inventory does not match admitted sources')
    # At most two directed relation kinds per distinct pair; reject oversized
    # payloads before walking them. Duplicate records within this bound coalesce.
    if type(edges) is not list or len(edges) > 2 * len(known) * max(0, len(known) - 1):
        raise ValueError('invalid edge inventory')
    normalized = set()
    for edge in edges:
        if type(edge) is not dict or set(edge) != {'source', 'target', 'kind'}:
            raise ValueError('invalid edge')
        source, target, kind = edge['source'], edge['target'], edge['kind']
        if any(type(value) is not str for value in (source, target, kind)):
            raise ValueError('invalid edge values')
        if source not in known or target not in known or source == target:
            raise ValueError('invalid edge endpoints')
        if kind not in ('import', 'test_affinity'):
            raise ValueError('invalid edge kind')
        normalized.add((source, target, kind))
    return sorted(normalized)


def try_build(sources: Mapping[str, str], backend: LocalBackend | None = None):
    """Attempt one explicitly supplied local backend and return a safe reason code."""
    if backend is None:
        return None, 'unavailable'
    try:
        raw = backend(MappingProxyType(dict(sources)))
    except Exception:
        # Exception text can contain source or local paths. Never retain it.
        return None, 'backend_error'
    try:
        return _edges(raw, set(sources)), None
    except (TypeError, ValueError):
        return None, 'invalid_output'
