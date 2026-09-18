from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch
import tempfile

from scripts import autonomous_orchestrator as ao
from scripts import harness_chat as hc
from scripts import receipt_review
from scripts import task_checks
import harnesslib
from scripts.harnesslib import ROOT, run_dir, write_json_atomic


class NeutralChatTests(unittest.TestCase):
    def test_explorer_localization_keeps_valid_greenfield_prospective_paths(self):
        task_id = "TASK-GREENFIELD-LOCALIZE"
        task = {"id": task_id, "files": []}
        handoff = {
            "status": "PASS",
            "relevant_files": [
                "docs/HARNESS_SMOKE_TEST.md",
                ".harness/runs/TASK-GREENFIELD-LOCALIZE/task.json",
                "../outside.py",
            ],
        }

        def fake_update_task(received_task_id, mutate, reactivate=True):
            self.assertEqual(received_task_id, task_id)
            self.assertTrue(reactivate)
            return mutate(task)

        with tempfile.TemporaryDirectory() as td, \
            patch.object(ao, "ROOT", Path(td)), \
            patch.object(ao, "_update_task", side_effect=fake_update_task):
            ao.localize_from_explorer(
                task_id,
                handoff,
                io=ao.RunnerIO(emit=lambda _: None),
            )

        self.assertEqual(
            task["files"],
            ["docs/HARNESS_SMOKE_TEST.md"],
        )

    def test_quota_failure_cools_provider_for_later_roles(self):
        task_id = "TASK-QUOTA-COOLDOWN"

        first = {
            "action": "use",
            "base_model_id": "copilot/auto",
            "reasoning_effort": "medium",
        }

        fallback = {
            "action": "use",
            "base_model_id": "cursor/auto",
            "reasoning_effort": None,
        }

        calls = []

        def fake_run_agent(task_id, role, **kwargs):
            provider = kwargs["provider_override"]
            calls.append((role, provider))

            if provider == "copilot":
                return {
                    "exit_code": 1,
                    "final_text": (
                        '{"errorCode":"quota_exceeded",'
                        '"message":"You have exceeded your monthly quota"}'
                    ),
                    "stderr_tail": "",
                }

            return {
                "exit_code": 0,
                "final_text": "ok",
                "stderr_tail": "",
            }

        try:
            ao._clear_provider_cooldowns(task_id)

            with patch.object(
                ao,
                "_runtime_selection",
                return_value=first,
            ), patch.object(
                ao,
                "_filtered_alternative",
                return_value=fallback,
            ), patch.object(
                ao,
                "run_agent",
                side_effect=fake_run_agent,
            ):
                result1 = ao.run_role_with_failover(
                    task_id,
                    "explorer",
                    "prompt",
                    io=ao.RunnerIO(emit=lambda _: None),
                )

                result2 = ao.run_role_with_failover(
                    task_id,
                    "planner",
                    "prompt",
                    io=ao.RunnerIO(emit=lambda _: None),
                )

            self.assertEqual(result1["exit_code"], 0)
            self.assertEqual(result2["exit_code"], 0)

            self.assertEqual(
                calls,
                [
                    ("explorer", "copilot"),
                    ("explorer", "cursor"),
                    ("planner", "cursor"),
                ],
            )

        finally:
            ao._clear_provider_cooldowns(task_id)

    def test_extract_json_object_accepts_fenced_provider_output(self):
        row = ao.extract_json_object('done\n```json\n{"task_id":"T1","status":"PASS"}\n```')
        self.assertEqual(row["task_id"], "T1")
        self.assertEqual(row["status"], "PASS")

    def test_create_task_needs_no_manual_task_json(self):
        path = hc.create_task("Corregí el bug de login y agregá tests")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(data["id"].startswith("CHAT-"))
            self.assertEqual(data["description"], "Corregí el bug de login y agregá tests")
            self.assertEqual(data["files"], [])
            self.assertEqual(data["acceptance_criteria"], [])
            # No request block: the existing bilingual risk router can operate
            # directly on original Spanish without requiring a translation CLI.
            self.assertNotIn("request", data)
        finally:
            path.unlink(missing_ok=True)

    def test_subscription_binding_is_valid_for_deterministic_checks(self):
        task_id = "NEUTRAL-CHECKS-TEST"
        run = run_dir(task_id)
        run.mkdir(parents=True, exist_ok=True)
        snapshot = run / "task.json"
        write_json_atomic(snapshot, {"id": task_id, "description": "test", "files": []})
        active = __import__('harnesslib').provider_active_path("subscriptions")
        for name in ("route.json", "context.json", "progress.json", "impact.json", "agent-budget.json"):
            write_json_atomic(run / name, {"task_id": task_id})
        model_path = __import__('harnesslib').provider_model_selections_path("subscriptions")
        inventory_path = __import__('harnesslib').provider_inventory_path("subscriptions")
        write_json_atomic(inventory_path, {"schema_version": 1, "provider": "subscriptions", "models": []})
        write_json_atomic(model_path, {
            "schema_version": 2,
            "task_id": task_id,
            "provider": "subscriptions",
            "inventory_path": inventory_path.relative_to(__import__('harnesslib').ROOT).as_posix(),
            "inventory_sha256": __import__('harnesslib').sha256_file(inventory_path),
            "selections": [],
        })
        old = active.read_bytes() if active.exists() else None
        try:
            write_json_atomic(active, {
                "schema_version": 3,
                "provider": "subscriptions",
                "overlay": __import__('harnesslib').worktree_identity(),
                "task_id": task_id,
                "task_path": f"tasks/{task_id}.json",
                "task_snapshot_path": snapshot.relative_to(__import__('harnesslib').runtime_root()).as_posix(),
                "route_path": f".harness/runs/{task_id}/route.json",
                "context_path": f".harness/runs/{task_id}/context.json",
                "progress_path": f".harness/runs/{task_id}/progress.json",
                "impact_path": f".harness/runs/{task_id}/impact.json",
                "agent_budget_path": f".harness/runs/{task_id}/agent-budget.json",
                "model_selections_path": model_path.relative_to(__import__('harnesslib').ROOT).as_posix(),
                "model_selections_sha256": __import__('harnesslib').sha256_file(model_path),
                "selections": [],
            })
            def only_fixture_binding(provider):
                return harnesslib.read_provider_active(provider) if provider == "subscriptions" else None
            with patch.object(harnesslib, "reject_legacy_provider_state"), \
                 patch.object(task_checks, "read_provider_active", side_effect=only_fixture_binding):
                task, path = task_checks._load_active_task(task_id)
            self.assertEqual(task["id"], task_id)
            self.assertEqual(path, snapshot.resolve())
        finally:
            if old is None:
                active.unlink(missing_ok=True)
            else:
                active.write_bytes(old)
            model_path.unlink(missing_ok=True)
            shutil.rmtree(run, ignore_errors=True)

    def test_receipt_consent_can_bind_to_neutral_subscription_session(self):
        task_id = "NEUTRAL-CONSENT-TEST"
        sid = "neutral-test-session"
        out = receipt_review.grant_consent(task_id, provider="subscriptions", session_id=sid)
        try:
            self.assertTrue(out["granted"])
            status = receipt_review.consent_status(task_id, provider="subscriptions", session_id=sid)
            self.assertTrue(status["granted"])
        finally:
            receipt_review.clear_consent(task_id, provider="subscriptions", session_id=sid)

    def test_runtime_failure_fails_over_to_another_provider(self):
        first = {
            "action": "use", "base_model_id": "codex/default", "reasoning_effort": "medium"
        }
        second = {
            "action": "use", "base_model_id": "claude/sonnet", "reasoning_effort": "high"
        }
        calls = []

        def fake_run_agent(task_id, role, **kwargs):
            calls.append(kwargs["provider_override"])
            if kwargs["provider_override"] == "codex":
                return {"exit_code": 1, "final_text": "quota limit reached", "stderr_tail": "", "runtime_provider": "codex", "model": "default"}
            return {"exit_code": 0, "final_text": "ok", "stderr_tail": "", "runtime_provider": "claude", "model": "sonnet"}

        with patch.object(ao, "_runtime_selection", return_value=first), \
             patch.object(ao, "_filtered_alternative", return_value=second), \
             patch.object(ao, "run_agent", side_effect=fake_run_agent):
            result = ao.run_role_with_failover("TASK-1", "explorer", "prompt", io=ao.RunnerIO(emit=lambda _: None))

        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(calls, ["codex", "claude"])


if __name__ == "__main__":
    unittest.main()
