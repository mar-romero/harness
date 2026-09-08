from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import product_planning  # noqa: E402


class ResearchRDDMaterializationGuardTests(unittest.TestCase):
    def _root(self):
        td = tempfile.TemporaryDirectory()
        root = Path(td.name)
        (root / "harness").mkdir(parents=True)
        (root / "harness" / "research-policy.json").write_text(json.dumps({
            "artifacts": {
                "research": ["planning/research/{id}.json", "planning/decisions/{id}.json"],
                "full": [
                    "planning/research/{id}.json",
                    "planning/domain/{id}.json",
                    "planning/decisions/{id}.json",
                    "planning/scenarios/{id}.json",
                ],
            },
            "promotion": {
                "research_status_required": "complete",
                "domain_status_required": "complete",
                "decisions_status_required": "approved",
                "scenarios_status_required": "complete",
            },
            "minimum_content": {
                "research": {"sources": 1, "findings": 1},
                "domain": {"vocabulary": 1, "invariants": 1},
                "decisions": {"decisions": 1},
                "scenarios": {"scenarios": 1},
            },
        }), encoding="utf-8")
        return td, root

    def test_research_mode_cannot_materialize_from_ready_flag_alone(self):
        td, root = self._root()
        self.addCleanup(td.cleanup)
        bundle = {
            "discovery_id": "D-1",
            "research_rdd": {"mode": "research", "ready": True, "artifacts": {}},
        }
        policy = {"research_driven_development": {"materialization_requires_research_ready": True}}
        with self.assertRaises(product_planning.PlanningError):
            product_planning._validate_research_rdd_materialization(bundle, policy, root)

    def test_research_mode_accepts_verified_artifacts(self):
        td, root = self._root()
        self.addCleanup(td.cleanup)
        refs = {
            "research": "planning/research/D-1.json",
            "decisions": "planning/decisions/D-1.json",
        }
        for kind, rel in refs.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "schema_version": 1,
                "discovery_id": "D-1",
                "artifact_type": kind,
                "status": "approved" if kind == "decisions" else "complete",
            }
            if kind == "research":
                payload.update({"sources": [{"kind": "repo", "ref": "README.md"}], "findings": ["evidence"]})
            elif kind == "decisions":
                payload.update({"decisions": [{"decision": "bounded option"}]})
            p.write_text(json.dumps(payload), encoding="utf-8")
        bundle = {
            "discovery_id": "D-1",
            "research_rdd": {"mode": "research", "ready": True, "artifacts": refs},
        }
        policy = {"research_driven_development": {"materialization_requires_research_ready": True}}
        product_planning._validate_research_rdd_materialization(bundle, policy, root)


if __name__ == "__main__":
    unittest.main()
