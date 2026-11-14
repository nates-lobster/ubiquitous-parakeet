# Laptop development environment (Arch Linux)

## What this sets up

- A project-local `.venv` (no system Python packages are modified).
- The Python package and test dependencies.
- A disposable workspace at `.local/workspace/` with `SOUL.md`, `LIFE_STORY.md`, `TODAY_LOG.md`, `AGENTS.md`, plugins, and `data/companion.db`.
- A mock sensor on non-Raspberry-Pi systems. It defaults to **absent**, so tests cannot accidentally start proactive behavior.

No cloud credentials, audio models, GPIO permissions, or hardware are needed for laptop-only tests.

## Setup

From the repository root:

```bash
./scripts/dev-setup.sh
source .venv/bin/activate
```

For the optional Hermes MCP server too:

```bash
./scripts/dev-setup.sh --mcp
```

To recreate only the workspace later (existing files are preserved):

```bash
.venv/bin/parakeet init --workspace .local/workspace
```

Useful local-only checks:

```bash
# Mock sensor (defaults to absent)
parakeet hardware-status
parakeet hardware-status --hardware-mode mock --mock-present

# Explicit presence inputs make gate tests deterministic; these never call a model.
parakeet gate --workspace .local/workspace --trigger proactive --presence absent
parakeet gate --workspace .local/workspace --trigger proactive --presence present
parakeet gate --workspace .local/workspace --trigger reactive --presence present
python -m unittest discover -s tests -v
```

A denied gate exits with status 2 and prints a JSON reason. Reactive speech bypasses the proactive cooldown, but not an absent-presence result. The default cooldown is 45 minutes. The Hermes hook uses `--reserve-proactive` to atomically mark an eligible attempt before inference, so parallel scheduled triggers cannot all pass and provider failures do not immediately retrigger.

## Optional local hardware

`hardware-status` in `auto` mode chooses the mock except on a detected Raspberry Pi. You can force `--hardware-mode mock` on any machine. The selected Pi integration is the LD2410's read-only UART report stream, with no configuration writes:

```bash
.venv/bin/python -m pip install -e '.[hardware]'
export PARAKEET_HARDWARE=real
export PARAKEET_PRESENCE_INTERFACE=uart
export PARAKEET_LD2410_PORT=/dev/serial0
export PARAKEET_LD2410_BAUD=256000
parakeet hardware-status --hardware-mode real --sensor-interface uart
```

The driver validates report framing and times out as an error rather than interpreting missing data as presence. For one setup run including MCP and hardware extras, use `./scripts/dev-setup.sh --mcp --hardware`. Confirm the exact LD2410 board revision, 256000-baud support on the selected Pi UART, Pi OS UART configuration, and serial-device permissions before testing. Common wiring is sensor TX to Pi RX plus common ground; verify the board's IO voltage and never connect 5 V logic to a Pi GPIO. See [Hermes integration](HERMES_INTEGRATION.md) before enabling the agent hook on real hardware.

The HAL retains a generic gpiozero digital-OUT option for alternate sensor wiring; it is not the selected LD2410 path. That path requires a confirmed BCM pin and logic polarity:

```bash
export PARAKEET_HARDWARE=real
export PARAKEET_PRESENCE_INTERFACE=gpio
export PARAKEET_PRESENCE_GPIO='<confirmed BCM pin>'
parakeet hardware-status --hardware-mode real --sensor-interface gpio
```

## Audio devices (first peripheral milestone)

Audio device discovery/capture/playback is runtime-selected rather than tied to a particular microphone, speaker, USB port, jack, or ALSA card number. The default behavior follows the OS's current input/output devices. You can explicitly select a discovered device by index or unique name for a test; indices can change when devices are added or removed.

On Raspberry Pi OS, install PortAudio and the project's optional audio extra:

```bash
sudo apt install libportaudio2
./scripts/dev-setup.sh --audio
.venv/bin/parakeet audio devices
.venv/bin/parakeet audio test
```

`audio test` records a short sample in memory and plays it back through the selected/default output. It does not save or transmit the recording. To check capture only, use `--no-playback`. If multiple devices are available and no OS default is set, select one explicitly, for example:

```bash
.venv/bin/parakeet audio test --input-device "USB" --output-device "Headphones"
```

Device discovery and this smoke test are local-only. They do not yet start wakeword detection, VAD, cloud STT, Hermes, or TTS-provider synthesis. The next audio layer should retain the same device abstraction while adding those provider adapters. Piper remains separately opt-in: install Piper and obtain a model through your preferred source; the existing adapter neither downloads nor plays anything unless explicitly called. Models/audio are ignored by Git.

## Laptop-to-Pi workflow

Develop and run the test suite on x86_64 first. On the Pi, install the package in a dedicated venv from a clone/sync, then install `.[mcp,hardware]` and point Hermes at that venv. Do not sync `.local/workspace/data/companion.db`, caregiver-filled `LIFE_STORY.md`, logs, `.env`, or model files to Git. Backups and retention should be chosen by the caregiver; this prototype does not automatically upload or purge personal data.
