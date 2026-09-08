import json
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import evidence
import gate
import orchestrator


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.task = "T-progress"

        shutil.rmtree(
            orchestrator.run_dir(self.task),
            ignore_errors=True,
        )

        self.route = {
            "task_id": self.task,
            "risk": "R2",
            "agents": [
                "explorer",
                "planner",
                "test-designer",
                "implementer",
                "reviewer",
                "test-auditor",
                "verifier",
            ],
            "human_gate": False,
            "isolation": "worktree",
            "tdd": {
                "test_designer": True,
                "required_evidence": ["design", "red", "green"],
            },
        }

    def _init(self, route=None):
        route = route or self.route

        rd = orchestrator.run_dir(self.task)
        rd.mkdir(parents=True, exist_ok=True)

        (rd / "route.json").write_text(
            json.dumps(route),
            encoding="utf-8",
        )

        return orchestrator.init_progress(self.task, route)

    def tearDown(self):
        shutil.rmtree(
            orchestrator.run_dir(self.task),
            ignore_errors=True,
        )

        shutil.rmtree(
            orchestrator.ROOT / ".worktrees" / self.task,
            ignore_errors=True,
        )

        (
            orchestrator.ROOT
            / ".harness"
            / "locks"
            / f"{self.task}.json"
        ).unlink(missing_ok=True)

    def test_progress_starts_before_implementation(self):
        state = self._init()

        self.assertEqual(state["current_step"], "EXPLORE")
        self.assertEqual(
            state["steps"][:4],
            ["EXPLORE", "PLAN", "TEST_DESIGN", "WORKTREE"],
        )
        self.assertIn("TEST_AUDIT", state["steps"])
        self.assertIn("IMPACT_VERIFY", state["steps"])

    def test_direct_pass_cannot_skip_typed_subagent_commit(self):
        self._init()

        with self.assertRaisesRegex(
            Exception,
            "use orchestrator.py commit",
        ):
            orchestrator.record(
                self.task,
                "PASS",
                step="EXPLORE",
            )

        self.assertEqual(
            orchestrator.load(self.task)["current_step"],
            "EXPLORE",
        )

    def test_commit_validates_persists_evidence_then_advances(self):
        self._init()

        incoming = (
            orchestrator.run_dir(self.task)
            / "incoming"
            / "explorer.json"
        )
        incoming.parent.mkdir(parents=True, exist_ok=True)

        incoming.write_text(
            json.dumps(
                {
                    "task_id": self.task,
                    "producer": "explorer",
                    "status": "PASS",
                    "facts": ["x"],
                    "unknowns": [],
                    "evidence_refs": ["repo:x"],
                    "relevant_files": ["x.py"],
                }
            ),
            encoding="utf-8",
        )

        state = orchestrator.commit(
            self.task,
            "explorer",
            incoming,
        )

        self.assertEqual(state["current_step"], "PLAN")

        self.assertTrue(
            (
                orchestrator.run_dir(self.task)
                / "handoffs"
                / "explorer.json"
            ).exists()
        )

        rows = evidence.read(self.task)

        self.assertEqual(
            rows[-1]["category"],
            "exploration",
        )
        self.assertEqual(
            rows[-1]["actor"],
            "explorer",
        )

    def test_invalid_handoff_does_not_advance(self):
        self._init()

        incoming = (
            orchestrator.run_dir(self.task)
            / "incoming"
            / "explorer.json"
        )
        incoming.parent.mkdir(parents=True, exist_ok=True)

        incoming.write_text(
            json.dumps(
                {
                    "task_id": self.task,
                    "producer": "planner",
                    "status": "PASS",
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaises(Exception):
            orchestrator.commit(
                self.task,
                "explorer",
                incoming,
            )

        self.assertEqual(
            orchestrator.load(self.task)["current_step"],
            "EXPLORE",
        )

    def test_reclassification_updates_risk_and_preserves_history(self):
        r1 = dict(self.route)
        r1.update(
            {
                "risk": "R1",
                "agents": ["implementer", "reviewer"],
                "isolation": "none",
                "tdd": {},
            }
        )

        state = orchestrator.init_progress(
            self.task,
            r1,
        )

        old_history = list(state["history"])

        state = self._init()

        self.assertEqual(
            state["risk"],
            "R2",
        )
        self.assertEqual(
            state["current_step"],
            "EXPLORE",
        )
        self.assertGreater(
            len(state["history"]),
            len(old_history),
        )
        self.assertEqual(
            state["history"][-1]["event"],
            "route_reconciled",
        )

    def test_evidence_rejects_checks_from_wrong_actor(self):
        self._init(
            {
                "task_id": self.task,
                "risk": "R1",
                "agents": ["implementer"],
                "human_gate": False,
                "isolation": "none",
                "tdd": {
                    "test_designer": False,
                },
            }
        )

        with self.assertRaisesRegex(
            SystemExit,
            "check-runner",
        ):
            evidence.append(
                self.task,
                "checks",
                "DETERMINISTIC",
                "fake pass",
                "PASS",
                "implementer",
                command="fake",
                exit_code=0,
            )


    def test_acceptance_rejects_verifier_when_route_does_not_include_verification(self):
        route = {
            "task_id": self.task,
            "risk": "R1",
            "agents": ["implementer", "reviewer"],
            "human_gate": False,
            "isolation": "none",
            "requirements": {"review": True, "verification": False, "human_gate": False},
            "tdd": {"test_designer": False},
        }
        self._init(route)

        with self.assertRaisesRegex(SystemExit, "routed reviewer"):
            evidence.append(
                self.task,
                "acceptance",
                "DETERMINISTIC",
                "fabricated verifier acceptance",
                "PASS",
                "verifier",
            )

    def test_standalone_acceptance_row_is_not_finish_gate_authority(self):
        route = {
            "task_id": self.task,
            "risk": "R1",
            "agents": ["implementer", "reviewer"],
            "human_gate": False,
            "isolation": "none",
            "requirements": {"review": True, "verification": False, "human_gate": False},
            "tdd": {"test_designer": False},
        }
        latest = {
            "acceptance": {
                "task_id": self.task,
                "category": "acceptance",
                "evidence_type": "DETERMINISTIC",
                "status": "PASS",
                "actor": "verifier",
            }
        }

        allowed, reason = gate._acceptance_decision(self.task, route, latest)
        self.assertFalse(allowed)
        self.assertIn("checks", reason)

    def test_acceptance_is_derived_from_authoritative_checks_and_review(self):
        route = {
            "task_id": self.task,
            "risk": "R1",
            "agents": ["implementer", "reviewer"],
            "human_gate": False,
            "isolation": "none",
            "requirements": {"review": True, "verification": False, "human_gate": False},
            "tdd": {"test_designer": False},
        }
        self._init(route)
        rd = orchestrator.run_dir(self.task)
        handoffs = rd / "handoffs"
        handoffs.mkdir(parents=True, exist_ok=True)

        review = {
            "task_id": self.task,
            "producer": "reviewer",
            "status": "PASS",
            "findings": [
                {"severity": "low", "claim": "acceptance covered", "evidence": ["checks-report"]}
            ],
            "evidence_refs": [f".harness/runs/{self.task}/checks-report.json"],
        }
        review_path = handoffs / "reviewer.json"
        review_path.write_text(json.dumps(review), encoding="utf-8")

        report_path = rd / "checks-report.json"
        report_path.write_text(
            json.dumps({"schema_version": 1, "task_id": self.task, "status": "PASS"}),
            encoding="utf-8",
        )

        evidence.append(
            self.task,
            "checks",
            "DETERMINISTIC",
            "authoritative checks",
            "PASS",
            "check-runner",
            command=f"python3 scripts/task_checks.py run {self.task}",
            exit_code=0,
            artifact=f".harness/runs/{self.task}/checks-report.json",
        )
        evidence.append(
            self.task,
            "review",
            "INFERRED",
            "review handoff persisted",
            "PASS",
            "reviewer",
            artifact=f".harness/runs/{self.task}/handoffs/reviewer.json",
        )

        latest = evidence.summary(self.task)["latest_by_category"]
        allowed, reason = gate._acceptance_decision(self.task, route, latest)
        self.assertTrue(allowed, reason)


if __name__ == "__main__":
    unittest.main()