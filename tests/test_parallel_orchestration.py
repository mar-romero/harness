from __future__ import annotations

import shutil
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import autonomous_orchestrator as ao
from scripts.harnesslib import run_dir


class ParallelOrchestrationTests(unittest.TestCase):
    def tearDown(self):
        shutil.rmtree(run_dir("PARALLEL-TEST"), ignore_errors=True)

    def test_review_gate_group_is_contiguous_and_stops_before_impact(self):
        state = {
            "current_step": "REVIEW",
            "steps": ["CHECKS", "VERIFY_ASSESS", "REVIEW", "TEST_AUDIT", "VERIFY", "IMPACT_VERIFY", "SECURITY_REVIEW", "CLOSE"],
        }
        with patch.object(ao, "parallel_policy", return_value={
            "enabled": True,
            "review_gate_stages": ["REVIEW", "TEST_AUDIT", "VERIFY"],
            "minimum_batch_size": 2,
        }):
            self.assertEqual(ao._parallel_review_steps(state), ["REVIEW", "TEST_AUDIT", "VERIFY"])

    def test_provider_limiter_allows_two_claude_workers_not_three(self):
        active = 0
        peak = 0
        lock = threading.Lock()
        barrier = threading.Barrier(3)

        def worker():
            nonlocal active, peak
            barrier.wait()
            with ao._provider_slot("claude"):
                with lock:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.08)
                with lock:
                    active -= 1

        with patch.object(ao, "parallel_policy", return_value={
            "per_provider_max_parallel": {"claude": 2},
            "default_provider_max_parallel": 1,
        }):
            ao._PROVIDER_SEMAPHORES.clear()
            threads = [threading.Thread(target=worker) for _ in range(3)]
            for t in threads: t.start()
            for t in threads: t.join(timeout=2)
        self.assertEqual(peak, 2)

    def test_parallel_review_runs_workers_concurrently_but_commits_in_order(self):
        task_id = "PARALLEL-TEST"
        run_dir(task_id).mkdir(parents=True, exist_ok=True)
        state = {
            "current_step": "REVIEW",
            "steps": ["REVIEW", "TEST_AUDIT", "VERIFY", "IMPACT_VERIFY", "CLOSE"],
        }
        started: list[str] = []
        commit_order: list[str] = []
        barrier = threading.Barrier(3)
        lock = threading.Lock()

        def fake_prepare(_task_id, step, role, *, io, extra=""):
            with lock:
                started.append(step)
            barrier.wait(timeout=2)
            time.sleep({"REVIEW": 0.06, "TEST_AUDIT": 0.03, "VERIFY": 0.01}[step])
            return {
                "handoff_path": Path("/tmp") / f"{step}.json",
                "handoff": {"task_id": task_id, "producer": role, "status": "PASS"},
                "runtime": {"runtime_provider": "claude", "model": "sonnet"},
            }

        def fake_commit(_task_id, step, role, prepared):
            commit_order.append(step)
            return {"state": {"current_step": step}, "handoff": prepared["handoff"], "runtime": prepared["runtime"]}

        with patch.object(ao, "parallel_policy", return_value={
            "enabled": True,
            "max_parallel_agents": 4,
            "minimum_batch_size": 2,
            "review_gate_stages": ["REVIEW", "TEST_AUDIT", "VERIFY"],
            "per_provider_max_parallel": {"claude": 2},
        }), patch.object(ao, "candidate_snapshot", return_value={"subject_hash": "abc123"}), \
             patch.object(ao, "prepare_typed_stage", side_effect=fake_prepare), \
             patch.object(ao, "commit_prepared_typed_stage", side_effect=fake_commit), \
             patch.object(ao, "load_progress", return_value={"current_step": "IMPACT_VERIFY"}):
            result = ao.run_parallel_review_batch(task_id, state, io=ao.RunnerIO(emit=lambda _: None))

        self.assertTrue(result["ok"])
        self.assertCountEqual(started, ["REVIEW", "TEST_AUDIT", "VERIFY"])
        self.assertEqual(commit_order, ["REVIEW", "TEST_AUDIT", "VERIFY"])


if __name__ == "__main__":
    unittest.main()
