import json, subprocess, sys, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
HOOK=ROOT/'.cursor'/'hooks'/'gate.py'
BOM=b'\xef\xbb\xbf'

def run(event, raw):
    r=subprocess.run([sys.executable,str(HOOK),event],input=raw,capture_output=True,cwd=ROOT,timeout=60)
    return r.returncode, json.loads(r.stdout.decode('utf-8'))

class CursorHookTests(unittest.TestCase):
    def test_bom_prefixed_shell_payload_is_allowed(self):
        code,out=run('pre-shell',BOM+json.dumps({'command':'git status','cwd':str(ROOT)}).encode('utf-8'))
        self.assertEqual((code,out['permission']),(0,'allow'))
    def test_bom_prefixed_destructive_command_is_denied(self):
        code,out=run('pre-shell',BOM+json.dumps({'command':'terraform destroy'}).encode('utf-8'))
        self.assertEqual((code,out['permission']),(2,'deny'))
    def test_protected_write_path_is_denied(self):
        payload={'tool_name':'Write','tool_input':{'file_path':str(ROOT/'AGENTS.md'),'content':'ñ'}}
        code,out=run('pre-write',BOM+json.dumps(payload,ensure_ascii=False).encode('utf-8'))
        self.assertEqual((code,out['permission']),(2,'deny'))
    def test_unparseable_payload_denies_with_output(self):
        for raw in (b'',b'not json'):
            code,out=run('pre-shell',raw)
            self.assertEqual((code,out['permission']),(2,'deny'))

if __name__=='__main__': unittest.main()
