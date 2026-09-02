import sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from compile_harness import generated
from harnesslib import load_manifest
class CompileTests(unittest.TestCase):
    def test_all_provider_agent_pairs_and_claude_wrappers_generated(self):
        m=load_manifest(); out=generated()
        agents=len(m['agents']); providers=len(m['providers']); skills=len([p for p in (Path(__file__).resolve().parents[1]/'.agents/skills').iterdir() if p.is_dir()])
        self.assertEqual(len(out),agents*providers+skills)
        self.assertTrue(any(str(p).endswith('.codex/agents/implementer.toml') for p in out))
        self.assertTrue(any(str(p).endswith('.claude/skills/software-engineering/SKILL.md') for p in out))
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
if __name__=='__main__': unittest.main()
