import sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import agent_budget as ab

class AgentBudgetUnitTests(unittest.TestCase):
    def route(self,risk="R1"):
        return {
            "risk":risk,
            "agents":["explorer","planner","debugger","implementer","reviewer","test-auditor","verifier"]
        }

    def test_r1_low_impact_defers_planner_and_debugger(self):
        task={"id":"B1","description":"small change","risk_factors":{}}
        s=ab.build_initial(task,self.route("R1"),{"impact":{"severity":"low"}})
        self.assertIn("explorer",s["current_agents"])
        self.assertIn("implementer",s["current_agents"])
        self.assertNotIn("planner",s["current_agents"])
        self.assertNotIn("debugger",s["current_agents"])
        self.assertIn("reviewer",s["mandatory_gate_agents"])

    def test_r2_initializes_planner_but_keeps_gates(self):
        task={"id":"B2","description":"schema change","risk_factors":{"schema_change":True}}
        s=ab.build_initial(task,self.route("R2"),{"impact":{"severity":"medium"}})
        self.assertIn("planner",s["current_agents"])
        self.assertIn("test-auditor",s["mandatory_gate_agents"])
        self.assertIn("verifier",s["mandatory_gate_agents"])

    def test_high_impact_initializes_planner(self):
        task={"id":"B3","description":"change","risk_factors":{}}
        s=ab.build_initial(task,self.route("R1"),{"impact":{"severity":"high"}})
        self.assertIn("planner",s["current_agents"])

    def test_existing_budget_reconciles_when_route_escalates_to_r3(self):
        task={"id":"B4","description":"security-sensitive change","risk_factors":{}}
        r2=self.route("R2")
        r3={**self.route("R3"), "agents": self.route("R3")["agents"] + ["security-reviewer"]}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with patch.object(ab, "run_dir", return_value=root / task["id"]):
                original=ab.init(task,r2)
                original["signals"]=["review_fail"]
                original["escalations"]=[{"signal":"review_fail"}]
                ab.write_json_atomic(ab.budget_path(task["id"]),original)
                reconciled=ab.init(task,r3)
        self.assertEqual(reconciled["risk"],"R3")
        self.assertIn("security-reviewer",reconciled["route_agents"])
        self.assertIn("security-reviewer",reconciled["mandatory_gate_agents"])
        self.assertEqual(reconciled["signals"],["review_fail"])
        self.assertEqual(reconciled["escalations"],[{"signal":"review_fail"}])
        self.assertEqual(reconciled["history"][-1]["event"],"route_reconciled")

if __name__=="__main__":
    unittest.main()
