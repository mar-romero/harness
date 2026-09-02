import sys, unittest
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from model_router import select_model

POLICY = {
    'model_classes': {
        'fast': {'required': {'reasoning': 2, 'tool_use': 2}, 'weights': {'reasoning': .15, 'tool_use': .10, 'reliability': .15, 'latency': .30, 'cost': .30}},
        'coding': {'required': {'reasoning': 3, 'coding': 4, 'tool_use': 4, 'reliability': 3}, 'weights': {'reasoning': .15, 'coding': .30, 'tool_use': .20, 'reliability': .20, 'latency': .075, 'cost': .075}},
        'reasoning': {'required': {'reasoning': 4, 'reliability': 3}, 'weights': {'reasoning': .4, 'reliability': .3, 'tool_use': .1, 'latency': .1, 'cost': .1}}
    },
    'risk_overrides': {'R0': {}, 'R1': {}, 'R2': {'required_floor': {'reliability': 3}}, 'R3': {'required_floor': {'reasoning': 4, 'reliability': 4, 'tool_use': 3}, 'no_eligible_action': 'block'}},
    'selection': {'default_no_inventory_action': 'inherit', 'default_no_eligible_action': 'inherit', 'prefer_provider_native': True, 'score_precision': 6},
    'inventory': {'max_age_hours': 168, 'allow_stale_for_r0_r2': True, 'allow_stale_for_r3': False}
}
NOW = datetime(2026, 9, 2, tzinfo=timezone.utc)

def inv(models, generated='2026-09-02T00:00:00Z'):
    return {'provider': 'codex', 'generated_at': generated, 'models': models}

def m(mid, reasoning, coding, tool, reliability, cost, latency):
    return {'id': mid, 'enabled': True, 'native': True, 'capabilities': {'reasoning': reasoning, 'coding': coding, 'tool_use': tool, 'reliability': reliability}, 'cost': cost, 'latency': latency}

class ModelRouterTests(unittest.TestCase):
    def test_coding_prefers_capability_fit(self):
        inventory = inv([m('cheap-fast',3,4,4,3,5,5), m('strong-code',5,5,5,5,2,2)])
        r = select_model(task_id='T1', provider='codex', agent='implementer', model_class='coding', risk='R2', inventory=inventory, policy=POLICY, now=NOW)
        self.assertEqual(r['status'], 'selected'); self.assertEqual(r['model_id'], 'strong-code')
    def test_fast_prefers_cost_latency_when_both_eligible(self):
        inventory = inv([m('slow-expensive',5,5,5,5,1,1), m('fast-cheap',3,3,4,4,5,5)])
        r = select_model(task_id='T2', provider='codex', agent='explorer', model_class='fast', risk='R1', inventory=inventory, policy=POLICY, now=NOW)
        self.assertEqual(r['model_id'], 'fast-cheap')
    def test_no_inventory_inherits(self):
        r = select_model(task_id='T3', provider='codex', agent='planner', model_class='reasoning', risk='R1', inventory=None, policy=POLICY, now=NOW)
        self.assertEqual(r['action'], 'inherit')
    def test_r3_without_eligible_model_blocks(self):
        inventory = inv([m('weak',3,5,5,3,5,5)])
        r = select_model(task_id='T4', provider='codex', agent='security-reviewer', model_class='reasoning', risk='R3', inventory=inventory, policy=POLICY, now=NOW)
        self.assertEqual(r['action'], 'block')
    def test_r3_stale_inventory_blocks(self):
        inventory = inv([m('strong',5,5,5,5,3,3)], generated='2026-08-01T00:00:00Z')
        r = select_model(task_id='T5', provider='codex', agent='security-reviewer', model_class='reasoning', risk='R3', inventory=inventory, policy=POLICY, now=NOW)
        self.assertEqual(r['action'], 'block'); self.assertTrue(r['inventory_stale'])

if __name__ == '__main__': unittest.main()
