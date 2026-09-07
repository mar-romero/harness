import json, os, shutil, sys, tempfile, unittest
from unittest import mock
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

    def test_test_harness_package_is_not_misclassified_as_test(self):
        self.assertFalse(
            ia._is_test(
                "examples/test-harness/src/test_harness/lexer.py"
            )
        )
        self.assertTrue(
            ia._is_test(
                "examples/test-harness/tests/test_lexer.py"
            )
        )

    def test_explicit_greenfield_paths_are_kept_as_seeds(self):
        policy={"max_context_seed_files":6}
        task={"id":"X","files":["examples/new-app/src/new.py"]}
        seeds,source=ia._seed_files(task,{}, {},policy)
        self.assertEqual(source,"explicit-task-files")
        self.assertEqual(seeds,["examples/new-app/src/new.py"])

    def test_git_changed_includes_untracked_files(self):
        outputs=[
            mock.Mock(returncode=0,stdout="tracked.py\n"),
            mock.Mock(returncode=0,stdout="cached.py\n"),
            mock.Mock(returncode=0,stdout="new.py\n"),
        ]
        with mock.patch.object(ia.subprocess,"run",side_effect=outputs) as run:
            changed=ia._git_changed("HEAD",cwd=ia.ROOT)
        self.assertEqual(changed,["cached.py","new.py","tracked.py"])
        self.assertIn("ls-files",run.call_args_list[2].args[0])

if __name__=="__main__":
    unittest.main()
