import importlib.util, json, shutil, tempfile, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("bv",ROOT/"scripts/benchmark_variant.py")
bv=importlib.util.module_from_spec(spec); spec.loader.exec_module(bv)
class T(unittest.TestCase):
    def test_impact(self):
        tmp=Path(tempfile.mkdtemp())
        try:
            (tmp/"harness").mkdir()
            (tmp/"harness/impact-policy.json").write_text(json.dumps({"finish_gate":{"R0":False,"R1":False,"R2":True,"R3":True},"graph_depth":2}))
            (tmp/"harness/context-policy.json").write_text(json.dumps({"graph_neighbor_depth":2,"graph_neighbor_bonus":350}))
            bv.disable_impact(tmp)
            self.assertFalse(json.loads((tmp/"harness/impact-policy.json").read_text())["finish_gate"]["R2"])
            self.assertEqual(json.loads((tmp/"harness/context-policy.json").read_text())["graph_neighbor_bonus"],0)
        finally: shutil.rmtree(tmp)
    def test_eager(self):
        tmp=Path(tempfile.mkdtemp())
        try:
            (tmp/"harness").mkdir()
            (tmp/"harness/agent-budget-policy.json").write_text(json.dumps({"support_agents":["explorer","planner","debugger"],"initial":{"always_if_routed":["explorer"],"planner_risks":["R2"],"planner_impact_severities":["high"]}}))
            bv.eager_agents(tmp)
            self.assertIn("debugger",json.loads((tmp/"harness/agent-budget-policy.json").read_text())["initial"]["always_if_routed"])
        finally: shutil.rmtree(tmp)
if __name__=="__main__": unittest.main()
