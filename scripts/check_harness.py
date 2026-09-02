#!/usr/bin/env python3
from __future__ import annotations
import json, re, subprocess, sys
from pathlib import Path
from harnesslib import ROOT, load_manifest, load_json
from compile_harness import generated

def fail(msg, errors): errors.append(msg)
def main():
    errors=[]; m=load_manifest()
    if m.get('schema_version')!=2: fail('manifest schema_version must be 2',errors)
    agents=m.get('agents',{}); skills_dir=ROOT/m['canonical']['skills_dir']; roles_dir=ROOT/m['canonical']['roles_dir']
    if len(agents)<9: fail('expected at least 9 canonical agents',errors)
    for name,meta in agents.items():
        rp=roles_dir/f'{name}.md'
        if not rp.exists() or len(rp.read_text().strip())<80: fail(f'missing/weak role: {name}',errors)
        if meta['mode'] not in {'read-only','writer'}: fail(f'invalid agent mode: {name}',errors)
    skill_names=[]
    for d in sorted(skills_dir.iterdir() if skills_dir.exists() else []):
        if not d.is_dir(): continue
        s=d/'SKILL.md'; skill_names.append(d.name)
        if not s.exists(): fail(f'missing SKILL.md: {d.name}',errors); continue
        txt=s.read_text(encoding='utf-8')
        if not txt.startswith('---\n'): fail(f'skill frontmatter must start first: {d.name}',errors)
        if f'name: {d.name}\n' not in txt: fail(f'skill name mismatch: {d.name}',errors)
        if not re.search(r'^description:\s+\S',txt,re.M): fail(f'missing skill description: {d.name}',errors)
    if len(skill_names)<20: fail('original skill inventory was not preserved',errors)
    for name,meta in agents.items():
        for s in meta.get('skills',[]):
            if s not in skill_names: fail(f'agent {name} references missing skill {s}',errors)
    for rel,content in generated().items():
        p=ROOT/rel
        if not p.exists(): fail(f'missing generated adapter {rel}',errors)
        elif p.read_text(encoding='utf-8')!=content: fail(f'generated adapter drift {rel}',errors)
    # hooks and policy
    for p in ['harness/policies/risk-policy.json','harness/context-policy.json','harness/language-policy.json','harness/handoff-policy.json','harness/orchestrator-policy.json','harness/attestation-policy.json','harness/evolution-policy.json','harness/evolution-experiment-policy.json','harness/runtime-evals/config.json','.claude/settings.json','.cursor/hooks.json','.gemini/settings.json']:
        try: json.loads((ROOT/p).read_text())
        except Exception as e: fail(f'invalid JSON {p}: {e}',errors)
    # final-v4 feature invariants
    try:
        lang=load_json('harness/language-policy.json')
        if lang.get('canonical_agent_language')!='en' or not lang.get('fail_closed_on_ambiguity'):
            fail('language boundary must be canonical English and fail closed',errors)
        risk=load_json('harness/policies/risk-policy.json')
        if 'attestation' not in risk.get('finish_requirements',{}).get('R3',[]):
            fail('R3 finish must require signed attestation evidence',errors)
        evo=load_json('harness/evolution-experiment-policy.json')
        if evo.get('auto_apply') is not False or evo.get('human_promotion_required') is not True:
            fail('champion/challenger evolution must remain human-reviewed and non-auto-apply',errors)
        required_scripts=['request_normalizer.py','runtime_eval.py','handoff.py','orchestrator.py','attest.py','context_graph.py','memory.py','evolution_experiment.py']
        for name in required_scripts:
            if not (ROOT/'scripts'/name).exists(): fail(f'missing final-v4 script {name}',errors)
        for name in ['explorer','plan','implementation-result','review','verification']:
            if not (ROOT/'harness/schema/handoffs'/f'{name}.schema.json').exists(): fail(f'missing typed handoff schema {name}',errors)
    except Exception as e:
        fail(f'final-v4 feature invariant error: {e}',errors)

    # no accidental runtime evidence committed in starter
    if (ROOT/'.harness/runs').exists() and any((ROOT/'.harness/runs').iterdir()): fail('starter contains runtime evidence',errors)
    if errors:
        print('HARNESS CHECK FAILED')
        for e in errors: print(' -',e)
        return 1
    agent_artifacts=len(agents)*len(m['providers']); wrappers=len(generated())-agent_artifacts
    print(f'HARNESS CHECK PASSED: {len(agents)} agents, {len(skill_names)} skills, {agent_artifacts} agent adapters, {wrappers} compatibility wrappers')
    return 0
if __name__=='__main__': raise SystemExit(main())
