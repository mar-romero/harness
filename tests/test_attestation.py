import sys,unittest,tempfile,subprocess,shutil,json
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import evidence, attest

class AttestTests(unittest.TestCase):
    def setUp(self):
        self.task='T-attest'
        shutil.rmtree(evidence.run_dir(self.task),ignore_errors=True)
        self.addCleanup(shutil.rmtree,evidence.run_dir(self.task),True)
        evidence.init(self.task)
        (evidence.run_dir(self.task) / 'route.json').write_text(json.dumps({'task_id': self.task, 'risk': 'R0'}), encoding='utf-8')
        (evidence.run_dir(self.task) / 'context.json').write_text(json.dumps({'task_id': self.task}), encoding='utf-8')
        # This test exercises signing and verification, not finish-gate acceptance.
        # Seed a valid non-acceptance evidence row so the attestation has a ledger head.
        evidence.append(self.task,'implementation','INFERRED','fixture evidence','PASS','implementer')

    def test_ed25519_sign_verify(self):
        with tempfile.TemporaryDirectory() as td:
            key=Path(td)/'k.pem'
            pub=Path(td)/'p.pem'
            subprocess.run(['openssl','genpkey','-algorithm','ED25519','-out',str(key)],check=True,stdout=subprocess.DEVNULL)
            subprocess.run(['openssl','pkey','-in',str(key),'-pubout','-out',str(pub)],check=True,stdout=subprocess.DEVNULL)
            candidate = {'subject_hash': 'a' * 64, 'base_commit': 'b' * 40, 'scope_expansion_sha256': None}
            with patch.object(attest, 'candidate_snapshot', return_value=candidate):
                dest,_=attest.create(self.task,'R0',str(key))
            self.assertTrue(attest.verify(dest,str(pub)))

if __name__=='__main__': unittest.main()
