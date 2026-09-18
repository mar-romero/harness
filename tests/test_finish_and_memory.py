import base64, json, os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import evidence, attest, memory
import gate
from harnesslib import runtime_root
from gate import finish_decision
from handoff import validate as validate_handoff

class FinishAndMemoryTests(unittest.TestCase):
    TASKS=('T-r3-att','T-mem','T-risk-mismatch','T-empty-ledger')

    def setUp(self):
        self.candidate_patch = patch.object(
            gate, 'candidate_snapshot',
            return_value={'subject_hash': 'fixture-subject', 'base_commit': 'fixture-base', 'scope_expansion_sha256': 'fixture-scope'},
        )
        self.candidate_patch.start()
        self.addCleanup(self.candidate_patch.stop)
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
        (rd/'context.json').write_text(json.dumps({'task_id': t, 'files': []}),encoding='utf-8')
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
            'candidate_subject_hash':'fixture-subject',
            'candidate_base_commit':'fixture-base',
            'scope_expansion_sha256':'fixture-scope',
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
            selections=Path(td)/'provider-model-selections.json'
            selections.write_text('{}', encoding='utf-8')
            subprocess.run(['openssl','genpkey','-algorithm','ED25519','-out',str(key)],check=True,stdout=subprocess.DEVNULL)
            candidate = {'subject_hash': 'a' * 64, 'base_commit': 'b' * 40, 'scope_expansion_sha256': None}
            with patch.object(
                attest, 'read_provider_active',
                side_effect=lambda provider: {'task_id': t} if provider == 'codex' else None,
            ), patch.object(attest, 'provider_model_selections_path', return_value=selections), \
                patch.object(attest, 'provider_active_path', return_value=selections), \
                patch.object(attest, 'candidate_snapshot', return_value=candidate), \
                patch.object(attest, 'validate_model_selections'), \
                patch.dict(os.environ, {'HARNESS_ATTESTATION_PUBLIC_KEY': str(Path(td) / 'public.pem')}):
                attest.derive_public(key, Path(td) / 'public.pem')
                attest.create(t,'R3',str(key))
                with patch.object(gate, 'validate_attestation_current', wraps=attest.validate_current) as validator:
                    self.assertTrue(finish_decision(t,'R3')['allow'])
                    validator.assert_called_once()

    def test_finish_rejects_risk_mismatch(self):
        t='T-risk-mismatch'
        self._route_progress(t,'R2')
        d=finish_decision(t,'R1')
        self.assertFalse(d['allow'])
        self.assertIn('risk_state_mismatch',d['failing'])

    def test_r3_attestation_rejects_mutated_candidate_or_runtime_state(self):
        payload = {
            'schema_version': 2, 'task_id': 'T-r3-att', 'risk': 'R3',
            'git_head': 'a' * 40, 'git_tree': 'b' * 40,
            'manifest_sha256': 'c' * 64, 'route_sha256': 'd' * 64,
            'context_sha256': 'e' * 64, 'model_selections_provider': 'codex',
            'model_selections_sha256': 'f' * 64, 'active_binding_sha256': '0' * 64,
            'inventory_path': '.harness/overlays/id/codex/enriched-inventory.json',
            'inventory_sha256': '1' * 64, 'candidate_subject_hash': '2' * 64,
            'candidate_base_commit': '3' * 40, 'scope_expansion_sha256': None,
            'evidence_head_hash': '4' * 64,
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'attestation.json'
            path.write_text(json.dumps({'payload': payload, 'signature': {
                'algorithm': 'Ed25519',
                'value_base64': base64.b64encode(b'signature').decode(),
            }}), encoding='utf-8')
            with patch.object(attest, 'make_payload', return_value=payload):
                with self.assertRaisesRegex(ValueError, 'public key required|signature verification failed'):
                    attest.validate_current(path, 'T-r3-att', 'R3')
            changed = dict(payload)
            changed['candidate_subject_hash'] = '5' * 64
            with patch.object(attest, 'make_payload', return_value=changed):
                with self.assertRaisesRegex(ValueError, 'public key required|signature verification failed'):
                    attest.validate_current(path, 'T-r3-att', 'R3')

    def test_r3_attestation_accepts_only_a_trusted_ed25519_signature(self):
        payload = {
            'schema_version': 2, 'task_id': 'T-r3-att', 'risk': 'R3',
            'git_head': 'a' * 40, 'git_tree': 'b' * 40,
            'manifest_sha256': 'c' * 64, 'route_sha256': 'd' * 64,
            'context_sha256': 'e' * 64, 'model_selections_provider': 'codex',
            'model_selections_sha256': 'f' * 64, 'active_binding_sha256': '0' * 64,
            'inventory_path': '.harness/overlays/id/codex/enriched-inventory.json',
            'inventory_sha256': '1' * 64, 'candidate_subject_hash': '2' * 64,
            'candidate_base_commit': '3' * 40, 'scope_expansion_sha256': None,
            'evidence_head_hash': '4' * 64,
        }
        with tempfile.TemporaryDirectory() as td:
            key = Path(td) / 'private.pem'
            pub = Path(td) / 'public.pem'
            path = Path(td) / 'attestation.json'
            subprocess.run(['openssl', 'genpkey', '-algorithm', 'ED25519', '-out', str(key)], check=True, stdout=subprocess.DEVNULL)
            attest.derive_public(key, pub)
            signature = attest.sign_bytes(attest.canonical(payload), key)
            doc = {'payload': payload, 'signature': {
                'algorithm': 'Ed25519',
                'value_base64': base64.b64encode(signature).decode(),
                'public_key_sha256': attest._public_key_fingerprint(pub),
            }}
            path.write_text(json.dumps(doc), encoding='utf-8')
            with patch.dict(os.environ, {'HARNESS_ATTESTATION_PUBLIC_KEY': str(pub)}), \
                    patch.object(attest, 'make_payload', return_value=payload):
                self.assertTrue(attest.validate_current(path, 'T-r3-att', 'R3')['allow'])
                mutated = dict(doc)
                mutated['payload'] = dict(payload, candidate_subject_hash='5' * 64)
                path.write_text(json.dumps(mutated), encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'signature verification failed'):
                    attest.validate_current(path, 'T-r3-att', 'R3')

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


class RealR3FinishIntegrationTests(unittest.TestCase):
    """Exercise attestation and finish-gate recomputation against live task state."""

    TASK = 'SHARED-RUNTIME-SESSION-001'

    def test_real_r3_finish_rejects_mutated_candidate_after_signed_attestation(self):
        rd = evidence.run_dir(self.TASK)
        backup = Path(tempfile.mkdtemp(prefix='harness-r3-finish-backup-')) / self.TASK
        shutil.copytree(rd, backup)
        target = ROOT / '.opencode/plugins/harness/index.ts'
        original = target.read_bytes()
        marker = b'\n# transient candidate mutation\n'
        if marker in original:
            original = original.split(marker, 1)[0].rstrip(b'\r\n') + b'\n'
            target.write_bytes(original)
        try:
            with tempfile.TemporaryDirectory() as td:
                key = Path(td) / 'private.pem'
                public = Path(td) / 'public.pem'
                subprocess.run(
                    ['openssl', 'genpkey', '-algorithm', 'ED25519', '-out', str(key)],
                    check=True, stdout=subprocess.DEVNULL,
                )
                attest.derive_public(key, public)
                legacy_quarantine = Path(td) / 'legacy'
                legacy_quarantine.mkdir()
                legacy_records = []
                legacy_names = (
                    'active-task.json', 'session.json', 'permission-audit.jsonl',
                    'catalog-snapshot.json', 'model-inventory.json',
                    'enriched-inventory.json', 'model-selections.json',
                )
                for provider in ('codex', 'opencode', 'subscriptions'):
                    for base in dict.fromkeys((ROOT, runtime_root())):
                        for name in legacy_names:
                            path = base / '.harness' / provider / name
                            if path.is_file():
                                held = legacy_quarantine / str(len(legacy_records))
                                path.replace(held)
                                legacy_records.append((held, path))
                try:
                    with patch.dict(os.environ, {'HARNESS_ATTESTATION_PUBLIC_KEY': str(public)}):
                        attest.create(self.TASK, 'R3', str(key))

                        progress_path = rd / 'progress.json'
                        progress = json.loads(progress_path.read_text(encoding='utf-8'))
                        progress['state'] = 'RUNNING'
                        progress['current_step'] = 'CLOSE'
                        progress['completed'] = [step for step in progress.get('steps', []) if step != 'CLOSE']
                        progress_path.write_text(json.dumps(progress), encoding='utf-8')

                        before = finish_decision(self.TASK, 'R3', require_publication=False)
                        self.assertFalse(any(item.startswith('attestation:') for item in before['failing']))

                        target.write_bytes(original + b'\n# transient candidate mutation\n')
                        after = finish_decision(self.TASK, 'R3', require_publication=False)
                        self.assertTrue(any(item.startswith('attestation:') for item in after['failing']))
                finally:
                    generated_index = 0
                    for provider in ('codex', 'opencode', 'subscriptions'):
                        for base in dict.fromkeys((ROOT, runtime_root())):
                            for name in legacy_names:
                                path = base / '.harness' / provider / name
                                if path.is_file():
                                    path.replace(legacy_quarantine / ('generated-' + str(generated_index)))
                                    generated_index += 1
                    for held, path in legacy_records:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        held.replace(path)
        finally:
            target.write_bytes(original)
            shutil.rmtree(rd, ignore_errors=True)
            shutil.copytree(backup, rd)
            shutil.rmtree(backup.parent, ignore_errors=True)

if __name__=='__main__': unittest.main()
