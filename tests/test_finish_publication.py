import json
import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import gate
from harnesslib import run_dir


class FinishPublicationTests(unittest.TestCase):
    task = 'T-finish-publish'

    def setUp(self):
        rd = run_dir(self.task)
        shutil.rmtree(rd, ignore_errors=True)
        rd.mkdir(parents=True, exist_ok=True)
        (rd / 'route.json').write_text(json.dumps({
            'task_id': self.task,
            'risk': 'R0',
            'agents': [],
            'human_gate': False,
            'isolation': 'worktree',
            'tdd': {},
        }), encoding='utf-8')
        (rd / 'progress.json').write_text(json.dumps({
            'task_id': self.task,
            'risk': 'R0',
            'state': 'RUNNING',
            'current_step': 'CLOSE',
            'steps': ['IMPLEMENT', 'CHECKS', 'CLOSE'],
            'completed': ['IMPLEMENT', 'CHECKS'],
        }), encoding='utf-8')

    def tearDown(self):
        shutil.rmtree(run_dir(self.task), ignore_errors=True)

    def _common(self):
        return (
            patch.object(gate, '_acceptance_decision', return_value=(True, None)),
            patch.object(gate, '_handoff_decision', return_value=([], [])),
            patch.object(gate, 'read_evidence', return_value=[]),
            patch.object(gate, 'tdd_finish_decision', return_value={'required': [], 'missing': [], 'failing': []}),
            patch.object(gate, 'impact_finish_decision', return_value={'required': [], 'missing': [], 'failing': []}),
        )

    def test_final_finish_gate_requires_publication_for_worktree_route(self):
        patches = self._common()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patch(
            'worktree.publish_status',
            return_value={'published': False, 'reason': 'publish.json missing'},
        ):
            decision = gate.finish_decision(self.task, 'R0')
        self.assertFalse(decision['allow'])
        self.assertIn('publication', decision['required'])
        self.assertIn('publication', decision['missing'])

    def test_prepublication_gate_can_validate_evidence_without_publication(self):
        patches = self._common()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            decision = gate.finish_decision(self.task, 'R0', require_publication=False)
        self.assertTrue(decision['allow'], decision)
        self.assertNotIn('publication', decision['required'])


if __name__ == '__main__':
    unittest.main()
