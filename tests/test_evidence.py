import json, sys, unittest, shutil
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import evidence
class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.task='TEST-EVIDENCE'; shutil.rmtree(evidence.run_dir(self.task),ignore_errors=True)
    def tearDown(self): shutil.rmtree(evidence.run_dir(self.task),ignore_errors=True)
    def test_append_summary_and_hash_chain(self):
        evidence.append(self.task,'checks','DETERMINISTIC','tests pass','PASS','implementer',command='x',exit_code=0)
        evidence.append(self.task,'review','DETERMINISTIC','review pass','PASS','reviewer')
        s=evidence.summary(self.task); self.assertEqual(s['entries'],2); self.assertTrue(s['valid_chain'])
        self.assertEqual(s['latest_by_category']['checks']['status'],'PASS')
        self.assertTrue(evidence.validate(self.task)['valid'])
    def test_independent_actor_enforced(self):
        with self.assertRaises(SystemExit):
            evidence.append(self.task,'review','DETERMINISTIC','self review','PASS','implementer')
    def test_tampering_detected(self):
        evidence.append(self.task,'checks','DETERMINISTIC','tests pass','PASS','implementer',command='x',exit_code=0)
        p=evidence.ledger_path(self.task); row=json.loads(p.read_text()); row['claim']='tampered'; p.write_text(json.dumps(row)+'\n')
        self.assertFalse(evidence.validate(self.task)['valid'])
        with self.assertRaises(ValueError): evidence.read(self.task)
if __name__=='__main__': unittest.main()
