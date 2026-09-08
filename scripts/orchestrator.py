#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from agent_budget import observe_progress as observe_agent_budget
from evidence import append as append_evidence
from evidence import validate as validate_evidence
from handoff import save as save_handoff
from harnesslib import ROOT, load_json, run_dir, safe_task_id, write_json_atomic
from impact_analysis import finish_decision as impact_finish_decision
from tdd_evidence import append as append_tdd_evidence
from tdd_evidence import finish_decision as tdd_finish_decision
from worktree import status as worktree_status

POLICY = ROOT / 'harness/orchestrator-policy.json'

SUBAGENT_STAGES = {
    'EXPLORE': ('explorer', 'exploration'),
    'PLAN': ('planner', 'planning'),
    'TEST_DESIGN': ('test-designer', 'test_design'),
    'IMPLEMENT': ('implementer', 'implementation'),
    'REVIEW': ('reviewer', 'review'),
    'TEST_AUDIT': ('test-auditor', 'test_audit'),
    'VERIFY': ('verifier', 'verification'),
    'SECURITY_REVIEW': ('security-reviewer', 'security_review'),
}


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00', 'Z')


def path(task):
    return run_dir(task) / 'progress.json'


def _route_path(task):
    return run_dir(task) / 'route.json'


def _route(task):
    p = _route_path(safe_task_id(task))
    if not p.exists():
        raise ValueError(f'route.json missing for {task}')
    return json.loads(p.read_text(encoding='utf-8'))


def _impact_required(risk):
    policy = load_json('harness/impact-policy.json')
    return bool((policy.get('finish_gate') or {}).get(risk, False))


def steps_for(route):
    agents = route.get('agents', [])
    tdd = route.get('tdd') or {}
    out = []

    if 'explorer' in agents:
        out.append('EXPLORE')
    if 'planner' in agents:
        out.append('PLAN')
    if tdd.get('test_designer') and 'test-designer' in agents:
        out.append('TEST_DESIGN')
    if route.get('isolation') == 'worktree':
        out.append('WORKTREE')

    out.extend(['IMPLEMENT', 'CHECKS', 'VERIFY_ASSESS'])

    receipt_review = route.get('receipt_review') or {}
    if receipt_review.get('consent_required'):
        out.append('REVIEW_CONSENT')
    if 'reviewer' in agents:
        out.append('REVIEW')
    if 'test-auditor' in agents:
        out.append('TEST_AUDIT')
    if 'verifier' in agents:
        out.append('VERIFY')
    if _impact_required(route.get('risk', 'R1')):
        out.append('IMPACT_VERIFY')
    if 'security-reviewer' in agents:
        out.append('SECURITY_REVIEW')
    if route.get('human_gate'):
        out.append('HUMAN_GATE')

    out.append('CLOSE')
    return out


def _first_incomplete(steps, completed):
    for step in steps:
        if step not in completed:
            return step
    return 'CLOSE'


def _reconcile_progress(existing, route):
    new_steps = steps_for(route)
    old_steps = list(existing.get('steps') or [])
    old_risk = existing.get('risk')
    new_risk = route['risk']

    if old_steps == new_steps and old_risk == new_risk:
        return existing, False

    completed = [s for s in existing.get('completed', []) if s in new_steps]
    current = _first_incomplete(new_steps, completed)
    old_current = existing.get('current_step')

    existing['risk'] = new_risk
    existing['steps'] = new_steps
    existing['completed'] = completed
    existing['current_step'] = current
    existing.setdefault('attempts', {})
    existing.setdefault('history', []).append({
        'at': now(),
        'event': 'route_reconciled',
        'old_risk': old_risk,
        'new_risk': new_risk,
        'old_step': old_current,
        'new_step': current,
        'old_steps': old_steps,
        'new_steps': new_steps,
    })

    if existing.get('state') == 'DONE' and current != 'CLOSE':
        existing['state'] = 'RUNNING'
        existing['recommended_action'] = 'continue'
    elif old_current != current and existing.get('state') in {'WAITING', 'BLOCKED', 'STALLED'}:
        existing['state'] = 'RUNNING'
        existing['recommended_action'] = 'continue'

    return existing, True


def init_progress(task, route, overwrite=False):
    safe_task_id(task)
    p = path(task)
    if p.exists() and not overwrite:
        state = json.loads(p.read_text(encoding='utf-8'))
        state, changed = _reconcile_progress(state, route)
        if changed:
            write_json_atomic(p, state)
        return state

    steps = steps_for(route)
    state = {
        'schema_version': 2,
        'task_id': task,
        'risk': route['risk'],
        'state': 'READY',
        'current_step': steps[0],
        'steps': steps,
        'completed': [],
        'attempts': {},
        'failures': 0,
        'recommended_action': 'start',
        'history': [{'at': now(), 'event': 'initialized', 'step': steps[0]}],
    }
    write_json_atomic(p, state)
    return state


def load(task):
    return json.loads(path(safe_task_id(task)).read_text(encoding='utf-8'))


def _next(state):
    cur = state['current_step']
    i = state['steps'].index(cur)
    return state['steps'][i + 1] if i + 1 < len(state['steps']) else None


def _latest_evidence(task, category):
    from evidence import read as read_evidence
    rows = read_evidence(task)
    found = None
    for row in rows:
        if row.get('category') == category:
            found = row
    return found


def _validate_pass_prerequisites(task, step, via_commit=False):
    route = _route(task)

    if step in SUBAGENT_STAGES and not via_commit:
        raise ValueError(
            f'{step} is a typed subagent stage; use orchestrator.py commit so handoff, evidence and progress are persisted in order'
        )

    if step == 'WORKTREE':
        wt = worktree_status(task)
        if not wt.get('exists') or not wt.get('lock'):
            raise ValueError('WORKTREE cannot PASS until the task worktree and writer lock both exist')

    if step == 'IMPLEMENT':
        tdd = tdd_finish_decision(task)
        if not tdd.get('allow'):
            missing = ','.join(tdd.get('missing', [])) or '-'
            failing = ','.join(tdd.get('failing', [])) or '-'
            raise ValueError(f'IMPLEMENT cannot PASS before required TDD evidence is valid; missing={missing}; failing={failing}')
        if route.get('isolation') == 'worktree':
            wt = worktree_status(task)
            if not wt.get('exists') or not wt.get('lock'):
                raise ValueError('IMPLEMENT cannot PASS without the assigned worktree and writer lock')

    if step == 'CHECKS':
        row = _latest_evidence(task, 'checks')

        if (
            not row
            or row.get('evidence_type') != 'DETERMINISTIC'
            or row.get('status') != 'PASS'
        ):
            raise ValueError(
                'CHECKS cannot PASS without deterministic PASS checks evidence'
            )

        if row.get('actor') != 'check-runner':
            raise ValueError(
                'CHECKS evidence must be produced by the allowlisted check-runner'
            )

        if row.get('exit_code') != 0:
            raise ValueError(
                'CHECKS cannot PASS when the deterministic check suite exit code is non-zero'
            )

        artifact = row.get('artifact')
        expected = f'.harness/runs/{task}/checks-report.json'

        if artifact != expected:
            raise ValueError(
                'CHECKS evidence must reference the authoritative checks report'
            )

        report_path = ROOT / artifact
        if not report_path.is_file():
            raise ValueError('CHECKS authoritative checks report is missing')

        report = json.loads(report_path.read_text(encoding='utf-8'))
        if report.get('task_id') != task or report.get('status') != 'PASS':
            raise ValueError(
                'CHECKS authoritative report does not contain a PASS for this task'
            )
    # DUAL_RDD_CONTROL_STAGES_V1:START
    if step == 'VERIFY_ASSESS':
        from receipt_review import verification_finish_decision
        decision = verification_finish_decision(task)
        if not decision.get('allow'):
            raise ValueError(
                'VERIFY_ASSESS cannot PASS until receipt/verification assessment is current: '
                + json.dumps(decision, ensure_ascii=False, sort_keys=True)
            )

    if step == 'REVIEW_CONSENT':
        from receipt_review import consent_decision
        decision = consent_decision(task)
        if not decision.get('allow'):
            raise ValueError(
                'REVIEW_CONSENT cannot PASS without explicit current-session consent: '
                + json.dumps(decision, ensure_ascii=False, sort_keys=True)
            )
    # DUAL_RDD_CONTROL_STAGES_V1:END

    if step == 'IMPACT_VERIFY':
        decision = impact_finish_decision(task)
        if not decision.get('allow'):
            raise ValueError('IMPACT_VERIFY cannot PASS until task-relative impact verification passes')

    if step == 'HUMAN_GATE':
        row = _latest_evidence(task, 'human_approval')
        if not row or row.get('evidence_type') != 'DETERMINISTIC' or row.get('status') != 'PASS':
            raise ValueError('HUMAN_GATE cannot PASS without deterministic human approval evidence')

    if step == 'CLOSE':
        from gate import finish_decision
        decision = finish_decision(task, route['risk'])
        if not decision.get('allow'):
            raise ValueError(
                'CLOSE cannot PASS because finish gate is blocking: '
                + json.dumps(decision, ensure_ascii=False, sort_keys=True)
            )


def record(task, status, step=None, note=None, _via_commit=False):
    policy = json.loads(POLICY.read_text(encoding='utf-8'))
    s = load(task)
    step = step or s['current_step']
    if step != s['current_step']:
        raise ValueError(f'expected current step {s["current_step"]}, got {step}')

    if status == 'PASS':
        _validate_pass_prerequisites(task, step, via_commit=_via_commit)

    s['attempts'][step] = int(s['attempts'].get(step, 0)) + 1
    evt = {
        'at': now(),
        'event': 'step_result',
        'step': step,
        'status': status,
        'attempt': s['attempts'][step],
    }
    if note:
        evt['note'] = note
    s['history'].append(evt)

    if status == 'PASS':
        if step not in s['completed']:
            s['completed'].append(step)
        nxt = _next(s)
        if nxt is None or step == 'CLOSE':
            s['state'] = 'DONE'
            s['current_step'] = 'CLOSE'
            s['recommended_action'] = 'none'
        else:
            s['state'] = 'RUNNING'
            s['current_step'] = nxt
            s['recommended_action'] = 'continue'
    elif status in {'BLOCKED', 'INSUFFICIENT'}:
        s['state'] = 'BLOCKED'
        s['failures'] += 1
        s['recommended_action'] = policy['failure_actions'].get(step, 'escalate_or_replan')
    elif status == 'FAIL':
        s['failures'] += 1
        if (
            step == 'SECURITY_REVIEW'
            or s['attempts'][step] >= int(policy['max_attempts_per_step'])
            or s['failures'] >= int(policy['max_total_failures'])
        ):
            s['state'] = 'STALLED'
            s['recommended_action'] = policy['failure_actions'].get(step, 'replan')
        else:
            s['state'] = 'WAITING'
            s['recommended_action'] = policy['failure_actions'].get(step, 'retry_once_then_replan')
    else:
        raise ValueError('status must be PASS, FAIL, BLOCKED or INSUFFICIENT')

    write_json_atomic(path(task), s)

    budget = observe_agent_budget(task, step, status, s)
    if budget is not None:
        s['agent_budget'] = budget
        write_json_atomic(path(task), s)
    return s


def _resolve_inside_repo(value):
    p = Path(value)
    p = p if p.is_absolute() else ROOT / p
    p = p.resolve()
    try:
        p.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ValueError('handoff path must stay inside the repository') from exc
    if not p.is_file():
        raise ValueError(f'handoff file not found: {p}')
    return p


def _normalize_handoff_status(value):
    if value == 'PASS':
        return 'PASS'
    if value == 'FAIL':
        return 'FAIL'
    if value == 'BLOCKED':
        return 'BLOCKED'
    if value == 'INSUFFICIENT':
        return 'INSUFFICIENT'
    raise ValueError(f'unsupported handoff status: {value}')


def commit(task, role, handoff_file, note=None):
    task = safe_task_id(task)
    state = load(task)
    step = state['current_step']
    expected = SUBAGENT_STAGES.get(step)
    if not expected:
        raise ValueError(f'{step} is not a typed subagent stage; use orchestrator.py record for control-plane stages')

    expected_role, category = expected
    if role != expected_role:
        raise ValueError(f'{step} requires role {expected_role}, got {role}')

    source = _resolve_inside_repo(handoff_file)
    data = json.loads(source.read_text(encoding='utf-8'))
    if data.get('task_id') != task:
        raise ValueError(f'handoff task_id {data.get("task_id")} does not match active task {task}')

    canonical = save_handoff(role, data)
    status = _normalize_handoff_status(data.get('status'))
    # DUAL_RDD_REVIEW_RECEIPT_V1:START
    if step == 'REVIEW' and status == 'PASS':
        route = _route(task)
        receipt_cfg = route.get('receipt_review') or {}
        if receipt_cfg.get('receipt_required') and receipt_cfg.get('receipt_kind') == 'review':
            from receipt_review import issue_review
            issue_review(task, canonical)
    # DUAL_RDD_REVIEW_RECEIPT_V1:END

    evidence_status = 'PASS' if status == 'PASS' else ('FAIL' if status == 'FAIL' else 'BLOCKED')

    artifact = canonical.relative_to(ROOT).as_posix()
    append_evidence(
        task,
        category,
        'INFERRED',
        f'{step} typed handoff validated and persisted',
        evidence_status,
        role,
        artifact=artifact,
        notes=note,
    )

    chain = validate_evidence(task)
    if not chain.get('valid'):
        raise ValueError(f'evidence chain validation failed after {step}: {chain.get("reason")}')

    if step == 'TEST_DESIGN':
        append_tdd_evidence(
            task,
            'design',
            'PASS' if status == 'PASS' else 'BLOCKED',
            'test-designer',
            'typed test design handoff accepted' if status == 'PASS' else 'test design blocked',
            artifact=artifact,
            notes=note,
        )

    return record(task, status, step=step, note=note, _via_commit=True)


def resume(task, step=None, note=None):
    s = load(task)
    if s['state'] not in {'WAITING', 'BLOCKED', 'STALLED'}:
        raise ValueError(f'cannot resume from state {s["state"]}')
    if step:
        if step not in s['steps']:
            raise ValueError('step not in route')
        s['current_step'] = step
    s['state'] = 'RUNNING'
    s['recommended_action'] = 'continue'
    s['history'].append({'at': now(), 'event': 'resumed', 'step': s['current_step'], 'note': note or ''})
    write_json_atomic(path(task), s)
    return s



# DUAL_RDD_RECONCILE_V1:START
def reconcile(task):
    task = safe_task_id(task)
    route = _route(task)
    state = load(task)
    state, changed = _reconcile_progress(state, route)
    if changed:
        write_json_atomic(path(task), state)
    return state
# DUAL_RDD_RECONCILE_V1:END


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('init')
    p.add_argument('task')
    p.add_argument('--route', required=True)
    p.add_argument('--overwrite', action='store_true')

    p = sub.add_parser('status')
    p.add_argument('task')

    p = sub.add_parser('reconcile')
    p.add_argument('task')

    p = sub.add_parser('record')
    p.add_argument('task')
    p.add_argument('--status', required=True, choices=['PASS', 'FAIL', 'BLOCKED', 'INSUFFICIENT'])
    p.add_argument('--step')
    p.add_argument('--note')

    p = sub.add_parser('commit')
    p.add_argument('task')
    p.add_argument('--role', required=True)
    p.add_argument('--handoff', required=True)
    p.add_argument('--note')

    p = sub.add_parser('resume')
    p.add_argument('task')
    p.add_argument('--step')
    p.add_argument('--note')

    a = ap.parse_args()
    try:
        if a.cmd == 'init':
            s = init_progress(a.task, json.loads(Path(a.route).read_text(encoding='utf-8')), a.overwrite)
        elif a.cmd == 'status':
            s = load(a.task)
        elif a.cmd == 'reconcile':
            s = reconcile(a.task)
        elif a.cmd == 'record':
            s = record(a.task, a.status, a.step, a.note)
        elif a.cmd == 'commit':
            s = commit(a.task, a.role, a.handoff, a.note)
        else:
            s = resume(a.task, a.step, a.note)
        print(json.dumps(s, indent=2, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({'ok': False, 'reason': str(e)}, ensure_ascii=False, indent=2))
        raise SystemExit(2)


if __name__ == '__main__':
    main()
