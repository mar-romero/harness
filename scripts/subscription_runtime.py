#!/usr/bin/env python3
"""Safe headless runners for subscription-authenticated coding CLIs.

This module never reads provider credential stores. By default it removes direct
model API credentials from child environments so the official CLI must use its
own browser/OAuth/subscription login. The bridge then normalizes the final output
and verifies that read-only roles did not mutate the Git working tree.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harnesslib import ROOT
from hook_bus import pre_agent as hook_pre_agent, post_agent as hook_post_agent
from provider_capabilities import probe as probe_capabilities

CONFIG_PATH = ROOT / "harness" / "subscription-providers.json"

ACI_TOOLS = [
    "repo_explore", "repo_search", "repo_read_range", "repo_symbol",
    "repo_callers", "repo_dependencies", "git_status", "git_diff",
    "tests_run", "lint_run", "diagnostics_get",
]
CLAUDE_ACI_TOOLS = [f"mcp__harness-aci__{name}" for name in ACI_TOOLS]


@dataclass(frozen=True)
class ProviderExecution:
    provider: str
    executable: str
    argv: list[str]
    env: dict[str, str]
    output_file: Path | None


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def provider_config(provider: str) -> dict[str, Any]:
    cfg = load_config().get("providers", {}).get(provider)
    if not isinstance(cfg, dict):
        raise ValueError(f"unknown subscription provider: {provider}")
    return cfg


def find_executable(provider: str) -> str | None:
    cfg = provider_config(provider)
    for name in cfg.get("command_candidates", []):
        found = shutil.which(str(name))
        if found:
            return found
    return None


def sanitized_environment(provider: str, base: dict[str, str] | None = None, *, allow_api_credentials: bool = False) -> tuple[dict[str, str], list[str]]:
    env = dict(base if base is not None else os.environ)
    removed: list[str] = []
    if not allow_api_credentials:
        for key in provider_config(provider).get("forbidden_env", []):
            if key in env:
                removed.append(str(key))
                env.pop(str(key), None)
    # Never forward the harness inventory override into a nested provider process.
    env.pop("HARNESS_MODEL_INVENTORY", None)
    return env, removed


def _normalize_effort(effort: str | None, supported: set[str]) -> str | None:
    if not effort:
        return None
    aliases = {"none": "minimal"}
    value = aliases.get(effort, effort)
    return value if value in supported else None


def build_command(*, provider: str, executable: str, prompt: str, model: str, effort: str | None,
                  mode: str, cwd: Path, max_turns: int, output_file: Path | None,
                  allow_shell: bool = False, capabilities: dict[str, bool] | None = None) -> list[str]:
    """Build a shell-free argv for an official CLI.

    `mode` is `read-only` or `writer`. Writer commands are intentionally more
    capable, but shell execution remains opt-in for Claude/Copilot to keep the
    single-writer boundary narrow.
    """
    writer = mode == "writer"
    runtime_model = None if model in ("", "default", "auto-default") else model
    def has(name: str) -> bool:
        return True if capabilities is None else bool(capabilities.get(name))

    if provider == "codex":
        if output_file is None:
            raise ValueError("Codex runner requires an output file")
        argv = [executable, "exec", "--json", "--output-last-message", str(output_file), "--cd", str(cwd)]
        if runtime_model:
            argv += ["--model", runtime_model]
        argv += ["--sandbox", "workspace-write" if writer else "read-only"]
        normalized = _normalize_effort(effort, {"minimal", "low", "medium", "high", "xhigh"})
        if normalized:
            argv += ["-c", f'model_reasoning_effort="{normalized}"']
        argv.append("-")
        return argv

    if provider == "claude":
        argv = [executable, "-p", "--output-format", "json", "--max-turns", str(max_turns)]
        if runtime_model:
            argv += ["--model", runtime_model]
        normalized = _normalize_effort(effort, {"low", "medium", "high", "xhigh", "max"})
        if normalized and has("effort"):
            argv += ["--effort", normalized]
        if writer:
            tools = ["Read", "Glob", "Grep", "Edit", "Write", *CLAUDE_ACI_TOOLS]
            if allow_shell:
                tools.append("Bash")
            argv += ["--permission-mode", "acceptEdits", "--allowedTools", *tools]
        else:
            argv += ["--permission-mode", "plan", "--allowedTools", "Read", "Glob", "Grep", *CLAUDE_ACI_TOOLS]
        return argv

    if provider == "copilot":
        argv = [
        executable, "--output-format=json", "--no-ask-user",
        "--disable-builtin-mcps", "--no-remote", "--no-remote-export", "-C", str(cwd),
        ]
        if runtime_model:
            argv += ["--model", runtime_model]
        normalized = _normalize_effort(effort, {"low", "medium", "high", "xhigh"})
        if normalized:
            argv += ["--effort", normalized]
        argv += ["--allow-tool=read", "--allow-tool=harness-aci", "--deny-tool=url", "--deny-tool=memory"]
        if writer:
            argv += ["--allow-tool=write"]
            argv += ["--allow-tool=shell"] if allow_shell else ["--deny-tool=shell"]
        else:
            argv += ["--deny-tool=write", "--deny-tool=shell"]
        return argv

    if provider == "cursor":
        argv = [executable, "-p", "--output-format", "json", "--trust"]
        if runtime_model:
            argv += ["--model", runtime_model]
        # Current Cursor CLI exposes Ask as an actual read-only mode. Keep the
        # Git fingerprint as a second line of defense for older/drifting builds.
        if has("mode"):
            argv += ["--mode=agent" if writer else "--mode=ask"]
        if writer:
            argv += ["--force"]
        return argv

    if provider == "grok":
        argv = [executable, "-p", prompt, "--output-format", "json", "--cwd", str(cwd), "--max-turns", str(max_turns), "--disable-web-search"]
        if runtime_model:
            argv += ["--model", runtime_model]
        normalized = _normalize_effort(effort, {"low", "medium", "high", "xhigh"})
        if normalized:
            argv += ["--effort", normalized]
        # dontAsk means anything not explicitly allowed is denied. This makes
        # headless reads deterministic and writer permissions narrow.
        argv += ["--permission-mode", "dontAsk", "--allow", "Read", "--allow", "Grep", "--allow", "MCPTool"]
        if writer:
            argv += ["--allow", "Edit"]
            if allow_shell:
                argv += ["--allow", "Bash(*)", "--deny", "Bash(git push*)", "--deny", "Bash(git reset --hard*)", "--deny", "Bash(rm -rf*)"]
        return argv

    if provider == "gemini":
        argv = [executable, "-p", prompt, "--output-format", "json"]
        if runtime_model:
            argv += ["--model", runtime_model]
        # Plan is the safest documented headless analysis mode. Auto-edit is
        # limited to the single writer; shell authorization remains managed by
        # Gemini's own policy/sandbox and the harness prompt.
        argv += ["--approval-mode", "auto_edit" if writer else "plan"]
        return argv

    raise ValueError(f"unsupported subscription provider: {provider}")


def _git_bytes(cwd: Path, args: list[str]) -> bytes:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=False)
    if proc.returncode != 0:
        return b""
    return proc.stdout


def git_fingerprint(cwd: Path) -> str | None:
    inside = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=cwd, text=True, capture_output=True, check=False)
    if inside.returncode != 0:
        return None
    h = hashlib.sha256()
    h.update(_git_bytes(cwd, ["status", "--porcelain=v1", "-z", "--untracked-files=all"]))
    h.update(b"\0DIFF\0")
    h.update(_git_bytes(cwd, ["diff", "--binary", "--no-ext-diff", "--", "."]))
    h.update(b"\0CACHED\0")
    h.update(_git_bytes(cwd, ["diff", "--cached", "--binary", "--no-ext-diff", "--", "."]))
    return h.hexdigest()


def _json_candidates(text: str) -> list[Any]:
    out: list[Any] = []
    stripped = text.strip()
    if stripped:
        try:
            out.append(json.loads(stripped))
        except Exception:
            pass
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith(("{", "[")):
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _find_text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        for key in ("result", "response", "final", "final_text", "text", "message", "content", "output"):
            if key in value:
                found = _find_text(value[key])
                if found:
                    return found
    if isinstance(value, list):
        texts = [x for x in (_find_text(v) for v in value) if x]
        if texts:
            return "\n".join(texts)
    return None


def _find_usage(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        for key in ("usage", "usage_stats", "token_usage", "tokens"):
            row = value.get(key)
            if isinstance(row, dict):
                return row
        for row in value.values():
            found = _find_usage(row)
            if found:
                return found
    if isinstance(value, list):
        for row in value:
            found = _find_usage(row)
            if found:
                return found
    return None


def parse_output(provider: str, stdout: str, stderr: str, output_file: Path | None) -> tuple[str, dict[str, Any] | None]:
    if provider == "codex" and output_file and output_file.is_file():
        text = output_file.read_text(encoding="utf-8", errors="replace").strip()
        usage = None
        for obj in _json_candidates(stdout):
            usage = _find_usage(obj) or usage
        return text, usage
    for obj in _json_candidates(stdout):
        text = _find_text(obj)
        if text:
            return text, _find_usage(obj)
    return stdout.strip() or stderr.strip(), None


def _prompt_with_contract(prompt: str, provider: str, role: str, effort: str | None, mode: str) -> str:
    extra = [
        prompt.strip(),
        "",
        "Harness runtime contract:",
        f"- role: {role}",
        f"- access mode: {mode}",
        f"- runtime provider: {provider}",
        "- follow AI_POLICY.md, the canonical role instructions, and the canonical skill pack embedded in this prompt",
        "- prefer the harness-aci MCP tools (especially repo_explore) over repeated raw search/read loops whenever the runtime exposes them",
        "- treat provider-native hooks/skills/subagents as supplemental; do not bypass harness roles, gates, typed handoffs, single-writer rules, or ACI policy",
        "- minimize repository reads; reuse compact context and repo_explore when available",
        "- do not publish, push, deploy, or change external systems",
    ]
    if effort:
        extra.append(f"- requested reasoning effort: {effort}")
    if mode != "writer":
        extra.append("- this is a READ-ONLY role: do not modify, create, rename, or delete repository files")
    return "\n".join(extra).strip()


def execute(*, provider: str, prompt: str, model: str, effort: str | None, role: str, mode: str,
            cwd: Path, max_turns: int, timeout: int = 900, task_id: str | None = None, allow_shell: bool = False,
            allow_api_credentials: bool = False) -> dict[str, Any]:
    executable = find_executable(provider)
    if not executable:
        return {
            "exit_code": 127,
            "provider": provider,
            "error": f"official CLI executable not found for {provider}",
            "final_text": "",
            "usage": None,
            "removed_env": [],
            "integrity": {"mode": mode, "changed": False, "before": None, "after": None},
        }
    cwd = cwd.resolve()
    full_prompt = _prompt_with_contract(prompt, provider, role, effort, mode)
    task_id = task_id or os.environ.get("HARNESS_TASK_ID") or "adhoc"
    boundary = hook_pre_agent(task_id=task_id, role=role, mode=mode, cwd=cwd, provider=provider)
    if not boundary.get("allow"):
        return {
            "exit_code": 75, "provider": provider, "error": boundary.get("reason"),
            "final_text": "", "usage": None, "removed_env": [],
            "integrity": {"mode": mode, "changed": False, "before": None, "after": None},
            "hook_bus": boundary,
        }
    before = git_fingerprint(cwd) if mode != "writer" else None
    env, removed = sanitized_environment(provider, allow_api_credentials=allow_api_credentials)
    env["HARNESS_TASK_ID"] = task_id
    env["HARNESS_ROLE"] = role
    env["HARNESS_RUNTIME_PROVIDER"] = provider
    env["HARNESS_ACI_SESSION_ID"] = hashlib.sha256(f"{task_id}:{role}:{provider}:{os.getpid()}".encode()).hexdigest()[:24]
    with tempfile.TemporaryDirectory(prefix="harness-subscription-") as tmp:
        output_file = Path(tmp) / "final.txt" if provider == "codex" else None
        capability_probe = probe_capabilities(provider, executable)
        argv = build_command(
            provider=provider, executable=executable, prompt=full_prompt, model=model,
            effort=effort, mode=mode, cwd=cwd, max_turns=max_turns,
            output_file=output_file, allow_shell=allow_shell,
            capabilities=capability_probe.get("features", {}) if capability_probe.get("probe_ok") else None,
        )
        stdin_prompt = (
            full_prompt
            if provider in {"codex", "claude", "copilot", "cursor"}
            else None
        )

        try:
            proc = subprocess.run(
                argv,
                cwd=cwd,
                env=env,
                text=True,
                encoding="utf-8",
                errors="strict",
                input=stdin_prompt,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
            exit_code = proc.returncode
            stdout, stderr = proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            exit_code = 124
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = (exc.stderr if isinstance(exc.stderr, str) else "") + f"\nTimed out after {timeout}s"
        final_text, usage = parse_output(provider, stdout, stderr, output_file)
    after = git_fingerprint(cwd) if mode != "writer" else None
    changed = bool(mode != "writer" and before is not None and after is not None and before != after)
    hook = hook_post_agent(
        task_id=task_id, role=role, mode=mode, cwd=cwd, provider=provider,
        exit_code=exit_code, read_only_changed=changed,
    )
    exit_code = int(hook.get("exit_code", exit_code))
    return {
        "exit_code": exit_code,
        "provider": provider,
        "executable": executable,
        "model": model,
        "reasoning_effort": effort,
        "final_text": final_text,
        "usage": usage,
        "stderr_tail": stderr[-4000:] if stderr else "",
        "removed_env": removed,
        "integrity": {"mode": mode, "changed": changed, "before": before, "after": after},
        "hook_bus": hook,
        "runtime_capabilities": capability_probe,
    }
