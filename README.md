# ubiquitous-parakeet

A local-first prototype foundation for the companion. The current stage is a laptop/Pi-friendly development harness: Python core, private SQLite workspace, deterministic presence/cooldown gate, safe mock HAL, optional GPIO/Piper adapters, runtime-selected audio-device discovery and local audio smoke tests, and an optional stdio MCP server.

**Live voice adapters are implemented but need API configuration and a Pi test.** The `parakeet voice run` path connects local openWakeWord and Silero VAD to Deepgram Flux, the configured Hermes CLI, and selectable Piper/Cartesia/Google TTS. `parakeet voice demo` remains a fake-provider test with a simulated thinking pause; it uses no microphone, network, or real speaker. Audio device checks run locally and the short recording is only held in memory. The gate can be tested with a fake callback and refuses to invoke that callback when presence/cooldown rules deny it. A Hermes plugin uses its documented `pre_llm_call` block hook, alongside the stdio MCP tools. Verify the plugin is loaded on the exact Hermes release before deployment: Hermes documents hook failures as isolated/fail-open, so this is not yet a hard system-wide firewall.

## Start on Arch Linux

```bash
./scripts/dev-setup.sh
source .venv/bin/activate
parakeet init --workspace .local/workspace
python -m unittest discover -s tests -v
```

The local workspace and SQLite database are git-ignored. See [Development setup](docs/DEVELOPMENT.md), [Hermes integration](docs/HERMES_INTEGRATION.md), and [safety notes](docs/SAFETY.md).

The final product name has not been selected; nothing here assumes a cloud provider, GPIO pin, or caregiver-specific data.
