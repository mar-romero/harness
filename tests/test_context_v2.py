import copy
import json
import os
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from context_compiler import build
import context_compiler as compiler
import context_graph as graph
from test_context import ContextFixture
from test_context_graph import validate_graph_schema


class ContextBudgetTests(ContextFixture):
    def test_exact_and_one_over_byte_token_and_file_limits(self):
        self.write('alpha.py', b'1234')
        self.write('bravo.py', b'12345')
        for count, size, tokens, expected in [(2, 9, 3, ['alpha.py', 'bravo.py']),
                                               (2, 8, 3, ['alpha.py']),
                                               (2, 9, 2, ['alpha.py']),
                                               (1, 9, 3, ['alpha.py']), (0, 9, 3, [])]:
            with self.subTest(count=count, size=size, tokens=tokens):
                self.policy.update(max_files=count, max_total_bytes=size, max_total_tokens_estimate=tokens)
                result = self.build(description='alpha bravo')
                self.assertEqual(self.paths(result), expected)
                self.assertEqual(result['total_bytes'], sum(item['bytes'] for item in result['files']))
                self.assertEqual(result['estimated_tokens'], sum(item['estimated_tokens'] for item in result['files']))

    def test_required_overflow_and_universal_file_cap(self):
        self.write('policy.md', b'12345')
        self.write('explicit.py', b'123456')
        self.write('ordinary.py', b'1')
        self.policy.update(always_include=['policy.md'], max_total_bytes=1,
                           max_total_tokens_estimate=1, max_files=2, max_file_bytes=5)
        result = self.build(['explicit.py'], 'ordinary')
        self.assertEqual(self.paths(result), ['policy.md', 'explicit.py'])
        self.assertEqual((result['total_bytes'], result['estimated_tokens']), (11, 4))
        self.policy['max_files'] = 1
        self.assertEqual(self.paths(self.build(['explicit.py'])), ['policy.md'])
        # Only explicit files bypass the per-file size cap (legacy semantics).
        self.policy.update(max_files=3, max_file_bytes=4)
        self.assertEqual(self.paths(self.build(['explicit.py'])), ['explicit.py'])

    def test_ordinary_high_score_never_bypasses_budgets_or_precedence(self):
        path = '.'.join('t%02d' % i for i in range(46)) + '.py'
        self.write(path, b'12345')
        self.write('required.md', b'1')
        self.write('explicit.py', b'1')
        query = ' '.join('t%02d' % i for i in range(46))
        self.policy.update(max_total_bytes=4, max_total_tokens_estimate=1)
        self.assertEqual(self.paths(self.build(description=query)), [])
        self.policy.update(always_include=['required.md'], max_files=2, max_total_bytes=1000,
                           max_total_tokens_estimate=1000)
        self.assertEqual(self.paths(self.build(['explicit.py'], query)), ['required.md', 'explicit.py'])


class ContextSecurityTests(ContextFixture):
    def test_each_generated_adapter_directory_is_excluded_before_open(self):
        for provider in ('codex', 'opencode', 'claude'):
            for directory in (f'.{provider}/agents', f'.{provider.upper()}/AGENTS',
                              f'.{provider.capitalize()}/Agents'):
                with self.subTest(directory=directory), tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary).resolve()
                    with patch.object(self, 'root', root), patch.object(compiler, 'ROOT', root), \
                            patch.object(graph, 'ROOT', root):
                        blocked = directory + '/worker.py'
                        self.write(blocked, b'import control\n')
                        self.write('control.py')
                        self.memories = [{'summary': blocked}]
                        original_open = Path.open
                        opened = []

                        def guarded_open(path, *args, **kwargs):
                            relative = path.relative_to(root).as_posix()
                            self.assertNotEqual(relative.casefold(), blocked.casefold(),
                                                'generated adapter opened before exclusion')
                            opened.append(relative)
                            return original_open(path, *args, **kwargs)

                        with patch.object(Path, 'open', guarded_open):
                            result = self.build([blocked, 'control.py'], 'worker control')
                        self.assertEqual(self.paths(result), ['control.py'])
                        self.assertEqual(result['graph_neighbors'], [])
                        self.assertIn('control.py', opened)

    def test_simulated_links_reparse_points_and_non_regular_files_before_read(self):
        target = self.write('blocked.md')
        original_lstat = Path.lstat
        original_stat = Path.stat
        document = {'edges': [], 'backend': {'requested': 'lexical', 'selected': 'lexical', 'fallback_reason': None}}
        for mode, attributes in [(stat.S_IFLNK, 0), (stat.S_IFREG, stat.FILE_ATTRIBUTE_REPARSE_POINT),
                                  (stat.S_IFIFO, 0)]:
            with self.subTest(mode=mode, attributes=attributes):
                def metadata(path, *args, **kwargs):
                    if path == target:
                        return SimpleNamespace(st_mode=mode, st_file_attributes=attributes)
                    return original_lstat(path, *args, **kwargs)
                def file_stat(path, *args, **kwargs):
                    if path == target and mode == stat.S_IFIFO:
                        return SimpleNamespace(st_mode=mode)
                    return original_stat(path, *args, **kwargs)
                with patch.object(compiler, 'build_graph_document', return_value=document), \
                        patch.object(Path, 'lstat', metadata), patch.object(Path, 'stat', file_stat), \
                        patch.object(Path, 'open', side_effect=AssertionError('unsafe file opened')):
                    self.assertEqual(self.paths(self.build(['blocked.md'])), [])

    def test_resolved_outside_candidate_is_rejected_before_read(self):
        target = self.write('escape.md')
        original_resolve = Path.resolve
        def resolve(path, *args, **kwargs):
            return self.root.parent / 'outside.md' if path == target else original_resolve(path, *args, **kwargs)
        with patch.object(Path, 'resolve', resolve), \
                patch.object(Path, 'open', side_effect=AssertionError('outside file opened')):
            self.assertEqual(self.paths(self.build(['escape.md'])), [])

    def test_changed_candidate_is_not_hashed_with_stale_size(self):
        target = self.write('changing.md', b'1234')
        original_open = Path.open
        def change(path, *args, **kwargs):
            if path == target:
                with original_open(target, 'wb') as stream:
                    stream.write(b'12345')
            return original_open(path, *args, **kwargs)
        with patch.object(Path, 'open', change):
            self.assertEqual(self.paths(self.build(['changing.md'])), [])

    def test_case_nested_secret_and_generated_exclusions_before_open(self):
        blocked = ['.env', 'nested/.ENV.local', 'nested/Secrets.json', 'private.PEM', 'private.KEY',
                   'nested/BUILD/leak.py', 'DIST/leak.py', '.GIT/leak.py', '.codex/agents/worker.toml']
        for path in blocked:
            self.write(path)
        self.write('control.py')
        self.memories = [{'summary': ' '.join(blocked) + ' ../../escape.py'}]
        original_open = Path.open
        opened = []
        def guarded_open(path, *args, **kwargs):
            opened.append(path.relative_to(self.root).as_posix())
            self.assertNotIn(path.relative_to(self.root).as_posix(), blocked)
            return original_open(path, *args, **kwargs)
        with patch.object(Path, 'open', guarded_open):
            result = self.build(blocked + ['control.py'])
        self.assertEqual(self.paths(result), ['control.py'])
        self.assertEqual(result['graph_neighbors'], [])
        self.assertIn('control.py', opened)

    def test_invalid_explicit_paths_are_not_normalized_into_admission(self):
        self.write('control.py')
        invalid = ['./control.py', '../control.py', 'dir/../control.py', 'dir\\control.py',
                   str(self.root / 'control.py'), 'C:/control.py', '', None, 3, 'bad\x00.py']
        self.assertEqual(self.paths(self.build(invalid)), [])

    def test_symlinks_and_directory_links_are_not_opened(self):
        target = self.write('control.py')
        self.write('real/inner.py')
        outside = self.root.parent / (self.root.name + '-outside.py')
        outside.write_bytes(b'outside')
        self.addCleanup(outside.unlink)
        try:
            (self.root / 'alias.py').symlink_to(target)
            (self.root / 'escape.py').symlink_to(outside)
            (self.root / 'aliasdir').symlink_to(self.root / 'real', target_is_directory=True)
        except OSError as exc:
            self.skipTest('symlink creation unavailable: ' + str(exc))
        original_open = Path.open
        def guarded_open(path, *args, **kwargs):
            self.assertNotIn(path.relative_to(self.root).parts[0], {'alias.py', 'escape.py', 'aliasdir'})
            return original_open(path, *args, **kwargs)
        with patch.object(Path, 'open', guarded_open):
            self.assertEqual(self.paths(self.build(['alias.py', 'escape.py', 'aliasdir/inner.py'])), [])

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'FIFO unavailable on this platform')
    def test_non_regular_files_are_omitted(self):
        os.mkfifo(self.root / 'pipe.py')
        self.assertEqual(self.paths(self.build(['pipe.py'])), [])

    def test_policy_validation_precedes_graph_or_candidate_reads(self):
        invalid = [('max_files', -1), ('max_files', True), ('max_files', 10001),
                   ('max_total_bytes', '20'), ('max_total_tokens_estimate', -1),
                   ('max_file_bytes', 0), ('graph_neighbor_bonus', 900),
                   ('max_memory_items', -1), ('exclude_dirs', ['../escape']),
                   ('exclude_globs', [None]), ('text_extensions', ['py']),
                   ('text_extensions', ['.exe']), ('always_include', ['/outside.py'])]
        baseline = copy.deepcopy(self.policy)
        for key, value in invalid:
            with self.subTest(key=key, value=value):
                self.policy = {**baseline, key: value}
                with patch.object(graph, '_sources', side_effect=AssertionError('scan before validation')):
                    with self.assertRaises(ValueError):
                        self.build()

    def test_disappearing_candidate_is_safely_omitted(self):
        target = self.write('vanish.md')
        original_open = Path.open
        def disappear(path, *args, **kwargs):
            if path == target:
                target.unlink(missing_ok=True)
            return original_open(path, *args, **kwargs)
        with patch.object(Path, 'open', disappear):
            self.assertEqual(self.paths(self.build(['vanish.md'])), [])


class ContextSchemaTests(ContextFixture):
    def test_legacy_metadata_values_survive_json_round_trip(self):
        self.write('control.py')
        task = {'id': 'T-legacy-round-trip', 'files': ['control.py'], 'description': 'control'}
        route = {'task_id': task['id'], 'risk': 'R3', 'agents': ['implementer', 'reviewer'],
                 'requirements': {'review': True, 'verification': True},
                 'custom': {'values': [0, False, None, 'preserve-me']}}
        original_route = copy.deepcopy(route)
        for version in (2, 3):
            with self.subTest(version=version):
                self.policy.update(version=version, max_files=7, max_total_bytes=12345,
                                   max_total_tokens_estimate=2345)
                result = json.loads(json.dumps(build(task, route)))
                self.assertEqual(result['task_id'], 'T-legacy-round-trip')
                self.assertEqual(result['policy_version'], version)
                self.assertEqual(result['route'], original_route)
                self.assertEqual(route, original_route)
                self.assertEqual(result['limits'],
                                 {'files': 7, 'bytes': 12345, 'estimated_tokens': 2345})
                self.assertEqual(build(task)['route'], {})

    def validate_schema(self, result, schema):
        # Extend the existing dependency-free oracle only for this new keyword.
        schema = copy.deepcopy(schema)
        reason_rule = schema['properties']['files']['items']['properties']['reason']
        self.assertEqual(reason_rule.pop('minItems'), 1)
        validate_graph_schema(self, result, schema)
        for item in result['files']:
            self.assertGreaterEqual(len(item['reason']), 1)

    def test_legacy_fields_and_schema_contract(self):
        self.write('control.py')
        result = self.build(['control.py'])
        required = {'task_id', 'policy_version', 'route', 'files', 'memory', 'graph_neighbors',
                    'total_bytes', 'estimated_tokens', 'limits'}
        self.assertTrue(required <= result.keys())
        schema_path = Path(__file__).resolve().parents[1] / 'harness/schema/context-pack.schema.json'
        self.assertTrue(schema_path.is_file(), 'auditable context-pack schema must exist')
        schema = json.loads(schema_path.read_text(encoding='utf-8'))
        self.validate_schema(result, schema)
        for mutate in [lambda doc: doc['files'][0].pop('reason'),
                       lambda doc: doc['files'][0].update(reason=[]),
                       lambda doc: doc['files'][0].update(sha256='wrong'),
                       lambda doc: doc['files'][0].update(bytes=-1),
                       lambda doc: doc['files'][0].update(path='../escape.py'),
                       lambda doc: doc['graph_backend'].update(selected='unknown')]:
            bad = copy.deepcopy(result)
            mutate(bad)
            with self.assertRaises(AssertionError):
                self.validate_schema(bad, schema)

class ContextV2Tests(unittest.TestCase):
    def test_token_budget_and_memory_field(self):
        r=build({'id':'T-cv2','description':'Update task routing','files':['scripts/task_router.py']})
        self.assertIn('estimated_tokens',r); self.assertIn('memory',r); self.assertLessEqual(r['estimated_tokens'],r['limits']['estimated_tokens'])
if __name__=='__main__': unittest.main()
