import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import codex_hook


class CodexHookTests(unittest.TestCase):
    def test_safe_bash_defers_to_codex_permission_layer(self):
        self.assertIsNone(codex_hook.evaluate({
            'tool_name': 'Bash',
            'tool_input': {'command': 'python -m unittest'},
        }))

    def test_dangerous_bash_is_denied(self):
        decision = codex_hook.evaluate({
            'tool_name': 'Bash',
            'tool_input': {'command': 'terraform destroy'},
        })
        self.assertFalse(decision['allow'])

    def test_protected_patch_is_denied(self):
        decision = codex_hook.evaluate({
            'tool_name': 'apply_patch',
            'tool_input': {'command': '*** Begin Patch\n*** Update File: harness/manifest.yaml\n*** End Patch'},
        })
        self.assertFalse(decision['allow'])

    def test_unparseable_patch_fails_closed(self):
        decision = codex_hook.evaluate({
            'tool_name': 'apply_patch',
            'tool_input': {'command': 'not a patch'},
        })
        self.assertFalse(decision['allow'])

    def test_known_harness_subagent_is_allowed(self):
        self.assertIsNone(codex_hook.evaluate({
            'tool_name': 'Agent',
            'tool_input': {'agent_type': 'reviewer'},
        }))

    def test_unknown_subagent_is_denied(self):
        decision = codex_hook.evaluate({
            'tool_name': 'Agent',
            'tool_input': {'agent_type': 'untrusted-writer'},
        })
        self.assertFalse(decision['allow'])
        self.assertIn('not allowlisted', decision['reason'])

    def test_subagent_without_a_type_fails_closed(self):
        decision = codex_hook.evaluate({
            'tool_name': 'Agent',
            'tool_input': {},
        })
        self.assertFalse(decision['allow'])

    def test_deny_output_uses_codex_pretool_protocol(self):
        decision = {'allow': False, 'reason': 'blocked'}
        from io import StringIO
        from unittest.mock import patch
        with patch('sys.stdout', new_callable=StringIO) as stdout:
            codex_hook.emit(decision)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload['hookSpecificOutput']['hookEventName'], 'PreToolUse')
        self.assertEqual(payload['hookSpecificOutput']['permissionDecision'], 'deny')


if __name__ == '__main__':
    unittest.main()
