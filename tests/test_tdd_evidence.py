import hashlib, json, os, shutil, subprocess, sys, unittest, uuid
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from harnesslib import run_dir
import tdd_evidence
from tdd_evidence import append, finish_decision, read


LOCK_HOLDER = r'''
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "scripts"))
from harnesslib import run_dir
task = sys.argv[1]
lock = run_dir(task) / "tdd-evidence.lock"
lock.parent.mkdir(parents=True, exist_ok=True)
with lock.open("a+b") as handle:
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
        os.fsync(handle.fileno())
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    print("LOCKED", flush=True)
    assert sys.stdin.readline().strip() == "RELEASE"
'''

APPEND_WORKER = r'''
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "scripts"))
from tdd_evidence import append
append(sys.argv[1], "design", "PASS", "test-designer", "concurrent append")
print("APPENDED", flush=True)
'''

SYNCED_APPEND_WORKER = r'''
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "scripts"))
from tdd_evidence import append
print("READY", flush=True)
assert sys.stdin.readline().strip() == "GO"
append(sys.argv[1], "design", "PASS", "test-designer", "simultaneous append")
print("APPENDED", flush=True)
'''

class TDDEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.task="TDDTEST-"+uuid.uuid4().hex[:10].upper()
        d=run_dir(self.task); d.mkdir(parents=True,exist_ok=True)
        (d/"route.json").write_text(json.dumps({"task_id":self.task,"tdd":{"required_evidence":["design","red","green"]}}),encoding="utf-8")
    def tearDown(self):
        shutil.rmtree(run_dir(self.task),ignore_errors=True)

    def test_finish_blocks_until_valid_red_green(self):
        self.assertFalse(finish_decision(self.task)["allow"])
        append(self.task,"design","PASS","test-designer","oracle established")
        append(self.task,"red","PASS","implementer","expected assertion fails",command="pytest x",exit_code=1)
        self.assertFalse(finish_decision(self.task)["allow"])
        append(self.task,"green","PASS","implementer","same test passes",command="pytest x",exit_code=0)
        self.assertTrue(finish_decision(self.task)["allow"])

    def test_red_zero_exit_is_invalid(self):
        append(self.task,"design","PASS","test-designer","oracle established")
        append(self.task,"red","PASS","implementer","bad red",command="pytest x",exit_code=0)
        append(self.task,"green","PASS","implementer","green",command="pytest x",exit_code=0)
        self.assertIn("tdd:red",finish_decision(self.task)["failing"])

    def _failed_legacy(self):
        append(self.task, "design", "PASS", "test-designer", "old design")
        append(self.task, "red", "PASS", "implementer", "old red", command="test", exit_code=1)
        append(self.task, "green", "PASS", "implementer", "old green", command="test", exit_code=0)
        legacy = run_dir(self.task) / "tdd-evidence.jsonl"
        rows = legacy.read_text(encoding="utf-8").splitlines()
        record = json.loads(rows[2])
        record["claim"] = "corrupted third entry"
        rows[2] = json.dumps(record)
        legacy.write_text("\n".join(rows) + "\n", encoding="utf-8")
        return legacy, legacy.read_bytes()

    def _supersede(self):
        result = subprocess.run(
            [sys.executable, "scripts/tdd_evidence.py", "supersede", self.task,
             "--reason", "preserve failed evidence and rerun TDD"],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_supersession_preserves_failure_and_requires_new_design_red_green(self):
        legacy, original = self._failed_legacy()
        self.assertIn("entry 3", finish_decision(self.task)["reason"])
        manifest = self._supersede()
        self.assertEqual(legacy.read_bytes(), original)
        self.assertEqual(manifest["task_id"], self.task)
        failed = manifest["superseded"][0]
        self.assertEqual(failed["path"], "tdd-evidence.jsonl")
        self.assertEqual(failed["sha256"], hashlib.sha256(original).hexdigest())
        self.assertIn("entry 3", failed["validation_error"])
        self.assertTrue(failed["reason"])
        self.assertEqual(read(self.task), [])
        decision = finish_decision(self.task)
        self.assertEqual(set(decision["missing"]), {"tdd:design", "tdd:red", "tdd:green"})
        self.assertEqual(decision["active_attempt"], manifest["active_attempt"])
        append(self.task, "design", "PASS", "test-designer", "new design")
        append(self.task, "red", "PASS", "implementer", "invalid red", command="test", exit_code=0)
        append(self.task, "green", "PASS", "implementer", "green", command="test", exit_code=0)
        self.assertIn("tdd:red", finish_decision(self.task)["failing"])
        append(self.task, "red", "PASS", "implementer", "expected failure", command="test", exit_code=1)
        append(self.task, "green", "PASS", "implementer", "same test passes", command="test", exit_code=0)
        self.assertTrue(finish_decision(self.task)["allow"])
        self.assertTrue(all(r["attempt"] == manifest["active_attempt"] for r in read(self.task)))
        self.assertEqual(legacy.read_bytes(), original)

    def test_implement_and_finish_gates_require_active_attempt(self):
        import evidence, gate, orchestrator
        directory = run_dir(self.task)
        route = json.loads((directory / "route.json").read_text(encoding="utf-8"))
        route.update({"risk": "R0", "agents": [], "isolation": "none"})
        (directory / "route.json").write_text(json.dumps(route), encoding="utf-8")
        (directory / "progress.json").write_text(json.dumps({
            "risk": "R0", "state": "RUNNING", "current_step": "CLOSE",
            "steps": ["IMPLEMENT", "CHECKS", "CLOSE"], "completed": ["IMPLEMENT", "CHECKS"],
        }), encoding="utf-8")
        (directory / "checks-report.json").write_text(json.dumps({
            "task_id": self.task, "status": "PASS",
        }), encoding="utf-8")
        evidence.append(self.task, "checks", "DETERMINISTIC", "fixture check", "PASS", "check-runner",
                        command="test", exit_code=0,
                        artifact=f".harness/runs/{self.task}/checks-report.json")
        self._failed_legacy()
        self._supersede()
        with self.assertRaisesRegex(ValueError, "required TDD evidence"):
            orchestrator._validate_pass_prerequisites(self.task, "IMPLEMENT", via_commit=True)
        self.assertIn("tdd:design", gate.finish_decision(self.task, "R0")["missing"])
        append(self.task, "design", "PASS", "test-designer", "new design")
        append(self.task, "red", "PASS", "implementer", "observed failure", command="test", exit_code=1)
        append(self.task, "green", "PASS", "implementer", "observed pass", command="test", exit_code=0)
        orchestrator._validate_pass_prerequisites(self.task, "IMPLEMENT", via_commit=True)
        self.assertTrue(gate.finish_decision(self.task, "R0")["allow"])
        tdd_evidence.path(self.task).write_bytes(b"corrupt active evidence\n")
        self.assertIn("tdd_evidence_chain", gate.finish_decision(self.task, "R0")["failing"])

    def test_deleted_selector_cannot_reuse_even_a_valid_legacy_chain(self):
        append(self.task, "design", "PASS", "test-designer", "old valid design")
        legacy = run_dir(self.task) / "tdd-evidence.jsonl"
        valid = legacy.read_bytes()
        legacy.write_bytes(b"broken chain\n")
        self._supersede()
        legacy.write_bytes(valid)
        (run_dir(self.task) / "tdd-attempts.json").unlink()
        with self.assertRaisesRegex(ValueError, "selection missing"):
            read(self.task)

    def test_records_cannot_be_copied_from_another_attempt(self):
        self._failed_legacy()
        first = self._supersede()
        append(self.task, "design", "PASS", "test-designer", "first attempt")
        active = run_dir(self.task) / first["active_attempt"]
        valid = active.read_bytes()
        active.write_bytes(b"broken chain\n")
        second = self._supersede()
        (run_dir(self.task) / second["active_attempt"]).write_bytes(valid)
        with self.assertRaisesRegex(ValueError, "attempt/task mismatch"):
            read(self.task)

    def test_missing_or_corrupt_selection_never_falls_back(self):
        self._failed_legacy()
        manifest = self._supersede()
        selector = run_dir(self.task) / "tdd-attempts.json"
        original = selector.read_bytes()
        for content in (b"{", b"{}", None):
            with self.subTest(content=content):
                if content is None:
                    selector.unlink()
                else:
                    selector.write_bytes(content)
                self.assertFalse(finish_decision(self.task)["allow"])
                with self.assertRaises(ValueError):
                    read(self.task)
                with self.assertRaises(ValueError):
                    append(self.task, "design", "PASS", "test-designer", "must not append")
                selector.write_bytes(original)
        active = run_dir(self.task) / manifest["active_attempt"]
        active.unlink()
        self.assertFalse(finish_decision(self.task)["allow"])
        with self.assertRaises(ValueError):
            append(self.task, "design", "PASS", "test-designer", "must not recreate")

    def test_changed_history_and_foreign_selector_fail_closed(self):
        legacy, original = self._failed_legacy()
        manifest = self._supersede()
        legacy.write_bytes(original + b"\n")
        self.assertFalse(finish_decision(self.task)["allow"])
        with self.assertRaises(ValueError):
            read(self.task)
        legacy.write_bytes(original)
        selector = run_dir(self.task) / "tdd-attempts.json"
        for change in ({"task_id": "OTHER-TASK"}, {"active_attempt": "../outside.jsonl"},
                       {"active_attempt": "tdd-evidence.jsonl"}, {"superseded": []}):
            with self.subTest(change=change):
                selector.write_text(json.dumps({**manifest, **change}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    read(self.task)

    def test_repeated_supersession_preserves_each_failed_attempt(self):
        legacy, original = self._failed_legacy()
        first = self._supersede()
        active = run_dir(self.task) / first["active_attempt"]
        active.write_bytes(b"invalid JSON\n")
        second = self._supersede()
        self.assertNotEqual(first["active_attempt"], second["active_attempt"])
        self.assertEqual(second["superseded"][0], first["superseded"][0])
        self.assertEqual(second["superseded"][1]["path"], first["active_attempt"])
        self.assertEqual(active.read_bytes(), b"invalid JSON\n")
        self.assertEqual(legacy.read_bytes(), original)
        self.assertEqual(read(self.task), [])
        self.assertFalse(finish_decision(self.task)["allow"])

    def test_supersession_rejects_valid_chain_and_blank_reason(self):
        append(self.task, "design", "PASS", "test-designer", "valid")
        with self.assertRaisesRegex(ValueError, "valid"):
            tdd_evidence.supersede(self.task, "cannot discard valid evidence")
        self.assertFalse((run_dir(self.task) / "tdd-attempts.json").exists())
        with self.assertRaisesRegex(ValueError, "reason"):
            tdd_evidence.supersede(self.task, "  ")

    def test_failed_selector_publication_blocks_fallback_and_preserves_legacy(self):
        legacy, original = self._failed_legacy()
        with patch.object(tdd_evidence, "write_json_atomic", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                tdd_evidence.supersede(self.task, "recovery")
        self.assertEqual(legacy.read_bytes(), original)
        with self.assertRaisesRegex(ValueError, "selection"):
            read(self.task)

    def test_concurrent_process_append_waits_for_task_lock_and_preserves_chain(self):
        """A process holding the task lock must block an independent append."""
        self._assert_process_waits(["-c", APPEND_WORKER, self.task])
        self.assertEqual(len(read(self.task)), 1)

    def test_supersession_waits_for_same_interprocess_lock(self):
        legacy, original = self._failed_legacy()
        self._assert_process_waits([
            "scripts/tdd_evidence.py", "supersede", self.task, "--reason", "locked recovery",
        ])
        self.assertEqual(legacy.read_bytes(), original)
        self.assertEqual(read(self.task), [])

    def _assert_process_waits(self, arguments):
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        holder = subprocess.Popen(
            [sys.executable, "-c", LOCK_HOLDER, self.task], cwd=Path(__file__).resolve().parents[1],
            env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )
        writer = None
        try:
            self.assertEqual(holder.stdout.readline().strip(), "LOCKED")
            writer = subprocess.Popen(
                [sys.executable, *arguments], cwd=Path(__file__).resolve().parents[1],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace",
            )
            with self.assertRaises(subprocess.TimeoutExpired):
                writer.wait(timeout=0.25)
            holder.stdin.write("RELEASE\n")
            holder.stdin.flush()
            self.assertEqual(holder.wait(timeout=10), 0, holder.stderr.read())
            self.assertEqual(writer.wait(timeout=10), 0, writer.stderr.read())
            self.assertTrue(writer.stdout.read().strip())
        finally:
            if writer is not None and writer.poll() is None:
                writer.kill()
                writer.wait(timeout=10)
            if holder.poll() is None:
                holder.kill()
                holder.wait(timeout=10)
            for process in (holder, writer):
                if process is None:
                    continue
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None and not stream.closed:
                        stream.close()

    def test_simultaneous_process_appends_form_one_valid_chain(self):
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        workers = []
        try:
            for _ in range(4):
                workers.append(subprocess.Popen(
                    [sys.executable, "-c", SYNCED_APPEND_WORKER, self.task],
                    cwd=Path(__file__).resolve().parents[1], env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace",
                ))
            for worker in workers:
                self.assertEqual(worker.stdout.readline().strip(), "READY")
            for worker in workers:
                worker.stdin.write("GO\n")
                worker.stdin.flush()
            for worker in workers:
                self.assertEqual(worker.wait(timeout=10), 0, worker.stderr.read())
                self.assertEqual(worker.stdout.readline().strip(), "APPENDED")
            rows = read(self.task)
            self.assertEqual(len(rows), 4)
            self.assertEqual(
                [row["prev_hash"] for row in rows],
                ["GENESIS", *[row["record_hash"] for row in rows[:-1]]],
            )
        finally:
            for worker in workers:
                if worker.poll() is None:
                    worker.kill()
                    worker.wait(timeout=10)
                for stream in (worker.stdin, worker.stdout, worker.stderr):
                    if stream is not None and not stream.closed:
                        stream.close()

if __name__=="__main__":
    unittest.main()
