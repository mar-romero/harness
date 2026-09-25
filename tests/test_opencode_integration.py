import json, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
sys.path.insert(0,str(ROOT/'scripts'/'providers'))
from harnesslib import provider_active_path, provider_enriched_inventory_path, provider_model_selections_path, provider_overlay_dir, read_provider_active, runtime_root
from compile_harness import generated
import opencode_activate_task

class OpenCodeIntegrationTests(unittest.TestCase):
    def test_invalid_binding_replacement_requires_matching_verified_backups_and_writes_receipt(self):
        with tempfile.TemporaryDirectory(prefix='opencode-replacement-') as temp:
            root = Path(temp)
            task_id = 'T-replace-invalid'
            overlay = root / '.harness' / 'overlays' / ('a' * 64) / 'opencode'
            preserved = root / '.harness' / 'legacy-preserved' / task_id / 'opencode'
            backup_names = {
                'active-task.json': 'active-task.pre-round-21-20260925.json',
                'model-selections.json': 'model-selections.pre-round-21-20260925.json',
                'enriched-inventory.json': 'enriched-inventory.pre-round-21-20260925.json',
            }
            for name, backup_name in backup_names.items():
                source = overlay / name
                backup = preserved / backup_name
                source.parent.mkdir(parents=True, exist_ok=True)
                backup.parent.mkdir(parents=True, exist_ok=True)
                source.write_text(
                    json.dumps({'task_id': task_id, 'file': name}), encoding='utf-8'
                )
                shutil.copyfile(source, backup)

            receipt_payload = {}
            def save_receipt(path, payload):
                receipt_payload.update(payload)
                return path

            with patch.object(opencode_activate_task, 'ROOT', root), \
                 patch.object(opencode_activate_task, 'provider_overlay_dir', return_value=overlay), \
                 patch.object(opencode_activate_task, 'provider_active_path', return_value=overlay / 'active-task.json'), \
                 patch.object(opencode_activate_task, 'read_provider_active', side_effect=ValueError('invalid binding')), \
                 patch.object(opencode_activate_task, 'reject_legacy_provider_state'), \
                 patch.object(opencode_activate_task, 'worktree_identity', return_value={'worktree_id': 'a' * 64}), \
                 patch.object(opencode_activate_task, 'run_dir', return_value=root / '.harness' / 'runs' / task_id), \
                 patch.object(opencode_activate_task, 'write_json_immutable', side_effect=save_receipt):
                receipt_path = opencode_activate_task._authorize_invalid_binding_replacement(
                    task_id, 'explicit-user-approval-2026-09-25'
                )

            self.assertEqual(receipt_payload['status'], 'AUTHORIZED_INVALID_BINDING_REPLACEMENT')
            self.assertEqual(len(receipt_payload['backups']), 3)
            self.assertTrue(receipt_payload['receipt_sha256'])
            self.assertIn('state-replacements', str(receipt_path))
            self.assertIn('opencode-', str(receipt_path))

    def test_invalid_binding_replacement_refuses_missing_gate(self):
        with self.assertRaisesRegex(ValueError, 'explicit --human-gate'):
            opencode_activate_task._authorize_invalid_binding_replacement(
                'T-replace-invalid', None
            )

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
        for agent in ('explorer','planner','debugger','implementer','test-designer','test-auditor','reviewer','verifier','security-reviewer','docs-researcher'):
            self.assertIn(f'resource: "{agent}"',text)

    def test_plugin_has_native_catalog_agent_context_permission_and_shell_integration(self):
        text=(ROOT/'.opencode/plugins/harness/index.ts').read_text()
        # The V2 plugin SDK (@opencode/plugin 2.0.7) defines the model domain as
        # ctx.model (ModelDomain with list(); see the package's
        # dist/promise/model.d.ts) and contains no `catalog` namespace anywhere
        # in its published types. The plugin legitimately migrated
        # ctx.catalog.model.list() -> ctx.model.list() for V2, so the needle
        # here follows the migrated API.
        for needle in ('ctx.model.list()', 'ctx.agent.transform', 'ctx.session.hook("context"', 'ctx.permission.hook("evaluate"', 'ctx.shell.hook("create.before"'):
            self.assertIn(needle,text)
        # Lock the migration: the V1-era catalog namespace must stay gone.
        self.assertNotIn('ctx.catalog.',text)
        self.assertNotIn('claude-sonnet',text.lower())
        self.assertNotIn('gpt-5',text.lower())

    def test_plugin_is_javascript_syntax_compatible(self):
        node=shutil.which('node')
        if not node: self.skipTest('node unavailable')
        src=ROOT/'.opencode/plugins/harness/index.ts'
        tmp=ROOT/'.harness/opencode/plugin-syntax.mjs'
        tmp.parent.mkdir(parents=True,exist_ok=True)
        tmp.unlink(missing_ok=True)
        tmp.write_text(src.read_text())
        try:
            p=subprocess.run([node,'--check',str(tmp)],text=True,encoding='utf-8',errors='replace',capture_output=True)
            self.assertEqual(p.returncode,0,p.stderr)
        finally:
            tmp.unlink(missing_ok=True)

    def test_plugin_cwd_containment_is_async_and_reparse_safe(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('node unavailable')
        helper = (ROOT / '.opencode/plugins/harness/path_guards.mjs').resolve()
        script = r'''
import assert from "node:assert/strict";
import { mkdtemp, mkdir, symlink, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
const { insideReal, assertNoReparse } = await import(pathToFileURL(process.argv[1]).href);
const root = await mkdtemp(path.join(os.tmpdir(), "harness-cwd-"));
const outside = await mkdtemp(path.join(os.tmpdir(), "harness-outside-"));
try {
  assert.equal(await insideReal(root, root), true);
  assert.equal(await insideReal(root, outside), false);
  const link = path.join(root, "link");
  try {
    await symlink(outside, link, process.platform === "win32" ? "junction" : "dir");
    assert.equal(await insideReal(root, link), false);
    await assert.rejects(() => assertNoReparse(root, link));
  } catch (error) {
    if (error?.code !== "EPERM" && error?.code !== "EACCES") throw error;
  }
} finally {
  await rm(root, { recursive: true, force: true });
  await rm(outside, { recursive: true, force: true });
}
'''
        result = subprocess.run(
            [node, '--input-type=module', '-e', script, str(helper)],
            cwd=ROOT, text=True, encoding='utf-8', errors='replace',
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_plugin_rechecks_reparse_boundaries_for_reads_and_atomic_temp_writes(self):
        text = (ROOT / '.opencode/plugins/harness/index.ts').read_text()
        self.assertIn('await assertNoReparse(root, selectionFile)', text)
        self.assertIn('await assertNoReparse(root, inventoryFile)', text)
        self.assertGreaterEqual(text.count('await assertNoReparse(root, tmp)'), 2)

    def test_plugin_never_refreshes_activation_bound_inventory(self):
        text = (ROOT / '.opencode/plugins/harness/index.ts').read_text()
        self.assertIn('activation-bound, immutable scored', text)
        self.assertNotIn('writeJsonAtomic(enrichedInventoryFile, inventoryPayload, root)', text)
        self.assertIn('await writeJsonAtomic(inventoryFile, inventoryPayload, root)', text)

    def test_plugin_rejects_missing_or_reparse_backed_executables(self):
        text = (ROOT / '.opencode/plugins/harness/index.ts').read_text()
        self.assertIn('lstatSync(value)', text)
        self.assertIn('realpathSync.native(value)', text)
        self.assertIn('must be a regular non-reparse file', text)
        self.assertIn('resolves through a reparse point', text)

    def test_orchestrator_has_narrow_control_plane_shell_permissions(self):
        text=(ROOT/'.opencode/agents/harness-orchestrator.md').read_text()

        shell_deny=(
            'action: shell\n'
            '    resource: "*"\n'
            '    effect: deny'
        )
        self.assertEqual(text.count(shell_deny), 1)

        allowed=(
            'python scripts/providers/opencode_activate_task.py *',
            'python scripts/request_normalizer.py *',
            'python scripts/product_planning.py validate *',
            'python scripts/product_planning.py materialize *',
            'python scripts/orchestrator.py *',
            'python scripts/evidence.py summary *',
            'python scripts/evidence.py validate *',
            'python scripts/agent_budget.py *',
            'python scripts/impact_analysis.py *',
            'python scripts/tdd_evidence.py *',
            'python scripts/gate.py finish *',
            'python scripts/worktree.py create *',
            'python scripts/worktree.py status *',
            'python scripts/worktree.py publish *',
            'python scripts/task_checks.py run *',
            'python scripts/check_harness.py*',
            'python scripts/run_evals.py*',
        )

        deny_pos=text.index(shell_deny)

        for command in allowed:
            rule=(
                'action: shell\n'
                f'    resource: "{command}"\n'
                '    effect: allow'
            )
            self.assertEqual(text.count(rule), 1, command)
            self.assertGreater(text.index(rule), deny_pos)

        self.assertNotIn(
            'resource: "python scripts/product_planning.py *"',
            text,
        )
        self.assertNotIn('resource: "python scripts/evidence.py *"', text)
        self.assertNotIn('resource: "python scripts/evidence.py add *"', text)
        self.assertNotIn('resource: "python scripts/worktree.py *"', text)
        self.assertNotIn('resource: "python scripts/task_checks.py *"', text)
        self.assertNotIn('resource: "python -c *"', text)
        self.assertNotIn('resource: "python -c *"', text)
        self.assertNotIn('resource: "echo *"', text)


    def test_orchestrator_allows_only_incoming_handoff_staging_and_uses_commit(self):
        text=(ROOT/'.opencode/agents/harness-orchestrator.md').read_text()
        rule=(
            'action: edit\n'
            '    resource: ".harness/runs/*/incoming/*.json"\n'
            '    effect: allow'
        )
        self.assertIn(rule,text)
        self.assertIn('orchestrator.py commit',text)
        self.assertIn('Never use `orchestrator.py record --status PASS` for EXPLORE',text)

    def test_orchestrator_does_not_use_shell_as_edit_transport(self):
        text=(ROOT/'.opencode/agents/harness-orchestrator.md').read_text()

        self.assertIn(
            'Run exactly one allowlisted control-plane command per shell invocation.',
            text,
        )
        self.assertIn(
            'Never use any of the following as a file-edit transport:',
            text,
        )
        self.assertIn(
            '`HARNESS_PERMISSION_POLICY_MISMATCH`',
            text,
        )

    def test_activate_task_writes_runtime_binding(self):
        task=ROOT/'tasks/TEST-OPENCODE-OVERLAY.json'
        active=provider_active_path('opencode')
        inventory=provider_enriched_inventory_path('opencode')
        legacy_root=runtime_root()/'.harness/opencode'
        legacy_names=(
            'active-task.json', 'session.json', 'permission-audit.jsonl',
            'catalog-snapshot.json', 'model-inventory.json',
        )

        old_inventory=inventory.read_text() if inventory.exists() else None
        # The live provider session may still emit legacy diagnostics while the
        # subprocess fixture runs. Quarantine the exact pre-existing bytes so
        # this test exercises the fail-closed migration boundary without
        # overwriting another session's state.
        held_legacy={}
        worktree_legacy_root=ROOT/'.harness/opencode'
        held_worktree_legacy={}
        quarantine=tempfile.TemporaryDirectory(prefix='harness-opencode-legacy-')
        quarantine_root=Path(quarantine.name)
        for name in legacy_names:
            path=legacy_root/name
            if path.exists():
                held_legacy[name]=path.read_bytes()
                path.unlink()
            local_path=worktree_legacy_root/name
            if local_path.exists():
                held_worktree_legacy[name]=local_path.read_bytes()
                local_path.unlink()
        held_active=active.read_bytes() if active.exists() else None
        active.unlink(missing_ok=True)
        # The activation subprocess also rewrites the real overlay
        # model-selections.json (task TEST-OPENCODE). Without a byte-exact
        # restore here, the fixture leaves a selections file whose task_id no
        # longer matches the restored active binding, which later fails
        # read_provider_active() with 'provider model selections task
        # mismatch' (seen as a full-suite failure of
        # test_receipt_routing_and_consent).
        selections=provider_model_selections_path('opencode')
        held_selections=selections.read_bytes() if selections.exists() else None
        selections.unlink(missing_ok=True)
        # Activation now binds model selections to an immutable worktree-local
        # inventory snapshot. Preserve that file too, otherwise restoring the
        # old active binding and selections would leave an invalid digest.
        overlay_inventory=provider_overlay_dir('opencode')/'enriched-inventory.json'
        held_overlay_inventory=overlay_inventory.read_bytes() if overlay_inventory.exists() else None
        task.parent.mkdir(parents=True, exist_ok=True)

        task.write_text(json.dumps({
            'id':'TEST-OPENCODE',
            'description':'Fix normal backend bug',
            'files':['scripts/task_router.py']
        }))

        # Empty-but-fresh scored inventory is valid for R1 and safely results in inherit.
        active.parent.mkdir(parents=True,exist_ok=True)
        inventory.parent.mkdir(parents=True,exist_ok=True)

        inventory.write_text(json.dumps({
            'schema_version':3,
            'provider':'opencode',
            'generated_at':'2099-01-01T00:00:00Z',
            'source':'test',
            'models':[]
        }))

        try:
            p=subprocess.run(
                [
                    sys.executable,
                    str(ROOT/'scripts/providers/opencode_activate_task.py'),
                    str(task.relative_to(ROOT))
                ],
                cwd=ROOT,
                text=True,
                encoding='utf-8',
                errors='replace',
                capture_output=True
            )

            self.assertEqual(p.returncode,0,p.stderr+p.stdout)

            data=json.loads(active.read_text())
            self.assertEqual(data['task_id'],'TEST-OPENCODE')
            self.assertEqual(data['provider'], 'opencode')
            self.assertIn('worktree_id', data['overlay'])
            self.assertEqual(data['risk'],'R1')
            self.assertTrue(data['selections'])
            self.assertTrue(
                all(x['action']=='inherit' for x in data['selections'])
            )
            snapshot=runtime_root()/'.harness/runs/TEST-OPENCODE/task.json'
            self.assertTrue(snapshot.is_file())
            frozen=json.loads(snapshot.read_text())
            self.assertEqual(frozen['id'],'TEST-OPENCODE')
            self.assertEqual(frozen['files'],['scripts/task_router.py'])
            self.assertEqual(
                data['task_snapshot_path'],
                '.harness/runs/TEST-OPENCODE/task.json'
            )
            validated = read_provider_active('opencode')
            self.assertEqual(validated['task_id'], 'TEST-OPENCODE')
        finally:
            task.unlink(missing_ok=True)
            shutil.rmtree(runtime_root()/'.harness/runs/TEST-OPENCODE',ignore_errors=True)
            active.unlink(missing_ok=True)
            # Restore the overlay selections byte-exactly, symmetric with the
            # active binding restore below, so the fixture leaves the worktree
            # provider overlay exactly as it found it.
            if selections.exists():
                selections.replace(quarantine_root/'model-selections.json')
            if held_selections is not None:
                selections.parent.mkdir(parents=True,exist_ok=True)
                selections.write_bytes(held_selections)
            if held_overlay_inventory is None:
                overlay_inventory.unlink(missing_ok=True)
            else:
                overlay_inventory.parent.mkdir(parents=True,exist_ok=True)
                overlay_inventory.write_bytes(held_overlay_inventory)

            # Preserve any diagnostic emitted during the subprocess in the
            # quarantine, then restore the exact pre-test legacy bytes. This
            # avoids a silent overwrite while keeping the integration fixture
            # side-effect free for the rest of the suite.
            for name in legacy_names:
                path=legacy_root/name
                if path.exists():
                    path.replace(quarantine_root/name)
                if name in held_legacy:
                    legacy_root.mkdir(parents=True,exist_ok=True)
                    path.write_bytes(held_legacy[name])
                local_path=worktree_legacy_root/name
                if local_path.exists():
                    local_path.unlink()
                if name in held_worktree_legacy:
                    worktree_legacy_root.mkdir(parents=True,exist_ok=True)
                    local_path.write_bytes(held_worktree_legacy[name])
            if active.exists():
                active.replace(quarantine_root/'active-task.json')
            if held_active is not None:
                active.parent.mkdir(parents=True,exist_ok=True)
                active.write_bytes(held_active)

            if old_inventory is None:
                inventory.unlink(missing_ok=True)
            else:
                inventory.write_text(old_inventory)
            quarantine.cleanup()
if __name__=='__main__': unittest.main()
