---
name: portable-skill-authoring
description: Create or modify Agent Skills that remain portable across supported providers and use progressive disclosure.
---

# portable-skill-authoring

Keep canonical skills under `.agents/skills/<name>/SKILL.md`. Frontmatter must include lowercase `name` and a specific `description`. Keep the body concise; move detailed references or scripts into the skill directory and load them only when necessary. Do not duplicate canonical skill bodies into provider directories unless a provider requires a minimal compatibility wrapper.
