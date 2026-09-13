import contextlib
import io
import json
import random
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import context_benchmark
import context_compiler
import context_graph


FIXTURE = ROOT / "tests" / "fixtures" / "context_benchmark"


class ContextBenchmarkTests(unittest.TestCase):
    def test_fixed_fixture_report_is_deterministic_and_uses_effective_material(self):
        first = context_benchmark.run_fixture(FIXTURE)
        second = context_benchmark.run_fixture(FIXTURE)

        self.assertEqual(
            json.dumps(first, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            json.dumps(second, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )
        self.assertEqual(first["baseline_tokens"], 118)
        self.assertEqual(first["candidate_tokens"], 106)
        self.assertEqual(first["relevant_surface_recall"], 1.0)
        self.assertEqual(first["omissions"], [])
        self.assertEqual(first["determinism"]["status"], "PASS")
        self.assertFalse(first["rollout_recommendation"]["automatic_enable"])
        self.assertEqual(first["selected_files"]["candidate_replaced_by_snippets"], ["src/processor.py"])
        self.assertEqual(first["selected_symbols"], [{"path": "src/processor.py", "symbol": "Processor.process", "start_line": 1}])

    def test_fixture_does_not_reach_memory_or_optional_backend(self):
        with patch.object(context_compiler, "search_memory", side_effect=AssertionError("memory reached")), \
             patch.object(context_graph, "try_build", side_effect=AssertionError("backend reached")):
            report = context_benchmark.run_fixture(FIXTURE)
        self.assertEqual(report["determinism"]["status"], "PASS")

    def test_measurement_uses_complete_file_baseline_not_additive_pack_total(self):
        pack = {
            "files": [
                {"path": "AGENTS.md", "estimated_tokens": 5},
                {"path": "src/worker.py", "estimated_tokens": 40},
                {"path": "tests/test_worker.py", "estimated_tokens": 10},
            ],
            "snippets": [
                {"path": "src/worker.py", "symbol": "worker", "start_line": 1, "estimated_tokens": 8, "fallback": False}
            ],
            "estimated_tokens": 9999,
        }
        report = context_benchmark.measure_context_pack(
            pack,
            required_paths=["AGENTS.md", "src/worker.py", "tests/test_worker.py"],
            required_symbols={"src/worker.py": ["worker"]},
        )
        self.assertEqual(report["baseline_tokens"], 55)
        self.assertEqual(report["candidate_tokens"], 23)
        self.assertEqual(report["token_reduction_percent"], 58.18)

    def test_partial_or_fallback_symbol_retains_complete_file_once(self):
        pack = {
            "files": [
                {"path": "src/worker.py", "estimated_tokens": 50},
                {"path": "tests/test_worker.py", "estimated_tokens": 10},
            ],
            "snippets": [
                {"path": "src/worker.py", "symbol": "worker", "start_line": 1, "estimated_tokens": 8, "fallback": False},
                {"path": "src/worker.py", "symbol": "helper", "start_line": 4, "estimated_tokens": 50, "fallback": True},
            ],
        }
        report = context_benchmark.measure_context_pack(
            pack,
            required_paths=["src/worker.py", "tests/test_worker.py"],
            required_symbols={"src/worker.py": ["worker"]},
        )
        self.assertEqual(report["candidate_tokens"], 60)
        self.assertEqual(report["fallback_count"], 1)
        self.assertEqual(report["selected_files"]["candidate_complete"], ["src/worker.py", "tests/test_worker.py"])
        self.assertEqual(report["selected_symbols"], [])
        self.assertIn("symbol:src/worker.py:worker", report["omissions"])
        self.assertLess(report["relevant_surface_recall"], 1.0)
        with self.assertRaises(context_benchmark.BenchmarkRegressionError):
            context_benchmark._require_safe_report(report)

    def test_required_path_omissions_fail_closed_with_diagnostics(self):
        required_paths = ["AGENTS.md", "src/worker.py", "tests/test_worker.py"]
        for missing in required_paths:
            with self.subTest(missing=missing):
                records = [
                    {"path": path, "estimated_tokens": 10}
                    for path in required_paths if path != missing
                ]
                report = context_benchmark.measure_context_pack(
                    {"files": records, "snippets": []},
                    required_paths=required_paths,
                    required_symbols={"src/worker.py": ["worker"]},
                )
                self.assertIn(f"path:{missing}", report["omissions"])
                self.assertLess(report["relevant_surface_recall"], 1.0)
                with self.assertRaisesRegex(context_benchmark.BenchmarkRegressionError, f"path:{missing}"):
                    context_benchmark._require_safe_report(report)

    def test_invalid_and_excluded_fixture_paths_fail_before_measurement(self):
        for path in ("/absolute.py", "../parent.py", "src\\windows.py", "C:drive.py", "control\x01.py"):
            with self.subTest(path=path):
                with self.assertRaises(context_benchmark.BenchmarkError):
                    context_benchmark._safe_relative_path(path)
        with self.assertRaises(context_benchmark.BenchmarkError):
            context_benchmark._fixture_file(FIXTURE / "repo", "missing.py")
        case = json.loads((FIXTURE / "case.json").read_text(encoding="utf-8"))
        case["policy"]["exclude_globs"].append("processor.py")
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory)
            (copied / "repo").mkdir()
            for source in (FIXTURE / "repo").rglob("*"):
                target = copied / "repo" / source.relative_to(FIXTURE / "repo")
                if source.is_dir():
                    target.mkdir(exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(source.read_bytes())
            (copied / "case.json").write_text(json.dumps(case), encoding="utf-8")
            with self.assertRaisesRegex(context_benchmark.BenchmarkError, "excluded by policy"):
                context_benchmark.run_fixture(copied)

    def test_duplicate_and_nonstring_path_or_symbol_declarations_fail_closed(self):
        with self.assertRaises(context_benchmark.BenchmarkError):
            context_benchmark._required_paths(["a.py", "a.py"])
        with self.assertRaises(context_benchmark.BenchmarkError):
            context_benchmark._required_symbols({"src/worker.py": ["worker", "worker"]})
        with self.assertRaises(context_benchmark.BenchmarkError):
            context_benchmark._required_symbols({"src/worker.py": [3]})

    def test_fixture_declared_symbol_must_exist_in_fixture_source(self):
        case = json.loads((FIXTURE / "case.json").read_text(encoding="utf-8"))
        case["required_symbols"] = {"src/processor.py": ["Processor.not_declared"]}
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory)
            shutil.copytree(FIXTURE / "repo", copied / "repo")
            (copied / "case.json").write_text(json.dumps(case), encoding="utf-8")
            with self.assertRaisesRegex(context_benchmark.BenchmarkError, "required symbol not found"):
                context_benchmark.run_fixture(copied)

    def test_escaped_resolved_repo_is_rejected_before_compiler_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "fixture"
            escaped_repo = Path(directory) / "escaped-repo"
            shutil.copytree(FIXTURE, fixture)
            shutil.copytree(FIXTURE / "repo", escaped_repo)
            original_resolve = Path.resolve

            def escape_repo(path, *args, **kwargs):
                if path == fixture / "repo":
                    return original_resolve(escaped_repo, *args, **kwargs)
                return original_resolve(path, *args, **kwargs)

            with patch.object(Path, "resolve", new=escape_repo):
                with self.assertRaisesRegex(context_benchmark.BenchmarkError, "strict descendant"):
                    context_benchmark.run_fixture(fixture)

    def test_fixture_and_repo_symlinks_are_rejected_when_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            linked_fixture = Path(directory) / "fixture-link"
            try:
                linked_fixture.symlink_to(FIXTURE, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlink creation unavailable: {exc}")
            with self.assertRaisesRegex(context_benchmark.BenchmarkError, "symlink or reparse"):
                context_benchmark.run_fixture(linked_fixture)
            repo_link_fixture = Path(directory) / "repo-link-fixture"
            repo_link_fixture.mkdir(exist_ok=True)
            shutil.copy2(FIXTURE / "case.json", repo_link_fixture / "case.json")
            try:
                (repo_link_fixture / "repo").symlink_to(FIXTURE / "repo", target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"repo symlink creation unavailable: {exc}")
            with self.assertRaisesRegex(context_benchmark.BenchmarkError, "symlink or reparse"):
                context_benchmark.run_fixture(repo_link_fixture)

    def test_reparse_point_is_rejected_before_resolution(self):
        class ReparseMetadata:
            st_mode = stat.S_IFDIR
            st_file_attributes = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

        with patch.object(Path, "lstat", return_value=ReparseMetadata()):
            with self.assertRaisesRegex(context_benchmark.BenchmarkError, "symlink or reparse"):
                context_benchmark._admitted_directory(FIXTURE, "fixture directory")

    def test_mocked_link_metadata_rejects_fixture_and_repo_roots(self):
        class LinkMetadata:
            st_mode = stat.S_IFLNK
            st_file_attributes = 0

        original_lstat = Path.lstat
        for target in (FIXTURE, FIXTURE / "repo"):
            with self.subTest(target=target):
                def linked_lstat(path, *args, **kwargs):
                    if path == target:
                        return LinkMetadata()
                    return original_lstat(path, *args, **kwargs)

                with patch.object(Path, "lstat", new=linked_lstat):
                    with self.assertRaisesRegex(context_benchmark.BenchmarkError, "symlink or reparse"):
                        context_benchmark.run_fixture(FIXTURE)

    def test_mocked_link_and_reparse_case_json_are_rejected_before_read(self):
        class LinkMetadata:
            st_mode = stat.S_IFLNK
            st_file_attributes = 0

        class ReparseMetadata:
            st_mode = stat.S_IFREG
            st_file_attributes = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

        original_lstat = Path.lstat
        for metadata in (LinkMetadata(), ReparseMetadata()):
            with self.subTest(metadata=type(metadata).__name__):
                def linked_lstat(path, *args, **kwargs):
                    if path == FIXTURE / "case.json":
                        return metadata
                    return original_lstat(path, *args, **kwargs)

                with patch.object(Path, "lstat", new=linked_lstat):
                    with self.assertRaisesRegex(context_benchmark.BenchmarkError, "symlink or reparse"):
                        context_benchmark.run_fixture(FIXTURE)

    def test_explicit_task_file_size_cap_rejects_one_over_and_accepts_exact_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory)
            shutil.copytree(FIXTURE / "repo", copied / "repo")
            case = json.loads((FIXTURE / "case.json").read_text(encoding="utf-8"))
            explicit_sizes = [
                (copied / "repo" / path).stat().st_size
                for path in case["task"]["files"]
            ]
            cap = max(explicit_sizes)
            case["policy"]["max_file_bytes"] = cap - 1
            (copied / "case.json").write_text(json.dumps(case), encoding="utf-8")
            with self.assertRaisesRegex(context_benchmark.BenchmarkError, "exceeds policy max_file_bytes"):
                context_benchmark._load_fixture(copied)
            case["policy"]["max_file_bytes"] = cap
            (copied / "case.json").write_text(json.dumps(case), encoding="utf-8")
            context_benchmark._load_fixture(copied)

    def test_multi_symbol_partial_snippets_retain_file_and_fail_symbol_recall(self):
        pack = {
            "files": [
                {"path": "src/worker.py", "estimated_tokens": 50},
                {"path": "tests/test_worker.py", "estimated_tokens": 10},
            ],
            "snippets": [
                {"path": "src/worker.py", "symbol": "first", "start_line": 1, "estimated_tokens": 8, "fallback": False},
            ],
        }
        report = context_benchmark.measure_context_pack(
            pack,
            required_paths=["src/worker.py", "tests/test_worker.py"],
            required_symbols={"src/worker.py": ["first", "second"]},
        )
        self.assertEqual(report["candidate_tokens"], 60)
        self.assertIn("symbol:src/worker.py:second", report["omissions"])
        self.assertLess(report["relevant_surface_recall"], 1.0)
        with self.assertRaises(context_benchmark.BenchmarkRegressionError):
            context_benchmark._require_safe_report(report)

    def test_controlled_nondeterminism_fails_closed(self):
        first = {
            "files": [
                {"path": "AGENTS.md", "estimated_tokens": 5},
                {"path": "AI_POLICY.md", "estimated_tokens": 5},
                {"path": "harness/manifest.yaml", "estimated_tokens": 5},
                {"path": "src/processor.py", "estimated_tokens": 40},
                {"path": "tests/test_processor.py", "estimated_tokens": 10},
            ],
            "snippets": [{"path": "src/processor.py", "symbol": "Processor.process", "start_line": 1, "estimated_tokens": 8, "fallback": False}],
        }
        second = json.loads(json.dumps(first))
        second["snippets"][0]["estimated_tokens"] = 9
        with patch.object(context_benchmark, "_compile_fixture", side_effect=[first, second]):
            with self.assertRaises(context_benchmark.BenchmarkRegressionError) as raised:
                context_benchmark.run_fixture(FIXTURE)
        self.assertEqual(raised.exception.report["determinism"], {"runs": 2, "status": "FAIL"})

    def test_cli_reports_success_and_omitted_context_as_json(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(context_benchmark.main(["--fixture", str(FIXTURE)]), 0)
        self.assertEqual(json.loads(stdout.getvalue())["determinism"]["status"], "PASS")
        omitted = {
            "files": [
                {"path": "AGENTS.md", "estimated_tokens": 5},
                {"path": "AI_POLICY.md", "estimated_tokens": 5},
                {"path": "harness/manifest.yaml", "estimated_tokens": 5},
                {"path": "src/processor.py", "estimated_tokens": 40},
            ],
            "snippets": [{"path": "src/processor.py", "symbol": "Processor.process", "start_line": 1, "estimated_tokens": 8, "fallback": False}],
        }
        stderr = io.StringIO()
        with patch.object(context_benchmark, "_compile_fixture", return_value=omitted), contextlib.redirect_stderr(stderr):
            self.assertEqual(context_benchmark.main(["--fixture", str(FIXTURE)]), 2)
        failure = json.loads(stderr.getvalue())
        self.assertEqual(failure["status"], "FAIL")
        self.assertIn("path:tests/test_processor.py", failure["report"]["omissions"])

    def test_cli_invalid_fixture_emits_parseable_error_json(self):
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stderr(stderr):
            self.assertEqual(context_benchmark.main(["--fixture", str(Path(directory) / "missing")]), 2)
        failure = json.loads(stderr.getvalue())
        self.assertEqual(failure["status"], "FAIL")
        self.assertIsInstance(failure["error"], str)

    def test_fixture_run_does_not_use_network_process_clock_or_randomness(self):
        def forbidden(*_args, **_kwargs):
            raise AssertionError("ambient host hook reached")

        with patch.object(socket, "socket", side_effect=forbidden), \
             patch.object(subprocess, "run", side_effect=forbidden), \
             patch.object(time, "time", side_effect=forbidden), \
             patch.object(random, "random", side_effect=forbidden):
            report = context_benchmark.run_fixture(FIXTURE)
        self.assertEqual(report["determinism"]["status"], "PASS")

    def test_rollout_contract_and_reproduction_docs_keep_file_delivery_default(self):
        policy = json.loads((ROOT / "harness" / "context-policy.json").read_text(encoding="utf-8"))
        rollout = policy["snippet_rollout"]
        self.assertEqual(rollout["default_delivery"], "file-level")
        self.assertFalse(rollout["automatic_enable"])
        self.assertTrue(rollout["human_approval_required"])
        self.assertEqual(rollout["benchmark_requirements"], {
            "deterministic": True,
            "minimum_relevant_surface_recall": 1.0,
            "maximum_omissions": 0,
        })
        documentation = (ROOT / "codegraph.md").read_text(encoding="utf-8")
        for expected in (
            "scripts/context_benchmark.py --fixture tests/fixtures/context_benchmark",
            "baseline_tokens",
            "candidate_tokens",
            "non-fallback",
            "human approval",
            "file-level",
        ):
            self.assertIn(expected, documentation)


if __name__ == "__main__":
    unittest.main()
