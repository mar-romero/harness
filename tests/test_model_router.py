import json, sys, unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from model_router import load_policy, select_model

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
    def minimum_sufficient_policy(self, risk='R2'):
        return {
            **POLICY,
            'selection': {
                **POLICY['selection'],
                'strategy': 'minimum_sufficient',
                'minimum_coverage_by_risk': {risk: .9},
                'no_sufficient_action_by_risk': {risk: 'block'},
            },
        }

    def test_minimum_sufficient_prefers_lowest_declared_size_tier(self):
        policy = self.minimum_sufficient_policy()
        target = {'reasoning': 4.5, 'coding': 4, 'tool_use': 4, 'reliability': 4.5}
        small = {**m('small-sufficient', 4.5, 4, 4, 4.5, 3, 3), 'size_tier': 1}
        large = {**m('large-overqualified', 5, 5, 5, 5, 5, 5), 'size_tier': 3}

        for models in ((small, large), (large, small)):
            with self.subTest(input_order=[model['id'] for model in models]):
                result = select_model(task_id='minimum-size', provider='codex', agent='implementer', model_class='coding',
                                      risk='R2', inventory=inv(list(models)), policy=policy, now=NOW, target=target)

                self.assertEqual(result['base_model_id'], 'small-sufficient')
                self.assertEqual(result['selection_strategy'], 'minimum_sufficient')
                self.assertEqual(result['minimum_coverage_threshold'], .9)
                self.assertEqual(result['eligible_models'], 2)
                self.assertEqual(result['sufficient_models'], 2)

    def test_minimum_sufficient_accepts_exact_coverage_boundary(self):
        policy = self.minimum_sufficient_policy()
        target = {'reasoning': 5, 'coding': 5, 'tool_use': 5, 'reliability': 5}
        inventory = inv([m('exact-boundary', 4.5, 4.5, 4.5, 4.5, 3, 3)])

        result = select_model(task_id='exact-boundary', provider='codex', agent='implementer', model_class='coding',
                              risk='R2', inventory=inventory, policy=policy, now=NOW, target=target)

        self.assertEqual(result['status'], 'selected')
        self.assertEqual(result['sufficient_models'], 1)
        self.assertEqual(result['score_breakdown']['capability_coverage'], {'reasoning': .9, 'coding': .9, 'tool_use': .9, 'reliability': .9})

    def test_minimum_sufficient_rejects_single_dimension_shortfall_despite_passing_aggregate(self):
        policy = self.minimum_sufficient_policy()
        target = {'reasoning': 5, 'coding': 5, 'tool_use': 5, 'reliability': 5}
        inventory = inv([m('single-dimension-shortfall', 5, 5, 5, 4.4, 5, 5)])
        coverage = {'reasoning': 1, 'coding': 1, 'tool_use': 1, 'reliability': .88}

        result = select_model(task_id='single-dimension-shortfall', provider='codex', agent='implementer', model_class='coding',
                              risk='R2', inventory=inventory, policy=policy, now=NOW, target=target)

        self.assertGreaterEqual(sum(coverage.values()) / len(coverage), .9)
        self.assertLess(coverage['reliability'], .9)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['action'], 'block')
        self.assertEqual(result['eligible_models'], 1)
        self.assertEqual(result['sufficient_models'], 0)

    def test_minimum_sufficient_applies_hard_floors_before_coverage(self):
        policy = self.minimum_sufficient_policy()
        policy['risk_overrides'] = {
            **POLICY['risk_overrides'],
            'R2': {**POLICY['risk_overrides']['R2'], 'no_eligible_action': 'block'},
        }
        target = {'reasoning': 2, 'coding': 2, 'tool_use': 2, 'reliability': 2}
        inventory = inv([m('coverage-without-coding-floor', 3, 3, 3, 3, 5, 5)])

        result = select_model(task_id='hard-floor-before-coverage', provider='codex', agent='implementer', model_class='coding',
                              risk='R2', inventory=inventory, policy=policy, now=NOW, target=target)

        self.assertEqual((result['status'], result['action']), ('blocked', 'block'))
        self.assertEqual((result['eligible_models'], result['sufficient_models']), (0, 0))

    def test_minimum_sufficient_without_size_tier_prefers_lowest_surplus(self):
        policy = self.minimum_sufficient_policy()
        target = {'reasoning': 4, 'coding': 4, 'tool_use': 4, 'reliability': 4}
        inventory = inv([
            m('near-fit', 4, 4, 4, 4, 3, 3),
            m('overqualified', 5, 5, 5, 5, 3, 3),
        ])

        result = select_model(task_id='surplus-order', provider='codex', agent='implementer', model_class='coding',
                              risk='R2', inventory=inventory, policy=policy, now=NOW, target=target)

        self.assertEqual(result['base_model_id'], 'near-fit')
        self.assertEqual(result['score_breakdown']['size_tier'], None)
        self.assertEqual(result['score_breakdown']['capability_surplus'], 0)

    def test_minimum_sufficient_uses_cost_then_latency_then_model_id_ties(self):
        policy = self.minimum_sufficient_policy()
        target = {'reasoning': 4, 'coding': 4, 'tool_use': 4, 'reliability': 4}
        cost_result = select_model(
            task_id='cost-tie', provider='codex', agent='implementer', model_class='coding', risk='R2',
            inventory=inv([m('lower-cost-efficiency', 4, 4, 4, 4, 2, 3), m('higher-cost-efficiency', 4, 4, 4, 4, 4, 3)]),
            policy=policy, now=NOW, target=target,
        )
        latency_result = select_model(
            task_id='latency-tie', provider='codex', agent='implementer', model_class='coding', risk='R2',
            inventory=inv([m('slower', 4, 4, 4, 4, 3, 2), m('faster', 4, 4, 4, 4, 3, 4)]),
            policy=policy, now=NOW, target=target,
        )
        model_id_result = select_model(
            task_id='model-id-tie', provider='codex', agent='implementer', model_class='coding', risk='R2',
            inventory=inv([m('z-model', 4, 4, 4, 4, 3, 3), m('a-model', 4, 4, 4, 4, 3, 3)]),
            policy=policy, now=NOW, target=target,
        )

        self.assertEqual(cost_result['base_model_id'], 'higher-cost-efficiency')
        self.assertEqual(latency_result['base_model_id'], 'faster')
        self.assertEqual(model_id_result['base_model_id'], 'a-model')

    def test_minimum_sufficient_prefers_higher_score_after_equal_surplus_cost_latency_regardless_of_input_order(self):
        policy = self.minimum_sufficient_policy()
        target = {'reasoning': 4, 'coding': 4, 'tool_use': 4, 'reliability': 4}
        lower_score = m('a-lower-score', 5, 4, 4, 4, 3, 3)
        higher_score = m('z-higher-score', 4, 5, 4, 4, 3, 3)

        for models in ((lower_score, higher_score), (higher_score, lower_score)):
            with self.subTest(input_order=[model['id'] for model in models]):
                result = select_model(
                    task_id='score-tie-break', provider='codex', agent='implementer', model_class='coding', risk='R2',
                    inventory=inv(list(models)), policy=policy, now=NOW, target=target,
                )

                self.assertEqual(result['base_model_id'], 'z-higher-score')

    def test_minimum_sufficient_schema_and_shape_assertions_for_new_fields(self):
        policy = self.minimum_sufficient_policy()
        inventory = {
            'schema_version': 2,
            **inv([{**m('schema-model', 5, 5, 5, 5, 3, 3), 'size_tier': 1}]),
        }
        inventory_schema = json.loads((ROOT / 'harness/schema/model-inventory.schema.json').read_text(encoding='utf-8'))
        selection_schema = json.loads((ROOT / 'harness/schema/model-selection.schema.json').read_text(encoding='utf-8'))
        model_schema = inventory_schema['properties']['models']['items']
        required_model_fields = {'id', 'enabled', 'capabilities', 'cost', 'latency'}
        required_selection_fields = set(selection_schema['required'])
        size_tier_schema = model_schema['properties']['size_tier']
        expected_selection_properties = {
            'selection_strategy': {'type': 'string'},
            'minimum_coverage_threshold': {'type': ['number', 'null']},
            'eligible_models': {'type': 'integer', 'minimum': 0},
            'sufficient_models': {'type': 'integer', 'minimum': 0},
        }

        self.assertEqual(inventory_schema['properties']['schema_version']['const'], 2)
        self.assertTrue(required_model_fields.issubset(model_schema['required']))
        self.assertNotIn('size_tier', model_schema['required'])
        self.assertEqual(size_tier_schema['type'], 'number')
        self.assertEqual(size_tier_schema['minimum'], 0)
        self.assertTrue(selection_schema['additionalProperties'])
        self.assertTrue(required_selection_fields.issubset(selection_schema['properties']))
        for field, expected_schema in expected_selection_properties.items():
            with self.subTest(selection_schema=field):
                self.assertEqual(selection_schema['properties'][field], expected_schema)

        model = inventory['models'][0]
        self.assertTrue(required_model_fields.issubset(model))
        self.assertIsInstance(model['size_tier'], (int, float))
        self.assertNotIsInstance(model['size_tier'], bool)
        self.assertGreaterEqual(model['size_tier'], 0)
        for label, invalid_model in {
            'missing-required-id': {key: value for key, value in model.items() if key != 'id'},
            'string-size-tier': {**model, 'size_tier': 'small'},
            'negative-size-tier': {**model, 'size_tier': -1},
        }.items():
            with self.subTest(inventory=label):
                has_required_shape = required_model_fields.issubset(invalid_model)
                has_valid_size_tier = (
                    isinstance(invalid_model.get('size_tier'), (int, float))
                    and not isinstance(invalid_model.get('size_tier'), bool)
                    and invalid_model['size_tier'] >= 0
                )
                self.assertFalse(has_required_shape and has_valid_size_tier)

        result = select_model(
            task_id='schema-fields', provider='codex', agent='implementer', model_class='coding', risk='R2',
            inventory=inventory, policy=policy, now=NOW,
            target={'reasoning': 4, 'coding': 4, 'tool_use': 4, 'reliability': 4},
        )
        self.assertTrue(required_selection_fields.issubset(result))
        self.assertEqual(result['selection_strategy'], 'minimum_sufficient')
        self.assertIsInstance(result['minimum_coverage_threshold'], (int, float))
        self.assertNotIsInstance(result['minimum_coverage_threshold'], bool)
        self.assertIsInstance(result['eligible_models'], int)
        self.assertNotIsInstance(result['eligible_models'], bool)
        self.assertGreaterEqual(result['eligible_models'], 0)
        self.assertIsInstance(result['sufficient_models'], int)
        self.assertNotIsInstance(result['sufficient_models'], bool)
        self.assertGreaterEqual(result['sufficient_models'], 0)
        for field, invalid_value in {
            'selection_strategy': 1,
            'minimum_coverage_threshold': '0.9',
            'eligible_models': -1,
            'sufficient_models': -1,
        }.items():
            with self.subTest(selection=field):
                invalid_result = {**result, field: invalid_value}
                has_required_shape = required_selection_fields.issubset(invalid_result)
                has_valid_new_fields = (
                    isinstance(invalid_result['selection_strategy'], str)
                    and isinstance(invalid_result['minimum_coverage_threshold'], (int, float))
                    and not isinstance(invalid_result['minimum_coverage_threshold'], bool)
                    and isinstance(invalid_result['eligible_models'], int)
                    and not isinstance(invalid_result['eligible_models'], bool)
                    and invalid_result['eligible_models'] >= 0
                    and isinstance(invalid_result['sufficient_models'], int)
                    and not isinstance(invalid_result['sufficient_models'], bool)
                    and invalid_result['sufficient_models'] >= 0
                )
                self.assertFalse(has_required_shape and has_valid_new_fields)

    def test_minimum_sufficient_filters_before_independence(self):
        policy = self.minimum_sufficient_policy()
        target = {'reasoning': 4.5, 'coding': 4.5, 'tool_use': 4.5, 'reliability': 4.5}
        inventory = inv([
            {**m('used-sufficient', 4.5, 4.5, 4.5, 4.5, 3, 3), 'family': 'used-family', 'vendor': 'used-vendor'},
            {**m('independent-insufficient', 4, 4, 4, 4, 5, 5), 'family': 'other-family', 'vendor': 'other-vendor'},
        ])

        result = select_model(
            task_id='independence', provider='codex', agent='reviewer', model_class='coding', risk='R2',
            inventory=inventory, policy=policy, now=NOW, target=target,
            avoid_models={'used-sufficient'}, avoid_families={'used-family'}, avoid_vendors={'used-vendor'},
        )

        self.assertEqual(result['base_model_id'], 'used-sufficient')
        self.assertEqual(result['independence']['strength'], 'same_model_fallback')
        self.assertEqual(result['sufficient_models'], 1)

    def test_production_policy_blocks_r3_below_and_accepts_exact_coverage_threshold(self):
        policy = load_policy()
        target = {'reasoning': 5, 'coding': 5, 'tool_use': 5, 'reliability': 5}
        below_threshold = m('r3-coverage-0.94', 4.7, 4.7, 4.7, 4.7, 5, 5)
        exact_threshold = m('r3-coverage-0.95', 4.75, 4.75, 4.75, 4.75, 5, 5)

        blocked = select_model(
            task_id='r3-below-threshold', provider='codex', agent='implementer', model_class='coding', risk='R3',
            inventory=inv([below_threshold]), policy=policy, now=NOW, target=target,
        )
        selected = select_model(
            task_id='r3-exact-threshold', provider='codex', agent='implementer', model_class='coding', risk='R3',
            inventory=inv([exact_threshold]), policy=policy, now=NOW, target=target,
        )

        self.assertEqual(blocked['minimum_coverage_threshold'], .95)
        self.assertEqual(
            (blocked['status'], blocked['action'], blocked['eligible_models'], blocked['sufficient_models']),
            ('blocked', 'block', 1, 0),
        )
        self.assertEqual(selected['minimum_coverage_threshold'], .95)
        self.assertEqual((selected['status'], selected['action'], selected['eligible_models'], selected['sufficient_models']),
                         ('selected', 'use', 1, 1))
        self.assertEqual(selected['score_breakdown']['capability_coverage'],
                         {'reasoning': .95, 'coding': .95, 'tool_use': .95, 'reliability': .95})

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

    def test_production_policy_blocks_r2_and_r3_without_eligible_or_sufficient_models(self):
        policy = load_policy()
        self.assertEqual(policy['selection']['strategy'], 'minimum_sufficient')
        self.assertEqual(policy['selection']['minimum_coverage_by_risk'], {'R0': .75, 'R1': .8, 'R2': .9, 'R3': .95})
        no_eligible = inv([m('weak', 3, 3, 3, 3, 5, 5)])
        target = {'reasoning': 5, 'coding': 5, 'tool_use': 5, 'reliability': 5}
        eligible_but_insufficient = inv([m('below-target', 4, 4, 4, 4, 5, 5)])

        for risk in ('R2', 'R3'):
            with self.subTest(risk=risk, scenario='no-inventory'):
                result = select_model(task_id='no-inventory', provider='codex', agent='implementer', model_class='coding',
                                      risk=risk, inventory=None, policy=policy, now=NOW, target=target)
                self.assertEqual((result['status'], result['action']), ('blocked', 'block'))
            with self.subTest(risk=risk, scenario='no-eligible'):
                result = select_model(task_id='no-eligible', provider='codex', agent='implementer', model_class='coding',
                                      risk=risk, inventory=no_eligible, policy=policy, now=NOW, target=target)
                self.assertEqual((result['status'], result['action'], result['eligible_models']), ('blocked', 'block', 0))
            with self.subTest(risk=risk, scenario='no-sufficient'):
                result = select_model(task_id='no-sufficient', provider='codex', agent='implementer', model_class='coding',
                                      risk=risk, inventory=eligible_but_insufficient, policy=policy, now=NOW, target=target)
                self.assertEqual((result['status'], result['action'], result['eligible_models'], result['sufficient_models']), ('blocked', 'block', 1, 0))

if __name__ == '__main__': unittest.main()
