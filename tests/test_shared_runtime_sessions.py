"""Behavioral session-isolation oracles using disposable real Git worktrees.

No test activates a task in the developer's checkout or reads a host inventory.
The two-process handshake releases both activations only after imports finish.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]

ACTIVATE_WORKER = r'''
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path.cwd() / "scripts"))
import harnesslib
from providers import opencode_activate_task as activation

# Only model discovery/selection is a deterministic fixture. Task activation,
# shared evidence persistence, Git resolution and binding writes remain real.
activation._select_inventory = lambda: (None, None, {"network_refresh": False})
activation.selections_for_task = lambda *args: [{
    "agent": "implementer", "model_id": "fixture/local", "status": "selected",
    "action": "use", "reasoning_effort": "low",
}]
print("READY", flush=True)
assert sys.stdin.readline().strip() == "GO"
active = activation.activate(Path.cwd() / "tasks" / (sys.argv[1] + ".json"))
print(json.dumps({
    "active_path": str(activation.ACTIVE.resolve()),
    "model_path": str(activation.provider_model_selections_path("opencode").resolve()),
    "runtime_root": str(harnesslib.runtime_root()),
    "shared_run": str(harnesslib.run_dir("SESSION-A")),
    "task_id": active["task_id"],
}))
'''


EVIDENCE_WORKER = r'''
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "scripts"))
import evidence
print("READY", flush=True)
assert sys.stdin.readline().strip() == "GO"
evidence.append("SESSION-A", "implementation", "DETERMINISTIC", sys.argv[1], "PASS", "implementer")
print("DONE", flush=True)
'''

CODEX_ACTIVATE_WORKER = r'''
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path.cwd() / "scripts"))
import harnesslib
from providers import codex_activate_task as activation

# Use a distinct local fixture inventory per checkout and exercise the real
# activator inventory loader/selector path, not a shared-run substitute.
inventory_path = activation.provider_enriched_inventory_path("codex")
inventory_path.parent.mkdir(parents=True, exist_ok=True)
inventory_path.write_text(json.dumps({
    "schema_version": 3,
    "provider": "codex",
    "generated_at": "2026-01-01T00:00:00Z",
    "models": [{
        "id": "fixture/" + harnesslib.worktree_identity()["worktree_id"][:8], "enabled": True,
        "capabilities": {"reasoning": 5, "coding": 5, "tool_use": 5, "reliability": 5},
        "cost": 1, "latency": 1,
    }],
}), encoding="utf-8")
activation.discover_provider = lambda *args, **kwargs: []
print("READY", flush=True)
assert sys.stdin.readline().strip() == "GO"
active = activation.activate(Path.cwd() / "tasks" / (sys.argv[1] + ".json"))
models = json.loads(activation.provider_model_selections_path("codex").read_text(encoding="utf-8"))
print(json.dumps({
    "active_path": str(activation.ACTIVE.resolve()),
    "model_path": str(activation.provider_model_selections_path("codex").resolve()),
    "inventory_path": str(inventory_path.resolve()),
    "inventory_model": models["selections"][0].get("model_id"),
    "task_id": active["task_id"],
}))
'''


SUBSCRIPTION_ACTIVATE_WORKER = r'''
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path.cwd() / "scripts"))
import harnesslib
import subscription_bridge as activation

activation.build_inventory = lambda providers=None: {
    "schema_version": 1,
    "provider": "subscriptions",
    "generated_at": "2026-01-01T00:00:00Z",
    "requested_providers": list(providers or ["cursor"]),
    "models": [{
        "id": "cursor/fixture-" + harnesslib.worktree_identity()["worktree_id"][:8],
        "provider": "cursor",
        "model_id": "fixture-" + harnesslib.worktree_identity()["worktree_id"][:8],
        "enabled": True,
        "capabilities": {"reasoning": 5, "coding": 5, "tool_use": 5, "reliability": 5},
    }],
    "providers": {"cursor": {"installed": True}},
}
activation.selections_for_task = lambda task, provider, inventory: [{
    "agent": "implementer", "model_id": inventory["models"][0]["id"],
    "base_model_id": inventory["models"][0]["id"], "status": "selected",
    "action": "use", "reasoning_effort": "low",
}]
print("READY", flush=True)
assert sys.stdin.readline().strip() == "GO"
active = activation.activate(Path.cwd() / "tasks" / (sys.argv[1] + ".json"))
print(json.dumps({
    "active_path": str(activation.ACTIVE_PATH.resolve()),
    "model_path": str(harnesslib.provider_model_selections_path("subscriptions").resolve()),
    "inventory_path": str(harnesslib.provider_inventory_path("subscriptions").resolve()),
    "task_id": active["task_id"],
    "worktree_id": active["overlay"]["worktree_id"],
}))
'''


class SharedRuntimeSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="harness-session-isolation-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.primary = self.base / "primary checkout"
        self.linked = self.base / "linked checkout"
        self.primary.mkdir()
        self.env = dict(os.environ)
        for name in list(self.env):
            if name.startswith(("HARNESS_", "GIT_")):
                self.env.pop(name)
        self.env.update({
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull, "PYTHONDONTWRITEBYTECODE": "1",
        })
        for name in ("scripts", "harness", ".agents"):
            shutil.copytree(ROOT / name, self.primary / name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for name in ("AGENTS.md", "AI_POLICY.md"):
            shutil.copyfile(ROOT / name, self.primary / name)
        (self.primary / ".gitignore").write_text(".harness/\n", encoding="utf-8")
        (self.primary / "tasks").mkdir()
        for task_id in ("SESSION-A", "SESSION-B"):
            (self.primary / "tasks" / (task_id + ".json")).write_text(json.dumps({
                "id": task_id, "description": "Document local runtime state",
                "acceptance_criteria": ["Local provider activation remains isolated"],
                "files": ["AGENTS.md"], "tags": ["docs"],
            }), encoding="utf-8")
        self.git("init", "-b", "main")
        self.git("config", "user.email", "session-fixture@test.invalid")
        self.git("config", "user.name", "Session Fixture")
        self.git("add", ".")
        self.git("commit", "-m", "disposable session fixture")
        self.git("worktree", "add", "-b", "linked", str(self.linked))

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.primary, env=self.env,
                              text=True, encoding="utf-8", errors="replace",
                              capture_output=True, check=True, timeout=30)

    def test_codex_refresh_active_uses_the_shared_migration_lock(self):
        result = self.python(self.primary, '''
import sys
from contextlib import contextmanager
from unittest.mock import patch
sys.path.insert(0, "scripts")
from providers import codex_activate_task as activation

@contextmanager
def lock_probe():
    yield

with patch.object(activation, "shared_migration_lock", lock_probe), \\
     patch.object(activation, "_refresh_active_unlocked", return_value={"refreshed": True}) as refresh:
    assert activation.refresh_active() == {"refreshed": True}
    refresh.assert_called_once_with()
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_enriched_inventory_path_honors_an_alternate_repository_root(self):
        result = self.python(self.primary, '''
import sys
from pathlib import Path
sys.path.insert(0, "scripts")
import harnesslib
root = Path("alternate-root")
expected = root.resolve() / "harness" / "model-inventories" / "codex.json"
assert harnesslib.provider_enriched_inventory_path("codex", root) == expected
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def _terminate_tree(self, proc):
        """Kill a fixture subprocess and any grandchildren it left behind.

        On Windows, killing only the direct child leaves grandchildren (git)
        holding the stdout/stderr pipes, so a later communicate() never sees
        EOF and blocks forever. taskkill /T removes the whole tree.
        """
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=15)
            proc.kill()
            proc.wait(timeout=15)
        except (OSError, subprocess.SubprocessError, ValueError):
            try:
                proc.kill()
            except OSError:
                pass

    def _stop_workers(self, workers):
        """Release fixture subprocesses with bounded waits in every path."""
        for worker in workers:
            if worker.poll() is None:
                self._terminate_tree(worker)
        for worker in workers:
            try:
                worker.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                self._terminate_tree(worker)
                try:
                    worker.communicate(timeout=15)
                except (subprocess.TimeoutExpired, ValueError):
                    pass

    def python(self, root, code, timeout=40):
        """Run fixture code with a hard, tree-scoped deadline: fail, never hang."""
        worker = subprocess.Popen(
            [sys.executable, "-c", code], cwd=root, env=self.env,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace",
        )
        try:
            stdout, stderr = worker.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            self._terminate_tree(worker)
            try:
                worker.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                pass
            partial = "".join(
                part.decode("utf-8", errors="replace") if isinstance(part, bytes) else (part or "")
                for part in (exc.stdout, exc.stderr)
            )
            self.fail(
                f"fixture subprocess exceeded {timeout}s in {root} "
                f"(runtime state unavailable or wedged); partial output: {partial[-2000:]}"
            )
        return subprocess.CompletedProcess(worker.args, worker.returncode, stdout, stderr)

    def test_real_linked_worktrees_share_identical_run_evidence(self):
        code = (
            'import sys; sys.path.insert(0, "scripts"); '
            'from harnesslib import run_dir; print(run_dir("SESSION-A"))'
        )
        primary = self.python(self.primary, code)
        linked = self.python(self.linked, code)
        self.assertEqual(primary.returncode, 0, primary.stderr)
        self.assertEqual(linked.returncode, 0, linked.stderr)
        self.assertEqual(primary.stdout, linked.stdout)
        shared = Path(primary.stdout.strip())
        shared.mkdir(parents=True)
        payload = b'{"evidence":"same durable bytes"}\n'
        (shared / "fixture-evidence.jsonl").write_bytes(payload)
        self.assertEqual((Path(linked.stdout.strip()) / "fixture-evidence.jsonl").read_bytes(), payload)

    def test_concurrent_activation_uses_disjoint_local_provider_overlays(self):
        workers = []
        try:
            for root, task_id in ((self.primary, "SESSION-A"), (self.linked, "SESSION-B")):
                workers.append(subprocess.Popen(
                    [sys.executable, "-c", ACTIVATE_WORKER, task_id], cwd=root,
                    env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                ))
            for worker in workers:
                self.assertEqual(worker.stdout.readline().strip(), "READY")
            for worker in workers:
                worker.stdin.write("GO\n")
                worker.stdin.flush()
            results = []
            for worker in workers:
                stdout, stderr = worker.communicate(timeout=60)
                self.assertEqual(worker.returncode, 0, stderr)
                results.append(json.loads(stdout.strip().splitlines()[-1]))
            first, second = results
            self.assertEqual(first["task_id"], "SESSION-A")
            self.assertEqual(second["task_id"], "SESSION-B")
            self.assertEqual(first["shared_run"], second["shared_run"])
            for task_id in ("SESSION-A", "SESSION-B"):
                snapshot = self.primary / ".harness" / "runs" / task_id / "task.json"
                self.assertEqual(json.loads(snapshot.read_text(encoding="utf-8"))["id"], task_id)
            self.assertNotEqual(first["active_path"], second["active_path"],
                                "Concurrent worktrees overwrite the same OpenCode active binding")
            self.assertNotEqual(first["model_path"], second["model_path"],
                                "Concurrent worktrees overwrite the same model-selection state")
            for root, result in zip((self.primary, self.linked), results):
                active_path = Path(result["active_path"])
                self.assertTrue(active_path.is_relative_to(root.resolve()))
                active = json.loads(active_path.read_text(encoding="utf-8"))
                self.assertEqual(active["task_id"], result["task_id"])
                self.assertEqual(active["provider"], "opencode")
                self.assertIn("worktree_id", active["overlay"])
                model_path = Path(result["model_path"])
                self.assertTrue(model_path.is_relative_to(root.resolve()))
                self.assertEqual(json.loads(model_path.read_text(encoding="utf-8"))["task_id"], result["task_id"])

            # A normal lifecycle clear must only affect the calling checkout.
            linked_active = Path(second["active_path"])
            linked_before = linked_active.read_bytes()
            cleared = subprocess.run(
                [sys.executable, "scripts/providers/opencode_activate_task.py", "--clear",
                 "--human-gate", "approved-session-clear-001"],
                cwd=self.primary, env=self.env, text=True, encoding="utf-8", errors="replace",
                capture_output=True, timeout=40,
            )
            self.assertNotEqual(cleared.returncode, 0,
                                "Windows clear must fail closed when atomic ownership cannot be proven")
            self.assertIn("source preserved", cleared.stderr)
            self.assertTrue(Path(first["active_path"]).exists())
            self.assertEqual(linked_active.read_bytes(), linked_before)
        finally:
            self._stop_workers(workers)

    def test_same_task_concurrent_activation_keeps_distinct_bindings(self):
        workers = []
        try:
            for root in (self.primary, self.linked):
                workers.append(subprocess.Popen(
                    [sys.executable, "-c", ACTIVATE_WORKER, "SESSION-A"], cwd=root,
                    env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                ))
            for worker in workers:
                self.assertEqual(worker.stdout.readline().strip(), "READY")
                worker.stdin.write("GO\n")
                worker.stdin.flush()
            results = []
            for worker in workers:
                stdout, stderr = worker.communicate(timeout=60)
                self.assertEqual(worker.returncode, 0, stderr)
                results.append(json.loads(stdout.strip().splitlines()[-1]))
            self.assertEqual(results[0]["task_id"], results[1]["task_id"])
            self.assertNotEqual(results[0]["active_path"], results[1]["active_path"])
            self.assertNotEqual(results[0]["model_path"], results[1]["model_path"])
            first_id = json.loads(Path(results[0]["active_path"]).read_text())["overlay"]["worktree_id"]
            second_id = json.loads(Path(results[1]["active_path"]).read_text())["overlay"]["worktree_id"]
            self.assertNotEqual(first_id, second_id)
        finally:
            self._stop_workers(workers)

    def test_concurrent_subscription_activation_keeps_local_sessions_and_inventories(self):
        workers = []
        try:
            for root, task_id in ((self.primary, "SESSION-A"), (self.linked, "SESSION-B")):
                workers.append(subprocess.Popen(
                    [sys.executable, "-c", SUBSCRIPTION_ACTIVATE_WORKER, task_id],
                    cwd=root, env=self.env, stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    encoding="utf-8", errors="replace",
                ))
            for worker in workers:
                self.assertEqual(worker.stdout.readline().strip(), "READY")
                worker.stdin.write("GO\n")
                worker.stdin.flush()
            results = []
            for worker in workers:
                stdout, stderr = worker.communicate(timeout=60)
                self.assertEqual(worker.returncode, 0, stderr)
                results.append(json.loads(stdout.strip().splitlines()[-1]))

            first, second = results
            self.assertNotEqual(first["active_path"], second["active_path"])
            self.assertNotEqual(first["model_path"], second["model_path"])
            self.assertNotEqual(first["inventory_path"], second["inventory_path"])
            self.assertNotEqual(first["worktree_id"], second["worktree_id"])
            for root, result in zip((self.primary, self.linked), results):
                active_path = Path(result["active_path"])
                self.assertTrue(active_path.is_relative_to(root.resolve()))
                active = json.loads(active_path.read_text(encoding="utf-8"))
                self.assertEqual(active["provider"], "subscriptions")
                self.assertEqual(active["task_id"], result["task_id"])
                inventory = json.loads(Path(result["inventory_path"]).read_text(encoding="utf-8"))
                self.assertEqual(inventory["provider"], "subscriptions")
                self.assertTrue(Path(result["inventory_path"]).is_relative_to(root.resolve()))
        finally:
            self._stop_workers(workers)

    def test_provider_session_and_audit_paths_are_worktree_local(self):
        code = '''
import json, sys
from pathlib import Path
sys.path.insert(0, "scripts")
import harnesslib
print(json.dumps({
    "worktree_id": harnesslib.worktree_identity()["worktree_id"],
    "session": str(harnesslib.provider_session_path("codex").resolve()),
    "audit": str(harnesslib.provider_audit_path("codex").resolve()),
    "catalog": str(harnesslib.provider_catalog_path("codex").resolve()),
}))
'''
        first = json.loads(self.python(self.primary, code).stdout)
        second = json.loads(self.python(self.linked, code).stdout)
        self.assertNotEqual(first["worktree_id"], second["worktree_id"])
        for key in ("session", "audit", "catalog"):
            self.assertNotEqual(first[key], second[key])
            self.assertTrue(Path(first[key]).is_relative_to(self.primary.resolve()))
            self.assertTrue(Path(second[key]).is_relative_to(self.linked.resolve()))

    def test_subscription_runtime_state_is_worktree_local(self):
        code = '''
import json, sys
sys.path.insert(0, "scripts")
import harnesslib
print(json.dumps({
    "inventory": str(harnesslib.provider_inventory_path("subscriptions").resolve()),
    "selection": str(harnesslib.provider_model_selections_path("subscriptions").resolve()),
    "session": str(harnesslib.provider_session_path("subscriptions").resolve()),
    "audit": str(harnesslib.provider_audit_path("subscriptions").resolve()),
}))
'''
        first = json.loads(self.python(self.primary, code).stdout)
        second = json.loads(self.python(self.linked, code).stdout)
        for key in first:
            self.assertNotEqual(first[key], second[key])
            self.assertTrue(Path(first[key]).is_relative_to(self.primary.resolve()))
            self.assertTrue(Path(second[key]).is_relative_to(self.linked.resolve()))

    def test_foreign_active_binding_is_rejected_by_real_consumer_validator(self):
        worker = subprocess.Popen(
            [sys.executable, "-c", CODEX_ACTIVATE_WORKER, "SESSION-A"],
            cwd=self.primary, env=self.env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace",
        )
        try:
            self.assertEqual(worker.stdout.readline().strip(), "READY")
            worker.stdin.write("GO\n")
            worker.stdin.flush()
            stdout, stderr = worker.communicate(timeout=60)
            self.assertEqual(worker.returncode, 0, stderr)
            result = json.loads(stdout.strip().splitlines()[-1])
        finally:
            self._stop_workers([worker])
        source = Path(result["active_path"])
        identity = self.python(self.linked, 'import sys; sys.path.insert(0, "scripts"); import harnesslib; print(harnesslib.worktree_identity()["worktree_id"])')
        self.assertEqual(identity.returncode, 0, identity.stderr)
        target = self.linked / ".harness" / "overlays" / identity.stdout.strip() / "codex" / "active-task.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        rejected = self.python(self.linked, '''
import sys
sys.path.insert(0, "scripts")
import harnesslib
try:
    harnesslib.read_provider_active("codex")
except ValueError as exc:
    assert "another worktree" in str(exc), exc
else:
    raise SystemExit("foreign active binding was accepted")
''')
        self.assertEqual(rejected.returncode, 0, rejected.stderr)

    def test_concurrent_real_evidence_appends_form_one_valid_shared_chain(self):
        workers = []
        try:
            for index, root in enumerate((self.primary, self.linked, self.primary, self.linked)):
                workers.append(subprocess.Popen(
                    [sys.executable, "-c", EVIDENCE_WORKER, f"worker-{index}"], cwd=root,
                    env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                ))
            for worker in workers:
                self.assertEqual(worker.stdout.readline().strip(), "READY")
            for worker in workers:
                worker.stdin.write("GO\n")
                worker.stdin.flush()
            for worker in workers:
                stdout, stderr = worker.communicate(timeout=60)
                self.assertEqual(worker.returncode, 0, stderr)
                self.assertIn("DONE", stdout)
            check = self.python(self.primary, 'import sys; sys.path.insert(0,"scripts"); import evidence; print(evidence.validate("SESSION-A"))')
            self.assertEqual(check.returncode, 0, check.stderr)
            self.assertIn("'valid': True", check.stdout)
            self.assertIn("'entries': 4", check.stdout)
            rows = json.loads("[" + ",".join(
                line for line in (self.primary / ".harness" / "runs" / "SESSION-A" / "evidence.jsonl").read_text(encoding="utf-8").splitlines()
            ) + "]")
            self.assertEqual({row["claim"] for row in rows}, {f"worker-{i}" for i in range(4)})
        finally:
            self._stop_workers(workers)

    def test_normal_clear_rejects_legacy_binding_without_mutation(self):
        legacy = self.primary / ".harness" / "opencode" / "active-task.json"
        legacy.parent.mkdir(parents=True)
        original = b'{"task_id":"SESSION-A","provider":"opencode"}\n'
        legacy.write_bytes(original)
        result = subprocess.run(
            [sys.executable, "scripts/providers/opencode_activate_task.py", "--clear"],
            cwd=self.primary, env=self.env, text=True, encoding="utf-8", errors="replace",
            capture_output=True, timeout=40,
        )
        self.assertNotEqual(result.returncode, 0,
                            "Normal clear silently accepts and deletes an unscoped legacy binding")
        self.assertEqual(legacy.read_bytes(), original)

    def test_receipt_consumer_does_not_select_other_worktrees_legacy_binding(self):
        legacy = self.primary / ".harness" / "opencode" / "active-task.json"
        legacy.parent.mkdir(parents=True)
        original = b'{"task_id":"SESSION-A","provider":"opencode"}\n'
        legacy.write_bytes(original)
        result = self.python(self.linked, '''
import json
import sys
sys.path.insert(0, "scripts")
import receipt_review
try:
    print(json.dumps({"provider": receipt_review._active_provider("SESSION-A")}))
except ValueError as exc:
    print(json.dumps({"rejected": str(exc)}))
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertIsNone(data.get("provider"),
                          "Receipt review in a linked checkout inherits another worktree's provider")
        self.assertEqual(legacy.read_bytes(), original)

    def test_overlay_resolver_rejects_hostile_provider_names(self):
        result = self.python(self.primary, '''
import sys
sys.path.insert(0, "scripts")
from harnesslib import provider_overlay_dir
for value in ("../opencode", "opencode/other", "C:\\\\outside", ""):
    try:
        provider_overlay_dir(value)
    except ValueError:
        continue
    raise SystemExit(f"accepted hostile provider name: {value!r}")
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_worktree_identity_is_repeatable_and_immutable_artifacts_reject_changes(self):
        result = self.python(self.primary, '''
import sys
from pathlib import Path
sys.path.insert(0, "scripts")
import harnesslib
first = harnesslib.worktree_identity()
second = harnesslib.worktree_identity()
assert first == second
target = harnesslib.run_dir("SESSION-A") / "immutable-fixture.json"
harnesslib.write_json_immutable(target, {"version": 1})
before = target.read_bytes()
try:
    harnesslib.write_json_immutable(target, {"version": 2})
except ValueError:
    pass
else:
    raise SystemExit("changed immutable artifact was accepted")
assert target.read_bytes() == before
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_model_selection_binds_task_provider_schema_and_inventory_integrity(self):
        result = self.python(self.primary, '''
import json
import sys
from pathlib import Path
sys.path.insert(0, "scripts")
import harnesslib
inventory = harnesslib.provider_enriched_inventory_path("codex")
inventory.parent.mkdir(parents=True, exist_ok=True)
inventory.write_text(json.dumps({"schema_version": 3, "provider": "codex", "models": []}), encoding="utf-8")
selection = harnesslib.provider_model_selections_path("codex")
selection.parent.mkdir(parents=True, exist_ok=True)
payload = {"schema_version": 2, "task_id": "SESSION-A", "provider": "codex",
           "inventory_path": inventory.relative_to(Path.cwd()).as_posix(),
           "inventory_sha256": harnesslib.sha256_file(inventory), "selections": []}
selection.write_text(json.dumps(payload), encoding="utf-8")
harnesslib.validate_model_selections("codex", "SESSION-A", selection)
inventory.write_text(json.dumps({"schema_version": 3, "provider": "codex", "models": [{"id": "mutated"}]}), encoding="utf-8")
try:
    harnesslib.validate_model_selections("codex", "SESSION-A", selection)
except ValueError as exc:
    assert "integrity" in str(exc)
else:
    raise SystemExit("mutated inventory was accepted")
payload["task_id"] = "SESSION-B"
selection.write_text(json.dumps(payload), encoding="utf-8")
try:
    harnesslib.validate_model_selections("codex", "SESSION-A", selection)
except ValueError as exc:
    assert "task" in str(exc)
else:
    raise SystemExit("foreign task selection was accepted")
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_concurrent_and_corrupt_immutable_artifacts_fail_closed(self):
        target = self.primary / ".harness" / "runs" / "SESSION-A" / "immutable-race.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        barrier = self.base / "immutable-ready"
        barrier_a = self.base / "immutable-ready-a"
        barrier_b = self.base / "immutable-ready-b"
        worker_code = '''
import sys
import time
from pathlib import Path
sys.path.insert(0, "scripts")
import harnesslib
ready = Path(sys.argv[2])
other = Path(sys.argv[4])
ready.open("a").close()
# Bounded barrier wait: never spin forever, never outlive the test parent.
deadline = time.monotonic() + 30
while not other.exists():
    if time.monotonic() > deadline:
        sys.exit("fixture barrier timeout")
    time.sleep(0.05)
path = Path(sys.argv[1])
try:
    harnesslib.write_json_immutable(path, {"writer": sys.argv[3]})
except ValueError:
    Path(sys.argv[5]).write_text("lost", encoding="utf-8")
else:
    Path(sys.argv[5]).write_text("won", encoding="utf-8")
'''
        workers = [
            subprocess.Popen([sys.executable, "-c", worker_code, str(target), str(barrier_a if i == 0 else barrier_b), str(i), str(barrier_b if i == 0 else barrier_a), str(self.base / f"immutable-result-{i}" )],
                             cwd=root, env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for i, root in enumerate((self.primary, self.linked))
        ]
        try:
            for worker in workers:
                # Bounded parent wait. The worker's own 30s barrier deadline
                # exits first, so _stop_workers in finally guarantees no
                # grandchild outlives the parent on any failure path.
                stdout, stderr = worker.communicate(timeout=60)
                self.assertEqual(worker.returncode, 0, stderr)
        finally:
            self._stop_workers(workers)
        self.assertIn(json.loads(target.read_text(encoding="utf-8"))["writer"], {"0", "1"})
        outcomes = {(self.base / "immutable-result-0").read_text(encoding="utf-8"), (self.base / "immutable-result-1").read_text(encoding="utf-8")}
        self.assertEqual(outcomes, {"won", "lost"})
        target.write_text("{corrupt", encoding="utf-8")
        result = self.python(self.primary, f'''
import sys
from pathlib import Path
sys.path.insert(0, "scripts")
import harnesslib
try:
    harnesslib.write_json_immutable(Path(r"{target}"), {{"writer": "repair"}})
except ValueError:
    pass
else:
    raise SystemExit("corrupt immutable artifact was overwritten")
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_all_provider_legacy_state_is_rejected_before_migration(self):
        result = self.python(self.primary, '''
import sys
from pathlib import Path
sys.path.insert(0, "scripts")
from harnesslib import reject_legacy_provider_state
for provider in ("codex", "opencode", "subscriptions"):
    for name in ("active-task.json", "session.json", "permission-audit.jsonl", "catalog-snapshot.json", "model-inventory.json", "enriched-inventory.json", "model-selections.json"):
        target = Path(".harness") / provider / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"legacy")
        try:
            reject_legacy_provider_state(provider)
        except ValueError:
            pass
        else:
            raise SystemExit(f"legacy state accepted for {provider}/{name}")
        assert target.read_bytes() == b"legacy"
        target.unlink()
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_linked_worktree_rejects_legacy_state_in_shared_runtime_root(self):
        legacy = self.primary / ".harness" / "opencode" / "active-task.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_bytes(b'{"legacy":true}\n')
        result = self.python(self.linked, '''
import sys
sys.path.insert(0, "scripts")
from harnesslib import reject_legacy_provider_state
try:
    reject_legacy_provider_state("opencode")
except ValueError:
    pass
else:
    raise SystemExit("shared-runtime legacy state was accepted from linked worktree")
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(legacy.read_bytes(), b'{"legacy":true}\n')

    def test_concurrent_codex_activations_use_distinct_local_inventories(self):
        workers = []
        try:
            for root in (self.primary, self.linked):
                workers.append(subprocess.Popen(
                    [sys.executable, "-c", CODEX_ACTIVATE_WORKER, "SESSION-A"], cwd=root,
                    env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                ))
            for worker in workers:
                self.assertEqual(worker.stdout.readline().strip(), "READY")
                worker.stdin.write("GO\n")
                worker.stdin.flush()
            results = []
            for worker in workers:
                stdout, stderr = worker.communicate(timeout=60)
                self.assertEqual(worker.returncode, 0, stderr)
                results.append(json.loads(stdout.strip().splitlines()[-1]))
            self.assertNotEqual(results[0]["active_path"], results[1]["active_path"])
            self.assertNotEqual(results[0]["inventory_path"], results[1]["inventory_path"])
            self.assertNotEqual(results[0]["model_path"], results[1]["model_path"])
            self.assertNotEqual(results[0]["inventory_model"], results[1]["inventory_model"])
            # The scored inventory is intentionally a fixed per-repo path
            # (<checkout>/harness/model-inventories/<provider>.json), not an
            # overlay artifact. Anchor locality to each checkout root instead
            # of the overlay tree; distinctness across checkouts is asserted
            # above and must not be weakened.
            for root, result in zip((self.primary, self.linked), results):
                self.assertTrue(
                    Path(result["inventory_path"]).is_relative_to(Path(root).resolve()),
                    result["inventory_path"],
                )
        finally:
            self._stop_workers(workers)


if __name__ == "__main__":
    unittest.main()
