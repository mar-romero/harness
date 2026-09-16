#!/usr/bin/env python3
"""Compile the smallest authoritative canonical skill pack for one harness role.

The subscription bridge must not rely on provider-specific skill discovery. This
module loads canonical `.agents/skills/*/SKILL.md` content and includes only the
skills relevant to the routed role/task, under a fixed token/character budget.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from harnesslib import ROOT, load_manifest

POLICY_PATH = ROOT / "harness" / "skill-runtime-policy.json"
SKILLS_ROOT = ROOT / ".agents" / "skills"


def _policy() -> dict[str, Any]:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def _skill_text(name: str) -> str:
    path = SKILLS_ROOT / name / "SKILL.md"
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    # Keep the canonical body but strip YAML frontmatter metadata from runtime
    # prompts because role/selection metadata is already represented separately.
    if text.startswith("---"):
        match = re.match(r"^---\s*\n.*?\n---\s*\n?", text, re.S)
        if match:
            text = text[match.end():].lstrip()
    return text


def skill_names_for_role(role: str, routed: dict[str, Any]) -> list[str]:
    manifest = load_manifest()
    agent = (manifest.get("agents") or {}).get(role) or {}
    policy = _policy()
    declared = [str(x) for x in agent.get("skills", [])]
    routed_skills = {str(x) for x in routed.get("skills", [])}
    dynamic_allow = [str(x) for x in (policy.get("dynamic_role_skills", {}).get(role) or [])]
    dynamic = [name for name in dynamic_allow if name in routed_skills]
    always = [str(x) for x in policy.get("always_include", [])]
    return list(dict.fromkeys([*declared, *dynamic, *always]))


def compile_skill_pack(role: str, routed: dict[str, Any], *, max_chars: int | None = None) -> dict[str, Any]:
    policy = _policy()
    cap = int(max_chars or policy.get("max_skill_chars_per_agent", 16800))
    selected: list[str] = []
    omitted: list[str] = []
    sections: list[str] = []
    used = 0
    for name in skill_names_for_role(role, routed):
        body = _skill_text(name)
        if not body:
            omitted.append(name)
            continue
        section = f"## Skill: {name}\n{body}\n"
        if sections and used + len(section) > cap:
            omitted.append(name)
            continue
        # The first skill is still bounded. It is better to include a truncated
        # canonical skill than silently drop every skill when a future skill grows.
        if not sections and len(section) > cap:
            section = section[: max(0, cap - 64)].rstrip() + "\n[skill truncated by runtime budget]\n"
        sections.append(section)
        selected.append(name)
        used += len(section)
    text = "\n".join(sections).strip()
    return {
        "role": role,
        "skills": selected,
        "omitted": omitted,
        "text": text,
        "chars": len(text),
        "estimated_tokens": max(1, len(text) // 4) if text else 0,
        "budget_chars": cap,
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("role")
    ap.add_argument("route_json")
    args = ap.parse_args()
    routed = json.loads(Path(args.route_json).read_text(encoding="utf-8"))
    print(json.dumps(compile_skill_pack(args.role, routed), indent=2, ensure_ascii=False))
