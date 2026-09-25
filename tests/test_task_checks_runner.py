import sys
import tempfile
import unittest
import json
import threading
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import task_checks


class TaskChecksRunnerTests(unittest.TestCase):
    def test_safe_env_pins_aci_python_without_forwarding_profile_variables(self):
        project = Path('project')
        for inherited_python in (None, 'untrusted-python'):
            with self.subTest(inherited_python=inherited_python):
                inherited = {
                    'PATH': 'path-without-python',
                    'USERPROFILE': 'private-profile',
                    'APPDATA': 'private-appdata',
                    'LOCALAPPDATA': 'private-localappdata',
                }
                if inherited_python is not None:
                    inherited['HARNESS_ACI_PYTHON'] = inherited_python
                with patch.dict(task_checks.os.environ, inherited, clear=True):
                    env = task_checks._safe_env(project)
                self.assertEqual(env, {
                    'PATH': 'path-without-python',
                    'PYTHONPATH': str(project / 'src'),
                    'PYTHONDONTWRITEBYTECODE': '1',
                    'HARNESS_ACI_PYTHON': sys.executable,
                    'GIT_CONFIG_NOSYSTEM': '1',
                    'GIT_CONFIG_GLOBAL': task_checks.os.devnull,
                    'GIT_CONFIG_SYSTEM': task_checks.os.devnull,
                    'GIT_CONFIG_COUNT': '2',
                    'GIT_CONFIG_KEY_0': 'safe.directory',
                    'GIT_CONFIG_VALUE_0': str(project.resolve()),
                    'GIT_CONFIG_KEY_1': 'safe.directory',
                    'GIT_CONFIG_VALUE_1': str(project.resolve()),
                })

    def test_safe_env_disables_python_bytecode(self):
        with tempfile.TemporaryDirectory() as td:
            env = task_checks._safe_env(Path(td))
        self.assertEqual(env['PYTHONDONTWRITEBYTECODE'], '1')

    def test_syntax_check_does_not_create_pycache(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / 'src'
            src.mkdir()
            (src / 'good.py').write_text('VALUE = 1\n', encoding='utf-8')
            result = task_checks._python_syntax_check(src)
            self.assertEqual(result['status'], 'PASS')
            self.assertEqual(result['files_checked'], 1)
            self.assertFalse(any(src.rglob('__pycache__')))

    def test_project_root_accepts_root_relative_tests_path(self):
        task = {
            'files': [
                'scripts/task_checks.py',
                'harness/models.json',
                'tests/test_model_router.py',
            ],
        }
        self.assertEqual(task_checks._project_root(task), Path('.'))

    def test_codex_binding_uses_immutable_snapshot(self):
        with tempfile.TemporaryDirectory() as td, patch.object(task_checks, 'ROOT', Path(td)):
            root = Path(td)
            snapshot = root / '.harness/runs/T/task.json'
            snapshot.parent.mkdir(parents=True)
            snapshot.write_text(json.dumps({'id': 'T', 'files': ['src/a.py']}), encoding='utf-8')
            active = {
                'task_id': 'T',
                'task_snapshot_path': '.harness/runs/T/task.json',
            }
            with patch.object(task_checks, 'read_provider_active', side_effect=[active, None, None]), \
                 patch.object(task_checks, 'runtime_reference', return_value=snapshot):
                task, path = task_checks._load_active_task('T')
        self.assertEqual(task['id'], 'T')
        self.assertEqual(path.name, 'task.json')

    def test_runtime_consistency_rejects_stale_agent_budget_risk(self):
        with tempfile.TemporaryDirectory() as td, patch.object(task_checks, 'ROOT', Path(td)), \
             patch.object(task_checks, 'run_dir', return_value=Path(td) / '.harness/runs/T'):
            run = Path(td) / '.harness/runs/T'
            run.mkdir(parents=True)
            (run / 'agent-budget.json').write_text(json.dumps({'risk': 'R2'}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'agent budget risk R2 != route risk R3'):
                task_checks._validate_runtime_consistency('T', {'risk': 'R3'})

    def test_provider_runtime_guard_uses_shared_migration_lock(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run = root / '.harness' / 'runs' / 'HARNESS-WORKTREE-MIGRATION-001'
            with patch.object(task_checks, 'runtime_root', return_value=root), \
                 patch.object(task_checks, 'run_dir', return_value=run):
                entered = threading.Event()
                finished = threading.Event()

                def enter_guard():
                    with task_checks._provider_runtime_guard(root):
                        entered.set()
                        finished.set()

                with task_checks._provider_runtime_guard(root):
                    thread = threading.Thread(target=enter_guard, daemon=True)
                    thread.start()
                    # The guard releases the shared lock while the child test
                    # process runs so provider activation can acquire it. The
                    # cleanup phase reacquires it before restoring state.
                    self.assertTrue(entered.wait(2.0), 'second guard could not enter setup')
                thread.join(5)
                self.assertTrue(entered.is_set())
                self.assertTrue(finished.is_set())
            self.assertTrue((run / 'migration' / 'migration.lock.json').is_file())

    def test_provider_runtime_guard_restores_legacy_state_when_setup_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run = root / '.harness' / 'runs' / 'HARNESS-WORKTREE-MIGRATION-001'
            first = root / '.harness' / 'codex' / 'active-task.json'
            second = root / '.harness' / 'codex' / 'session.json'
            first.parent.mkdir(parents=True)
            first.write_text('{}', encoding='utf-8')
            second.write_text('{}', encoding='utf-8')
            original_replace = Path.replace
            state = {'moves': 0}

            def fail_on_second_move(path, target):
                if path in (first, second):
                    state['moves'] += 1
                    if state['moves'] == 2:
                        raise OSError('injected setup failure')
                return original_replace(path, target)

            with patch.object(task_checks, 'runtime_root', return_value=root), \
                 patch.object(task_checks, 'run_dir', return_value=run), \
                 patch.object(Path, 'replace', fail_on_second_move):
                with self.assertRaisesRegex(OSError, 'injected setup failure'):
                    with task_checks._provider_runtime_guard(root):
                        pass
            self.assertEqual(first.read_bytes(), b'{}')
            self.assertTrue(second.is_file())

    def test_provider_runtime_guard_restores_legacy_state_when_cleanup_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run = root / '.harness' / 'runs' / 'HARNESS-WORKTREE-MIGRATION-001'
            overlay = root / '.harness' / 'overlays'
            first = root / '.harness' / 'codex' / 'active-task.json'
            first.parent.mkdir(parents=True)
            first.write_text('{}', encoding='utf-8')
            overlay.mkdir(parents=True)
            (overlay / 'generated.json').write_text('{}', encoding='utf-8')
            with patch.object(task_checks, 'runtime_root', return_value=root), \
                 patch.object(task_checks, 'run_dir', return_value=run), \
                 patch.object(task_checks, '_move_overlay_to_recovery',
                              side_effect=OSError('injected cleanup failure')):
                with self.assertRaisesRegex(RuntimeError, 'overlay recovery move failed'):
                    with task_checks._provider_runtime_guard(root):
                        pass
            self.assertTrue(first.is_file())
            self.assertEqual((overlay / 'generated.json').read_bytes(), b'{}')

    def test_provider_runtime_guard_does_not_merge_after_cleanup_and_move_fail(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run = root / '.harness' / 'runs' / 'HARNESS-WORKTREE-MIGRATION-001'
            overlay = root / '.harness' / 'overlays'
            active = overlay / 'codex' / 'active-task.json'
            active.parent.mkdir(parents=True)
            active.write_bytes(b'original-active')
            with patch.object(task_checks, 'runtime_root', return_value=root), \
                 patch.object(task_checks, 'run_dir', return_value=run), \
                 patch.object(task_checks, '_move_overlay_to_recovery',
                              side_effect=OSError('partial cleanup')), \
                 patch.object(task_checks.shutil, 'move', side_effect=OSError('move failed')):
                with self.assertRaisesRegex(RuntimeError, 'overlay recovery move failed'):
                    with task_checks._provider_runtime_guard(root):
                        (overlay / 'codex' / 'partial.json').write_bytes(b'partial-generated')

            self.assertTrue(overlay.is_dir(), 'atomic fallback must restore the original overlay')
            self.assertEqual((overlay / 'codex' / 'active-task.json').read_bytes(), b'original-active')
            self.assertFalse((overlay / 'codex' / 'partial.json').exists())
            recovered = list((root / '.harness' / 'legacy-preserved' /
                              'task-checks-overlay-recovery').iterdir())
            self.assertEqual(len(recovered), 1)
            self.assertEqual((recovered[0] / 'failed-overlays' / 'codex' /
                              'partial.json').read_bytes(), b'partial-generated')

    def test_provider_runtime_guard_fail_closed_when_all_overlay_moves_fail(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run = root / '.harness' / 'runs' / 'HARNESS-WORKTREE-MIGRATION-001'
            overlay = root / '.harness' / 'overlays'
            active = overlay / 'codex' / 'active-task.json'
            active.parent.mkdir(parents=True)
            active.write_bytes(b'original-active')
            with patch.object(task_checks, 'runtime_root', return_value=root), \
                 patch.object(task_checks, 'run_dir', return_value=run), \
                 patch.object(task_checks, '_move_overlay_to_recovery',
                              side_effect=OSError('injected cleanup failure')), \
                 patch.object(task_checks.shutil, 'move', side_effect=OSError('move failed')), \
                 patch.object(task_checks.os, 'replace', side_effect=OSError('replace failed')):
                with self.assertRaisesRegex(RuntimeError, 'overlay recovery move failed'):
                    with task_checks._provider_runtime_guard(root):
                        (overlay / 'codex' / 'partial.json').write_bytes(b'partial-generated')

            self.assertTrue(overlay.is_dir())
            self.assertEqual((overlay / 'codex' / 'active-task.json').read_bytes(), b'original-active')
            self.assertFalse((overlay / 'codex' / 'partial.json').exists())
            recovered = list((root / '.harness' / 'legacy-preserved' /
                              'task-checks-overlay-recovery').iterdir())
            self.assertEqual(len(recovered), 1)
            self.assertEqual((recovered[0] / 'failed-overlays' / 'codex' /
                              'partial.json').read_bytes(), b'partial-generated')

    def test_provider_runtime_guard_preserves_held_state_when_cleanup_probe_rejects_reparse(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run = root / '.harness' / 'runs' / 'HARNESS-WORKTREE-MIGRATION-001'
            legacy = root / '.harness' / 'codex' / 'active-task.json'
            legacy.parent.mkdir(parents=True)
            legacy.write_bytes(b'held-state')
            with patch.object(task_checks, 'runtime_root', return_value=root), \
                 patch.object(task_checks, 'run_dir', return_value=run), \
                 patch.object(task_checks, '_restore_legacy_exclusive', side_effect=ValueError('reparse cleanup probe')):
                with self.assertRaisesRegex(RuntimeError, 'legacy restore failed'):
                    with task_checks._provider_runtime_guard(root):
                        pass

            recovery = root / '.harness' / 'legacy-preserved' / 'task-checks-recovery'
            self.assertTrue(any(p.read_bytes() == b'held-state' for p in recovery.rglob('*active-task.json')))

    def test_legacy_restore_collision_preserves_both_states(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            held = root / 'held.json'
            destination = root / '.harness' / 'codex' / 'active-task.json'
            held.write_bytes(b'held-state')
            destination.parent.mkdir(parents=True)
            destination.write_bytes(b'concurrent-state')

            restored = task_checks._restore_legacy_exclusive(held, destination)

            self.assertFalse(restored)
            self.assertEqual(destination.read_bytes(), b'concurrent-state')
            self.assertEqual(held.read_bytes(), b'held-state')

    def test_legacy_restore_write_failure_removes_partial_destination(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            held = root / 'held.json'
            destination = root / '.harness' / 'codex' / 'active-task.json'
            held.write_bytes(b'held-state')

            with patch.object(task_checks.os, 'fdopen', side_effect=OSError('fd wrapper failed')):
                with self.assertRaisesRegex(OSError, 'fd wrapper failed'):
                    task_checks._restore_legacy_exclusive(held, destination)

            self.assertFalse(destination.exists())
            self.assertEqual(held.read_bytes(), b'held-state')

    def test_legacy_preservation_collision_never_replaces_existing_copy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / 'active.json'
            destination = root / 'preserved' / 'active.json'
            source.write_bytes(b'live-state')
            destination.parent.mkdir()
            destination.write_bytes(b'older-evidence')

            with self.assertRaises(FileExistsError):
                task_checks._preserve_legacy_exclusive(source, destination)

            self.assertEqual(source.read_bytes(), b'live-state')
            self.assertEqual(destination.read_bytes(), b'older-evidence')

    def test_preservation_identity_failure_still_restores_held_legacy_state(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run = root / '.harness' / 'runs' / 'HARNESS-WORKTREE-MIGRATION-001'
            legacy = root / '.harness' / 'codex' / 'active-task.json'
            legacy.parent.mkdir(parents=True)
            legacy.write_bytes(b'original-state')

            with patch.object(task_checks, 'runtime_root', return_value=root), \
                 patch.object(task_checks, 'run_dir', return_value=run), \
                 patch.object(task_checks, '_preserve_legacy_exclusive',
                              side_effect=RuntimeError('identity changed')):
                with self.assertRaisesRegex(RuntimeError, 'legacy preservation failed'):
                    with task_checks._provider_runtime_guard(root):
                        legacy.write_bytes(b'generated-state')

            self.assertEqual(legacy.read_bytes(), b'generated-state')
            recovery = root / '.harness' / 'legacy-preserved' / 'task-checks-recovery'
            self.assertTrue(any(p.read_bytes() == b'original-state' for p in recovery.rglob('*active-task.json')))

    def test_successful_provider_runtime_guard_preserves_generated_and_restores_original(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run = root / '.harness' / 'runs' / 'HARNESS-WORKTREE-MIGRATION-001'
            legacy = root / '.harness' / 'codex' / 'active-task.json'
            legacy.parent.mkdir(parents=True)
            legacy.write_bytes(b'original-state')

            with patch.object(task_checks, 'runtime_root', return_value=root), \
                 patch.object(task_checks, 'run_dir', return_value=run):
                with task_checks._provider_runtime_guard(root):
                    legacy.write_bytes(b'generated-state')

            self.assertEqual(legacy.read_bytes(), b'original-state')
            recovery = root / '.harness' / 'legacy-preserved' / 'task-checks'
            self.assertTrue(any(
                p.read_bytes() == b'generated-state'
                for p in recovery.rglob('*active-task.json')
            ))

    def test_provider_runtime_guard_preserves_foreign_active_binding(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run = root / '.harness/runs/HARNESS-WORKTREE-MIGRATION-001'
            overlay = root / '.harness/overlays/codex'
            active = overlay / 'active-task.json'
            active.parent.mkdir(parents=True)
            active.write_text(json.dumps({'task_id': 'before'}), encoding='utf-8')

            with patch.object(task_checks, 'runtime_root', return_value=root), \
                 patch.object(task_checks, 'run_dir', return_value=run):
                with self.assertRaisesRegex(RuntimeError, 'ownership collision'):
                    with task_checks._provider_runtime_guard(root):
                        active.write_text(json.dumps({'task_id': 'foreign'}), encoding='utf-8')

            self.assertEqual(json.loads(active.read_text())['task_id'], 'foreign')

    def test_run_records_timeout_without_raising(self):
        with tempfile.TemporaryDirectory() as td:
            result = task_checks._run(
                [sys.executable, '-c', 'import time; time.sleep(2)'],
                Path(td), task_checks._safe_env(Path(td)), timeout=0.05,
            )
        self.assertEqual(result['exit_code'], -1)
        self.assertTrue(result['timed_out'])

    def test_run_checks_integration_writes_bound_pass_report(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run = root / '.harness' / 'runs' / 'TASK-RUNNER-001'
            run.mkdir(parents=True)
            (root / 'tests').mkdir()
            task = {'id': 'TASK-RUNNER-001', 'files': ['tests']}
            route = {'task_id': task['id'], 'risk': 'R2', 'isolation': 'worktree'}
            candidate = {
                'subject_hash': 'a' * 64,
                'base_commit': 'b' * 40,
                'scope_expansion_sha256': 'c' * 64,
            }
            pass_command = {'argv': ['fake'], 'cwd': str(root), 'exit_code': 0,
                            'stdout': '', 'stderr': ''}
            with patch.object(task_checks, '_require_checks_step'), \
                 patch.object(task_checks, '_load_active_task', return_value=(task, run / 'task.json')), \
                 patch.object(task_checks, '_route', return_value=route), \
                 patch.object(task_checks, '_validate_runtime_consistency'), \
                 patch.object(task_checks, '_execution_root', return_value=root), \
                 patch.object(task_checks, '_project_root', return_value=Path('.')), \
                 patch.object(task_checks, '_run', return_value=pass_command), \
                 patch.object(task_checks, 'candidate_snapshot', return_value=candidate), \
                 patch.object(task_checks, 'run_dir', return_value=run), \
                 patch.object(task_checks, 'runtime_root', return_value=root), \
                 patch.object(task_checks, 'append_evidence', return_value={'record_hash': 'd' * 64}), \
                 patch.object(task_checks, 'validate_evidence', return_value={'valid': True, 'head_hash': 'e' * 64}), \
                 patch.object(task_checks, '_provider_runtime_guard', return_value=unittest.mock.MagicMock()):
                report = task_checks.run_checks(task['id'])

            self.assertEqual(report['status'], 'PASS')
            self.assertEqual(report['candidate_subject_hash'], candidate['subject_hash'])
            self.assertEqual(report['evidence_head_hash'], 'e' * 64)
            self.assertEqual(json.loads((run / 'checks-report.json').read_text())['status'], 'PASS')

    def test_run_checks_fails_when_candidate_changes_during_execution(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run = root / '.harness' / 'runs' / 'TASK-RUNNER-002'
            run.mkdir(parents=True)
            (root / 'tests').mkdir()
            task = {'id': 'TASK-RUNNER-002', 'files': ['tests']}
            route = {'task_id': task['id'], 'risk': 'R2', 'isolation': 'worktree'}
            first = {'subject_hash': 'a' * 64, 'base_commit': 'b' * 40,
                     'scope_expansion_sha256': 'c' * 64}
            second = {'subject_hash': 'd' * 64, 'base_commit': 'b' * 40,
                      'scope_expansion_sha256': 'c' * 64}
            snapshots = iter((first, second))
            pass_command = {'argv': ['fake'], 'cwd': str(root), 'exit_code': 0,
                            'stdout': '', 'stderr': ''}
            with patch.object(task_checks, '_require_checks_step'), \
                 patch.object(task_checks, '_load_active_task', return_value=(task, run / 'task.json')), \
                 patch.object(task_checks, '_route', return_value=route), \
                 patch.object(task_checks, '_validate_runtime_consistency'), \
                 patch.object(task_checks, '_execution_root', return_value=root), \
                 patch.object(task_checks, '_project_root', return_value=Path('.')), \
                 patch.object(task_checks, '_run', return_value=pass_command), \
                 patch.object(task_checks, 'candidate_snapshot', side_effect=lambda _task: next(snapshots)), \
                 patch.object(task_checks, 'run_dir', return_value=run), \
                 patch.object(task_checks, 'runtime_root', return_value=root), \
                 patch.object(task_checks, 'append_evidence', return_value={'record_hash': 'd' * 64}), \
                 patch.object(task_checks, 'validate_evidence', return_value={'valid': True, 'head_hash': 'e' * 64}), \
                 patch.object(task_checks, '_provider_runtime_guard', return_value=unittest.mock.MagicMock()):
                report = task_checks.run_checks(task['id'])

            self.assertEqual(report['status'], 'FAIL')
            self.assertTrue(report['candidate_mutated_during_checks'])
            self.assertEqual(report['candidate_before_subject_hash'], first['subject_hash'])
            self.assertEqual(report['candidate_subject_hash'], second['subject_hash'])

    def test_real_candidate_mutation_during_a_check_is_detected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run = root / '.harness/runs/TASK-RUNNER-REAL-MUTATION'
            run.mkdir(parents=True)
            candidate = root / 'candidate.txt'
            candidate.write_text('before\n', encoding='utf-8')
            task = {'id': 'TASK-RUNNER-REAL-MUTATION', 'files': ['candidate.txt']}
            route = {'task_id': task['id'], 'risk': 'R2', 'isolation': 'local'}

            self.assertEqual(
                subprocess.run(['git', 'init', '-b', 'main'], cwd=root,
                                capture_output=True, text=True).returncode, 0
            )
            subprocess.run(['git', 'config', 'user.email', 'runner@test.invalid'], cwd=root, check=True)
            subprocess.run(['git', 'config', 'user.name', 'Runner Test'], cwd=root, check=True)
            subprocess.run(['git', 'add', 'candidate.txt'], cwd=root, check=True)
            subprocess.run(['git', 'commit', '-m', 'base'], cwd=root, check=True,
                            capture_output=True)
            import receipt_review
            base_commit = subprocess.check_output(
                ['git', 'rev-parse', 'HEAD'], cwd=root, text=True
            ).strip()

            def mutate_then_check(argv, cwd, env, timeout=600):
                candidate.write_text('after\n', encoding='utf-8')
                return {'argv': argv, 'cwd': str(cwd), 'exit_code': 0,
                        'stdout': '', 'stderr': ''}

            with patch.object(task_checks, 'ROOT', root), \
                 patch.object(task_checks, '_require_checks_step'), \
                 patch.object(task_checks, '_load_active_task', return_value=(task, run / 'task.json')), \
                 patch.object(task_checks, '_route', return_value=route), \
                 patch.object(task_checks, '_validate_runtime_consistency'), \
                 patch.object(task_checks, '_execution_root', return_value=root), \
                 patch.object(task_checks, '_project_root', return_value=Path('.')), \
                 patch.object(task_checks, '_run', side_effect=mutate_then_check), \
                 patch.object(receipt_review, '_lock_or_publish',
                              return_value=(base_commit, None, root)), \
                 patch.object(task_checks, 'run_dir', return_value=run), \
                 patch.object(task_checks, 'runtime_root', return_value=root), \
                 patch.object(task_checks, 'append_evidence', return_value={'record_hash': 'd' * 64}), \
                 patch.object(task_checks, 'validate_evidence', return_value={'valid': True, 'head_hash': 'e' * 64}):
                report = task_checks.run_checks(task['id'])

            self.assertEqual(report['status'], 'FAIL')
            self.assertTrue(report['candidate_mutated_during_checks'])

    def test_validate_planned_files_reports_directories_and_missing_paths(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'tests').mkdir()
            result = task_checks._validate_planned_files(
                {'files': ['tests', 'missing.txt']}, root
            )
        self.assertEqual(result, [
            {'path': 'tests', 'exists': True},
            {'path': 'missing.txt', 'exists': False},
        ])


if __name__ == '__main__':
    unittest.main()
