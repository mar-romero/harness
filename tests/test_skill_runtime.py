import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from skill_compiler import compile_skill_pack, skill_names_for_role
from subscription_bridge import _default_prompt


class SkillRuntimeTests(unittest.TestCase):
    def test_role_skill_pack_is_canonical_and_bounded(self):
        routed = {
            "risk": "R2",
            "skills": ["adaptive-tdd", "test-strategy", "software-engineering", "worktree-isolation", "verification"],
        }
        pack = compile_skill_pack("implementer", routed)
        self.assertIn("adaptive-tdd", pack["skills"])
        self.assertIn("agent-computer-interface", pack["skills"])
        self.assertIn("worktree-isolation", pack["skills"])
        self.assertLessEqual(pack["chars"], pack["budget_chars"])
        self.assertIn("RED -> GREEN", pack["text"])

    def test_default_subscription_prompt_embeds_skill_content_not_only_names(self):
        task = {"id": "T-skill", "description": "fix bug", "files": []}
        route = {
            "risk": "R2", "risk_reasons": ["test"], "requirements": {},
            "tdd": {"mode": "tdd_required"},
            "skills": ["adaptive-tdd", "software-engineering", "worktree-isolation"],
        }
        context = {"files": [], "retrieval": {}, "estimated_tokens": 0}
        prompt = _default_prompt("implementer", task, route, context)
        self.assertIn("Canonical harness skills selected", prompt)
        self.assertIn("Skill: adaptive-tdd", prompt)
        self.assertIn("Skill: agent-computer-interface", prompt)
        self.assertIn("runtime_skills", prompt)


if __name__ == "__main__":
    unittest.main()
