import json, os, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import openrouter_sync


class OpenRouterSyncV2Tests(unittest.TestCase):
    def test_percentile_and_inverse_scores_are_bounded(self):
        p = openrouter_sync._percentiles({'a':10,'b':20,'c':30})
        inv = openrouter_sync._inverse_rank_scores({'cheap':1,'expensive':10})
        self.assertTrue(all(1 <= x <= 5 for x in p.values()))
        self.assertGreater(inv['cheap'], inv['expensive'])

    def test_codex_discovery_prefers_account_cache(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            (home/'models_cache.json').write_text(json.dumps({
                'models': [
                    {'slug':'gpt-5.6-sol','visibility':'list'},
                    {'slug':'gpt-5.6-luna','visibility':'list'}
                ]
            }))
            cfg = json.loads((ROOT/'harness/model-providers/codex.json').read_text())
            with patch.dict(os.environ, {'CODEX_HOME': td}, clear=False):
                rows = openrouter_sync.discover_codex(cfg)
            self.assertEqual({x['id'] for x in rows}, {'gpt-5.6-sol','gpt-5.6-luna'})
            self.assertTrue(all(x['availability_source']=='codex-models-cache' for x in rows))


if __name__ == '__main__': unittest.main()
