import copy
import hashlib
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from context_compiler import build
import context_compiler as compiler
import context_graph as graph


class ContextFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.policy = json.loads((Path(__file__).resolve().parents[1] /
                                  'harness/context-policy.json').read_text(encoding='utf-8'))
        self.policy.update(always_include=[], graph_backend='lexical')
        self.memories = []
        for module in (compiler, graph):
            for name, value in (('ROOT', self.root), ('load_json', lambda _: copy.deepcopy(self.policy))):
                mock = patch.object(module, name, value)
                mock.start()
                self.addCleanup(mock.stop)
        self.memory_mock = patch.object(compiler, 'search_memory', side_effect=lambda *_: self.memories)
        self.memory_mock.start()
        self.addCleanup(self.memory_mock.stop)

    def write(self, path, content=b'x'):
        destination = self.root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        return destination

    def build(self, files=(), description='', **kwargs):
        return build({'id': 'T-context-fixture', 'files': list(files), 'description': description, **kwargs})

    def paths(self, result):
        return [record['path'] for record in result['files']]


class ContextRankingTests(ContextFixture):
    def test_memory_limits_and_legacy_policy_defaults(self):
        self.write('widget.md')
        self.write('second.md')
        self.memories = [{'title': 'widget'}, {'title': 'second'}]
        self.policy.update(max_memory_items=1, max_memory_text_chars=6)
        result = self.build()
        self.assertEqual(self.paths(result), ['widget.md'])
        self.assertEqual(result['memory'], self.memories[:1])
        self.policy['max_memory_text_chars'] = 0
        self.assertEqual(self.paths(self.build()), [])
        self.policy['max_memory_items'] = 0
        self.assertEqual(self.build()['memory'], [])
        self.policy['version'] = 2
        for key in ('memory_bonus', 'related_test_bonus', 'path_token_bonus', 'max_path_token_matches',
                    'max_memory_text_chars'):
            self.policy.pop(key, None)
        self.assertEqual(self.paths(self.build(['widget.md'])), ['widget.md'])

    def test_memory_changes_only_matching_candidate_and_is_bounded(self):
        self.write('widget.py')
        self.write('other.py')
        baseline = self.build(description='other')
        self.memories = [{'title': 'widget', 'summary': 'widget ' * 10000,
                          'lessons': ['../../.env'], 'tags': ['widget']}]
        result = self.build(description='other')
        records = {item['path']: item for item in result['files']}
        self.assertIn('widget.py', records)
        self.assertIn('memory', records['widget.py']['reason'])
        self.assertLess(records['widget.py']['score'], 900)
        self.assertEqual(records['other.py'], baseline['files'][0])
        self.assertEqual(result['memory'], self.memories)
        self.memories = [None, 42, {'title': [], 'summary': {}, 'tags': [None], 'lessons': 9}]
        self.assertEqual(self.paths(self.build(description='other')), ['other.py'])

    def test_determinism_precedence_and_auditable_metadata(self):
        for path in ['zeta.py', 'alpha.py', 'seed.py', 'required.md', 'tests/test_seed.py']:
            self.write(path, b'import alpha\nimport zeta\n' if path == 'seed.py' else b'12345')
        self.policy['always_include'] = ['required.md']
        self.memories = [{'title': 'alpha zeta required seed test_seed'}]
        result = self.build(['seed.py'], 'alpha zeta seed test_seed')
        self.assertEqual(result, self.build(['seed.py'], 'alpha zeta seed test_seed'))
        self.assertEqual(self.paths(result)[:2], ['required.md', 'seed.py'])
        records = {item['path']: item for item in result['files']}
        self.assertIn('related-test', records['tests/test_seed.py']['reason'])
        self.assertIn('dependency-graph-neighbor', records['alpha.py']['reason'])
        self.assertLess(self.paths(result).index('alpha.py'), self.paths(result).index('zeta.py'))
        for item in result['files']:
            raw = (self.root / item['path']).read_bytes()
            self.assertEqual(item['bytes'], len(raw))
            self.assertEqual(item['estimated_tokens'], max(1, (len(raw) + 3) // 4))
            self.assertEqual(item['sha256'], hashlib.sha256(raw).hexdigest())
            self.assertTrue(item['reason'])

    def test_graph_depth_results_and_backend(self):
        self.write('seed.py', b'import alpha\n')
        self.write('alpha.py', b'import bravo\n')
        self.write('bravo.py')
        for depth, limit, expected in [(0, 20, []), (2, 0, []), (1, 20, ['alpha.py']),
                                        (2, 1, ['alpha.py']), (2, 2, ['alpha.py', 'bravo.py'])]:
            with self.subTest(depth=depth, limit=limit):
                self.policy.update(graph_neighbor_depth=depth, graph_max_results=limit)
                result = self.build(['seed.py'])
                self.assertEqual(result['graph_neighbors'], expected)
                self.assertEqual(result.get('graph_backend'),
                                 {'requested': 'lexical', 'selected': 'lexical', 'fallback_reason': None})
        self.policy['graph_backend'] = 'auto'
        with patch.object(graph, 'try_build', return_value=(None, 'unavailable')):
            self.assertEqual(self.build()['graph_backend']['fallback_reason'], 'unavailable')
        with patch.object(graph, 'try_build', return_value=([('seed.py', 'bravo.py', 'import')], None)):
            result = self.build(['seed.py'])
            self.assertEqual(result['graph_backend']['selected'], 'codegraph')
            self.assertEqual(result['graph_neighbors'], ['bravo.py'])

    def test_all_ranking_contributions_have_exact_reasons(self):
        self.write('seed.py')
        self.write('tests/test_seed.py')
        self.memories = [{'summary': 'test_seed'}]
        result = self.build(['seed.py'], 'test_seed')
        related = next(item for item in result['files'] if item['path'] == 'tests/test_seed.py')
        self.assertEqual(set(related['reason']),
                         {'dependency-graph-neighbor', 'related-test', 'memory', 'path-token:test_seed'})
        self.assertEqual(related['score'], 350 + 40 + 60 + 20)

class ContextTests(unittest.TestCase):
    def test_always_policy_and_excludes_secret(self):
        out=build({'id':'T-context','description':'change router behavior','files':['scripts/task_router.py']})
        paths={x['path'] for x in out['files']}
        self.assertIn('AGENTS.md',paths); self.assertIn('scripts/task_router.py',paths); self.assertNotIn('.env',paths)
        self.assertLessEqual(out['total_bytes'],out['limits']['bytes'])
if __name__=='__main__': unittest.main()
