# ubiquitous-parakeet

A local-first prototype foundation for the companion. The current stage is a laptop/Pi-friendly development harness: Python core, private SQLite workspace, deterministic presence/cooldown gate, safe mock HAL, optional GPIO/Piper adapters, runtime-selected audio-device discovery and local audio smoke tests, and an optional stdio MCP server.

**There is no cloud voice pipeline or automatic model invocation in this repository yet.** Audio device checks run locally, record only in memory, and do not send or save audio. The gate can be tested with a fake callback and has a helper that refuses to invoke that callback when presence/cooldown rules deny it. A Hermes plugin uses its documented `pre_llm_call` block hook, alongside the stdio MCP tools. Verify the plugin is loaded on the exact Hermes release before deployment: Hermes documents hook failures as isolated/fail-open, so this is not yet a hard system-wide firewall.

## Start on Arch Linux

```bash
./scripts/dev-setup.sh
source .venv/bin/activate
parakeet init --workspace .local/workspace
python -m unittest discover -s tests -v
```

The local workspace and SQLite database are git-ignored. See [Development setup](docs/DEVELOPMENT.md), [Hermes integration](docs/HERMES_INTEGRATION.md), and [safety notes](docs/SAFETY.md).

The final product name has not been selected; nothing here assumes a cloud provider, GPIO pin, or caregiver-specific data.
