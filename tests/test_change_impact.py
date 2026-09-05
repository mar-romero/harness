import json, os, shutil, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import impact_analysis as ia

class ImpactAnalysisUnitTests(unittest.TestCase):
    def test_reverse_dependency_and_test_detection(self):
        graph={
            "src/a.py":["src/core.py"],
            "src/b.py":["src/a.py"],
            "tests/test_a.py":["src/a.py"],
        }
        rev=ia._reverse(graph)
        self.assertIn("src/b.py",rev["src/a.py"])
        self.assertIn("tests/test_a.py",rev["src/a.py"])
        affected,layers,_=ia._bounded_neighborhood(graph,["src/a.py"],2,20)
        self.assertIn("src/core.py",affected)
        self.assertIn("src/b.py",affected)
        self.assertTrue(ia._is_test("tests/test_a.py"))

    def test_severity_grows_with_fanin(self):
        task={"id":"X","risk_factors":{}}
        policy={
            "critical_path_tokens":[],
            "severity_thresholds":{"low_max":.34,"medium_max":.59,"high_max":.79}
        }
        graph={"a.py":[]}
        rev={"a.py":set()}
        low=ia._severity(task,"R1",["a.py"],graph,rev,[],policy)
        rev={"a.py":{f"x{i}.py" for i in range(20)}}
        high=ia._severity(task,"R1",["a.py"],graph,rev,[f"x{i}.py" for i in range(20)],policy)
        self.assertGreater(high["score"],low["score"])

if __name__=="__main__":
    unittest.main()
