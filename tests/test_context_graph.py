import contextlib
import copy
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import context_graph as cg


def validate_graph_schema(test, document, schema):
    """Fail closed on this contract's schema vocabulary, without dependencies.

    This is a deliberately limited test oracle, not a general Draft 2020-12
    validator. New keywords must gain validation and negative tests here first.
    """
    types = {"object": dict, "array": list, "string": str, "integer": int}
    keywords = {"$schema", "$defs", "$ref", "title", "type", "required", "properties",
                "additionalProperties", "const", "enum", "uniqueItems", "items",
                "minimum", "maximum", "minLength", "pattern"}

    def reference(ref):
        test.assertIs(type(ref), str)
        test.assertTrue(ref.startswith("#/$defs/"), "only local definition references are supported")
        name = ref[len("#/$defs/"):]
        test.assertIn(name, schema.get("$defs", {}), "unresolved schema reference")
        return schema["$defs"][name]

    def meta(rule, active=()):
        test.assertIs(type(rule), dict)
        test.assertFalse(set(rule) - keywords, "unsupported schema keyword")
        test.assertNotIn(id(rule), active, "recursive schemas are unsupported")
        active = (*active, id(rule))
        if "$schema" in rule:
            test.assertEqual(rule["$schema"], "https://json-schema.org/draft/2020-12/schema")
        if "title" in rule:
            test.assertIs(type(rule["title"]), str)
        if "type" in rule:
            test.assertIs(type(rule["type"]), str)
            test.assertIn(rule["type"], types)
        if "required" in rule:
            test.assertIs(type(rule["required"]), list)
            for value in rule["required"]:
                test.assertIs(type(value), str)
            test.assertEqual(len(rule["required"]), len(set(rule["required"])))
        for key in ("additionalProperties", "uniqueItems"):
            if key in rule:
                test.assertIs(type(rule[key]), bool)
        for key in ("minimum", "maximum", "minLength"):
            if key in rule:
                test.assertIs(type(rule[key]), int, "only integer bounds are supported")
                if key == "minLength":
                    test.assertGreaterEqual(rule[key], 0)
        if "pattern" in rule:
            test.assertIs(type(rule["pattern"]), str)
            try:
                re.compile(rule["pattern"])
            except re.error as exc:
                test.fail(f"invalid schema regex: {exc}")
        # The contract uses scalar constants/enums only; reject extensions.
        for key in ("const", "enum"):
            if key not in rule:
                continue
            values = [rule[key]] if key == "const" else rule[key]
            test.assertIs(type(values), list)
            test.assertTrue(values)
            for value in values:
                test.assertIn(type(value), (str, int, bool, type(None)))
            if key == "enum":
                encoded = [json.dumps(value) for value in values]
                test.assertEqual(len(encoded), len(set(encoded)), "duplicate enum value")
        for key in ("properties", "$defs"):
            if key in rule:
                test.assertIs(type(rule[key]), dict)
                for name, child in rule[key].items():
                    test.assertIs(type(name), str)
                    meta(child, active)
        if "items" in rule:
            meta(rule["items"], active)
        if "$ref" in rule:
            meta(reference(rule["$ref"]), active)

    def validate(value, rule):
        if "$ref" in rule:
            validate(value, reference(rule["$ref"]))
        if "type" in rule:
            test.assertIs(type(value), types[rule["type"]])
        if "const" in rule:
            test.assertIs(type(value), type(rule["const"]))
            test.assertEqual(value, rule["const"])
        if "enum" in rule:
            test.assertTrue(any(type(value) is type(item) and value == item for item in rule["enum"]))
        if type(value) is dict:
            test.assertTrue(set(rule.get("required", [])) <= set(value), "missing required field")
            properties = rule.get("properties", {})
            if rule.get("additionalProperties") is False:
                test.assertFalse(set(value) - set(properties), "unexpected field")
            for key in value.keys() & properties.keys():
                validate(value[key], properties[key])
        elif type(value) is list:
            if rule.get("uniqueItems"):
                serialized = [json.dumps(item, sort_keys=True) for item in value]
                test.assertEqual(len(serialized), len(set(serialized)), "duplicate array item")
            if "items" in rule:
                for item in value:
                    validate(item, rule["items"])
        elif type(value) is str:
            test.assertGreaterEqual(len(value), rule.get("minLength", 0))
            if "pattern" in rule:
                test.assertRegex(value, rule["pattern"])
        elif type(value) is int:
            if "minimum" in rule:
                test.assertGreaterEqual(value, rule["minimum"])
            if "maximum" in rule:
                test.assertLessEqual(value, rule["maximum"])

    meta(schema)
    validate(document, schema)


class ContextGraphTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.policy = {
            "version": 3,
            "exclude_dirs": [".git", "vendor"],
            "exclude_globs": ["secrets.*", "ignored_*.py"],
            "max_file_bytes": 120000,
            "graph_backend": "lexical",
            "graph_max_source_bytes": 120000,
            "graph_neighbor_depth": 2,
            "graph_max_results": 20,
        }
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(cg, "ROOT", self.root).start()
        mock.patch.object(cg, "load_json", return_value=self.policy).start()

    def source(self, path, text):
        dest = self.root / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(text.encode("utf-8") if isinstance(text, str) else text)

    def document(self):
        with mock.patch.object(sys, "argv", ["context_graph.py"]), \
                mock.patch.object(cg, "write_json_atomic") as write, \
                contextlib.redirect_stdout(io.StringIO()):
            cg.main()
        return write.call_args.args[1]

    def assert_schema(self, document, schema=None):
        if schema is None:
            schema = json.loads((Path(__file__).resolve().parents[1] /
                                 "harness/schema/context-graph.schema.json").read_text(encoding="utf-8"))
        validate_graph_schema(self, document, schema)
        # Graph invariants beyond JSON Schema: canonical order and cross references.
        node_paths = [node["path"] for node in document["nodes"]]
        self.assertEqual(node_paths, sorted(set(node_paths)))
        relations = []
        for edge in document["edges"]:
            self.assertIn(edge["source"], node_paths)
            self.assertIn(edge["target"], node_paths)
            self.assertNotEqual(edge["source"], edge["target"])
            relations.append((edge["source"], edge["target"], edge["kind"]))
        self.assertEqual(relations, sorted(set(relations)))
        self.assertEqual(document["counts"], {"nodes": len(node_paths), "edges": len(relations)})
        self.assertLessEqual(len(node_paths), document["limits"]["max_files"])
        self.assertLessEqual(len(relations), document["limits"]["max_edges"])

    def test_relative_from_import_resolves_module_names(self):
        for path in ("pkg/b.py", "pkg/c.py", "pkg/child/b.py", "pkg/child/c.py",
                     "pkg/alias.py", "pkg/child/alias.py"):
            self.source(path, "# module\n")
        self.source("pkg/__init__.py", "# package\n")
        self.source("pkg/child/__init__.py", "# child package\n")
        cases = (
            ("from . import b\n", ["pkg/child/__init__.py", "pkg/child/b.py"]),
            ("from .. import b\n", ["pkg/__init__.py", "pkg/b.py"]),
            ("from .. import b as alias, c, missing\n",
             ["pkg/__init__.py", "pkg/b.py", "pkg/c.py"]),
            ("from . import (\n b as alias, # comment\n c,\n)\n",
             ["pkg/child/__init__.py", "pkg/child/b.py", "pkg/child/c.py"]),
            ("from .... import b\n", []),
        )
        for statement, targets in cases:
            with self.subTest(statement=statement):
                self.source("pkg/child/a.py", statement)
                self.assertEqual(cg.build_graph().get("pkg/child/a.py", []), targets)

    def test_relative_from_import_works_without_package_initializer(self):
        self.source("pkg/a.py", "from . import b\n")
        self.source("pkg/b.py", "# module\n")
        self.assertEqual(cg.build_graph(), {"pkg/a.py": ["pkg/b.py"]})
        self.assertEqual(cg.neighborhood(cg.build_graph(), ["pkg/b.py"], 1), ["pkg/a.py"])

    def test_relative_from_import_rejects_top_level_boundary(self):
        for path in ("b.py", "pkg/b.py", "pkg/child/b.py"):
            self.source(path, "# module\n")
        for dots, expected in ((".", ["pkg/child/b.py"]), ("..", ["pkg/b.py"]),
                               ("...", []), ("....", [])):
            with self.subTest(dots=dots):
                self.source("pkg/child/a.py", f"from {dots} import b\n")
                self.assertEqual(cg.build_graph().get("pkg/child/a.py", []), expected)

    def test_schema_rejects_invalid_instance_types(self):
        self.source("a.py", "import b\n")
        self.source("b.py", "# target\n")
        baseline = self.document()
        for field, value in (("schema_version", True), ("nodes", tuple(baseline["nodes"])),
                             ("edges", tuple(baseline["edges"]))):
            with self.subTest(field=field), self.assertRaises(AssertionError):
                self.assert_schema({**baseline, field: value})

    def test_schema_rejects_malformed_or_unsupported_rules(self):
        baseline = json.loads((Path(__file__).resolve().parents[1] /
                               "harness/schema/context-graph.schema.json").read_text(encoding="utf-8"))
        for field, value in (("type", "not-a-type"), ("additionalProperties", "false"),
                             ("$schema", "unknown-dialect"), ("minItems", 1)):
            schema = copy.deepcopy(baseline)
            schema[field] = value
            with self.subTest(field=field), self.assertRaises(AssertionError):
                self.assert_schema(self.document(), schema)

    def test_schema_validates_nested_rules_and_references_before_instances(self):
        baseline = json.loads((Path(__file__).resolve().parents[1] /
                               "harness/schema/context-graph.schema.json").read_text(encoding="utf-8"))
        cases = [
            (("required",), "nodes"), (("required",), ["nodes", "nodes"]),
            (("properties",), []), (("properties", "nodes", "items"), False),
            (("properties", "nodes", "uniqueItems"), 1),
            (("properties", "counts", "properties", "nodes", "minimum"), True),
            (("properties", "backend", "properties", "selected", "enum"), []),
            (("properties", "backend", "properties", "selected", "enum"), ["lexical", "lexical"]),
            (("$defs", "path", "minLength"), -1), (("$defs", "path", "pattern"), "["),
            (("$defs", "path", "maxLength"), 10),
            (("$defs", "unused"), {"unknown": True}),
            (("properties", "nodes", "items", "properties", "path", "$ref"), "#/$defs/missing"),
            (("properties", "nodes", "items", "properties", "path", "$ref"), "https://example.invalid/schema"),
            (("$defs", "path", "$ref"), "#/$defs/path"),
        ]
        # An empty graph still must validate schemas for unvisited nodes/edges.
        document = self.document()
        for path, value in cases:
            schema = copy.deepcopy(baseline)
            target = schema
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            with self.subTest(path=path, value=value), self.assertRaises(AssertionError):
                self.assert_schema(document, schema)

    def test_schema_rejects_invalid_records_and_metadata(self):
        self.source("a.py", "import b\n")
        self.source("b.py", "# target\n")
        baseline = self.document()
        cases = [
            (("extra",), True), (("backend", "selected"), "unknown"),
            (("backend", "fallback_reason"), "private exception"),
            (("repository", "root"), "/checkout"),
            (("nodes", 0, "path"), "../escape.py"), (("nodes", 0, "path"), ""),
            (("nodes", 0, "extra"), "source content"),
            (("nodes",), baseline["nodes"] * 2), (("edges",), baseline["edges"] * 2),
            (("edges", 0, "kind"), "call"), (("edges", 0, "target"), "missing.py"),
            (("edges", 0, "target"), "a.py"),
            (("counts", "nodes"), True), (("counts", "nodes"), -1),
            (("counts", "edges"), 99), (("limits", "max_source_bytes"), 0),
            (("limits", "max_files"), 0), (("limits", "max_files"), 10001),
            (("limits", "max_total_bytes"), 0), (("limits", "max_total_bytes"), 64000001),
            (("limits", "max_edges"), -1), (("limits", "max_edges"), 100001),
            (("limits", "max_files"), 1), (("limits", "max_edges"), 0),
            (("limits", "max_depth"), 33), (("limits", "max_results"), 10001),
        ]
        for path, value in cases:
            document = copy.deepcopy(baseline)
            target = document
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            with self.subTest(path=path, value=value), self.assertRaises(AssertionError):
                self.assert_schema(document)
        for field in baseline:
            document = copy.deepcopy(baseline)
            del document[field]
            with self.subTest(missing=field), self.assertRaises(AssertionError):
                self.assert_schema(document)

    def test_normalized_document_is_stable_and_adjacency_compatible(self):
        self.source("z.py", "import a\nimport a\n")
        self.source("a.py", "import z\n")
        self.source("isolated.py", "value = 1\n")
        first = self.document()
        self.assertIsInstance(first["nodes"], list)
        self.assertEqual(first["schema_version"], 1)
        self.assertEqual(first["repository"], {"root": ".", "path_format": "relative-posix"})
        self.assertEqual(first["nodes"], [{"path": "a.py"}, {"path": "isolated.py"}, {"path": "z.py"}])
        self.assertEqual(first["edges"], [
            {"source": "a.py", "target": "z.py", "kind": "import"},
            {"source": "z.py", "target": "a.py", "kind": "import"},
        ])
        self.assertEqual(first["counts"], {"nodes": 3, "edges": 2})
        self.assertEqual(first["backend"], {"requested": "lexical", "selected": "lexical", "fallback_reason": None})
        self.assertEqual(json.dumps(first), json.dumps(self.document()))
        self.assert_schema(first)
        self.assertEqual(cg.build_graph(), {"a.py": ["z.py"], "z.py": ["a.py"]})

    def test_exclusions_and_exact_byte_limit(self):
        self.policy["graph_max_source_bytes"] = 16
        self.source("a.py", "import b\n")
        self.source("b.py", b"#" + b"x" * 15)
        self.source("over.py", b"import a\n" + b"x" * 8)
        self.source("unicode.py", "#" + "é" * 8)
        for path in ("vendor/no.py", "pkg/vendor/no.py", ".git/no.py",
                     "secrets.py", "pkg/secrets.py", "ignored_x.py"):
            self.source(path, "import a\n")
        result = self.document()
        self.assertEqual(result["nodes"], [{"path": "a.py"}, {"path": "b.py"}])
        self.assertEqual(cg.candidates(), ["a.py", "b.py"])
        self.assertEqual(result["edges"], [{"source": "a.py", "target": "b.py", "kind": "import"}])
        self.assert_schema(result)

    def test_case_insensitive_exclusions_prune_before_open_and_reads_are_bounded(self):
        self.policy.update(graph_backend="auto", graph_max_source_bytes=16)
        self.policy["exclude_dirs"].append("cache/generated")
        admitted = {"a.py": b"import b\n", "b.py": b"#" * 16,
                    "pkg/cache/generated/ok.py": b"# rooted rule\n"}
        for path, content in admitted.items():
            self.source(path, content)
        forbidden = ("VeNdOr/no.py", "pkg/VENDOR/no.py", ".GIT/no.py",
                     "Cache/Generated/no.py", "SECRETS.PY", "pkg/Secrets.py",
                     "IGNORED_x.PY", "over.py", "unicode_over.py")
        for path in forbidden:
            self.source(path, "#" * 17 if path == "over.py" else "#" + "é" * 8
                        if path == "unicode_over.py" else "import a\n")
        opened, reads, visited = [], [], []
        original_open, original_walk = Path.open, cg.os.walk

        @contextlib.contextmanager
        def guarded_open(path, mode="r", *args, **kwargs):
            relative = path.relative_to(self.root).as_posix()
            self.assertIn(relative, admitted, "excluded/oversized source was opened")
            self.assertEqual(mode, "rb")
            opened.append(relative)
            with original_open(path, mode, *args, **kwargs) as stream:
                def read(size=-1):
                    self.assertEqual(size, 17, "source reads must request limit plus one")
                    reads.append((relative, size))
                    return stream.read(size)
                wrapped = mock.Mock(wraps=stream)
                wrapped.read.side_effect = read
                yield wrapped

        def observed_walk(*args, **kwargs):
            for row in original_walk(*args, **kwargs):
                visited.append(Path(row[0]).relative_to(self.root).as_posix())
                yield row

        backend = mock.Mock(return_value={"nodes": [{"path": path} for path in admitted], "edges": []})
        with mock.patch.object(Path, "open", guarded_open), mock.patch.object(cg.os, "walk", observed_walk):
            document = cg.build_graph_document(backend=backend)
        self.assertCountEqual(opened, admitted)
        self.assertCountEqual(reads, [(path, 17) for path in admitted])
        self.assertFalse(set(visited) & {"VeNdOr", "pkg/VENDOR", ".GIT", "Cache/Generated"})
        self.assertEqual(dict(backend.call_args.args[0]),
                         {path: content.decode() for path, content in admitted.items()})
        self.assertEqual(document["nodes"], [{"path": path} for path in sorted(admitted)])
        self.assert_schema(document)
        graph = {"a.py": list(forbidden) + ["b.py"], "pkg/VENDOR/no.py": ["beyond.py"]}
        self.assertEqual(cg.neighborhood(graph, ["a.py"], 2), ["b.py", "over.py", "unicode_over.py"])

    def test_source_growing_after_stat_is_rejected_by_bounded_read(self):
        self.policy["graph_max_source_bytes"] = 16
        self.source("growing.py", b"#" * 17)
        original_stat, original_open = Path.stat, Path.open
        reads = []

        def earlier_stat(path, *args, **kwargs):
            result = original_stat(path, *args, **kwargs)
            if path == self.root / "growing.py" and kwargs.get("follow_symlinks") is False:
                result = mock.Mock(st_mode=result.st_mode, st_dev=result.st_dev,
                                   st_ino=result.st_ino, st_size=16)
            return result

        @contextlib.contextmanager
        def observed_open(path, *args, **kwargs):
            with original_open(path, *args, **kwargs) as stream:
                wrapped = mock.Mock(wraps=stream)
                def read(size=-1):
                    reads.append(size)
                    return stream.read(size)
                wrapped.read.side_effect = read
                yield wrapped

        with mock.patch.object(Path, "stat", earlier_stat), mock.patch.object(Path, "open", observed_open):
            document = cg.build_graph_document()
        self.assertEqual(reads, [17])
        self.assertEqual(document["nodes"], [])
        self.assert_schema(document)

    def test_external_resolution_and_symlinks_do_not_contribute(self):
        for path in ("a.py", "escape.py", "linked.py"):
            self.source(path, "import a\n")
        resolve = Path.resolve
        is_symlink = Path.is_symlink

        def resolved(path, *args, **kwargs):
            if path == self.root / "escape.py":
                return self.root.parent / "outside.py"
            return resolve(path, *args, **kwargs)

        def linked(path):
            return path == self.root / "linked.py" or is_symlink(path)

        with mock.patch.object(Path, "resolve", resolved), mock.patch.object(Path, "is_symlink", linked):
            self.assertEqual(self.document()["nodes"], [{"path": "a.py"}])

    def test_invalid_policy_is_rejected(self):
        for key, value in (
            ("graph_backend", "command-from-path"),
            ("graph_max_source_bytes", 0), ("graph_max_source_bytes", True),
            ("graph_max_source_bytes", 1.5), ("graph_max_source_bytes", 1000001),
            ("graph_neighbor_depth", -1), ("graph_neighbor_depth", 33),
            ("graph_max_results", -1), ("graph_max_results", 10001),
            ("exclude_dirs", ["../outside"]), ("exclude_globs", [None]),
        ):
            with self.subTest(key=key, value=value), mock.patch.dict(self.policy, {key: value}):
                with self.assertRaises(ValueError):
                    self.document()

    def test_security_policy_requires_version_and_exclusions_before_io(self):
        self.policy["graph_backend"] = "auto"
        invalid_policies = []
        for key in ("version", "exclude_dirs", "exclude_globs"):
            policy = dict(self.policy)
            del policy[key]
            invalid_policies.append((key, policy))
        for version in (None, True, 1, 4, "3", 3.0):
            invalid_policies.append(("version", {**self.policy, "version": version}))
        for key, policy in invalid_policies:
            backend = mock.Mock(return_value={"nodes": [], "edges": []})
            with self.subTest(key=key, policy=policy), \
                    mock.patch.dict(self.policy, policy, clear=True), \
                    mock.patch.object(cg.os, "walk", return_value=iter(())) as walk, \
                    mock.patch.object(Path, "open") as source_open:
                with self.assertRaisesRegex(ValueError, key):
                    cg.build_graph_document(backend=backend)
                walk.assert_not_called()
                source_open.assert_not_called()
                backend.assert_not_called()

    def test_security_policy_keeps_explicit_legacy_exclusions(self):
        self.policy["version"] = 2
        for key in tuple(self.policy):
            if key.startswith("graph_"):
                del self.policy[key]
        self.source("ok.py", "# admitted\n")
        self.source("vendor/no.py", "# excluded\n")
        document = cg.build_graph_document()
        self.assertEqual(document["nodes"], [{"path": "ok.py"}])
        self.assertEqual(document["backend"]["selected"], "lexical")
        self.assert_schema(document)

    def test_security_budget_invalid_limits_fail_before_io(self):
        for key, minimum, maximum in (
            ("graph_max_files", 1, 10000),
            ("graph_max_total_bytes", 1, 64000000),
            ("graph_max_edges", 0, 100000),
        ):
            for value in (minimum - 1, maximum + 1, True, 1.5, None, "10"):
                with self.subTest(key=key, value=value), \
                        mock.patch.dict(self.policy, {key: value}), \
                        mock.patch.object(cg.os, "walk", return_value=iter(())) as walk, \
                        mock.patch.object(Path, "open") as source_open:
                    backend = mock.Mock()
                    with self.assertRaisesRegex(ValueError, key):
                        cg.build_graph_document(backend=backend)
                    walk.assert_not_called()
                    source_open.assert_not_called()
                    backend.assert_not_called()

    def test_security_budget_file_cutoff_is_deterministic_before_open(self):
        for path in ("pkg/d.py", "b.py", "pkg/c.py", "a.py"):
            self.source(path, "# file\n")
        self.source("00_over.py", "#" * 17)
        self.source("vendor/no.py", "# excluded\n")
        self.policy.update(graph_backend="auto", graph_max_files=2, graph_max_source_bytes=16)
        original_open, original_walk = Path.open, cg.os.walk
        documents = []
        for reverse in (False, True):
            opened = []

            @contextlib.contextmanager
            def guarded_open(path, *args, **kwargs):
                relative = path.relative_to(self.root).as_posix()
                self.assertIn(relative, ("a.py", "b.py"), "source opened after file cutoff")
                opened.append(relative)
                with original_open(path, *args, **kwargs) as stream:
                    yield stream

            def reordered_walk(*args, **kwargs):
                for current, directories, names in original_walk(*args, **kwargs):
                    if reverse:
                        directories.reverse()
                        names.reverse()
                    yield current, directories, names

            backend = mock.Mock(return_value={"nodes": [{"path": "a.py"}, {"path": "b.py"}], "edges": []})
            with mock.patch.object(Path, "open", guarded_open), \
                    mock.patch.object(cg.os, "walk", reordered_walk):
                document = cg.build_graph_document(backend=backend)
            self.assertEqual(opened, ["a.py", "b.py"])
            self.assertEqual(list(backend.call_args.args[0]), ["a.py", "b.py"])
            self.assertEqual(document["nodes"], [{"path": "a.py"}, {"path": "b.py"}])
            self.assertEqual(document["limits"]["max_files"], 2)
            self.assert_schema(document)
            documents.append(document)
        self.assertEqual(documents[0], documents[1])

    def test_security_budget_aggregate_cutoff_is_prefix_and_counts_raw_bytes(self):
        self.source("a.py", "é")  # Two bytes, one decoded character.
        self.source("b.py", b"###")
        self.source("c.py", b"#")
        original_open = Path.open
        for budget, expected in ((4, ["a.py"]), (5, ["a.py", "b.py"]),
                                 (6, ["a.py", "b.py", "c.py"])):
            opened, consumed = [], []
            self.policy.update(graph_max_total_bytes=budget, graph_max_files=10)

            @contextlib.contextmanager
            def guarded_open(path, *args, **kwargs):
                relative = path.relative_to(self.root).as_posix()
                self.assertIn(relative, expected, "source opened after aggregate cutoff")
                opened.append(relative)
                with original_open(path, *args, **kwargs) as stream:
                    def read(size=-1):
                        self.assertGreater(size, 0)
                        self.assertLessEqual(size, budget - sum(consumed))
                        raw = stream.read(size)
                        consumed.append(len(raw))
                        return raw
                    wrapped = mock.Mock(wraps=stream)
                    wrapped.read.side_effect = read
                    yield wrapped

            with self.subTest(budget=budget):
                with mock.patch.object(Path, "open", guarded_open):
                    document = cg.build_graph_document()
                self.assertEqual(opened, expected)
                self.assertEqual([node["path"] for node in document["nodes"]], expected)
                self.assertLessEqual(sum(consumed), budget)
                self.assertEqual(document["limits"]["max_total_bytes"], budget)
                self.assert_schema(document)

    def test_security_budget_growth_consumes_allowance_without_extra_read(self):
        self.source("a.py", "#" * 6)
        self.source("b.py", "#")
        self.policy.update(graph_max_total_bytes=4, graph_max_files=2)
        original_open, original_stat = Path.open, Path.stat
        opened, reads = [], []

        def stale_size(path, *args, **kwargs):
            result = original_stat(path, *args, **kwargs)
            if path == self.root / "a.py" and kwargs.get("follow_symlinks") is False:
                return mock.Mock(st_mode=result.st_mode, st_dev=result.st_dev,
                                 st_ino=result.st_ino, st_size=1)
            return result

        @contextlib.contextmanager
        def guarded_open(path, *args, **kwargs):
            self.assertEqual(path.name, "a.py", "growth exhausted the aggregate budget")
            opened.append(path.name)
            with original_open(path, *args, **kwargs) as stream:
                def read(size=-1):
                    self.assertEqual(size, 4, "growth probe must fit the remaining budget")
                    reads.append(size)
                    return stream.read(size)
                wrapped = mock.Mock(wraps=stream)
                wrapped.read.side_effect = read
                yield wrapped

        with mock.patch.object(Path, "stat", stale_size), mock.patch.object(Path, "open", guarded_open):
            document = cg.build_graph_document()
        self.assertEqual(opened, ["a.py"])
        self.assertEqual(reads, [4])
        self.assertEqual(document["nodes"], [], "a partial source must not enter the graph")
        self.assert_schema(document)

    def test_security_budget_failed_io_cannot_bypass_limits(self):
        self.source("a.py", "#")
        self.source("b.py", "#")
        self.policy["graph_max_files"] = 1
        with mock.patch.object(Path, "open", side_effect=PermissionError("unreadable")) as source_open:
            document = cg.build_graph_document()
        self.assertEqual(source_open.call_count, 1, "failed opens must consume file allowance")
        self.assertEqual(document["nodes"], [])

        self.policy.update(graph_max_files=10, graph_max_total_bytes=4)
        original_open = Path.open
        opened, reads = [], []

        @contextlib.contextmanager
        def failing_read(path, *args, **kwargs):
            opened.append(path.name)
            with original_open(path, *args, **kwargs) as stream:
                def read(size=-1):
                    reads.append(size)
                    stream.read(size)
                    raise OSError("partial read is unknowable")
                wrapped = mock.Mock(wraps=stream)
                wrapped.read.side_effect = read
                yield wrapped

        with mock.patch.object(Path, "open", failing_read):
            document = cg.build_graph_document()
        self.assertEqual(opened, ["a.py"], "failed reads must keep their byte reservation")
        self.assertEqual(reads, [4])
        self.assertEqual(document["nodes"], [])
        self.assert_schema(document)

    def test_security_budget_repeated_stems_stop_at_edge_bound(self):
        for number in range(12):
            self.source(f"pkg{number:02}/shared.py", "# source\n")
            self.source(f"pkg{number:02}/test_shared.py", "# test\n")
        self.policy.update(graph_max_files=8, graph_max_edges=5)
        document = cg.build_graph_document()
        self.assertEqual(document["counts"], {"nodes": 8, "edges": 4})
        self.assertEqual(document["limits"]["max_edges"], 5)
        self.assertEqual(document["edges"], [
            {"source": "pkg00/shared.py", "target": "pkg00/test_shared.py", "kind": "test_affinity"},
            {"source": "pkg00/test_shared.py", "target": "pkg00/shared.py", "kind": "test_affinity"},
            {"source": "pkg00/test_shared.py", "target": "pkg01/shared.py", "kind": "test_affinity"},
            {"source": "pkg01/shared.py", "target": "pkg00/test_shared.py", "kind": "test_affinity"},
        ])
        self.assertEqual(document, cg.build_graph_document())
        self.assert_schema(document)
        self.policy["graph_max_edges"] = 0
        self.assertEqual(cg.build_graph_document()["edges"], [])

    def test_security_budget_excess_backend_edges_fall_back_before_normalization(self):
        self.source("a.py", "import b\n")
        self.source("b.py", "import a\n")
        self.policy.update(graph_backend="auto", graph_max_edges=1)
        backend = mock.Mock(return_value={
            "nodes": [{"path": "a.py"}, {"path": "b.py"}],
            "edges": [{"source": "a.py", "target": "b.py", "kind": "import"},
                      {"source": "b.py", "target": "a.py", "kind": "import"}],
        })
        document = cg.build_graph_document(backend=backend)
        self.assertEqual(document["backend"], {"requested": "auto", "selected": "lexical",
                                             "fallback_reason": "invalid_output"})
        self.assertEqual(document["edges"], [{"source": "a.py", "target": "b.py", "kind": "import"}])
        backend.assert_called_once()
        self.assert_schema(document)

    def test_relative_imports_cycles_and_malformed_source(self):
        self.source("pkg/a.py", "from .b import value\nimport nonexistent\n")
        self.source("pkg/b.py", "from .a import value\ndef broken(:\n")
        self.source("pkg/sub/c.py", "from ..a import value\n")
        self.source("web/main.js", "import value from './sub/value.js';\n")
        self.source("web/sub/value.js", "const v = require('../main');\n")
        self.source("tests/test_a.py", "# affinity without an import\n")
        graph = cg.build_graph()
        self.assertEqual(graph, {
            "pkg/a.py": ["pkg/b.py", "tests/test_a.py"],
            "pkg/b.py": ["pkg/a.py"],
            "pkg/sub/c.py": ["pkg/a.py"],
            "tests/test_a.py": ["pkg/a.py"],
            "web/main.js": ["web/sub/value.js"],
            "web/sub/value.js": ["web/main.js"],
        })
        self.assertEqual(cg.neighborhood(graph, ["pkg/b.py"], 2), ["pkg/a.py", "pkg/sub/c.py", "tests/test_a.py"])
        self.assertEqual(self.document(), self.document())
        self.assert_schema(self.document())

    def test_auto_unavailable_is_offline_lexical_fallback(self):
        self.source("a.py", "import b\n")
        self.source("b.py", "value = 1\n")
        self.policy["graph_backend"] = "auto"
        with mock.patch("subprocess.run") as run, mock.patch("subprocess.Popen") as popen, \
                mock.patch("shutil.which") as which, mock.patch("socket.socket") as socket:
            result = self.document()
        self.assertEqual(result["backend"], {
            "requested": "auto", "selected": "lexical", "fallback_reason": "unavailable",
        })
        self.assertEqual(result["edges"], [{"source": "a.py", "target": "b.py", "kind": "import"}])
        for boundary in (run, popen, which, socket):
            boundary.assert_not_called()
        self.assert_schema(result)

    def test_injected_backend_receives_only_admitted_sources(self):
        self.policy.update(graph_backend="auto", graph_max_source_bytes=16)
        self.source("a.py", "import b\n")
        self.source("b.py", "value = 1\n")
        self.source("secrets.py", "secret = True\n")
        self.source("over.py", "#" * 17)
        observed = []

        def backend(sources):
            observed.append(dict(sources))
            with self.assertRaises(TypeError):
                sources["forged.py"] = "import a"
            return {"nodes": [{"path": "b.py"}, {"path": "a.py"}], "edges": [
                {"source": "b.py", "target": "a.py", "kind": "import"},
                {"source": "b.py", "target": "a.py", "kind": "import"},
            ]}

        first = cg.build_graph_document(backend=backend)
        self.assertEqual(first["backend"], {"requested": "auto", "selected": "codegraph", "fallback_reason": None})
        self.assertEqual(first["edges"], [{"source": "b.py", "target": "a.py", "kind": "import"}])
        self.assertEqual(first["counts"], {"nodes": 2, "edges": 1})
        self.assertEqual(first, cg.build_graph_document(backend=backend))
        self.assertEqual(observed, [{"a.py": "import b\n", "b.py": "value = 1\n"}] * 2)
        self.assert_schema(first)

    def test_backend_canonicalizes_multiple_edges_across_input_orders(self):
        self.policy["graph_backend"] = "auto"
        for path in ("a.py", "b.py", "c.py"):
            self.source(path, "# isolated\n")
        expected = [
            {"source": "a.py", "target": "b.py", "kind": "import"},
            {"source": "a.py", "target": "b.py", "kind": "test_affinity"},
            {"source": "a.py", "target": "c.py", "kind": "import"},
            {"source": "c.py", "target": "a.py", "kind": "import"},
        ]
        serializations = []
        for order in (list(reversed(expected)), expected[2:] + expected[:2], expected):
            backend = mock.Mock(return_value={
                "nodes": [{"path": path} for path in ("c.py", "b.py", "a.py")],
                "edges": order + [order[0]],
            })
            document = cg.build_graph_document(backend=backend)
            self.assertEqual(document["backend"], {"requested": "auto", "selected": "codegraph", "fallback_reason": None})
            self.assertEqual(document["edges"], expected)
            self.assertEqual(document["counts"], {"nodes": 3, "edges": 4})
            self.assert_schema(document)
            serializations.append(json.dumps(document))
        self.assertEqual(len(set(serializations)), 1)

    def test_cli_persists_default_task_and_explicit_outputs(self):
        self.policy["exclude_dirs"].extend(["scripts", ".harness"])
        scripts = Path(__file__).resolve().parents[1] / "scripts"
        (self.root / "scripts").mkdir()
        for name in ("context_graph.py", "codegraph_backend.py", "harnesslib.py"):
            shutil.copy2(scripts / name, self.root / "scripts" / name)
        self.source("harness/context-policy.json", json.dumps(self.policy))
        self.source("a.py", "import b\n")
        self.source("b.py", "# target\n")
        expected = cg.build_graph_document()
        cases = [
            ([], self.root / ".harness/context-graph.json"),
            (["--task-id", "CLI-TEST"], self.root / ".harness/runs/CLI-TEST/context-graph.json"),
            (["--task-id", "IGNORED-TASK", "--output", "output/custom graph.json"],
             self.root / "output/custom graph.json"),
            (["--output", str(self.root / "absolute/graph.json")], self.root / "absolute/graph.json"),
        ]
        for arguments, destination in cases:
            with self.subTest(arguments=arguments):
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("stale artifact", encoding="utf-8")
                serialized = []
                for _ in range(2):
                    result = subprocess.run([sys.executable, str(self.root / "scripts/context_graph.py"), *arguments],
                                            cwd=self.root, text=True, capture_output=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stderr, "")
                    self.assertEqual(json.loads(result.stdout), {"output": str(destination), "nodes": 2, "edges": 1})
                    serialized.append(destination.read_bytes())
                    document = json.loads(serialized[-1])
                    self.assertEqual(document, expected)
                    self.assert_schema(document)
                self.assertEqual(serialized[0], serialized[1])
                self.assertEqual(list(destination.parent.iterdir()), [destination], "atomic writer left temporary files")
        self.assertFalse((self.root / ".harness/runs/IGNORED-TASK").exists())

    def test_backend_errors_and_invalid_results_fall_back_without_leaks(self):
        self.source("a.py", "import b\n")
        self.source("b.py", "value = 1\n")
        baseline = self.document()
        self.policy["graph_backend"] = "auto"
        valid_nodes = [{"path": "a.py"}, {"path": "b.py"}]
        invalid = [
            None, [], {"nodes": valid_nodes},
            {"nodes": valid_nodes, "edges": [], "private": "not retained"},
            {"nodes": [{"path": "../outside.py"}], "edges": []},
            {"nodes": [{"path": "a.py"}, {"path": "a.py"}], "edges": []},
            {"nodes": [{"path": "a.py", "source": "not retained"}, {"path": "b.py"}], "edges": []},
        ]
        for target in ("../outside.py", "/outside.py", "C:/outside.py", "secrets.py", "missing.py", "a.py", 1):
            invalid.append({"nodes": valid_nodes, "edges": [{"source": "a.py", "target": target, "kind": "import"}]})
        invalid.extend([
            {"nodes": valid_nodes, "edges": [{"source": "a.py", "target": "b.py", "kind": "call"}]},
            {"nodes": valid_nodes, "edges": [{"source": "a.py", "target": "b.py", "kind": "import", "text": "private"}]},
            {"nodes": valid_nodes, "edges": [None]},
            {"nodes": valid_nodes, "edges": [{"source": "a.py", "target": "b.py", "kind": "import"}] * 5},
        ])
        for raw in invalid:
            with self.subTest(raw=raw):
                adapter = mock.Mock(return_value=raw)
                result = cg.build_graph_document(backend=adapter)
                self.assertEqual(result["backend"]["fallback_reason"], "invalid_output")
                self.assertEqual(result["backend"]["selected"], "lexical")
                self.assertEqual(result["nodes"], baseline["nodes"])
                self.assertEqual(result["edges"], baseline["edges"])
                adapter.assert_called_once()
                self.assert_schema(result)
        adapter = mock.Mock(side_effect=RuntimeError("private source must never enter metadata"))
        result = cg.build_graph_document(backend=adapter)
        self.assertEqual(result["backend"]["fallback_reason"], "backend_error")
        self.assertNotIn("private source", json.dumps(result))
        self.assertEqual(result["edges"], baseline["edges"])
        adapter.assert_called_once()

    def test_lexical_mode_does_not_call_an_injected_backend(self):
        self.source("a.py", "value = 1\n")
        backend = mock.Mock()
        result = cg.build_graph_document(backend=backend)
        backend.assert_not_called()
        self.assertEqual(result["backend"], {"requested": "lexical", "selected": "lexical", "fallback_reason": None})

    def test_empty_repository_and_explicit_backend_selection(self):
        self.policy["graph_backend"] = "codegraph"
        result = self.document()
        self.assertEqual(result["counts"], {"nodes": 0, "edges": 0})
        self.assertEqual(result["backend"], {"requested": "codegraph", "selected": "lexical", "fallback_reason": "unavailable"})
        result = cg.build_graph_document(backend=lambda sources: {"nodes": [], "edges": []})
        self.assertEqual(result["backend"], {"requested": "codegraph", "selected": "codegraph", "fallback_reason": None})
        self.assert_schema(result)

    def test_neighborhood_enforces_breadth_and_depth_before_expansion(self):
        graph = {"s.py": ["z.py", "a.py"], "a.py": ["b.py", "s.py"], "x.py": ["s.py"]}
        self.policy.update(graph_max_results=2, graph_neighbor_depth=1)
        self.assertEqual(cg.neighborhood(graph, ["s.py"], 8), ["a.py", "x.py"])
        self.policy["graph_max_results"] = 20
        self.assertEqual(cg.neighborhood(graph, ["s.py"], 8), ["a.py", "x.py", "z.py"])
        self.policy["graph_neighbor_depth"] = 3
        self.assertEqual(cg.neighborhood(graph, iter(["s.py"]), 3), ["a.py", "b.py", "x.py", "z.py"])
        self.policy["graph_max_results"] = 0
        self.assertEqual(cg.neighborhood(graph, ["s.py"], 3), [])
        self.policy.update(graph_max_results=20, graph_neighbor_depth=0)
        self.assertEqual(cg.neighborhood(graph, ["s.py"], 3), [])

    def test_neighborhood_filters_unsafe_paths_and_invalid_depth(self):
        graph = {
            "s.py": ["ok.py", "../outside.py", "secrets.py", "vendor/hidden.py", "C:/outside.py"],
            "secrets.py": ["beyond.py"],
            "pkg/ignored_x.py": ["s.py"],
        }
        self.assertEqual(cg.neighborhood(graph, ["s.py", "secrets.py"], 2), ["ok.py"])
        self.assertEqual(cg.neighborhood(graph, [], 2), [])
        for depth in (-1, True, 1.5, 33):
            with self.subTest(depth=depth), self.assertRaises(ValueError):
                cg.neighborhood(graph, ["s.py"], depth)

    def test_dotted_imports_remain_supported_outside_python(self):
        self.source("pkg/a.java", "import pkg.b;\n")
        self.source("pkg/b.java", "class b {}\n")
        self.assertEqual(cg.build_graph(), {"pkg/a.java": ["pkg/b.java"]})


if __name__ == "__main__":
    unittest.main()
