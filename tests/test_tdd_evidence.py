import json, shutil, sys, unittest, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from harnesslib import run_dir
from tdd_evidence import append, finish_decision

class TDDEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.task="TDDTEST-"+uuid.uuid4().hex[:10].upper()
        d=run_dir(self.task); d.mkdir(parents=True,exist_ok=True)
        (d/"route.json").write_text(json.dumps({"task_id":self.task,"tdd":{"required_evidence":["design","red","green"]}}),encoding="utf-8")
    def tearDown(self):
        shutil.rmtree(run_dir(self.task),ignore_errors=True)

    def test_finish_blocks_until_valid_red_green(self):
        self.assertFalse(finish_decision(self.task)["allow"])
        append(self.task,"design","PASS","test-designer","oracle established")
        append(self.task,"red","PASS","implementer","expected assertion fails",command="pytest x",exit_code=1)
        self.assertFalse(finish_decision(self.task)["allow"])
        append(self.task,"green","PASS","implementer","same test passes",command="pytest x",exit_code=0)
        self.assertTrue(finish_decision(self.task)["allow"])

    def test_red_zero_exit_is_invalid(self):
        append(self.task,"design","PASS","test-designer","oracle established")
        append(self.task,"red","PASS","implementer","bad red",command="pytest x",exit_code=0)
        append(self.task,"green","PASS","implementer","green",command="pytest x",exit_code=0)
        self.assertIn("tdd:red",finish_decision(self.task)["failing"])

if __name__=="__main__":
    unittest.main()
