# Codex adapter

This folder contains the Codex integration and generated role bindings.

- `config.toml`: Codex runtime, MCP, concurrency, and permission settings.
- `hooks.json`: session, context, shell, write, and delegation hooks.
- `aci_mcp_entry.py`: Codex entry point for the ACI MCP server.
- `agents/`: generated TOML role adapters and model settings.
- `README.md`: explains this adapter boundary.

Edit canonical sources and run `scripts/compile_harness.py`.
