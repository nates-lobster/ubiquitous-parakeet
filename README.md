# ubiquitous-parakeet

A local-first prototype foundation for the **Memory-Buddy** ambient companion concept. The current stage is intentionally a laptop-friendly development harness: Python core, private SQLite workspace, deterministic presence/cooldown gate, safe mock HAL, optional GPIO/Piper adapters, and an optional stdio MCP server.

**There is no cloud client or automatic model invocation in this repository yet.** The gate can be tested with a fake callback and has a helper that refuses to invoke that callback when presence/cooldown rules deny it. A NousResearch Hermes plugin now uses its documented `pre_llm_call` block hook, alongside the stdio MCP tools. Verify the plugin is loaded on the exact Hermes release before deployment: Hermes documents hook failures as isolated/fail-open, so this is not yet a hard system-wide firewall.

## Start on Arch Linux

```bash
./scripts/dev-setup.sh
source .venv/bin/activate
parakeet init --workspace .local/workspace
python -m unittest discover -s tests -v
```

The local workspace and SQLite database are git-ignored. See [Development setup](docs/DEVELOPMENT.md), [Hermes integration](docs/HERMES_INTEGRATION.md), and [safety notes](docs/SAFETY.md).

The project is still codenamed `ubiquitous-parakeet`; nothing here assumes a final product name, cloud provider, GPIO pin, or caregiver data.
