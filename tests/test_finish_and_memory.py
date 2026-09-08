import json, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import evidence, attest, memory
from gate import finish_decision
from handoff import validate as validate_handoff

class FinishAndMemoryTests(unittest.TestCase):
    TASKS=('T-r3-att','T-mem','T-risk-mismatch','T-empty-ledger')

    def setUp(self):
        for t in self.TASKS:
            shutil.rmtree(evidence.run_dir(t),ignore_errors=True)
        shutil.rmtree(ROOT/'.harness/memory',ignore_errors=True)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for t in self.TASKS:
            shutil.rmtree(evidence.run_dir(t),ignore_errors=True)
        shutil.rmtree(ROOT/'.harness/memory',ignore_errors=True)

    def _route_progress(self,t,risk,agents=None,human_gate=False):
        agents=agents or []
        rd=evidence.run_dir(t)
        rd.mkdir(parents=True,exist_ok=True)
        route={
            'task_id':t,
            'risk':risk,
            'agents':agents,
            'human_gate':human_gate,
            'tdd':{},
            'isolation':'none',
            'requirements':{'verification':'verifier' in agents},
        }
        (rd/'route.json').write_text(json.dumps(route),encoding='utf-8')
        steps=['IMPLEMENT','CHECKS']
        if 'reviewer' in agents: steps.append('REVIEW')
        if 'test-auditor' in agents: steps.append('TEST_AUDIT')
        if 'verifier' in agents: steps.append('VERIFY')
        if risk in {'R2','R3'}: steps.append('IMPACT_VERIFY')
        if 'security-reviewer' in agents: steps.append('SECURITY_REVIEW')
        if human_gate: steps.append('HUMAN_GATE')
        steps.append('CLOSE')
        (rd/'progress.json').write_text(json.dumps({
            'task_id':t,
            'risk':risk,
            'state':'RUNNING',
            'current_step':'CLOSE',
            'steps':steps,
            'completed':steps[:-1],
        }),encoding='utf-8')
        if risk in {'R2','R3'}:
            (rd/'impact-verification.json').write_text(json.dumps({'task_id':t,'status':'PASS'}),encoding='utf-8')
        return route

    def _write_handoff(self,t,role,data):
        rd=evidence.run_dir(t)
        dest=rd/'handoffs'/f'{role}.json'
        dest.parent.mkdir(parents=True,exist_ok=True)
        validate_handoff(role,data)
        dest.write_text(json.dumps(data),encoding='utf-8')
        return f'.harness/runs/{t}/handoffs/{role}.json'

    def _seed_checks(self,t):
        rd=evidence.run_dir(t)
        report_rel=f'.harness/runs/{t}/checks-report.json'
        (rd/'checks-report.json').write_text(json.dumps({
            'schema_version':1,
            'task_id':t,
            'status':'PASS',
            'commands':[{'argv':['fixture-check'],'exit_code':0}],
        }),encoding='utf-8')
        evidence.append(
            t,'checks','DETERMINISTIC','authoritative fixture checks','PASS','check-runner',
            command='fixture-check',exit_code=0,artifact=report_rel,
        )

    def _seed_reviewer(self,t):
        artifact=self._write_handoff(t,'reviewer',{
            'task_id':t,
            'producer':'reviewer',
            'status':'PASS',
            'findings':[{
                'severity':'low',
                'claim':'fixture review passed',
                'evidence':['fixture evidence'],
            }],
            'evidence_refs':['fixture:review'],
        })
        evidence.append(t,'review','INFERRED','review','PASS','reviewer',artifact=artifact)

    def _seed_verifier(self,t):
        artifact=self._write_handoff(t,'verifier',{
            'task_id':t,
            'producer':'verifier',
            'status':'PASS',
            'criteria':[{
                'criterion':'fixture acceptance criterion',
                'status':'PASS',
                'evidence':['fixture evidence'],
            }],
            'evidence_refs':['fixture:verification'],
        })
        evidence.append(t,'verification','INFERRED','verify','PASS','verifier',artifact=artifact)

    def _seed_security(self,t):
        artifact=self._write_handoff(t,'security-reviewer',{
            'task_id':t,
            'producer':'security-reviewer',
            'status':'PASS',
            'trust_boundaries':['fixture boundary'],
            'findings':[],
            'residual_risks':[],
            'evidence_refs':['fixture:security'],
        })
        evidence.append(t,'security_review','INFERRED','secure','PASS','security-reviewer',artifact=artifact)

    def test_r3_requires_attestation_then_passes(self):
        t='T-r3-att'
        self._route_progress(t,'R3',['reviewer','verifier','security-reviewer'],human_gate=True)
        evidence.init(t)
        self._seed_checks(t)
        self._seed_reviewer(t)
        self._seed_verifier(t)
        self._seed_security(t)
        evidence.append(t,'human_approval','DETERMINISTIC','approved','PASS','human')

        before=finish_decision(t,'R3')
        self.assertIn('attestation',before['missing'])

        with tempfile.TemporaryDirectory() as td:
            key=Path(td)/'k.pem'
            subprocess.run(['openssl','genpkey','-algorithm','ED25519','-out',str(key)],check=True,stdout=subprocess.DEVNULL)
            attest.create(t,'R3',str(key))

        self.assertTrue(finish_decision(t,'R3')['allow'])

    def test_finish_rejects_risk_mismatch(self):
        t='T-risk-mismatch'
        self._route_progress(t,'R2')
        d=finish_decision(t,'R1')
        self.assertFalse(d['allow'])
        self.assertIn('risk_state_mismatch',d['failing'])

    def test_finish_rejects_empty_ledger_for_reviewed_route(self):
        t='T-empty-ledger'
        self._route_progress(t,'R2',['reviewer','verifier'])
        d=finish_decision(t,'R2')
        self.assertFalse(d['allow'])
        self.assertTrue('review' in d['missing'] or 'handoff:reviewer' in d['missing'])

    def test_memory_only_after_finish_gate(self):
        t='T-mem'
        self._route_progress(t,'R0',['reviewer'])
        evidence.init(t)
        with self.assertRaises(Exception):
            memory.promote(t,'x','y',['z'],[],'human')

        self._seed_checks(t)
        self._seed_reviewer(t)
        dest=memory.promote(t,'x','y',['z'],['tag'],'human')
        self.assertTrue(dest.exists())

if __name__=='__main__': unittest.main()
