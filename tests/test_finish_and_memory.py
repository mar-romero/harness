import sys,unittest,tempfile,subprocess,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'scripts'))
import evidence, attest, memory
from gate import finish_decision
class FinishAndMemoryTests(unittest.TestCase):
    def tearDown(self):
        for t in ['T-r3-att','T-mem']: shutil.rmtree(evidence.run_dir(t),ignore_errors=True)
        shutil.rmtree(ROOT/'.harness/memory',ignore_errors=True)
    def test_r3_requires_attestation_then_passes(self):
        t='T-r3-att'; evidence.init(t)
        evidence.append(t,'acceptance','DETERMINISTIC','ok','PASS','implementer')
        evidence.append(t,'checks','DETERMINISTIC','tests','PASS','implementer',command='tests',exit_code=0)
        evidence.append(t,'review','DETERMINISTIC','review','PASS','reviewer')
        evidence.append(t,'verification','DETERMINISTIC','verify','PASS','verifier')
        evidence.append(t,'security_review','DETERMINISTIC','secure','PASS','security-reviewer')
        evidence.append(t,'human_approval','DETERMINISTIC','approved','PASS','human')
        self.assertIn('attestation',finish_decision(t,'R3')['missing'])
        with tempfile.TemporaryDirectory() as td:
            key=Path(td)/'k.pem'; subprocess.run(['openssl','genpkey','-algorithm','ED25519','-out',str(key)],check=True,stdout=subprocess.DEVNULL)
            attest.create(t,'R3',str(key))
        self.assertTrue(finish_decision(t,'R3')['allow'])
    def test_memory_only_after_finish_gate(self):
        t='T-mem'; rd=evidence.run_dir(t); rd.mkdir(parents=True,exist_ok=True)
        (rd/'route.json').write_text('{"risk":"R0"}')
        evidence.init(t)
        with self.assertRaises(Exception): memory.promote(t,'x','y',['z'],[],'human')
        evidence.append(t,'acceptance','DETERMINISTIC','ok','PASS','implementer')
        dest=memory.promote(t,'x','y',['z'],['tag'],'human')
        self.assertTrue(dest.exists())
if __name__=='__main__': unittest.main()
