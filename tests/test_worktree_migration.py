"""Tests for scripts/worktree_migration.py using only temporary fixture repos."""
import json
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import harnesslib
import worktree
import worktree_migration


TASK_A = "WTM-ALPHA-001"
TASK_B = "WTM-BETA-001"


def _git_run(*args, cwd):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), text=True, encoding="utf-8",
        errors="replace", capture_output=True,
    )


class MigrationFixture(unittest.TestCase):
    """Isolated temporary git repository per test; no live-repo actions."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="wtmig-"))
        repo = self.tmp
        for args in (
            ("init", "-b", "main"),
            ("config", "user.email", "harness@test.invalid"),
            ("config", "user.name", "Harness Test"),
            ("config", "core.logAllRefUpdates", "true"),
        ):
            result = _git_run(*args, cwd=repo)
            self.assertEqual(result.returncode, 0, result.stderr)
        (repo / "README.md").write_text("base\n", encoding="utf-8")
        _git_run("add", "README.md", cwd=repo)
        _git_run("commit", "-m", "base", cwd=repo)

        policies = repo / "harness" / "policies"
        policies.mkdir(parents=True, exist_ok=True)
        (policies / "risk-policy.json").write_text(
            json.dumps({
                "secret_path_patterns": ["*secret-key*", "*credential*", "*api-key*"],
                "protected_paths": [],
            }),
            encoding="utf-8",
        )

        self.old = (
            harnesslib.ROOT, worktree.ROOT, worktree_migration.ROOT,
        )
        harnesslib.ROOT = repo
        worktree.ROOT = repo
        worktree_migration.ROOT = repo
        self.repo = repo

    def tearDown(self):
        (harnesslib.ROOT, worktree.ROOT, worktree_migration.ROOT) = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- helpers -----------------------------------------------------------
    def _git(self, *args, cwd=None):
        result = _git_run(*args, cwd=cwd or self.repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def make_worktree(self, task):
        worktree.create(task, execute=True)
        path = worktree.wt(task)
        self.assertTrue(path.is_dir())
        return path

    def write_legacy(self, wt, text, name="active-task.json", provider="codex"):
        path = wt / ".harness" / provider / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def eligible_legacy(self, wt, provider="codex"):
        payload = {"provider": provider, "task_id": wt.name, "state": "idle"}
        return self.write_legacy(
            wt, json.dumps(payload, indent=2), provider=provider
        )

    def receipt_dir(self, wid):
        return worktree_migration._receipt_dir(wid)

    def wid_of(self, wt):
        return harnesslib.worktree_identity(wt)["worktree_id"]


class CrossWorktreeIsolationTests(MigrationFixture):
    def test_inventory_rejects_invalid_lock_using_canonical_validation(self):
        wt = self.make_worktree(TASK_A)
        lock_path = worktree.lock(TASK_A)
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        payload["worktree_id"] = "foreign-worktree-id"
        lock_path.write_text(json.dumps(payload), encoding="utf-8")

        snapshot = worktree_migration._lock_for_path(wt)

        self.assertFalse(snapshot["lock_valid"], snapshot)
        self.assertIn("identity", snapshot["lock_reason"])

    def test_two_worktrees_different_active_tasks_isolated_both_directions(self):
        wt_a = self.make_worktree(TASK_A)
        wt_b = self.make_worktree(TASK_B)

        lock_a = worktree_migration._lock_for_path(wt_a)
        lock_b = worktree_migration._lock_for_path(wt_b)

        self.assertTrue(lock_a["lock"], lock_a)
        self.assertTrue(lock_b["lock"], lock_b)
        self.assertEqual(lock_a["task"], TASK_A)
        self.assertEqual(lock_b["task"], TASK_B)
        self.assertNotEqual(lock_a["task"], lock_b["task"])

        # Each worktree resolves its own overlay directory; nothing is shared.
        overlay_a = harnesslib.provider_active_path("codex", wt_a)
        overlay_b = harnesslib.provider_active_path("codex", wt_b)
        self.assertNotEqual(overlay_a.parent, overlay_b.parent)
        self.assertTrue(str(overlay_a.parent).startswith(str(wt_a)))
        self.assertTrue(str(overlay_b.parent).startswith(str(wt_b)))

        # Writes made under one worktree's overlay are invisible to the other.
        overlay_a.parent.mkdir(parents=True, exist_ok=True)
        overlay_b.parent.mkdir(parents=True, exist_ok=True)
        (overlay_a.parent / "state-a.json").write_text("{}", encoding="utf-8")
        (overlay_b.parent / "state-b.json").write_text("{}", encoding="utf-8")

        state_a = worktree_migration.overlay_state("codex", wt_a)
        state_b = worktree_migration.overlay_state("codex", wt_b)
        self.assertEqual(state_a["files"], ["state-a.json"])
        self.assertEqual(state_b["files"], ["state-b.json"])

        with mock.patch.object(harnesslib, "ROOT", wt_a), mock.patch.object(
            worktree_migration, "ROOT", wt_a
        ):
            self.assertEqual(
                harnesslib.run_dir(TASK_A),
                self.repo / ".harness" / "runs" / TASK_A,
            )
        with mock.patch.object(harnesslib, "ROOT", wt_b), mock.patch.object(
            worktree_migration, "ROOT", wt_b
        ):
            self.assertEqual(
                harnesslib.run_dir(TASK_B),
                self.repo / ".harness" / "runs" / TASK_B,
            )
        self.assertNotEqual(
            self.repo / ".harness" / "runs" / TASK_A,
            self.repo / ".harness" / "runs" / TASK_B,
        )
        self.assertFalse((self.repo / ".harness" / "codex").exists())

    def test_per_worktree_diagnostics_and_provider_state_independence(self):
        wt_a = self.make_worktree(TASK_A)
        wt_b = self.make_worktree(TASK_B)
        self.write_legacy(wt_a, '{ "provider": "codex", "task_id": "%s" }' % TASK_A)
        marker_b = harnesslib.provider_active_path("codex", wt_b)
        marker_b.parent.mkdir(parents=True, exist_ok=True)
        marker_b.write_text("{}", encoding="utf-8")

        report = worktree_migration.inventory()

        items = {Path(item["path"]).name: item for item in report["worktrees"]}
        self.assertIn(TASK_A, items)
        self.assertIn(TASK_B, items)
        item_a, item_b = items[TASK_A], items[TASK_B]

        self.assertNotEqual(item_a["worktree_id"], item_b["worktree_id"])
        # Legacy diagnostics belong only to the worktree that owns the file.
        self.assertEqual(item_a["providers"]["codex"]["legacy"], [
            str(wt_a / ".harness" / "codex" / "active-task.json"),
        ])
        self.assertEqual(item_b["providers"]["codex"]["legacy"], [])
        # B has no legacy diagnostics at all; overlay state is per-worktree.
        self.assertEqual(item_b["providers"]["codex"]["files"], ["active-task.json"])
        self.assertEqual(
            items[TASK_B]["providers"]["opencode"]["legacy"], [],
        )
        self.assertIn("planning_tasks_untracked_hashes", item_a)
        self.assertIn("planning_tasks_untracked_hashes", item_b)
        self.assertTrue(Path(item_a["inventory_record"]).is_file())
        self.assertTrue(Path(item_b["inventory_record"]).is_file())

    def test_unrelated_untracked_planning_and_tasks_bytes_are_preserved(self):
        wt = self.make_worktree(TASK_A)
        planning = wt / "planning" / "unrelated-note.md"
        task_file = wt / "tasks" / "unrelated-task.json"
        planning.parent.mkdir(parents=True, exist_ok=True)
        task_file.parent.mkdir(parents=True, exist_ok=True)
        planning_bytes = b"keep this planning note\n"
        task_bytes = b'{"id":"UNRELATED-001","state":"draft"}\n'
        planning.write_bytes(planning_bytes)
        task_file.write_bytes(task_bytes)
        legacy = self.eligible_legacy(wt)

        report = worktree_migration.inventory()
        item = next(x for x in report["worktrees"] if Path(x["path"]).name == TASK_A)

        self.assertEqual(
            item["planning_tasks_untracked_hashes"]["planning/unrelated-note.md"],
            harnesslib.sha256_file(planning),
        )
        self.assertEqual(
            item["planning_tasks_untracked_hashes"]["tasks/unrelated-task.json"],
            harnesslib.sha256_file(task_file),
        )
        worktree_migration.migrate_legacy("codex", wt)
        self.assertTrue((wt / ".harness" / "overlays").rglob(legacy.name).__next__().is_file())
        self.assertEqual(planning.read_bytes(), planning_bytes)
        self.assertEqual(task_file.read_bytes(), task_bytes)

    def test_inventory_classification_contract_marks_dirty_worktree(self):
        wt = self.make_worktree(TASK_A)
        marker = wt / "unrelated-runtime-note.txt"
        marker.write_text("dirty\n", encoding="utf-8")

        report = worktree_migration.inventory()
        item = next(x for x in report["worktrees"] if Path(x["path"]).name == TASK_A)

        self.assertEqual(item["classification"], "dirty", item)
        self.assertIn("unrelated-runtime-note.txt", item["dirty"])

    def test_registered_branch_owner_wins_over_custom_directory_name(self):
        wt = self.make_worktree(TASK_A)
        custom_name = Path("custom-physical-directory-name")
        with mock.patch.object(worktree_migration, "_worktree_task", return_value=TASK_A):
            self.assertEqual(
                worktree_migration._task_owner_for_worktree(custom_name), TASK_A
            )

    def test_shared_runs_evidence_receipts_visible_via_git_common_dir(self):
        wt_a = self.make_worktree(TASK_A)
        wt_b = self.make_worktree(TASK_B)
        wid_a = self.wid_of(wt_a)

        with mock.patch.object(harnesslib, "ROOT", wt_a), mock.patch.object(
            worktree_migration, "ROOT", wt_a
        ):
            shared_from_linked = harnesslib.run_dir(TASK_A)
            self.assertTrue(
                worktree_migration.migration_workdir().is_relative_to(self.repo)
            )
        self.assertEqual(
            shared_from_linked, self.repo / ".harness" / "runs" / TASK_A
        )

        run_task_a = harnesslib.run_dir(TASK_A)
        run_task_a.mkdir(parents=True, exist_ok=True)
        evidence = run_task_a / "evidence.json"
        evidence.write_text('{"visible": true}', encoding="utf-8")

        # Visible from the canonical root and from the other linked worktree.
        self.assertTrue(evidence.is_file())
        with mock.patch.object(harnesslib, "ROOT", wt_b), mock.patch.object(
            worktree_migration, "ROOT", wt_b
        ):
            self.assertEqual(
                harnesslib.run_dir(TASK_A) / "evidence.json", evidence
            )
            self.assertTrue(
                worktree_migration.migration_workdir().is_relative_to(self.repo)
            )

        receipt_dir = self.receipt_dir(wid_a)
        receipt_dir.mkdir(parents=True, exist_ok=True)
        receipt = receipt_dir / "shared-receipt.json"
        receipt.write_text("{}", encoding="utf-8")
        self.assertTrue(receipt_dir.is_relative_to(self.repo))
        self.assertTrue(receipt_dir.is_dir())

        # Read the same durable receipt through both linked-worktree runtime
        # resolvers; path equality alone would not prove shared visibility.
        with mock.patch.object(harnesslib, "ROOT", wt_a):
            bytes_a = harnesslib.run_dir("HARNESS-WORKTREE-MIGRATION-001").joinpath(
                "migration", "receipts", wid_a, "shared-receipt.json"
            ).read_bytes()
        with mock.patch.object(harnesslib, "ROOT", wt_b):
            bytes_b = harnesslib.run_dir("HARNESS-WORKTREE-MIGRATION-001").joinpath(
                "migration", "receipts", wid_a, "shared-receipt.json"
            ).read_bytes()
        self.assertEqual(bytes_a, bytes_b)


class LegacyMigrationDurableTests(MigrationFixture):
    def test_shared_reparse_rejection_never_hashes_source(self):
        wt = self.make_worktree(TASK_A)
        source = self.repo / ".harness" / "codex" / "active-task.json"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(json.dumps({
            "provider": "codex", "task_id": TASK_A, "state": "idle",
        }), encoding="utf-8")

        real_reject = worktree_migration._reject_reparse_components
        real_sha = worktree_migration.sha256_file

        def reject_shared(path):
            if Path(path) == source:
                raise ValueError("reparse shared fixture")
            return real_reject(path)

        def reject_hash(path):
            if Path(path) == source:
                raise AssertionError("reparse source must not be hashed")
            return real_sha(path)

        with mock.patch.object(
            worktree_migration, "_reject_reparse_components", side_effect=reject_shared
        ), mock.patch.object(
            worktree_migration, "sha256_file", side_effect=reject_hash
        ):
            report = worktree_migration.inventory()

        shared = [item for item in report["shared_legacy"] if item["path"] == str(source)]
        self.assertEqual(len(shared), 1, shared)
        self.assertIn("reparse_source_rejected", shared[0]["reason"])
        self.assertTrue(Path(shared[0]["record"]).is_file())

    def test_reparse_source_is_rejected_without_read_or_copy(self):
        wt = self.make_worktree(TASK_A)
        source = self.eligible_legacy(wt)

        real_reject = worktree_migration._reject_reparse_components

        def reject_only_source(path):
            if Path(path) == source:
                raise ValueError("reparse fixture")
            return real_reject(path)

        with mock.patch.object(
            worktree_migration,
            "_reject_reparse_components",
            side_effect=reject_only_source,
        ):
            result = worktree_migration.migrate_legacy("codex", wt)

        entry = result["results"][0]
        self.assertIn("reparse_source_rejected", entry["rejected"])
        self.assertTrue(source.is_file())
        self.assertFalse((wt / ".harness" / "overlays").exists())

    def test_changed_source_during_copy_removes_untrusted_destination(self):
        wt = self.make_worktree(TASK_A)
        source = self.eligible_legacy(wt)
        original = source.read_bytes()
        changed = original.replace(b'"idle"', b'"changed"')
        real_write = worktree_migration.write_json_exclusive_bytes

        def write_then_change(path, data):
            real_write(path, data)
            source.write_bytes(changed)

        with mock.patch.object(
            worktree_migration,
            "write_json_exclusive_bytes",
            side_effect=write_then_change,
        ), self.assertRaisesRegex(ValueError, "source changed during migration"):
            worktree_migration.migrate_legacy("codex", wt)

        migrated = wt / ".harness" / "overlays"
        self.assertTrue(migrated.exists())
        self.assertFalse(any(migrated.rglob("active-task.json")))

    def test_changed_source_after_receipt_write_is_not_reported_as_success(self):
        wt = self.make_worktree(TASK_A)
        source = self.eligible_legacy(wt)
        real_write = worktree_migration.write_json_immutable
        state = {'receipt_written': False}

        def write_then_change(path, payload):
            result = real_write(path, payload)
            if 'receipts' in path.parts and path.name.endswith('.json'):
                state['receipt_written'] = True
                source.write_text(source.read_text(encoding='utf-8').replace('idle', 'changed'), encoding='utf-8')
            return result

        with mock.patch.object(
            worktree_migration,
            'write_json_immutable',
            side_effect=write_then_change,
        ), self.assertRaisesRegex(ValueError, 'after migration receipt'):
            worktree_migration.migrate_legacy('codex', wt)

        self.assertTrue(state['receipt_written'])
        self.assertFalse(any((wt / '.harness' / 'overlays').rglob('active-task.json')))

    def test_shared_legacy_state_is_not_attributed_to_caller(self):
        wt = self.make_worktree(TASK_A)
        source = self.repo / ".harness" / "codex" / "active-task.json"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(json.dumps({
            "provider": "codex", "task_id": TASK_A, "state": "idle",
        }), encoding="utf-8")

        report = worktree_migration.inventory()
        shared = [item for item in report["shared_legacy"] if item["path"] == str(source)]
        self.assertEqual(len(shared), 1, shared)
        self.assertIsNone(shared[0]["owner_worktree_id"])
        self.assertIn("ambiguous", shared[0]["reason"])
        self.assertTrue(Path(shared[0]["record"]).is_file())
        item = next(item for item in report["worktrees"] if item["path"] == str(wt))
        self.assertNotIn(str(source), item["providers"]["codex"]["legacy"])

        result = worktree_migration.migrate_legacy("codex", wt)
        self.assertTrue(result["no_legacy_state"])
        self.assertTrue(source.is_file())

    def test_malformed_shared_json_is_rejected_durably(self):
        wt = self.make_worktree(TASK_A)
        source = self.repo / ".harness" / "codex" / "session.json"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")

        report = worktree_migration.inventory()
        shared = [item for item in report["shared_legacy"] if item["path"] == str(source)]
        self.assertEqual(len(shared), 1, shared)
        self.assertIn("malformed", shared[0]["reason"])
        self.assertTrue(Path(shared[0]["record"]).is_file())
        self.assertTrue(source.is_file())

        result = worktree_migration.migrate_legacy("codex", wt)
        entry = next(x for x in result["results"] if x["source"] == str(source))
        self.assertIn("malformed", entry["rejected"])
        self.assertIn("rejections", Path(entry["record"]).parts)
        self.assertTrue(source.is_file())

    def test_migration_matrix_covers_all_providers_without_overwrite(self):
        wt = self.make_worktree(TASK_A)
        for provider in worktree_migration.PROVIDERS:
            source = self.eligible_legacy(wt, provider=provider)
            result = worktree_migration.migrate_legacy(provider, wt)
            entry = next(x for x in result["results"] if x["source"] == str(source))
            self.assertIn("migrated_to", entry, (provider, result))
            self.assertEqual(Path(entry["migrated_to"]).read_bytes(), source.read_bytes())
            self.assertTrue(source.is_file())

    def test_foreign_legacy_task_is_rejected_for_source_worktree(self):
        wt = self.make_worktree(TASK_A)
        source = self.write_legacy(
            wt,
            json.dumps({"provider": "codex", "task_id": TASK_B, "state": "idle"}),
        )

        result = worktree_migration.migrate_legacy("codex", wt)

        entry = result["results"][0]
        self.assertIn("foreign", entry["rejected"])
        self.assertTrue(source.is_file())

    def test_migrations_share_one_common_runtime_lock(self):
        wt_a = self.make_worktree(TASK_A)
        wt_b = self.make_worktree(TASK_B)
        self.eligible_legacy(wt_a)
        self.eligible_legacy(wt_b)

        started = threading.Event()
        finished = threading.Event()
        result = {}

        def migrate_b():
            started.set()
            result["value"] = worktree_migration.migrate_legacy("codex", wt_b)
            finished.set()

        with worktree_migration._claim_migration_lock(self.wid_of(wt_a)):
            thread = threading.Thread(target=migrate_b, daemon=True)
            thread.start()
            self.assertTrue(started.wait(5))
            self.assertFalse(finished.wait(0.5), "second worktree bypassed shared lock")
        thread.join(60)
        self.assertFalse(thread.is_alive(), "second migration did not resume after unlock")
        self.assertIn("results", result["value"])
        anchor = worktree_migration.migration_workdir() / "migration.lock.json"
        self.assertTrue(anchor.is_file())

    def test_same_source_two_processes_are_serialized_and_idempotent(self):
        wt = self.make_worktree(TASK_A)
        source = self.eligible_legacy(wt)
        child = (
            "import json, sys; from pathlib import Path; "
            "sys.path.insert(0, str(Path(sys.argv[1]) / 'scripts')); "
            "import harnesslib, worktree, worktree_migration; "
            "root=Path(sys.argv[2]); wt=Path(sys.argv[3]); "
            "harnesslib.ROOT=root; worktree.ROOT=root; worktree_migration.ROOT=root; "
            "print(json.dumps(worktree_migration.migrate_legacy('codex', wt)))"
        )
        processes = [
            subprocess.Popen(
                [sys.executable, '-c', child, str(ROOT), str(self.repo), str(wt)],
                cwd=str(self.repo), text=True, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for _ in range(2)
        ]
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, stderr)
            results.append(json.loads(stdout))

        entries = [result['results'][0] for result in results]
        self.assertEqual(sum(bool(entry.get('migrated_to')) for entry in entries), 1, entries)
        self.assertEqual(sum(bool(entry.get('already_migrated')) for entry in entries), 1, entries)
        self.assertTrue(source.is_file())

        wid = self.wid_of(wt)
        receipts = sorted(self.receipt_dir(wid).glob('codex-*.json'))
        self.assertEqual(len(receipts), 1, list(self.receipt_dir(wid).iterdir()))
        receipt = json.loads(receipts[0].read_text(encoding='utf-8'))
        destination = Path(receipt['dest'])
        self.assertTrue(destination.is_file())
        self.assertEqual(harnesslib.sha256_file(destination), receipt['dest_sha256'])
        self.assertEqual(receipt['dest_sha256'], receipt['source_sha256'])
        self.assertEqual(list(self.receipt_dir(wid).glob('*.collision.json')), [])

    def test_legacy_migration_issues_durable_receipt(self):
        wt = self.make_worktree(TASK_A)
        source = self.eligible_legacy(wt)
        wid = self.wid_of(wt)

        result = worktree_migration.migrate_legacy("codex", wt)

        self.assertNotIn("collision", result["results"][0], result)
        operation = Path(result["operation_record"])
        self.assertTrue(operation.is_file())
        self.assertEqual(json.loads(operation.read_text(encoding="utf-8"))["status"], "COMPLETED")
        migrated = result["results"][0]
        self.assertIn("migrated_to", migrated, migrated)
        dest = Path(migrated["migrated_to"])
        self.assertTrue(dest.is_relative_to(wt / ".harness" / "overlays" / wid))
        self.assertEqual(
            harnesslib.sha256_file(dest), harnesslib.sha256_file(source)
        )

        receipts = sorted(self.receipt_dir(wid).glob("codex-*.json"))
        self.assertEqual(len(receipts), 1, list(self.receipt_dir(wid).iterdir()))
        receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
        self.assertEqual(receipt["schema_version"], 1)
        self.assertEqual(receipt["status"], "MIGRATED")
        self.assertEqual(receipt["provider"], "codex")
        self.assertEqual(receipt["task_id"], TASK_A)
        self.assertEqual(receipt["worktree_id"], wid)
        self.assertEqual(receipt["source"], str(source))
        self.assertEqual(receipt["source_sha256"], harnesslib.sha256_file(source))
        self.assertEqual(receipt["dest"], str(dest))
        # Legacy source is never fabricated into an active binding.
        overlay = dest.parent.parent
        self.assertFalse((overlay / "active-task.json").is_file())

    def test_collision_fails_closed_without_overwrite(self):
        wt = self.make_worktree(TASK_A)
        source = self.eligible_legacy(wt)
        original = source.read_text(encoding="utf-8")
        wid = self.wid_of(wt)

        first = worktree_migration.migrate_legacy("codex", wt)
        dest = Path(first["results"][0]["migrated_to"])
        dest_bytes = dest.read_bytes()

        # Change the legacy source after the receipt was issued.
        changed = json.loads(original)
        changed["state"] = "modified"
        source.write_text(json.dumps(changed, indent=2), encoding="utf-8")

        second = worktree_migration.migrate_legacy("codex", wt)

        self.assertIn("collision", second["results"][0], second)
        self.assertTrue(second["results"][0]["collision"])
        # Fail closed: the previously migrated overlay archive was not touched.
        self.assertEqual(dest.read_bytes(), dest_bytes)
        collision = sorted(self.receipt_dir(wid).glob(
            "codex-active-task.json-*.collision.json"))
        self.assertEqual(len(collision), 1, list(self.receipt_dir(wid).iterdir()))
        record = json.loads(collision[0].read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "COLLISION")
        self.assertEqual(record["source"], str(source))

        # Repeating the same collision is an immutable no-op, not a receipt
        # overwrite caused by a fresh timestamp.
        before = collision[0].read_bytes()
        third = worktree_migration.migrate_legacy("codex", wt)
        self.assertTrue(third["results"][0]["collision"])
        self.assertEqual(collision[0].read_bytes(), before)

    def test_tampered_destination_with_matching_receipt_fails_closed(self):
        wt = self.make_worktree(TASK_A)
        source = self.eligible_legacy(wt)
        first = worktree_migration.migrate_legacy("codex", wt)
        dest = Path(first["results"][0]["migrated_to"])
        dest.write_text("tampered\n", encoding="utf-8")

        second = worktree_migration.migrate_legacy("codex", wt)

        self.assertTrue(second["results"][0].get("collision"), second)
        self.assertEqual(dest.read_text(encoding="utf-8"), "tampered\n")
        self.assertTrue(source.is_file())

    def test_tampered_receipt_metadata_fails_closed(self):
        wt = self.make_worktree(TASK_A)
        source = self.eligible_legacy(wt)
        first = worktree_migration.migrate_legacy("codex", wt)
        wid = self.wid_of(wt)
        receipt_path = next(self.receipt_dir(wid).glob("codex-active-task.json-*.json"))
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["task_id"] = TASK_B
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

        second = worktree_migration.migrate_legacy("codex", wt)

        self.assertTrue(second["results"][0].get("collision"), second)
        self.assertTrue(Path(first["results"][0]["migrated_to"]).is_file())
        self.assertTrue(source.is_file())

    def test_explicitly_bound_shared_state_migrates_to_bound_worktree(self):
        wt = self.make_worktree(TASK_A)
        wid = self.wid_of(wt)
        source = self.repo / ".harness" / "codex" / "active-task.json"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(json.dumps({
            "provider": "codex",
            "task_id": TASK_A,
            "worktree_id": wid,
            "worktree_root": str(wt),
            "state": "idle",
        }), encoding="utf-8")

        result = worktree_migration.migrate_legacy("codex", wt)

        self.assertIn("migrated_to", result["results"][0], result)
        self.assertEqual(result["worktree_id"], wid)
        self.assertTrue(source.is_file())
        self.assertTrue(Path(result["results"][0]["migrated_to"]).is_file())

    def test_unregistered_worktree_is_rejected_without_mutation(self):
        with tempfile.TemporaryDirectory(prefix="wtmig-unregistered-") as td:
            root = Path(td)
            with self.assertRaisesRegex(ValueError, "Git identity|registered"):
                worktree_migration.migrate_legacy("codex", root)

    def test_identical_rerun_is_an_immutable_noop(self):
        wt = self.make_worktree(TASK_A)
        source = self.eligible_legacy(wt)
        wid = self.wid_of(wt)
        dest = Path(
            worktree_migration.migrate_legacy("codex", wt)["results"][0]["migrated_to"]
        )
        dest_bytes = dest.read_bytes()
        before = {
            p.name: p.read_bytes()
            for p in sorted(self.receipt_dir(wid).iterdir())
        }

        rerun = worktree_migration.migrate_legacy("codex", wt)

        entry = rerun["results"][0]
        self.assertTrue(entry.get("already_migrated"), entry)
        self.assertEqual(dest.read_bytes(), dest_bytes)
        after = {
            p.name: p.read_bytes()
            for p in sorted(self.receipt_dir(wid).iterdir())
        }
        self.assertEqual(after, before)
        self.assertTrue(source.is_file())

    def test_valid_receipt_with_missing_destination_fails_closed_without_recreation(self):
        wt = self.make_worktree(TASK_A)
        source = self.eligible_legacy(wt)
        first = worktree_migration.migrate_legacy("codex", wt)
        dest = Path(first["results"][0]["migrated_to"])
        dest.unlink()

        rerun = worktree_migration.migrate_legacy("codex", wt)

        entry = rerun["results"][0]
        self.assertTrue(entry.get("collision"), rerun)
        self.assertFalse(dest.exists())
        self.assertTrue(source.is_file())

    def test_legacy_malformed_foreign_and_secret_like_are_rejected_durably(self):
        wt = self.make_worktree(TASK_A)
        wid = self.wid_of(wt)
        cases = [
            ("malformed", "session.json", "{not json"),
            ("malformed", "permission-audit.jsonl", json.dumps([1, 2, 3])),
            ("foreign", "catalog-snapshot.json",
             json.dumps({"provider": "opencode", "task_id": TASK_A})),
            ("ambiguous", "model-inventory.json",
             json.dumps({"provider": "codex", "state": "idle"})),
        ]
        sources = [
            (expected, self.write_legacy(wt, text, name=name))
            for expected, name, text in cases
        ]
        # secret-like content inside an otherwise eligible payload.
        secret_text = json.dumps({"provider": "codex", "task_id": TASK_A,
                                  "note": "super secret password here"})
        sources.append(("secret_like_content",
                        self.write_legacy(wt, secret_text,
                                          name="active-task.json")))

        result = worktree_migration.migrate_legacy("codex", wt)

        by_source = {entry["source"]: entry for entry in result["results"]}
        for expected, source in sources:
            entry = by_source[str(source)]
            self.assertIn("rejected", entry, entry)
            self.assertIn(expected, entry["rejected"], entry)
            record_path = Path(entry["record"])
            self.assertTrue(record_path.is_file(), record_path)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "REJECTED")
            self.assertEqual(record["worktree_id"], wid)
            self.assertIn(expected, record["reason"])
            # No mutation of the legacy source itself.
            self.assertTrue(source.is_file())
        # Nothing was archived into the overlay migrated directory.
        overlay = (wt / ".harness" / "overlays" / wid / "codex" / "migrated")
        self.assertFalse(overlay.exists(), list(wt.rglob("migrated")))
        # Secret-like file *names* are denied by policy pattern matching.
        reason = worktree_migration._rejection_reason(
            "codex", Path("api-key.json"), None)
        self.assertEqual(reason, "secret_like_path", reason)

    def test_invalid_legacy_worktree_root_type_is_rejected_durably(self):
        wt = self.make_worktree(TASK_A)
        source = self.write_legacy(wt, json.dumps({
            "provider": "codex",
            "task_id": TASK_A,
            "worktree_root": {"not": "a path"},
        }), name="active-task.json")

        result = worktree_migration.migrate_legacy("codex", wt)

        entry = next(x for x in result["results"] if x["source"] == str(source))
        self.assertIn("rejected", entry)
        self.assertIn("worktree_root", entry["rejected"])
        self.assertTrue(Path(entry["record"]).is_file())
        self.assertTrue(source.is_file())


class QuarantineRollbackGuardTests(MigrationFixture):
    def _quarantined(self, gate="HUMAN-GATE-TOKEN-0001"):
        wt = self.make_worktree(TASK_A)
        source = self.eligible_legacy(wt)
        result = worktree_migration.migrate_legacy(
            "codex", wt, quarantine=True, human_gate=gate)
        return wt, source, result

    def _historical_quarantine(self, wt=None, source=None):
        """Build a historical manifest without exercising destructive code."""
        wt = wt or self.make_worktree(TASK_A)
        source = source or self.eligible_legacy(wt)
        result = worktree_migration.migrate_legacy("codex", wt)
        entry = result["results"][0]
        dest = Path(entry["migrated_to"])
        digest = harnesslib.sha256_file(source)
        backup_dir = dest.parent / "quarantine-backup"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"{source.name}.{digest[:16]}"
        backup.write_bytes(source.read_bytes())
        manifest = {
            "schema_version": worktree_migration.QUARANTINE_SCHEMA,
            "status": "QUARANTINED",
            "task_id": TASK_A,
            "provider": "codex",
            "worktree_root": str(wt),
            "worktree_id": self.wid_of(wt),
            "source": str(source),
            "source_sha256": digest,
            "backup": str(backup),
            "backup_sha256": digest,
            "overlay_copy": str(dest),
            "overlay_copy_sha256": harnesslib.sha256_file(dest),
            "receipt": str(self.receipt_dir(self.wid_of(wt)) / "historical.json"),
            "human_gate": "recorded",
        }
        manifest["receipt_sha256"] = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        manifest_path = self.receipt_dir(self.wid_of(wt)) / "historical.quarantine.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        source.unlink()
        return wt, source, manifest_path, backup

    def test_quarantine_refused_without_human_gate_token(self):
        wt = self.make_worktree(TASK_A)
        source = self.eligible_legacy(wt)
        wid = self.wid_of(wt)
        original = source.read_text(encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "human gate"):
            worktree_migration.migrate_legacy("codex", wt, quarantine=True)

        # Non-destructive refusal: source untouched, nothing quarantined.
        self.assertTrue(source.is_file())
        self.assertEqual(source.read_text(encoding="utf-8"), original)
        manifests = list(self.receipt_dir(wid).glob("*quarantine.json"))
        self.assertEqual(manifests, [], list(self.receipt_dir(wid).iterdir()))
        with self.assertRaisesRegex(ValueError, "human gate"):
            worktree_migration.quarantine_legacy(
                "codex", wt, source, harnesslib.sha256_file(source), wid, None)
        with self.assertRaisesRegex(ValueError, "too short"):
            worktree_migration.quarantine_legacy(
                "codex", wt, source, harnesslib.sha256_file(source), wid, "short")

    def test_quarantine_rejects_destructive_removal_and_preserves_source(self):
        wt, source, result = self._quarantined()
        self.assertTrue(source.exists())
        entry = result["results"][0]
        self.assertIn("rejected", entry["quarantine"])
        manifest = json.loads(Path(entry["quarantine"]["record"]).read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "QUARANTINE_REJECTED")
        self.assertIn("source preserved", manifest["reason"])
        self.assertEqual(manifest["human_gate"], "recorded")

    def test_authorized_quarantine_rerun_reuses_same_rejection(self):
        wt, source, first = self._quarantined()
        first_record = Path(first["results"][0]["quarantine"]["record"])
        original_bytes = first_record.read_bytes()

        again = worktree_migration.migrate_legacy(
            "codex", wt, quarantine=True, human_gate="HUMAN-GATE-TOKEN-0001")
        entry = again["results"][0]
        self.assertTrue(entry["already_migrated"])
        self.assertEqual(entry["quarantine"]["record"], str(first_record))
        self.assertEqual(first_record.read_bytes(), original_bytes)
        self.assertTrue(source.is_file())
        self.assertFalse(any(self.receipt_dir(self.wid_of(wt)).glob("*.collision.*")))

    def test_quarantine_rejection_mismatch_fails_closed(self):
        wt, source, first = self._quarantined()
        record = Path(first["results"][0]["quarantine"]["record"])
        payload = json.loads(record.read_text(encoding="utf-8"))
        payload["reason"] = "tampered"
        record.write_text(json.dumps(payload), encoding="utf-8")
        altered_bytes = record.read_bytes()

        with self.assertRaisesRegex(ValueError, "quarantine rejection receipt collision"):
            worktree_migration.migrate_legacy(
                "codex", wt, quarantine=True, human_gate="HUMAN-GATE-TOKEN-0001")
        self.assertEqual(record.read_bytes(), altered_bytes)
        self.assertTrue(source.is_file())

    def test_rollback_from_verified_backup_restores_source(self):
        wt = self.make_worktree(TASK_A)
        source = self.eligible_legacy(wt)
        original = source.read_text(encoding="utf-8")
        _wt, source, manifest, _backup = self._historical_quarantine(wt, source)
        self.assertFalse(source.exists())

        rolled = worktree_migration.rollback_receipt(manifest)

        self.assertEqual(rolled["restored"], str(source))
        self.assertTrue(source.is_file())
        self.assertEqual(source.read_text(encoding="utf-8"), original)
        record = json.loads(Path(rolled["record"]).read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "ROLLED_BACK")
        self.assertTrue(Path(str(manifest) + ".rollback.json").is_file())

    def test_rollback_stops_on_concurrent_backup_change(self):
        wt, source, manifest, backup = self._historical_quarantine()
        backup.write_text('{"mutated by a concurrent writer": true}',
                          encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "backup hash mismatch"):
            worktree_migration.rollback_receipt(manifest)

        self.assertFalse(source.exists())
        # Nothing was restored or destroyed; the manifest is unchanged.
        self.assertTrue(backup.is_file())
        self.assertFalse(Path(str(manifest) + ".rollback.json").exists())

    def test_rollback_refuses_reparse_and_existing_source_paths(self):
        wt, source, manifest_path, _backup = self._historical_quarantine()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        backup = Path(manifest["backup"])

        # Re-parse point in the restore path is rejected before any write.
        outside = self.tmp / "outside-target"
        outside.mkdir(exist_ok=True)
        link = self.repo / ".harness" / "codex" / "link-dir"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("symlink unavailable: %s" % exc)
        reparse_manifest = dict(manifest)
        reparse_manifest["source"] = str(link / "restored.json")
        path = self.receipt_dir(self.wid_of(wt)) / "synthetic.quarantine.json"
        path.write_text(json.dumps(reparse_manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "reparse|symlink"):
            worktree_migration.rollback_receipt(path)
        self.assertFalse((self.repo / ".harness" / "codex" / "restored.json").exists())

        # Source path already present: refused, nothing overwritten.
        compare = self.receipt_dir(self.wid_of(wt)) / "existing.quarantine.json"
        existing_manifest = dict(manifest)
        existing_manifest["source"] = str(source.parent / "already-there.json")
        (source.parent / "already-there.json").write_text("{}", encoding="utf-8")
        compare.write_text(json.dumps(existing_manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "already exists"):
            worktree_migration.rollback_receipt(compare)
        self.assertEqual(
            (source.parent / "already-there.json").read_text(encoding="utf-8"),
            "{}",
        )
        self.assertTrue(backup.is_file())

        # Exclusive byte writes reject destinations behind a reparse point.
        with self.assertRaisesRegex(ValueError, "reparse|symlink"):
            worktree_migration.write_json_exclusive_bytes(
                link / "escaped.json", b"{}")

    def test_exclusive_byte_write_failure_removes_partial_destination(self):
        destination = self.tmp / "exclusive.json"
        with mock.patch.object(
            worktree_migration.os, "fdopen", side_effect=OSError("fd wrapper failed")
        ):
            with self.assertRaisesRegex(OSError, "fd wrapper failed"):
                worktree_migration.write_json_exclusive_bytes(destination, b"bytes")
        self.assertFalse(destination.exists())

    def test_rollback_rejects_tampered_backup_provenance(self):
        wt, source, manifest_path, _backup = self._historical_quarantine()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["backup"] = str(self.tmp / "outside-backup.json")
        forged = self.receipt_dir(self.wid_of(wt)) / "forged.quarantine.json"
        forged.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "integrity|backup provenance"):
            worktree_migration.rollback_receipt(forged)
        self.assertFalse(source.exists())

    def test_quarantine_preserves_changed_source(self):
        wt = self.make_worktree(TASK_A)
        source = self.eligible_legacy(wt)
        original_digest = harnesslib.sha256_file(source)
        migrated = worktree_migration.migrate_legacy("codex", wt)
        source.write_text('{"changed": true}\n', encoding="utf-8")

        rejected = worktree_migration.quarantine_legacy(
            "codex", wt, source, original_digest,
            self.wid_of(wt), "HUMAN-GATE-TOKEN-0001")
        self.assertIn("source preserved", rejected["rejected"])
        self.assertTrue(source.is_file())
        self.assertEqual(source.read_text(encoding="utf-8"), '{"changed": true}\n')


if __name__ == "__main__":
    unittest.main()
