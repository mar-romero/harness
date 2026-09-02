import sys,unittest,tempfile,subprocess,json,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'scripts'))
import evidence, attest
class AttestTests(unittest.TestCase):
    def setUp(self):
        self.task='T-attest'; shutil.rmtree(evidence.run_dir(self.task),ignore_errors=True); evidence.init(self.task); evidence.append(self.task,'acceptance','DETERMINISTIC','ok','PASS','implementer')
    def tearDown(self): shutil.rmtree(evidence.run_dir(self.task),ignore_errors=True)
    def test_ed25519_sign_verify(self):
        with tempfile.TemporaryDirectory() as td:
            key=Path(td)/'k.pem'; pub=Path(td)/'p.pem'; subprocess.run(['openssl','genpkey','-algorithm','ED25519','-out',str(key)],check=True,stdout=subprocess.DEVNULL); subprocess.run(['openssl','pkey','-in',str(key),'-pubout','-out',str(pub)],check=True,stdout=subprocess.DEVNULL)
            dest,_=attest.create(self.task,'R0',str(key)); self.assertTrue(attest.verify(dest,str(pub)))
if __name__=='__main__': unittest.main()
