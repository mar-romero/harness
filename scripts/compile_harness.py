#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path
from harnesslib import ROOT, load_manifest

ACI_SERVER = "harness-aci"
ACI_INSPECT_TOOLS = [
    "repo_search", "repo_read_range", "repo_symbol", "repo_callers",
    "repo_dependencies", "git_status", "git_diff",
]
ACI_CHECK_TOOLS = ["tests_run", "lint_run", "diagnostics_get"]
CODEX_ACTIVE = ROOT / ".harness" / "codex" / "active-task.json"
CODEX_ORCHESTRATOR = Path(".codex/agents/harness-orchestrator.toml")
CODEX_DEFAULT_AGENT = Path(".codex/agents/default.toml")
CODEX_HOOKS = Path(".codex/hooks.json")
CODEX_ACI_ENTRY = Path(".codex/aci_mcp_entry.py")
ORCHESTRATOR_ROLE = ROOT / ".agents" / "roles" / "harness-orchestrator.md"


def _codex_hook_command(script: str) -> str:
    """Return a repository-relative, cross-machine Python hook command.

    Codex runs project hooks from the project workspace. Keeping both the
    interpreter and script relative avoids embedding the compiler host's user,
    drive, or checkout path into the generated adapter.
    """
    return f"python scripts/{script}"


def _codex_binding(name):
    """Return one active per-agent Codex model binding, if a task is activated."""
    try:
        active = json.loads(CODEX_ACTIVE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    for selection in active.get("selections", []):
        if selection.get("agent") != name:
            continue
        if selection.get("status") != "selected" or selection.get("action") != "use":
            return None
        model = selection.get("base_model_id") or selection.get("model_id")
        if not model:
            return None
        return {"model": str(model), "effort": selection.get("reasoning_effort")}
    return None


def aci_tools_for(name, meta):
    allow_checks = meta.get('mode') != 'read-only' or 'shell' in meta.get('capabilities', []) or name == 'test-auditor'
    return ACI_INSPECT_TOOLS + (ACI_CHECK_TOOLS if allow_checks else [])


def claude_aci_tools(name, meta):
    return [f"mcp__{ACI_SERVER}__{tool}" for tool in aci_tools_for(name, meta)]


def gemini_aci_tools(name, meta):
    return [f"mcp_{ACI_SERVER}_{tool}" for tool in aci_tools_for(name, meta)]


def q(s): return json.dumps(s,ensure_ascii=False)
def yaml_list(items): return '['+', '.join(items)+']'


def front_body(provider,name,meta,body):
    desc=meta['description']; mode=meta['mode']; turns=meta.get('max_turns',20); skills=meta.get('skills',[])
    readonly = mode=='read-only'
    shell = (not readonly) or ('shell' in meta.get('capabilities', []))
    if provider=='codex':
        # Codex custom agents support per-agent model and model_reasoning_effort.
        # Omit them unless a durable Codex task activation selected explicit values.
        sandbox = 'sandbox_mode = "read-only"\n' if readonly else ''
        binding = _codex_binding(name)
        runtime = ''
        if binding:
            runtime += f'model = {q(binding["model"])}\n'
            if binding.get('effort'):
                runtime += f'model_reasoning_effort = {q(binding["effort"])}\n'
        return f'name = {q(name)}\ndescription = {q(desc)}\n{runtime}{sandbox}\ndeveloper_instructions = """\n{body.rstrip()}\n"""\n'
    if provider=='claude':
        if readonly:
            builtins=['Read','Glob','Grep'] + (['Bash'] if shell else [])
            dis='Edit, Write' if shell else 'Edit, Write, Bash'
        else:
            builtins=['Read','Glob','Grep','Bash','Edit','Write']
            dis='Agent'
        tools=', '.join(builtins + claude_aci_tools(name, meta))
        lines=['---',f'name: {name}',f'description: {desc}','model: inherit',f'maxTurns: {turns}',f'tools: {tools}',f'disallowedTools: {dis}']
        if skills: lines.append('skills: ['+', '.join(skills)+']')
        if meta.get('isolation')=='worktree': lines.append('isolation: worktree')
        return '\n'.join(lines)+f'\n---\n\n{body.rstrip()}\n'
    if provider=='cursor':
        return f'---\nname: {name}\ndescription: {desc}\nmodel: inherit\nreadonly: {str(readonly).lower()}\n---\n\n{body.rstrip()}\n'
    if provider=='gemini':
        tools=['read_file','read_many_files','list_directory','glob','grep_search','activate_skill'] + gemini_aci_tools(name, meta)
        if not readonly: tools += ['write_file','replace','run_shell_command']
        elif shell: tools += ['run_shell_command']
        return f'---\nname: {name}\ndescription: {desc}\nkind: local\nmax_turns: {turns}\ntools: [{", ".join(tools)}]\n---\n\n{body.rstrip()}\n'
    if provider=='opencode':
        # OpenCode V2 uses ordered permissions (last match wins). Keep the
        # generated adapters explicit so provider safety survives recompilation.
        caps=set(meta.get('capabilities', []))
        perms=[
            ('read','*','allow'), ('glob','*','allow'), ('grep','*','allow'), ('list','*','allow'),
            ('lsp','*','allow'), ('skill','*','allow'), (f'{ACI_SERVER}_repo_*','*','allow'), (f'{ACI_SERVER}_git_*','*','allow'), (f'{ACI_SERVER}_tests_run','*','allow' if (not readonly or shell or name=='test-auditor') else 'deny'), (f'{ACI_SERVER}_lint_run','*','allow' if (not readonly or shell or name=='test-auditor') else 'deny'), (f'{ACI_SERVER}_diagnostics_get','*','allow' if (not readonly or shell or name=='test-auditor') else 'deny'), ('external_directory','*','deny'),
            ('edit','*','deny' if readonly else 'allow'),
            ('shell','*','allow' if shell else 'deny'),
            ('webfetch','*','deny'), ('websearch','*','deny'), ('subagent','*','deny'),
        ]
        if name=='docs-researcher':
            perms += [('webfetch','*','allow'), ('websearch','*','allow')]
        lines=[]
        for action,resource,effect in perms:
            lines += [f'  - action: {action}', f'    resource: {q(resource)}', f'    effect: {effect}']
        return f'---\ndescription: {desc}\nmode: subagent\nsteps: {turns}\npermissions:\n'+ '\n'.join(lines)+f'\n---\n\n{body.rstrip()}\n'
    if provider=='copilot':
        tools=(['read','search','execute'] if shell else ['read','search']) if readonly else ['read','search','edit','execute']
        tools += [f'{ACI_SERVER}/{tool}' for tool in aci_tools_for(name, meta)]
        return f'---\nname: {name}\ndescription: {desc}\ntools: [{", ".join(tools)}]\n---\n\n{body.rstrip()}\n'
    raise ValueError(provider)


def target(provider,name):
    ext='.toml' if provider=='codex' else '.agent.md' if provider=='copilot' else '.md'
    return Path(load_manifest()['providers'][provider]['agent_dir'])/(name+ext)


def generated():
    m=load_manifest(); out={}
    for provider in m['providers']:
        for name,meta in m['agents'].items():
            body=(ROOT/m['canonical']['roles_dir']/f'{name}.md').read_text(encoding='utf-8')
            out[target(provider,name)] = front_body(provider,name,meta,body)
    # The primary orchestration contract is a separate canonical role: it is
    # not a worker role in manifest.yaml and therefore is not emitted for every
    # provider. OpenCode has its native primary adapter; Codex receives the
    # equivalent selectable custom agent.
    orchestrator_body = ORCHESTRATOR_ROLE.read_text(encoding='utf-8').rstrip()
    out[CODEX_ORCHESTRATOR] = (
        'name = "harness-orchestrator"\n'
        'description = "Coordinate one routed harness task through specialist agents and evidence-backed closure."\n\n'
        'developer_instructions = """\n'
        + orchestrator_body
        + '\n"""\n'
    )
    # Codex gives a project custom agent precedence when its name matches a
    # built-in agent. `default` is the primary fallback agent, so bind it to
    # the same durable lifecycle without creating a second source of truth.
    out[CODEX_DEFAULT_AGENT] = (
        'name = "default"\n'
        'description = "Primary harness coordinator for routed, evidence-backed work in this repository."\n\n'
        'developer_instructions = """\n'
        + orchestrator_body
        + '\n"""\n'
    )
    # Project-local config resolves relative paths from `.codex`. Keep the
    # stdio entrypoint there, then import the canonical implementation from
    # `scripts/` so desktop, CLI, and IDE clients start it consistently.
    out[CODEX_ACI_ENTRY] = '''#!/usr/bin/env python3
"""Generated entrypoint for the project-scoped Harness ACI MCP server."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

if os.environ.get("HARNESS_ACI_DIAGNOSTICS") == "1":
    path = ROOT / ".harness" / "codex" / "aci-mcp-diagnostics.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"event": "entrypoint_started"}) + "\\n")

try:
    from aci_mcp import main
except BaseException as exc:
    if os.environ.get("HARNESS_ACI_DIAGNOSTICS") == "1":
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"event": "entrypoint_import_failed", "error": str(exc), "error_type": type(exc).__name__}) + "\\n")
    raise

if os.environ.get("HARNESS_ACI_DIAGNOSTICS") == "1":
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"event": "entrypoint_ready"}) + "\\n")

if __name__ == "__main__":
    exit_code = main()
    if os.environ.get("HARNESS_ACI_DIAGNOSTICS") == "1":
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"event": "entrypoint_main_returned", "exit_code": exit_code}) + "\\n")
    raise SystemExit(exit_code)
'''
    out[CODEX_HOOKS] = json.dumps({
        "description": "Run canonical safety gates before Codex shell commands and file patches.",
        "hooks": {
            "SessionStart": [{
                "matcher": "startup|resume|clear|compact",
                "hooks": [{
                    "type": "command",
                    "command": _codex_hook_command("codex_context_hook.py"),
                    "timeout": 3,
                    "statusMessage": "Loading active harness context",
                }],
            }],
            "SubagentStart": [{
                "hooks": [{
                    "type": "command",
                    "command": _codex_hook_command("codex_context_hook.py"),
                    "timeout": 3,
                    "statusMessage": "Loading active harness context",
                }],
            }],
            "PreToolUse": [
                {
                    "matcher": "^Bash$",
                    "hooks": [{
                        "type": "command",
                        "command": _codex_hook_command("codex_hook.py"),
                        "timeout": 3,
                        "statusMessage": "Checking repository command policy",
                    }],
                },
                {
                    "matcher": "^apply_patch$",
                    "hooks": [{
                        "type": "command",
                        "command": _codex_hook_command("codex_hook.py"),
                        "timeout": 3,
                        "statusMessage": "Checking repository write policy",
                    }],
                },
                {
                    "matcher": "^Agent$",
                    "hooks": [{
                        "type": "command",
                        "command": _codex_hook_command("codex_hook.py"),
                        "timeout": 3,
                        "statusMessage": "Checking harness subagent allowlist",
                    }],
                },
            ]
        },
    }, indent=2) + "\n"
    # Claude Code currently discovers project skills from .claude/skills, while the
    # other supported providers can consume .agents/skills directly. Generate tiny
    # Claude compatibility wrappers so the canonical skill body still lives once.
    for skill_dir in sorted((ROOT/m['canonical']['skills_dir']).iterdir()):
        if not skill_dir.is_dir() or not (skill_dir/'SKILL.md').exists():
            continue
        canonical=(skill_dir/'SKILL.md').read_text(encoding='utf-8')
        import re
        dm=re.search(r'^description:\s*(.+)$', canonical, re.M)
        desc=dm.group(1).strip() if dm else f'Canonical {skill_dir.name} skill.'
        wrapper=f'---\nname: {skill_dir.name}\ndescription: {desc}\n---\n\n@../../../.agents/skills/{skill_dir.name}/SKILL.md\n\nCanonical guidance remains in `.agents/skills/{skill_dir.name}/`.\n'
        out[Path('.claude/skills')/skill_dir.name/'SKILL.md']=wrapper
    return out


def compile_all(check=False):
    expected=generated(); bad=[]
    for rel,content in expected.items():
        p=ROOT/rel
        if check:
            if not p.exists() or p.read_text(encoding='utf-8')!=content: bad.append(rel.as_posix())
        else:
            p.parent.mkdir(parents=True,exist_ok=True); p.write_text(content,encoding='utf-8')
    if check and bad:
        print('OUT-OF-DATE GENERATED ADAPTERS:'); [print(' -',x) for x in bad]; return 1
    print('generated artifacts are in sync' if check else f'generated {len(expected)} provider artifacts'); return 0


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--check',action='store_true'); a=ap.parse_args(); raise SystemExit(compile_all(a.check))
if __name__=='__main__': main()
