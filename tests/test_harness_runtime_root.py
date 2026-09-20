import subprocess
import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "scripts"))

import harnesslib
from task_checks import _isolated_child_env, _safe_env


class RuntimeRootTests(unittest.TestCase):
    def _git(self, cwd, *args):
        return subprocess.run(
            ["git", *args], cwd=cwd, text=True, encoding="utf-8", errors="replace",
            capture_output=True, check=True
        )

    def _repo_with_linked_worktree(self, base: Path):
        primary = base / "primary"
        linked = base / "linked"
        primary.mkdir()
        self._git(primary, "init", "-b", "main")
        self._git(primary, "config", "user.email", "harness@test.invalid")
        self._git(primary, "config", "user.name", "Harness Test")
        (primary / "README.md").write_text("base\n", encoding="utf-8")
        self._git(primary, "add", "README.md")
        self._git(primary, "commit", "-m", "base")
        self._git(primary, "worktree", "add", "-b", "linked", str(linked))
        return primary, linked

    def test_primary_and_linked_worktrees_share_runtime_and_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            primary, linked = self._repo_with_linked_worktree(Path(td))
            with patch.object(harnesslib, "ROOT", primary):
                primary_root = harnesslib.runtime_root()
                primary_run = harnesslib.run_dir("T-runtime")
                primary_run.mkdir(parents=True)
                artifact = primary_run / "evidence.jsonl"
                artifact.write_text('{"from":"primary"}\n', encoding="utf-8")
            with patch.object(harnesslib, "ROOT", linked):
                self.assertEqual(harnesslib.runtime_root(), primary_root)
                linked_run = harnesslib.run_dir("T-runtime")
                self.assertEqual(linked_run, primary_run)
                self.assertEqual(artifact.read_text(encoding="utf-8"), '{"from":"primary"}\n')

    def test_single_worktree_runtime_root_remains_checkout_root(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            self._git(repo, "init", "-b", "main")
            with patch.object(harnesslib, "ROOT", repo):
                self.assertEqual(harnesslib.runtime_root(), repo.resolve())
                self.assertEqual(
                    harnesslib.run_dir("T-runtime"),
                    repo.resolve() / ".harness" / "runs" / "T-runtime",
                )

    def test_unavailable_empty_or_invalid_common_directory_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.object(harnesslib, "ROOT", root), patch.object(harnesslib, "git") as git:
                git.return_value.returncode = 1
                git.return_value.stdout = ""
                with self.assertRaisesRegex(ValueError, "common git directory"):
                    harnesslib.runtime_root()
                git.return_value.returncode = 0
                git.return_value.stdout = "   \n"
                with self.assertRaisesRegex(ValueError, "empty"):
                    harnesslib.runtime_root()
                git.return_value.stdout = "missing/.git\n"
                with self.assertRaisesRegex(ValueError, "invalid"):
                    harnesslib.runtime_root()

    def test_fixture_seam_cannot_redirect_outside_current_fixture_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "fixture"
            root.mkdir()
            outside = Path(td) / "outside"
            outside.mkdir()
            with patch.object(harnesslib, "ROOT", root), patch.dict(
                os.environ,
                {"HARNESS_FIXTURE_SEAM": "1", "HARNESS_FIXTURE_RUNTIME_ROOT": str(outside)},
                clear=False,
            ):
                with self.assertRaisesRegex(ValueError, "fixture runtime root is invalid"):
                    harnesslib.runtime_root()

    def test_task_check_environment_uses_synthetic_windows_profile(self):
        env = _safe_env(ROOT)
        with _isolated_child_env(env) as child:
            if os.name == "nt":
                self.assertNotEqual(child.get("USERPROFILE"), os.environ.get("USERPROFILE"))
                self.assertNotEqual(child.get("APPDATA"), os.environ.get("APPDATA"))
                self.assertEqual(child.get("GIT_CONFIG_NOSYSTEM"), "1")
                self.assertEqual(child.get("GIT_CONFIG_GLOBAL"), os.devnull)
            else:
                self.assertIs(child, env)
                self.assertNotIn("USERPROFILE", child)
                self.assertNotIn("APPDATA", child)
                self.assertNotIn("GIT_CONFIG_NOSYSTEM", child)


if __name__ == "__main__":
    unittest.main()
