import json, os, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import openrouter_sync


class OpenRouterSyncV2Tests(unittest.TestCase):
    def test_project_env_falls_back_to_repo_dotenv_and_process_env_wins(self):
        with tempfile.TemporaryDirectory() as td, patch.object(openrouter_sync, 'ROOT', Path(td)):
            Path(td, '.env').write_text(
                'OPENROUTER_API_KEY=dotenv-key\n',
                encoding='utf-8',
            )
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(
                    openrouter_sync._project_env('OPENROUTER_API_KEY'),
                    'dotenv-key',
                )
            with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'process-key'}, clear=True):
                self.assertEqual(
                    openrouter_sync._project_env('OPENROUTER_API_KEY'),
                    'process-key',
                )

    def test_refresh_uses_openrouter_key_from_repo_dotenv(self):
        candidate = {
            'id': 'gpt-example',
            'enabled': True,
            'native': True,
            'vendor': 'openai',
            'family': 'openai/gpt-example',
            'supported_efforts': [],
            'supports_tools': True,
            'supports_reasoning': True,
            'context_window': 0,
        }
        models = {'data': [{
            'id': 'openai/gpt-example',
            'supported_parameters': ['tools', 'reasoning'],
            'context_length': 100000,
        }]}
        benchmarks = {'data': [{
            'model_permaslug': 'openai/gpt-example',
            'intelligence_index': 80,
            'coding_index': 75,
            'agentic_index': 70,
        }]}

        with tempfile.TemporaryDirectory() as td, \
                patch.object(openrouter_sync, 'discover_provider', return_value=[candidate]), \
                patch.object(openrouter_sync, 'ROOT', Path(td)), \
                patch.object(openrouter_sync, '_fetch_sources', return_value=(models, benchmarks)) as fetch:
            Path(td, '.env').write_text('OPENROUTER_API_KEY=dotenv-key\n', encoding='utf-8')
            Path(td, 'harness/model-providers').mkdir(parents=True)
            Path(td, 'harness/model-providers/codex.json').write_text(
                json.dumps({
                    'provider': 'codex',
                    'enriched_inventory': '.harness/model-inventories/codex.json',
                    'openrouter_aliases': {},
                    'openrouter': {'fetch_endpoint_health': False},
                }),
                encoding='utf-8',
            )
            with patch.dict(os.environ, {}, clear=True):
                payload, _ = openrouter_sync.refresh_provider_inventory(
                    'codex',
                    fetch_endpoints=False,
                )

        fetch.assert_called_once_with('dotenv-key')
        row = payload['models'][0]
        self.assertGreater(row['capabilities']['reasoning'], 0)
        self.assertGreater(row['capabilities']['coding'], 0)

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

    def test_codex_refresh_resolves_against_openrouter_catalog(self):
        candidate = {
            'id': 'gpt-example',
            'openrouter_id': 'openai/gpt-example',
            'openrouter_match': 'native-derived',
            'enabled': True,
            'native': True,
            'vendor': 'openai',
            'family': 'openai/gpt-example',
            'supported_efforts': [],
            'supports_tools': True,
            'context_window': 0,
        }
        models = {'data': [{
            'id': 'openai/gpt-example',
            'supported_parameters': ['tools'],
            'context_length': 100000,
            'pricing': {'prompt': '0.000001', 'completion': '0.000002'},
        }]}
        benchmarks = {'data': [{
            'model_id': 'openai/gpt-example',
            'intelligence_index': 80,
            'coding_index': 75,
            'agentic_index': 70,
        }]}

        with tempfile.TemporaryDirectory() as td, \
                patch.object(openrouter_sync, 'discover_provider', return_value=[candidate]), \
                patch.object(openrouter_sync, 'ROOT', Path(td)):
            Path(td, 'harness/model-providers').mkdir(parents=True)
            Path(td, 'harness/model-providers/codex.json').write_text(
                json.dumps({
                    'provider': 'codex',
                    'enriched_inventory': '.harness/model-inventories/codex.json',
                    'openrouter_aliases': {},
                    'openrouter': {'fetch_endpoint_health': False},
                }),
                encoding='utf-8',
            )
            payload, _ = openrouter_sync.refresh_provider_inventory(
                'codex',
                models_payload=models,
                benchmarks_payload=benchmarks,
                fetch_endpoints=False,
            )

        row = payload['models'][0]
        self.assertEqual(row['openrouter_id'], 'openai/gpt-example')
        self.assertEqual(row['openrouter_match'], 'auto-exact-normalized')
        self.assertGreater(row['capabilities']['reasoning'], 0)
        self.assertGreater(row['capabilities']['coding'], 0)

    def test_codex_refresh_does_not_trust_unverified_derived_id(self):
        candidate = {
            'id': 'gpt-internal-sol',
            'openrouter_id': 'openai/gpt-internal-sol',
            'openrouter_match': 'native-derived',
            'enabled': True,
            'native': True,
            'vendor': 'openai',
            'family': 'openai/gpt-internal',
            'supported_efforts': [],
            'supports_tools': True,
            'context_window': 0,
        }
        benchmarks = {'data': [{
            'model_id': 'openai/gpt-internal-sol',
            'intelligence_index': 99,
            'coding_index': 99,
        }]}

        with tempfile.TemporaryDirectory() as td, \
                patch.object(openrouter_sync, 'discover_provider', return_value=[candidate]), \
                patch.object(openrouter_sync, 'ROOT', Path(td)):
            Path(td, 'harness/model-providers').mkdir(parents=True)
            Path(td, 'harness/model-providers/codex.json').write_text(
                json.dumps({
                    'provider': 'codex',
                    'enriched_inventory': '.harness/model-inventories/codex.json',
                    'openrouter_aliases': {},
                    'openrouter': {'fetch_endpoint_health': False},
                }),
                encoding='utf-8',
            )
            payload, _ = openrouter_sync.refresh_provider_inventory(
                'codex',
                models_payload={'data': []},
                benchmarks_payload=benchmarks,
                fetch_endpoints=False,
            )

        row = payload['models'][0]
        self.assertIsNone(row['openrouter_id'])
        self.assertEqual(row['openrouter_match'], 'unmatched')
        self.assertEqual(row['capabilities']['reasoning'], 0)
        self.assertEqual(row['capabilities']['coding'], 0)

    def test_versioned_benchmark_enriches_stable_catalog_model(self):
        candidate = {
            'id': 'gpt-example',
            'openrouter_id': 'openai/gpt-example',
            'openrouter_match': 'native-derived',
            'enabled': True,
            'native': True,
            'vendor': 'openai',
            'family': 'openai/gpt-example',
            'supported_efforts': [],
            'supports_tools': True,
            'context_window': 0,
        }
        models = {'data': [{
            'id': 'openai/gpt-example',
            'supported_parameters': ['tools'],
            'context_length': 100000,
        }]}
        benchmarks = {'data': [{
            'model_id': 'openai/gpt-example-20260903',
            'intelligence_index': 80,
            'coding_index': 75,
            'agentic_index': 70,
        }]}

        with tempfile.TemporaryDirectory() as td, \
                patch.object(openrouter_sync, 'discover_provider', return_value=[candidate]), \
                patch.object(openrouter_sync, 'ROOT', Path(td)):
            Path(td, 'harness/model-providers').mkdir(parents=True)
            Path(td, 'harness/model-providers/codex.json').write_text(
                json.dumps({
                    'provider': 'codex',
                    'enriched_inventory': '.harness/model-inventories/codex.json',
                    'openrouter_aliases': {},
                    'openrouter': {'fetch_endpoint_health': False},
                }),
                encoding='utf-8',
            )
            payload, _ = openrouter_sync.refresh_provider_inventory(
                'codex',
                models_payload=models,
                benchmarks_payload=benchmarks,
                fetch_endpoints=False,
            )

        row = payload['models'][0]
        self.assertEqual(row['openrouter_id'], 'openai/gpt-example')
        self.assertEqual(
            row['openrouter_benchmark_id'],
            'openai/gpt-example-20260903',
        )
        self.assertEqual(row['openrouter_benchmark_match'], 'unique-versioned')
        self.assertGreater(row['capabilities']['reasoning'], 0)
        self.assertGreater(row['capabilities']['coding'], 0)

    def test_codex_refresh_materializes_shared_and_raw_inventories(self):
        candidate = {
            'id': 'gpt-example',
            'enabled': True,
            'native': True,
            'vendor': 'openai',
            'family': 'openai/gpt-example',
            'supported_efforts': [],
            'supports_tools': True,
            'supports_reasoning': True,
            'context_window': 123000,
            'availability_source': 'codex-models-cache',
            'availability_generated_at': '2026-09-07T00:00:00Z',
        }
        models = {'data': [{
            'id': 'openai/gpt-example',
            'supported_parameters': ['tools'],
            'context_length': 123000,
        }]}

        with tempfile.TemporaryDirectory() as td, \
                patch.object(openrouter_sync, 'discover_provider', return_value=[candidate]), \
                patch.object(openrouter_sync, '_fetch_sources', return_value=(models, {'data': []})), \
                patch.object(openrouter_sync, 'ROOT', Path(td)):
            payload, _ = openrouter_sync.refresh_provider_inventory(
                'codex',
                api_key='test-key',
                fetch_endpoints=False,
            )
            shared = json.loads(Path(
                td, '.harness/openrouter/model-inventory.json'
            ).read_text(encoding='utf-8'))
            raw = json.loads(Path(
                td, '.harness/codex/model-inventory.json'
            ).read_text(encoding='utf-8'))

        self.assertEqual(shared['provider'], 'openrouter')
        self.assertEqual(shared['models'][0]['id'], 'openai/gpt-example')
        self.assertEqual(raw['provider'], 'codex')
        self.assertEqual(raw['models'][0]['id'], 'gpt-example')
        self.assertEqual(raw['models'][0]['capabilities']['coding'], 0.0)
        self.assertEqual(
            payload['openrouter_catalog_path'],
            '.harness/openrouter/model-inventory.json',
        )
        self.assertEqual(
            payload['raw_inventory_path'],
            '.harness/codex/model-inventory.json',
        )

    def test_refresh_falls_back_to_shared_openrouter_catalog(self):
        candidate = {
            'id': 'gpt-example',
            'enabled': True,
            'native': True,
            'vendor': 'openai',
            'family': 'openai/gpt-example',
            'supported_efforts': [],
            'supports_tools': True,
            'supports_reasoning': True,
            'context_window': 0,
        }

        with tempfile.TemporaryDirectory() as td, \
                patch.object(openrouter_sync, 'discover_provider', return_value=[candidate]), \
                patch.object(
                    openrouter_sync,
                    '_fetch_sources',
                    side_effect=openrouter_sync.urllib.error.URLError('offline'),
                ), \
                patch.object(openrouter_sync, 'ROOT', Path(td)):
            shared_path = Path(td, '.harness/openrouter/model-inventory.json')
            shared_path.parent.mkdir(parents=True)
            shared_path.write_text(json.dumps({
                'provider': 'openrouter',
                'models': [{'id': 'openai/gpt-example'}],
            }), encoding='utf-8')
            payload, _ = openrouter_sync.refresh_provider_inventory(
                'codex',
                benchmarks_payload={'data': []},
                fetch_endpoints=False,
            )

        self.assertEqual(
            payload['models'][0]['openrouter_id'],
            'openai/gpt-example',
        )
        self.assertEqual(
            payload['models'][0]['openrouter_match'],
            'auto-exact-normalized',
        )
        self.assertIn('offline', payload['openrouter_fetch_error'])


if __name__ == '__main__': unittest.main()
