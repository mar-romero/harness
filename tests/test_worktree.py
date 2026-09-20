import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import harnesslib
import task_checks
import worktree


class WorktreeLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self._git("init", "-b", "main")
        self._git("config", "user.email", "harness@test.invalid")
        self._git("config", "user.name", "Harness Test")
        self._git("config", "core.logAllRefUpdates", "true")
        (self.repo / "README.md").write_text("base\n", encoding="utf-8")
        self._git("add", "README.md")
        self._git("commit", "-m", "base")

        self.old_worktree_root = worktree.ROOT
        self.old_harnesslib_root = harnesslib.ROOT
        worktree.ROOT = self.repo
        harnesslib.ROOT = self.repo
        self.task = "CODEGRAPH-TEST-001"

    def tearDown(self):
        worktree.ROOT = self.old_worktree_root
        harnesslib.ROOT = self.old_harnesslib_root
        self.tmp.cleanup()

    def _git(self, *args, cwd=None):
        return subprocess.run(
            ["git", *args],
            cwd=cwd or self.repo,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=True,
        )

    def _read_lock(self, task=None):
        return json.loads(worktree.lock(task or self.task).read_text(encoding="utf-8"))

    def _write_lock(self, data, task=None):
        path = worktree.lock(task or self.task)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_create_establishes_valid_bound_lock_and_status(self):
        created = worktree.create(self.task, execute=True)

        self.assertEqual(created["branch"], f"agent/{self.task}")
        self.assertTrue((self.repo / ".harness" / "locks" / f"{self.task}.json").is_file())

        state = worktree.status(self.task)
        self.assertTrue(state["exists"], state)
        self.assertTrue(state["lock"], state)
        self.assertTrue(state["lock_valid"], state)
        self.assertEqual(state["branch"], f"agent/{self.task}")
        self.assertEqual(state["integration_branch"], "main")

    def test_lock_records_and_verifies_resolved_git_identity(self):
        worktree.create(self.task, execute=True)
        lock = self._read_lock()
        for key in ("worktree_root", "git_common_dir", "git_dir", "worktree_id"):
            self.assertTrue(lock.get(key), key)
        lock["worktree_id"] = "0" * 64
        self._write_lock(lock)
        state = worktree.status(self.task)
        self.assertFalse(state["lock_valid"], state)
        self.assertIn("worktree identity mismatch", state["lock_reason"])

    def test_reparse_worktree_path_is_rejected_before_git_access(self):
        outside = self.repo.parent / "outside-worktree"
        outside.mkdir(exist_ok=True)
        link = self.repo / ".worktrees" / self.task
        link.parent.mkdir(parents=True)
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink unavailable: {exc}")
        with self.assertRaisesRegex(ValueError, "reparse|symlink"):
            worktree.create(self.task, execute=True)

    def test_common_lock_namespace_is_visible_from_linked_worktree(self):
        created = worktree.create(self.task, execute=True)
        linked_root = Path(created["worktree"])

        worktree.ROOT = linked_root
        harnesslib.ROOT = linked_root
        state = worktree.status(self.task)

        self.assertTrue(state["lock"], state)
        self.assertEqual(worktree.lock(self.task), self.repo / ".harness" / "locks" / f"{self.task}.json")
        self.assertEqual(worktree.wt(self.task), self.repo / ".worktrees" / self.task)

    def test_malformed_lock_is_not_authorization_for_gate_consumers(self):
        worktree.create(self.task, execute=True)
        worktree.lock(self.task).write_text("{not json", encoding="utf-8")

        state = worktree.status(self.task)
        self.assertFalse(state["lock"], state)
        self.assertFalse(state["lock_valid"], state)
        self.assertIn("writer lock is invalid", state["lock_reason"])

        with self.assertRaisesRegex(ValueError, "writer lock does not exist"):
            task_checks._execution_root(self.task, {"isolation": "worktree"})

    def test_lock_mismatches_are_rejected(self):
        worktree.create(self.task, execute=True)
        valid = self._read_lock()

        cases = [
            ("task_id", "OTHER-TASK-001", "task_id mismatch"),
            ("worktree", str(self.repo / ".worktrees" / "OTHER-TASK-001"), "worktree path mismatch"),
            ("branch", "agent/OTHER-TASK-001", "branch mismatch"),
            ("integration_branch", "missing/branch", "integration_branch is not a local branch"),
            ("base_commit", "0" * 40, "base_commit is not a commit"),
        ]

        for key, value, reason in cases:
            with self.subTest(key=key):
                mutated = dict(valid)
                mutated[key] = value
                self._write_lock(mutated)

                state = worktree.status(self.task)
                self.assertFalse(state["lock"], state)
                self.assertIn(reason, state["lock_reason"])

    def test_recover_adopts_registered_benchmark_shaped_worktree_with_reflog_provenance(self):
        task = "CODEGRAPH-BENCHMARK-001"
        self._git("branch", "feat/codegraph")
        base = self._git("rev-parse", "feat/codegraph").stdout.strip()
        path = self.repo / ".worktrees" / task
        path.parent.mkdir(parents=True, exist_ok=True)
        # Create the branch before attaching the worktree so Git records the
        # authoritative creation entry that recovery verifies.  ``worktree add
        # -b`` does not reliably retain a branch reflog in temporary repos.
        self._git("branch", "--create-reflog", f"agent/{task}", "feat/codegraph")
        self._git("worktree", "add", str(path), f"agent/{task}")
        (path / "benchmark.txt").write_text("result\n", encoding="utf-8")
        self._git("add", "benchmark.txt", cwd=path)
        self._git("commit", "-m", "benchmark result", cwd=path)

        recovered = worktree.recover(task, execute=True)

        self.assertEqual(recovered["integration_branch"], "feat/codegraph")
        self.assertEqual(recovered["base_commit"], base)
        state = worktree.status(task)
        self.assertTrue(state["lock"], state)
        self.assertEqual(self._read_lock(task)["base_commit"], base)

    def test_recover_adopts_official_worktree_after_create_lock_claim_fails(self):
        task = "CREATE-FAILURE-001"

        with mock.patch.object(
            worktree,
            "_write_lock_exclusive",
            side_effect=ValueError("simulated atomic lock claim failure"),
        ):
            with self.assertRaisesRegex(SystemExit, "simulated atomic lock claim failure"):
                worktree.create(task, execute=True)

        path = worktree.wt(task)
        self.assertTrue(path.is_dir())
        self.assertTrue(worktree._worktree_registered(path))
        self.assertFalse(worktree.lock(task).exists())

        recovered = worktree.recover(task, execute=True)

        self.assertEqual(recovered["integration_branch"], "main")
        self.assertEqual(
            recovered["base_commit"],
            self._git("rev-parse", "main").stdout.strip(),
        )
        self.assertTrue(worktree.status(task)["lock"])

    def test_recover_preserves_root_integration_branch_for_explicit_base(self):
        task = "EXPLICIT-BASE-001"
        self._git("branch", "feat/alternate")
        alternate = self.repo / ".worktrees" / "alternate-source"
        self._git("worktree", "add", str(alternate), "feat/alternate")
        (alternate / "alternate.txt").write_text("alternate\n", encoding="utf-8")
        self._git("add", "alternate.txt", cwd=alternate)
        self._git("commit", "-m", "alternate base", cwd=alternate)
        self._git("worktree", "remove", "--force", str(alternate))
        base = self._git("rev-parse", "feat/alternate").stdout.strip()

        with mock.patch.object(
            worktree,
            "_write_lock_exclusive",
            side_effect=ValueError("simulated atomic lock claim failure"),
        ):
            with self.assertRaisesRegex(SystemExit, "simulated atomic lock claim failure"):
                worktree.create(task, base="feat/alternate", execute=True)

        recovered = worktree.recover(task, execute=True)

        self.assertEqual(recovered["integration_branch"], "main")
        self.assertEqual(recovered["base_commit"], base)
        metadata = self._read_lock(task)
        self.assertEqual(metadata["integration_branch"], "main")
        self.assertEqual(metadata["base_commit"], base)

    def test_recover_after_create_lock_claim_failure_does_not_overwrite_lock(self):
        task = "CREATE-FAILURE-LOCKED-001"

        with mock.patch.object(
            worktree,
            "_write_lock_exclusive",
            side_effect=ValueError("simulated atomic lock claim failure"),
        ):
            with self.assertRaisesRegex(SystemExit, "simulated atomic lock claim failure"):
                worktree.create(task, execute=True)

        lock = worktree.lock(task)
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text('{"another_writer": true}\n', encoding="utf-8")
        before = lock.read_text(encoding="utf-8")

        with self.assertRaisesRegex(SystemExit, "writer lock already exists"):
            worktree.recover(task, execute=True)

        self.assertEqual(lock.read_text(encoding="utf-8"), before)

    def test_recover_refuses_ambiguous_head_created_provenance(self):
        task = "RECOVER-HEAD-001"
        path = self.repo / ".worktrees" / task
        path.parent.mkdir(parents=True, exist_ok=True)
        # This is a real reflog entry, but it deliberately identifies HEAD
        # rather than a local integration branch and must remain unrecoverable.
        self._git("branch", "--create-reflog", f"agent/{task}", "HEAD")
        self._git("worktree", "add", str(path), f"agent/{task}")

        with self.assertRaisesRegex(SystemExit, "ambiguous"):
            worktree.recover(task, execute=True)

        self.assertFalse(worktree.lock(task).exists())

    def test_second_exclusive_claim_fails_without_replacing_lock(self):
        worktree.create(self.task, execute=True)
        before = worktree.lock(self.task).read_text(encoding="utf-8")

        with self.assertRaisesRegex(SystemExit, "writer lock already exists"):
            worktree.recover(self.task, execute=True)

        after = worktree.lock(self.task).read_text(encoding="utf-8")
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
