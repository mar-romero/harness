import importlib.util, shutil, tempfile, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("hb",ROOT/"scripts/harness_benchmark.py")
hb=importlib.util.module_from_spec(spec); spec.loader.exec_module(hb)
class T(unittest.TestCase):
    def test_validate(self):
        s,c=hb.load_suite(ROOT/"benchmarks/examples/suite.json")
        self.assertEqual(s["name"],"example-cpa"); self.assertEqual(c[0][1]["id"],"BENCH-CPA-ZERO")
    def test_usage(self):
        u=hb.extract_executor_usage('{"usage":{"input_tokens":100,"output_tokens":20,"total_tokens":120},"cost_usd":0.03}')
        self.assertEqual(u["tokens"],120); self.assertEqual(u["cost_usd"],0.03)
    def test_aggregate(self):
        rows=[
          {"variant":"baseline","metrics":{"task_success":0.0,"first_pass_success":0.0,"hidden_test_pass_rate":0.0,"bug_introduction":1.0,"mutation_score":None,"impact_recall":None,"tdd_valid_red":None,"human_intervention":0.0,"tokens":100,"cost_usd":1,"latency_seconds":10,"agents_used":1}},
          {"variant":"full","metrics":{"task_success":1.0,"first_pass_success":1.0,"hidden_test_pass_rate":1.0,"bug_introduction":0.0,"mutation_score":None,"impact_recall":1.0,"tdd_valid_red":1.0,"human_intervention":0.0,"tokens":150,"cost_usd":1.5,"latency_seconds":15,"agents_used":4}}
        ]
        a,d=hb.aggregate(rows,["baseline","full"]); self.assertEqual(a["full"]["metrics"]["task_success_rate"]["mean"],1.0)
        self.assertEqual(d["full"]["task_success_rate"],1.0)
if __name__=="__main__": unittest.main()
