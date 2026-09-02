import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from evolution_engine import analyze_runs

POLICY = {
    'minimum_baseline_runs': 2,
    'baseline_window': 5,
    'metrics': {
        'task_success_rate': {'direction':'higher','material_delta':.03,'critical_delta':.08},
        'unsafe_action_attempts': {'direction':'lower','material_delta':1.0,'critical_delta':1.0},
        'tokens': {'direction':'lower','material_delta':.10,'critical_delta':.30,'relative':True}
    }
}

def run(rid, success=.90, tokens=1000, unsafe=0, rc=0):
    return {'schema_version':1,'run_id':rid,'created_at':rid,'deterministic':{'unit_tests':{'returncode':rc},'evals':{'returncode':rc}},'metrics':{'task_success_rate':success,'tokens':tokens,'unsafe_action_attempts':unsafe}}

class EvolutionEngineTests(unittest.TestCase):
    def test_insufficient_baseline(self):
        r=analyze_runs([run('1'),run('2')], POLICY); self.assertEqual(r['status'],'INSUFFICIENT_BASELINE')
    def test_no_change_when_candidate_within_thresholds(self):
        r=analyze_runs([run('1',.90,1000),run('2',.91,1050),run('3',.90,1070)], POLICY)
        self.assertEqual(r['status'],'NO_CHANGE'); self.assertFalse(r['proposals'])
    def test_metric_regression_requires_human_review(self):
        r=analyze_runs([run('1',.92,1000),run('2',.91,1000),run('3',.84,1450)], POLICY)
        self.assertEqual(r['status'],'HUMAN_REVIEW_REQUIRED')
        self.assertTrue(any(x.get('metric')=='task_success_rate' for x in r['findings']))
        self.assertTrue(any(x['target_area']=='context' for x in r['proposals']))
    def test_unsafe_attempt_is_critical(self):
        r=analyze_runs([run('1'),run('2'),run('3',unsafe=1)], POLICY)
        self.assertEqual(r['status'],'HUMAN_REVIEW_REQUIRED')
        self.assertTrue(any(x['target_area']=='security-gates' for x in r['proposals']))
    def test_deterministic_failure_blocks_optimization(self):
        r=analyze_runs([run('1'),run('2'),run('3',rc=1)], POLICY)
        self.assertEqual(r['status'],'BLOCKED_REGRESSION')
        self.assertEqual(r['findings'][0]['kind'],'hard_regression')

if __name__ == '__main__': unittest.main()
