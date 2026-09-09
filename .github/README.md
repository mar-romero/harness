# GitHub and Copilot adapter

This folder integrates the harness with GitHub and Copilot.

- `agents/`: generated Copilot role adapters.
- `copilot-instructions.md`: Copilot-specific instructions.
- `mcp.json`: ACI MCP configuration.
- `workflows/`: CI automation, including harness checks.
- `README.md`: explains this integration boundary.

Workflows must validate the harness without storing secrets.
