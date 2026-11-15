# NousResearch Hermes Agent integration

This adapter targets the **NousResearch Hermes Agent** public native-plugin and MCP interfaces: `mcp_servers` in `$HERMES_HOME/config.yaml`, and a native `plugin.yaml` + `register(ctx)` Python plugin using the documented `pre_llm_call` directive hook ([MCP](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp), [hooks](https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks), [plugin guide](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins)). The standalone stdio server and local pre-inference gate are separate pieces: the MCP tools provide visibility/logging; the hook is what asks the deterministic gate before a Hermes turn enters inference.

The Hermes plugin files are in `hermes_plugin/`. It invokes the installed `parakeet` CLI in a bounded subprocess. Eligible proactive triggers atomically reserve the cooldown in SQLite before inference, so overlapping triggers cannot all pass and a provider failure does not cause an immediate retry loop. An absent person blocks proactive calls; sensor/CLI errors, malformed gate output, or subprocess timeouts fail closed. Direct user-initiated/reactive turns skip presence sensing and the proactive cooldown. No provider client or credential is used by this repo.

## Laptop setup and isolated Hermes home

The agent workspace and Hermes' own configuration/persona have different discovery rules. Hermes reads `SOUL.md` only from `$HERMES_HOME/SOUL.md`; set its working directory to the companion workspace so it loads that workspace's `AGENTS.md`. The agent is instructed there to consult `LIFE_STORY.md` and `TODAY_LOG.md` when relevant; Hermes does not auto-load those filenames.

For safe testing, use a dedicated Hermes home so the gate hook cannot affect your normal Hermes sessions:

```bash
./scripts/dev-setup.sh --mcp
source .venv/bin/activate
# Only needed for real UART/GPIO tests, not for laptop mock mode:
python -m pip install -e '.[hardware]'
export HERMES_HOME="$HOME/.hermes-buddy-dev"
parakeet init --workspace "$HOME/.hermes/workspaces/alzheimers_buddy"
./scripts/install-hermes-plugin.sh
hermes plugins doctor "$HERMES_HOME/plugins/ubiquitous-parakeet" --ci
hermes plugins enable ubiquitous-parakeet
```

The current Hermes binary is not bundled here. Run `hermes plugins doctor` with the Hermes Agent installation you plan to use. The installer refuses to overwrite an existing plugin, and it never edits `config.yaml` or overwrites an existing `SOUL.md`.

Merge this into the active `$HERMES_HOME/config.yaml` (do not replace the rest of your settings):

```yaml
terminal:
  cwd: /home/youruser/.hermes/workspaces/alzheimers_buddy

mcp_servers:
  ubiquitous-parakeet:
    command: /absolute/path/to/ubiquitous-parakeet/.venv/bin/parakeet-mcp
    args: [--workspace, /home/youruser/.hermes/workspaces/alzheimers_buddy]
    env:
      PARAKEET_HARDWARE: mock
      PARAKEET_PRESENCE_INTERFACE: uart
      PARAKEET_LD2410_PORT: /dev/serial0
      PARAKEET_LD2410_BAUD: "256000"
      PARAKEET_LD2410_TIMEOUT: "1.0"
    timeout: 10
    connect_timeout: 5
    supports_parallel_tool_calls: false

plugins:
  enabled:
    - ubiquitous-parakeet
  entries:
    ubiquitous-parakeet:
      settings:
        command: /absolute/path/to/ubiquitous-parakeet/.venv/bin/parakeet
        workspace: /home/youruser/.hermes/workspaces/alzheimers_buddy
        hardware_mode: mock
        mock_present: false
        uart_port: /dev/serial0
        uart_baud: 256000
        sensor_timeout: 1.0
        timeout_seconds: 3
```

`examples/hermes-mcp.yaml` holds the MCP stanza by itself. Set `terminal.cwd` to the same workspace. Review `SOUL.md` first, then merge it manually into `$HERMES_HOME/SOUL.md`; keep any existing user persona rules. Do not assume the workspace's `SOUL.md` is automatically picked up.

## Deterministic smoke checks

On a laptop, keep `hardware_mode: mock` and leave the mock absent by default. A deliberate mock-presence test is available from the CLI:

```bash
parakeet gate --workspace "$HOME/.hermes/workspaces/alzheimers_buddy" \
  --trigger proactive --presence present
```

That command only prints an eligibility decision; it does not call Hermes or a cloud model. To test the Hermes hook itself, use the isolated `HERMES_HOME`, confirm the plugin is enabled, run `hermes plugins doctor ... --ci`, and inspect the gate result before testing any live provider. In the isolated plugin settings only, temporarily set `mock_present: true` to test an allowed turn, then restore it to `false`; never use this override in a deployment. Never use a caregiver's real story or an API key for this test.

Scheduled/proactive turns must start their prompt with the configured marker `[PARAKEET:PROACTIVE]`. Unmarked turns are treated as user-initiated/reactive and bypass the presence detector and proactive cooldown; the wakeword/user action is the authorization to respond. Only proactive check-ins require a positive sensor reading and the cooldown check. Do not create a cron schedule until its timing, recipient, and caregiver consent are decided. If a proactive check is denied, Hermes ends the turn before inference and shows the local reason.

## Raspberry Pi UART setup (LD2410)

The selected implementation reads the LD2410's basic UART report stream without sending configuration commands. Install both MCP and UART support into the project venv on the Pi:

```bash
python -m pip install -e '.[mcp,hardware]'
```

Confirm the exact LD2410 board revision and its UART pin labels. The usual report baud is 256000, 8 data bits, no parity, one stop bit; the software default port is `/dev/serial0`. Raspberry Pi OS must have the UART enabled and the serial console disabled on that UART. Verify which hardware UART backs `serial0` on your Pi and that it supports 256000 baud. Connect sensor TX to Pi RX and common ground; the sensor's supply may be 5 V, but only connect a verified 3.3 V UART logic signal to Pi GPIO—never put 5 V on a Pi GPIO. Follow the board's own wiring specification before connecting it.

For the deployment config, change `PARAKEET_HARDWARE` / `hardware_mode` from `mock` to `real` and set the correct `PARAKEET_LD2410_PORT`. The code deliberately does not fall back to mock when a real UART is missing or unreadable: the hook blocks the turn. Check access to the selected serial device (for example, the `dialout` group) and test `parakeet hardware-status --hardware-mode real --sensor-interface uart` locally before enabling Hermes.

## Limits that matter

- The MCP `check_in_eligibility` tool is not a firewall; the Hermes plugin hook is the pre-inference gate.
- Hermes documents `pre_llm_call` as a directive hook before inference, but plugin-hook exceptions/timeouts are generally isolated by Hermes. This callback catches errors, returns a block on failures, and bounds its subprocess to 3 seconds; still verify plugin loading and the specific Hermes release with `hermes plugins doctor` before deployment. Hermes may also make provider metadata probes (for example, model-list/context-length checks) before this hook; therefore this integration blocks prompt-bearing inference when denied, but does **not** guarantee zero outbound network traffic. A strict zero-network state requires putting all provider access behind a separate trusted wrapper/firewall that is not implemented here.
- The hook runs once per user turn, not before every internal tool-loop provider request. It checks presence at the start of proactive turns only; it is not continuous monitoring.
- No Hermes release was specified. The code uses the currently documented stable plugin/MCP contracts, but compatibility must be checked against the actual Pi-installed Hermes release before unattended use.
- No routine, medication schedule, caregiver recipient, model provider, or LLM credentials are configured. Those are intentionally left for a later informed decision.
