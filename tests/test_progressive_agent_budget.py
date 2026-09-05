import sys, unittest
from pathlib import Path
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

if __name__=="__main__":
    unittest.main()
