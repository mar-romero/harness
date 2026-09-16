import tomllib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import compile_harness
from compile_harness import generated
from harnesslib import load_manifest

class CompileTests(unittest.TestCase):
    def test_codex_generated_adapters_do_not_depend_on_active_task_runtime_state(self):
        with tempfile.TemporaryDirectory() as td:
            active = Path(td) / "active-task.json"

            with patch.object(compile_harness, "CODEX_ACTIVE", active):
                without_active_task = compile_harness.generated()[
                    Path(".codex/agents/explorer.toml")
                ]

                active.write_text(
                    json.dumps(
                        {
                            "task_id": "TASK-test",
                            "selections": [
                                {
                                    "agent": "explorer",
                                    "status": "selected",
                                    "action": "use",
                                    "base_model_id": "gpt-5.6-sol",
                                    "reasoning_effort": "high",
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )

                with_active_task = compile_harness.generated()[
                    Path(".codex/agents/explorer.toml")
                ]

            self.assertEqual(without_active_task, with_active_task)

    def test_all_provider_agent_pairs_and_claude_wrappers_generated(self):
        m=load_manifest(); out=generated()
        agents=len(m['agents']); providers=len(m['providers']); skills=len([p for p in (Path(__file__).resolve().parents[1]/'.agents/skills').iterdir() if p.is_dir()])
        self.assertEqual(len(out),agents*providers+skills+4)
        self.assertTrue(any(p.as_posix().endswith('.codex/agents/implementer.toml') for p in out))
        self.assertIn(Path('.codex/agents/harness-orchestrator.toml'), out)
        self.assertIn(Path('.codex/agents/default.toml'), out)
        self.assertIn(Path('.codex/hooks.json'), out)
        self.assertIn(Path('.codex/aci_mcp_entry.py'), out)
        self.assertTrue(any(p.as_posix().endswith('.claude/skills/software-engineering/SKILL.md') for p in out))
    def test_strict_read_only_agents_do_not_receive_shell_where_configurable(self):
        out=generated()
        claude=out[Path('.claude/agents/explorer.md')].split('---')[1]
        tools_line=next(line for line in claude.splitlines() if line.startswith('tools:'))
        self.assertNotIn('Bash', tools_line)
        self.assertNotIn('run_shell_command', out[Path('.gemini/agents/explorer.md')].split('---')[1])
        self.assertIn('effect: deny', out[Path('.opencode/agents/explorer.md')])
    def test_debugger_and_verifier_can_execute_without_write_tools(self):
        out=generated()
        dbg=out[Path('.claude/agents/debugger.md')].split('---')[1]
        self.assertIn('Bash',dbg); self.assertNotIn('Write',dbg.split('tools:')[1].split('\n')[0])
        ver=out[Path('.gemini/agents/verifier.md')].split('---')[1]
        self.assertIn('run_shell_command',ver); self.assertNotIn('write_file',ver)

    def test_codex_primary_orchestrator_uses_codex_activation_and_fails_closed(self):
        text=generated()[Path('.codex/agents/harness-orchestrator.toml')]
        parsed=tomllib.loads(text)
        self.assertEqual(parsed['name'], 'harness-orchestrator')
        self.assertIn('scripts/providers/codex_activate_task.py', text)
        self.assertIn('progress.json.current_step', text)
        self.assertIn('scripts/orchestrator.py commit', text)

    def test_codex_default_agent_uses_the_orchestrator_lifecycle(self):
        text=generated()[Path('.codex/agents/default.toml')]
        parsed=tomllib.loads(text)
        self.assertEqual(parsed['name'], 'default')
        self.assertIn('scripts/providers/codex_activate_task.py', text)
        self.assertIn('route.json.tdd', text)
        self.assertIn('OpenCode-only, stop and report the failed-closed limitation', text)

    def test_codex_hooks_are_generated_for_shell_and_patches(self):
        import json
        payload=json.loads(generated()[Path('.codex/hooks.json')])
        self.assertNotIn('version', payload)
        self.assertEqual(
            payload['hooks']['SessionStart'][0]['matcher'],
            'startup|resume|clear|compact',
        )
        self.assertEqual(len(payload['hooks']['PreToolUse']), 3)
        self.assertEqual(
            payload['hooks']['PreToolUse'][0]['matcher'],
            '^Bash$',
        )
        self.assertEqual(
            payload['hooks']['PreToolUse'][1]['matcher'],
            '^apply_patch$',
        )
        self.assertEqual(
            payload['hooks']['PreToolUse'][2]['matcher'],
            '^Agent$',
        )
        command=payload['hooks']['SessionStart'][0]['hooks'][0]['command']
        self.assertIn('codex_context_hook.py', command)
        self.assertEqual(command, 'python scripts/codex_context_hook.py')
        self.assertNotIn('cmd.exe', command)
        self.assertNotIn('\\Users\\', command)
if __name__=='__main__': unittest.main()
