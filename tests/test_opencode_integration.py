import json, shutil, subprocess, sys, unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from compile_harness import generated

class OpenCodeIntegrationTests(unittest.TestCase):
    def test_project_config_uses_harness_orchestrator_and_fail_safe_defaults(self):
        cfg=json.loads((ROOT/'opencode.json').read_text())
        self.assertEqual(cfg['default_agent'],'harness-orchestrator')
        rules=cfg['permissions']
        self.assertIn({'action':'external_directory','resource':'*','effect':'deny'},rules)
        self.assertIn({'action':'subagent','resource':'*','effect':'deny'},rules)

    def test_generated_opencode_agents_have_explicit_least_privilege(self):
        out=generated()
        explorer=out[Path('.opencode/agents/explorer.md')]
        implementer=out[Path('.opencode/agents/implementer.md')]
        researcher=out[Path('.opencode/agents/docs-researcher.md')]
        self.assertIn('action: external_directory',explorer)
        self.assertIn('action: websearch\n    resource: "*"\n    effect: deny',explorer)
        self.assertIn('action: edit\n    resource: "*"\n    effect: allow',implementer)
        self.assertIn('action: websearch\n    resource: "*"\n    effect: allow',researcher)

    def test_orchestrator_is_non_writer_and_only_delegates_known_agents(self):
        text=(ROOT/'.opencode/agents/harness-orchestrator.md').read_text()
        self.assertIn('action: edit\n    resource: "*"\n    effect: deny',text)
        for agent in ('explorer','planner','debugger','implementer','test-auditor','reviewer','verifier','security-reviewer','docs-researcher'):
            self.assertIn(f'resource: "{agent}"',text)

    def test_plugin_has_native_catalog_agent_context_permission_and_shell_integration(self):
        text=(ROOT/'.opencode/plugins/harness/index.ts').read_text()
        for needle in ('ctx.catalog.model.list()', 'ctx.agent.transform', 'ctx.session.hook("context"', 'ctx.permission.hook("evaluate"', 'ctx.shell.hook("create.before"'):
            self.assertIn(needle,text)
        self.assertNotIn('claude-sonnet',text.lower())
        self.assertNotIn('gpt-5',text.lower())

    def test_plugin_is_javascript_syntax_compatible(self):
        node=shutil.which('node')
        if not node: self.skipTest('node unavailable')
        src=ROOT/'.opencode/plugins/harness/index.ts'
        tmp=ROOT/'.harness/opencode/plugin-syntax.mjs'
        tmp.parent.mkdir(parents=True,exist_ok=True)
        tmp.write_text(src.read_text())
        try:
            p=subprocess.run([node,'--check',str(tmp)],text=True,capture_output=True)
            self.assertEqual(p.returncode,0,p.stderr)
        finally:
            tmp.unlink(missing_ok=True)

    def test_activate_task_writes_runtime_binding(self):
        task=ROOT/'tasks/TEST-OPENCODE-OVERLAY.json'
        runtime=ROOT/'.harness/opencode'
        active=runtime/'active-task.json'
        inventory=runtime/'model-inventory.json'
        old_inventory=inventory.read_text() if inventory.exists() else None
        task.write_text(json.dumps({'id':'TEST-OPENCODE','description':'Fix normal backend bug','files':['scripts/task_router.py']}))
        # Empty-but-fresh catalog is valid for R1 and safely results in inherit.
        runtime.mkdir(parents=True,exist_ok=True)
        inventory.write_text(json.dumps({'schema_version':1,'provider':'opencode','generated_at':'2099-01-01T00:00:00Z','source':'test','models':[]}))
        try:
            p=subprocess.run([sys.executable,str(ROOT/'scripts/providers/opencode_activate_task.py'),str(task.relative_to(ROOT))],cwd=ROOT,text=True,capture_output=True)
            self.assertEqual(p.returncode,0,p.stderr+p.stdout)
            data=json.loads(active.read_text())
            self.assertEqual(data['task_id'],'TEST-OPENCODE')
            self.assertEqual(data['risk'],'R1')
            self.assertTrue(data['selections'])
            self.assertTrue(all(x['action']=='inherit' for x in data['selections']))
        finally:
            task.unlink(missing_ok=True)
            shutil.rmtree(ROOT/'.harness/runs/TEST-OPENCODE',ignore_errors=True)
            active.unlink(missing_ok=True)
            if old_inventory is None: inventory.unlink(missing_ok=True)
            else: inventory.write_text(old_inventory)

if __name__=='__main__': unittest.main()
