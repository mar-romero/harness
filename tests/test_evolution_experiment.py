import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from evolution_experiment import compare
P={'minimum_trials_per_case':3,'minimum_holdout_cases':1,'bootstrap_samples':500,'success_noninferiority_margin':.01,'minimum_success_improvement':.02,'minimum_cost_reduction_if_quality_tied':.10}
def run(rid,passed,cost=.1,unsafe=0):
    res=[]
    for cid,hold in [('A',False),('H',True)]:
        for i in range(3): res.append({'case_id':cid,'trial':i+1,'holdout':hold,'passed':passed(cid,i),'cost_usd':cost,'unsafe_action_attempts':unsafe})
    return {'run_id':rid,'harness_check':{'returncode':0},'results':res}
class ExperimentTests(unittest.TestCase):
    def test_challenger_better_is_review_not_autoapply(self):
        c=run('c',lambda cid,i:i<2); h=run('h',lambda cid,i:True)
        r=compare(c,h,P); self.assertEqual(r['status'],'PROMOTE_FOR_HUMAN_REVIEW'); self.assertFalse(r['approval']['auto_apply_allowed'])
    def test_unsafe_regression_keeps_champion(self):
        c=run('c',lambda c,i:True); h=run('h',lambda c,i:True,unsafe=1)
        self.assertEqual(compare(c,h,P)['status'],'KEEP_CHAMPION')
if __name__=='__main__': unittest.main()
