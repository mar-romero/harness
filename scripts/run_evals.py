#!/usr/bin/env python
from __future__ import annotations
import json, re
from pathlib import Path
from harnesslib import ROOT, load_manifest
from task_router import route
from gate import command_decision


def fixture_evals(failures):
    count=0
    for p in sorted((ROOT/'evals/cases').glob('*.json')):
        count += 1; c=json.loads(p.read_text())
        if c['kind']=='route':
            out=route(c['task']); exp=c['expect']
            if out['risk']!=exp['risk']: failures.append(f'{p.name}: risk {out["risk"]} != {exp["risk"]}')
            for a in exp.get('agents_contains',[]):
                if a not in out['agents']: failures.append(f'{p.name}: missing agent {a}')
            for a in exp.get('agents_excludes',[]):
                if a in out['agents']: failures.append(f'{p.name}: unexpected agent {a}')
        elif c['kind']=='gate':
            out=command_decision(c['command'],c.get('risk','R1'))
            if out['allow']!=c['expect_allow']: failures.append(f'{p.name}: allow={out["allow"]}')
            if out.get('human_gate',False)!=c.get('expect_human_gate',False): failures.append(f'{p.name}: human_gate mismatch')
        elif c['kind']=='route_pair':
            left=route(c['left']); right=route(c['right'])
            if left['risk']!=right['risk']: failures.append(f'{p.name}: cross-lingual risk mismatch {left["risk"]} != {right["risk"]}')
            if left['agents']!=right['agents']: failures.append(f'{p.name}: cross-lingual agent route mismatch')
            if left['skills']!=right['skills']: failures.append(f'{p.name}: cross-lingual skill route mismatch')
            if left['risk']!=c.get('expect_risk'): failures.append(f'{p.name}: expected risk {c.get("expect_risk")}, got {left["risk"]}')
        else: failures.append(f'{p.name}: unknown kind')
    return count


def agent_contract_evals(failures):
    m=load_manifest(); count=0; writers=[]
    for name,meta in m['agents'].items():
        count += 1
        p=ROOT/m['canonical']['roles_dir']/f'{name}.md'; text=p.read_text(encoding='utf-8') if p.exists() else ''
        if not text: failures.append(f'agent:{name}: missing canonical role'); continue
        if not re.search(r'Do not[^.\n]*delegate', text, re.I): failures.append(f'agent:{name}: missing no-delegation boundary')
        if meta['mode']=='read-only':
            if 'Do not edit' not in text: failures.append(f'agent:{name}: read-only role does not explicitly forbid edits')
        else: writers.append(name)
        for skill in meta.get('skills',[]):
            if not (ROOT/m['canonical']['skills_dir']/skill/'SKILL.md').exists(): failures.append(f'agent:{name}: missing skill {skill}')
    if writers != ['implementer']: failures.append(f'agents: expected sole writer implementer, got {writers}')
    return count


def skill_contract_evals(failures):
    m=load_manifest(); count=0
    for d in sorted((ROOT/m['canonical']['skills_dir']).iterdir()):
        if not d.is_dir(): continue
        count += 1; p=d/'SKILL.md'
        if not p.exists(): failures.append(f'skill:{d.name}: missing SKILL.md'); continue
        text=p.read_text(encoding='utf-8')
        if not text.startswith('---\n'): failures.append(f'skill:{d.name}: frontmatter not first')
        if not re.search(rf'^name:\s*{re.escape(d.name)}\s*$',text,re.M): failures.append(f'skill:{d.name}: name mismatch')
        if not re.search(r'^description:\s+\S',text,re.M): failures.append(f'skill:{d.name}: description missing')
        # Portable canonical skills must not hard-code one provider's agent directory.
        body=text.split('---',2)[-1]
        if re.search(r'(?m)^\s*(\.claude/agents|\.codex/agents|\.cursor/agents|\.gemini/agents|\.opencode/agents|\.github/agents)',body):
            failures.append(f'skill:{d.name}: provider-specific agent path leaked into canonical body')
    return count


def main():
    failures=[]
    fixture_count=fixture_evals(failures)
    agent_count=agent_contract_evals(failures)
    skill_count=skill_contract_evals(failures)
    if failures:
        print('EVALS FAILED'); [print(' -',x) for x in failures]; return 1
    total=fixture_count+agent_count+skill_count
    print(f'EVALS PASSED: {total} deterministic evaluations '
          f'({fixture_count} fixtures, {agent_count} agent contracts, {skill_count} skill contracts)')
    return 0
if __name__=='__main__': raise SystemExit(main())
