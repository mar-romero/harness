import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import harnesslib
import worktree


class WorktreePublishTests(unittest.TestCase):
    def test_cleanup_recovers_when_git_remove_unregisters_but_leaves_residual_directory(self):
        self._runtime(["src/app.py"])
        created = worktree.create(self.task, execute=True)
        path = Path(created["worktree"])

        (path / "src").mkdir(parents=True)
        (path / "src/app.py").write_text("VALUE = 1\n", encoding="utf-8")

        with patch(
            "gate.finish_decision",
            return_value={"allow": True, "missing": [], "failing": []},
        ):
            original_git = worktree.git

            def flaky_git(*args, **kwargs):
                if args[:2] == ("worktree", "remove"):
                    # Simulate Windows behavior observed in production:
                    # Git successfully unregisters/removes the worktree, but a
                    # residual directory remains and the command reports failure.
                    original_git(*args, **kwargs)

                    path.mkdir(parents=True, exist_ok=True)
                    (path / "residual.tmp").write_text(
                        "cleanup residue\n",
                        encoding="utf-8",
                    )

                    raise subprocess.CalledProcessError(
                        255,
                        ["git", *args],
                    )

                return original_git(*args, **kwargs)

            with patch.object(worktree, "git", side_effect=flaky_git):
                result = worktree.publish(self.task, execute=True)

        self.assertEqual(result["status"], "PASS")
        # Git unregistered the original worktree, but the test deliberately
        # recreated the path with a different identity after unregistering.
        # The migration must preserve that replacement rather than risk
        # deleting an unrelated directory.
        self.assertTrue(path.exists())
        self.assertEqual((path / "residual.tmp").read_text(encoding="utf-8"), "cleanup residue\n")
        self.assertFalse(worktree.lock(self.task).exists())

        listed = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=self.repo,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=True,
        ).stdout

        self.assertNotIn(str(path), listed)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        subprocess.run(['git', 'init', '-b', 'main'], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(['git', 'config', 'user.email', 'harness@test.invalid'], cwd=self.repo, check=True)
        subprocess.run(['git', 'config', 'user.name', 'Harness Test'], cwd=self.repo, check=True)
        (self.repo / 'README.md').write_text('base\n', encoding='utf-8')
        shutil.copytree(ROOT / 'harness', self.repo / 'harness')
        subprocess.run(['git', 'add', 'README.md'], cwd=self.repo, check=True)
        subprocess.run(['git', 'commit', '-m', 'base'], cwd=self.repo, check=True, capture_output=True)

        self.old_worktree_root = worktree.ROOT
        self.old_harnesslib_root = harnesslib.ROOT
        worktree.ROOT = self.repo
        harnesslib.ROOT = self.repo
        self.task = 'T-publish'

    def tearDown(self):
        worktree.ROOT = self.old_worktree_root
        harnesslib.ROOT = self.old_harnesslib_root
        self.tmp.cleanup()

    def _runtime(self, files):
        rd = harnesslib.run_dir(self.task)
        rd.mkdir(parents=True, exist_ok=True)
        (rd / 'route.json').write_text(json.dumps({
            'task_id': self.task,
            'risk': 'R1',
            'agents': ['implementer', 'reviewer'],
            'human_gate': False,
            'isolation': 'worktree',
            'tdd': {},
        }), encoding='utf-8')
        (rd / 'progress.json').write_text(json.dumps({
            'task_id': self.task,
            'risk': 'R1',
            'state': 'RUNNING',
            'current_step': 'CLOSE',
            'steps': ['WORKTREE', 'IMPLEMENT', 'CHECKS', 'REVIEW', 'CLOSE'],
            'completed': ['WORKTREE', 'IMPLEMENT', 'CHECKS', 'REVIEW'],
        }), encoding='utf-8')
        (rd / 'task.json').write_text(json.dumps({
            'id': self.task,
            'files': files,
        }), encoding='utf-8')

    def test_publish_commits_fast_forwards_and_cleans_up(self):
        self._runtime(['src/app.py'])
        created = worktree.create(self.task, execute=True)
        path = Path(created['worktree'])
        (path / 'src').mkdir(parents=True)
        (path / 'src/app.py').write_text('VALUE = 1\n', encoding='utf-8')
        base = created['base_commit']

        with patch('gate.finish_decision', return_value={'allow': True, 'missing': [], 'failing': []}):
            result = worktree.publish(self.task, execute=True)

        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['changed_files'], ['src/app.py'])
        self.assertTrue((self.repo / 'src/app.py').is_file())
        self.assertFalse(path.exists())
        self.assertFalse(worktree.lock(self.task).exists())
        head = subprocess.run(
            ['git', 'rev-parse', 'HEAD'], cwd=self.repo, text=True,
            encoding='utf-8', errors='replace', capture_output=True, check=True,
        ).stdout.strip()
        self.assertNotEqual(head, base)
        status = worktree.publish_status(self.task)
        self.assertTrue(status['published'], status)

    def test_publish_excludes_upstream_merge_from_task_surface(self):
        self._runtime(['src/app.py'])
        created = worktree.create(self.task, execute=True)
        path = Path(created['worktree'])
        (self.repo / 'upstream.txt').write_text('upstream\n', encoding='utf-8')
        subprocess.run(['git', 'add', 'upstream.txt'], cwd=self.repo, check=True)
        subprocess.run(['git', 'commit', '-m', 'upstream'], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(['git', 'merge', '--no-edit', 'main'], cwd=path, check=True, capture_output=True)
        (path / 'src').mkdir(parents=True)
        (path / 'src/app.py').write_text('VALUE = 1\n', encoding='utf-8')

        with patch('gate.finish_decision', return_value={'allow': True, 'missing': [], 'failing': []}):
            result = worktree.publish(self.task, execute=True)

        self.assertEqual(result['changed_files'], ['src/app.py'])
        self.assertTrue((self.repo / 'upstream.txt').is_file())

        # A later canonical commit must not invalidate the already-integrated publication.
        (self.repo / 'later.txt').write_text('later\n', encoding='utf-8')
        subprocess.run(['git', 'add', 'later.txt'], cwd=self.repo, check=True)
        subprocess.run(['git', 'commit', '-m', 'later'], cwd=self.repo, check=True, capture_output=True)
        status = worktree.publish_status(self.task)
        self.assertTrue(status['published'], status)

    def test_publish_rejects_changes_outside_frozen_task_surface(self):
        self._runtime(['src/app.py'])
        created = worktree.create(self.task, execute=True)
        path = Path(created['worktree'])
        (path / 'src').mkdir(parents=True)
        (path / 'src/app.py').write_text('VALUE = 1\n', encoding='utf-8')
        (path / 'rogue.txt').write_text('not authorized\n', encoding='utf-8')

        with patch('gate.finish_decision', return_value={'allow': True, 'missing': [], 'failing': []}):
            with self.assertRaisesRegex(ValueError, 'outside task.files'):
                worktree.publish(self.task, execute=True)

        self.assertFalse((self.repo / 'src/app.py').exists())
        self.assertTrue(path.exists())
        self.assertTrue(worktree.lock(self.task).exists())

    def test_publish_rejects_active_codex_model_binding(self):
        self._runtime(['src/app.py'])
        active = harnesslib.provider_active_path('codex', self.repo)
        active.parent.mkdir(parents=True, exist_ok=True)
        run = harnesslib.run_dir(self.task)
        for name in ('context.json', 'impact.json', 'agent-budget.json'):
            (run / name).write_text(json.dumps({'task_id': self.task}), encoding='utf-8')
        model_path = harnesslib.provider_model_selections_path('codex', self.repo)
        model_path.parent.mkdir(parents=True, exist_ok=True)
        model_path.write_text(json.dumps({
            'schema_version': 2,
            'task_id': self.task,
            'provider': 'codex',
            'inventory_path': None,
            'inventory_sha256': None,
            'selections': [],
        }), encoding='utf-8')
        active.write_text(json.dumps({
            'schema_version': 3, 'provider': 'codex', 'task_id': self.task,
            'overlay': harnesslib.worktree_identity(self.repo),
            'task_path': f'tasks/{self.task}.json',
            'route_path': f'.harness/runs/{self.task}/route.json',
            'context_path': f'.harness/runs/{self.task}/context.json',
            'progress_path': f'.harness/runs/{self.task}/progress.json',
            'task_snapshot_path': f'.harness/runs/{self.task}/task.json',
            'impact_path': f'.harness/runs/{self.task}/impact.json',
            'agent_budget_path': f'.harness/runs/{self.task}/agent-budget.json',
            'model_selections_path': model_path.relative_to(self.repo).as_posix(),
            'model_selections_sha256': harnesslib.sha256_file(model_path),
            'selections': [],
        }), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Codex task binding is still active'):
            worktree._publication_preconditions(self.task)

    def test_publish_rejects_rename_that_deletes_file_outside_surface(self):
        # Seed a tracked file before creating the task worktree. The task only
        # authorizes the destination path; --no-renames must still expose the
        # unauthorized deletion of the original path.
        (self.repo / 'legacy.py').write_text('OLD = 1\n', encoding='utf-8')
        subprocess.run(['git', 'add', 'legacy.py'], cwd=self.repo, check=True)
        subprocess.run(['git', 'commit', '-m', 'legacy'], cwd=self.repo, check=True, capture_output=True)

        self._runtime(['src/app.py'])
        created = worktree.create(self.task, execute=True)
        path = Path(created['worktree'])
        (path / 'src').mkdir(parents=True)
        (path / 'legacy.py').rename(path / 'src/app.py')

        with patch('gate.finish_decision', return_value={'allow': True, 'missing': [], 'failing': []}):
            with self.assertRaisesRegex(ValueError, 'outside task.files'):
                worktree.publish(self.task, execute=True)

        self.assertTrue((self.repo / 'legacy.py').is_file())
        self.assertFalse((self.repo / 'src/app.py').exists())

    def test_publish_accepts_immutable_candidate_bound_scope_expansion(self):
        self._runtime(['src/app.py'])
        created = worktree.create(self.task, execute=True)
        path = Path(created['worktree'])
        (path / 'src').mkdir(parents=True)
        (path / 'src/app.py').write_text('VALUE = 1\n', encoding='utf-8')
        (path / 'tests').mkdir(parents=True)
        (path / 'tests/test_finish_and_memory.py').write_text('fixture\n', encoding='utf-8')

        import receipt_review
        candidate = receipt_review.candidate_snapshot(self.task)
        expansion = {
            'schema_version': 2,
            'task_id': self.task,
            'status': 'APPROVED',
            'approved_by': 'verifier',
            'reason': 'reviewed hardening coverage',
            'base_commit': created['base_commit'],
            'candidate_subject_hash': candidate['subject_hash'],
            'expanded_files': ['tests/test_finish_and_memory.py'],
            'changed_files': ['tests/test_finish_and_memory.py'],
        }
        expansion['document_sha256'] = hashlib.sha256(
            json.dumps(expansion, sort_keys=True, separators=(',', ':')).encode()
        ).hexdigest()
        expansion_path = harnesslib.run_dir(self.task) / 'scope-expansion.json'
        expansion_path.write_text(json.dumps(expansion), encoding='utf-8')

        with patch('gate.finish_decision', return_value={'allow': True, 'missing': [], 'failing': []}):
            result = worktree.publish(self.task, execute=True)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['changed_files'], ['src/app.py', 'tests/test_finish_and_memory.py'])

    def test_scope_expansion_mutation_and_secret_paths_are_rejected(self):
        self._runtime(['src/app.py'])
        created = worktree.create(self.task, execute=True)
        path = Path(created['worktree'])
        (path / 'src').mkdir(parents=True)
        (path / 'src/app.py').write_text('VALUE = 1\n', encoding='utf-8')
        import receipt_review
        candidate = receipt_review.candidate_snapshot(self.task)
        expansion = {
            'schema_version': 2, 'task_id': self.task, 'status': 'APPROVED',
            'approved_by': 'verifier', 'reason': 'fixture',
            'base_commit': created['base_commit'],
            'candidate_subject_hash': candidate['subject_hash'],
            'expanded_files': ['secrets.pem'], 'changed_files': ['secrets.pem'],
        }
        expansion['document_sha256'] = hashlib.sha256(
            json.dumps(expansion, sort_keys=True, separators=(',', ':')).encode()
        ).hexdigest()
        expansion_path = harnesslib.run_dir(self.task) / 'scope-expansion.json'
        expansion_path.write_text(json.dumps(expansion), encoding='utf-8')
        with patch('gate.finish_decision', return_value={'allow': True, 'missing': [], 'failing': []}):
            with self.assertRaisesRegex(ValueError, 'protected or secret|scope-expansion'):
                worktree.publish(self.task, execute=True)


if __name__ == '__main__':
    unittest.main()
