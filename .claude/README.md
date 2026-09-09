# Claude adapter

This folder adapts the canonical harness to Claude.

- `agents/`: generated Claude role adapters.
- `skills/`: generated wrappers pointing to canonical skill bodies.
- `hooks/`: provider bridge for pre-command and pre-write gates.
- `settings.json`: Claude hook and permission configuration.
- `README.md`: explains this adapter boundary.

Edit `.agents/` or `harness/manifest.yaml`, then regenerate adapters.
