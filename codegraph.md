# Repository graph contract

`scripts/context_graph.py` builds an offline file graph. The normalized v1 artifact
is defined by `harness/schema/context-graph.schema.json`. Task-context ranking can
also consume the optional CodeGraph bridge; the normalized artifact remains
lexical unless a trusted in-process backend is explicitly supplied.

## APIs and artifact

`build_graph_document()` returns the normalized document. The CLI persists the
same document to `.harness/context-graph.json`, or the task run directory when
`--task-id` is supplied. `--output` selects an explicit artifact destination.

```json
{
  "schema_version": 1,
  "repository": {"root": ".", "path_format": "relative-posix"},
  "backend": {"requested": "auto", "selected": "lexical", "fallback_reason": "unavailable"},
  "nodes": [{"path": "a.py"}, {"path": "b.py"}],
  "edges": [{"source": "a.py", "target": "b.py", "kind": "import"}],
  "counts": {"nodes": 2, "edges": 1},
  "limits": {
    "max_source_bytes": 120000, "max_files": 1000, "max_total_bytes": 8000000,
    "max_edges": 50000, "max_depth": 2, "max_results": 20
  }
}
```

Nodes include admitted isolated files and are sorted by repository-relative POSIX
path. Edges are unique directed `(source, target, kind)` records sorted in that
order; both endpoints must be admitted nodes. `import` is a lexical dependency.
`test_affinity` is a filename heuristic emitted in both directions. Reverse import
references are computed during traversal, without fabricating reverse imports.
Self edges and unresolved imports are omitted. Counts describe emitted records;
two different relation kinds between the same files count as two edges.

No absolute checkout path, source content, timestamp, or backend exception text is
retained. Unchanged source and policy with a deterministic backend produce the same
serialized document. The CLI replaces its former counter/adjacency envelope with
this normalized shape.

`build_graph()` preserves the existing sorted `dict[str, list[str]]` adjacency
contract consumed by `context_compiler.py` and `impact_analysis.py`. It merges
relation kinds and omits keys without outgoing edges. Consumers need no migration.

## Source policy and expansion

`harness/context-policy.json` controls the graph:

| Field | Meaning | Default and accepted range |
| --- | --- | --- |
| `graph_backend` | Requested backend | `auto`, `lexical`, or `codegraph` |
| `graph_max_source_bytes` | Maximum admitted file size in bytes | 120000; integer 1 through 1000000 |
| `graph_max_files` | Maximum source-file open attempts per graph build | 1000; integer 1 through 10000 |
| `graph_max_total_bytes` | Maximum aggregate source bytes read per graph build | 8000000; integer 1 through 64000000 |
| `graph_max_edges` | Maximum emitted edges and accepted backend edge records | 50000; integer 0 through 100000 |
| `graph_neighbor_depth` | Maximum traversal depth | 2; integer 0 through 32 |
| `graph_max_results` | Maximum returned neighbors, excluding seeds | 20; integer 0 through 10000 |

The policy must declare supported integer `version` 2 or 3 and explicitly contain
both `exclude_dirs` and `exclude_globs` lists. Missing or malformed exclusion lists
and missing or unsupported versions raise `ValueError` before scanning or invoking
a backend. Empty lists are permitted only when explicitly configured. Missing
graph fields use these defaults; a missing source byte limit first uses the
existing `max_file_bytes`. Invalid graph settings also raise `ValueError` before
scanning. Booleans and fractional numbers are not integer limits.

Directory names without a slash match any path component. Multi-component
directory exclusions are rooted at the repository. Glob exclusions match either
the basename or full relative path. Exclusion matching is case-insensitive on all
platforms. Excluded directories are pruned before traversal. Both kinds of
exclusion apply before reading and again to neighborhood inputs.

Only the supported source extensions are admitted. Symlinks, paths resolving
outside the repository, non-regular files, unreadable files, and files exceeding
the per-file byte limit are omitted. Each directory's files are considered in
sorted order, then its allowed subdirectories are visited in sorted depth-first
order. The scan stops before another source is opened when the file-open or
aggregate-byte allowance is exhausted, or when the next eligible file cannot fit
the remaining bytes; later smaller files are not substituted. Excluded and
already-oversized files consume neither allowance.

Reads are unbuffered and capped by both the per-file limit plus one and the
remaining aggregate bytes. Exact limits are accepted. The final size check rejects
partial or growing sources; bytes read from rejected files still consume the
aggregate allowance. Failed opens consume a file attempt, and failed reads retain
their reserved bytes because partial I/O cannot be measured safely. These are raw
byte bounds, before UTF-8 replacement decoding. Invalid UTF-8 bytes are replaced
and malformed syntax still receives lexical matching. The scan assumes a stable
checkout; concurrent filesystem replacement is not a supported snapshot guarantee.
The budgets bound source I/O and graph construction, not filesystem directory
enumeration or the runtime of a trusted injected backend.

Lexical imports are considered first in stable source/pattern order, followed by
test-affinity pairs in stable source order. Construction stops when the edge
allowance is reached, or before an affinity pair would exceed it; affinity stays
bidirectional even when an odd allowance leaves one unused slot. A zero edge
allowance emits no edges. The retained records are then sorted canonically. The
file, byte, and edge limits are emitted in the artifact, including for partial
graphs; counts always describe the retained graph, not full-repository coverage.

`neighborhood(graph, seeds, depth=None)` walks forward and reverse references
breadth first. Explicit depth is validated and capped by policy. Each layer is
sorted before taking the remaining allowance, and truncated nodes are never
expanded. Cycles terminate through a visited set. Zero depth or result allowance
returns an empty list. The result is sorted, excludes seeds, and never exceeds
`graph_max_results`. Depth and result limits bound neighborhood expansion; the
separate file, byte, and edge budgets bound graph construction.

## Automatic task-context integration

Task context compilation uses `graph_backend: "auto"` by default. During every
context build, `scripts/context_compiler.py` queries the bounded
`scripts/codegraph_bridge.py` surface when the external CodeGraph CLI and a
local `.codegraph/` index are available. Only validated repository-relative
paths are used to boost context ranking; provider text, absolute paths and
untrusted metadata are not persisted.

The bridge applies a hard per-stream output cap and terminates a provider that
exceeds it; that task then records a fixed fallback reason.

The bridge is optional and never downloads a third-party executable. Operators
can inspect readiness with:

```powershell
python scripts/codegraph_bridge.py status
```

When CodeGraph is unavailable, task context compilation keeps the existing
lexical graph and symbol fallback. The generated context records whether the
CodeGraph path was `ready` or used a fixed fallback reason, so the selection is
observable without making provider availability a correctness prerequisite.

## Optional in-process backend

`scripts/codegraph_backend.py` remains a separate in-process adapter seam for
trusted hosts that explicitly pass a local callable to `build_graph_document`.
It does not discover or install providers. The task-context integration above
uses the safe external bridge for bounded path ranking; the normalized graph
artifact still retains the lexical graph unless an in-process backend is
explicitly supplied.

An explicitly supplied trusted in-process callable can be exercised through
`build_graph_document(backend=callable)`. This is the local adapter seam in
`scripts/codegraph_backend.py`, not an external executable or network contract.
The callable must perform local computation without side effects; the seam is not
a sandbox for arbitrary provider code. It receives only an immutable mapping of
admitted relative paths to bounded decoded text, with no repository root or policy.

The callable returns exactly `nodes` and `edges`, using the records above. It must
return every admitted node exactly once. Edges may only use admitted endpoints and
the two defined kinds. Edge records are limited to `graph_max_edges` and twice the
number of distinct ordered node pairs before normalization; duplicates within
both bounds coalesce.
Ordering and counts are rebuilt locally. Extra fields, unsafe or unknown paths,
missing nodes, invalid types, self edges, and excessive records reject the entire
result and use the lexical graph.

`lexical` does not call an injected backend. Other modes attempt an injected
backend once, without retry. Missing injection, ordinary exceptions, and invalid
results yield fixed `unavailable`, `backend_error`, or `invalid_output` reasons.
A valid result selects `codegraph` with a null fallback reason. Provider selection
and any external side effects require their own accepted contract and task.

## Contract regression checks

`python -m unittest tests.test_context_graph -v` exercises both graph backends,
canonical ordering across distinct edge permutations, policy exclusions before
file reads, bounded reads, and CLI persistence in isolated repositories.

The tests validate emitted documents and schema definitions with a dependency-free
oracle for the keywords used by v1, plus graph-specific ordering, endpoint, and
count invariants. This is a restricted schema vocabulary, not a general Draft
2020-12 validator: unsupported keywords, unresolved or recursive references, and
malformed rules fail the check even when the graph has no records. Extending the
schema requires extending the oracle and its negative tests in the same change.

## Context-retrieval benchmark

Run the fixed offline measurement from the repository root:

```sh
uv run python scripts/context_benchmark.py --fixture tests/fixtures/context_benchmark
```

The fixture supplies a miniature repository, its task, its required source/test and
policy paths, required Python symbols, and an entire lexical-only context policy.
It does not read task memory, use an optional graph backend, contact a service, use
clock/random input, or inspect the host checkout. The benchmark builds the existing
file-level pack twice and compares canonical JSON bytes; either a different result
or a missing required surface fails with a nonzero exit and a path/symbol diagnostic.

`baseline_tokens` is the sum of the selected complete-file records. It deliberately
does **not** use the context pack's additive `estimated_tokens`, which includes both
file and snippet records. `candidate_tokens` is the effective delivery material:
each selected file remains complete exactly once unless it is a Python file with a
nonempty declared required-symbol surface entirely covered by selected non-fallback
snippets. In that one case only the required snippets are counted. Any partial or
fallback snippet keeps the complete file. The report also gives the source and
effective candidate selections, fallback count, recall across required paths and
symbols, omissions, a deterministic-run status, and the percentage reduction. A
complete-file fallback avoids incomplete delivery but does not certify symbol
retrieval: a required symbol must still have a selected non-fallback snippet for
the recall report to accept it.

The benchmark is evidence, not a delivery switch. `snippet_rollout` in
`harness/context-policy.json` keeps `file-level` as the default, disables automatic
enablement, and requires deterministic zero-omission/100%-recall evidence plus
explicit human approval before any future rollout. Existing context delivery does
not consume this benchmark and remains unchanged.
