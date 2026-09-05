import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from tdd_policy import profile_task

class AdaptiveTDDPolicyTests(unittest.TestCase):
    def task(self, desc, **factors):
        return {"id":"TDD-UNIT","description":desc,"risk_factors":factors,"tags":[],"files":[]}

    def test_bug_requires_tdd(self):
        p=profile_task(self.task("Fix regression in CPA calculation", bug_fix=True),risk="R1",bug=True)
        self.assertEqual(p["mode"],"tdd_required")
        self.assertTrue(p["test_designer"])
        self.assertIn("red",p["required_evidence"])

    def test_legacy_refactor_characterizes_first(self):
        p=profile_task(self.task("Refactor legacy campaign normalization without behavior change"),risk="R1")
        self.assertEqual(p["mode"],"characterization_then_tdd")
        self.assertIn("characterization",p["required_evidence"])

    def test_unknown_contract_spikes_first(self):
        p=profile_task(self.task("Verify Meta API contract then implement breakdown ingestion"),risk="R2",external=True)
        self.assertEqual(p["mode"],"spike_then_tdd")
        self.assertIn("contract",p["required_evidence"])

    def test_visual_only_allows_test_after(self):
        p=profile_task(self.task("CSS visual only spacing adjustment"),risk="R0")
        self.assertEqual(p["mode"],"test_after_allowed")
        self.assertFalse(p["test_designer"])

    def test_r2_relaxation_requires_human_exception(self):
        t=self.task("Implement persistence change", persistence=True)
        t["test_strategy"]={"mode":"test_after_allowed"}
        p=profile_task(t,risk="R2")
        self.assertEqual(p["mode"],"tdd_required")
        t["test_strategy"]["exception"]={"human_approved":True,"reason":"Generated migration only"}
        p=profile_task(t,risk="R2")
        self.assertEqual(p["mode"],"test_after_allowed")

if __name__=="__main__":
    unittest.main()
