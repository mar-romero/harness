import json, sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from model_task_profile import profile_task, target_for_agent
from model_router import load_policy, select_effort, select_model


class DynamicModelRoutingV2Tests(unittest.TestCase):
    def setUp(self):
        self.policy = load_policy()

    def test_architecture_profile_requires_more_reasoning_than_mechanical_task(self):
        arch = profile_task({'description': 'Design a multi-region PostgreSQL architecture with failover'}, 'R2', self.policy)
        typo = profile_task({'description': 'Fix README typo formatting'}, 'R0', self.policy)
        self.assertGreater(arch['requirements']['reasoning'], typo['requirements']['reasoning'])
        self.assertGreater(arch['requirements']['reliability'], typo['requirements']['reliability'])

    def test_implementer_target_is_more_coding_heavy_than_planner(self):
        profile = profile_task({'description': 'Implement a database migration with tests'}, 'R2', self.policy)
        planner = target_for_agent(profile, 'planner', 'R2', self.policy)
        implementer = target_for_agent(profile, 'implementer', 'R2', self.policy)
        self.assertGreater(implementer['coding'], planner['coding'])
        self.assertGreaterEqual(planner['reasoning'], implementer['reasoning'])

    def test_effort_is_separate_and_respects_risk_cap(self):
        target = {'reasoning': 5, 'coding': 5, 'tool_use': 5, 'reliability': 5}
        effort_r0, _ = select_effort(target, 'implementer', 'R0', ['low','medium','high','xhigh','max'], self.policy)
        effort_r3, _ = select_effort(target, 'security-reviewer', 'R3', ['low','medium','high','xhigh','max'], self.policy)
        self.assertEqual(effort_r0, 'medium')
        self.assertEqual(effort_r3, 'max')

    def test_independent_reviewer_uses_comparable_different_model(self):
        inventory = {
            'provider': 'codex', 'generated_at': '2099-01-01T00:00:00Z',
            'models': [
                {'id':'model-a','enabled':True,'native':True,'family':'family-a','vendor':'openai','supported_efforts':['low','medium','high'],
                 'capabilities':{'reasoning':5,'coding':5,'tool_use':5,'reliability':5},'cost':3,'latency':3},
                {'id':'model-b','enabled':True,'native':True,'family':'family-b','vendor':'openai','supported_efforts':['low','medium','high'],
                 'capabilities':{'reasoning':4.9,'coding':4.8,'tool_use':5,'reliability':5},'cost':3,'latency':3},
            ]
        }
        target = {'reasoning':4.5,'coding':4,'tool_use':4,'reliability':4.5}
        result = select_model(task_id='T', provider='codex', agent='reviewer', model_class='reasoning', risk='R2',
                              inventory=inventory, policy=self.policy, target=target,
                              avoid_models={'model-a'}, avoid_families={'family-a'}, avoid_vendors=set())
        self.assertEqual(result['base_model_id'], 'model-b')
        self.assertIn(result['independence']['strength'], {'different_model','different_family'})


if __name__ == '__main__': unittest.main()
