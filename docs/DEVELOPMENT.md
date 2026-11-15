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
parakeet gate --workspace .local/workspace --trigger reactive --presence absent
python -m unittest discover -s tests -v
```

A denied gate exits with status 2 and prints a JSON reason. Reactive, user-initiated turns bypass the presence detector and proactive cooldown; the detector is only consulted for proactive check-ins. The default cooldown is 45 minutes. The Hermes hook uses `--reserve-proactive` to atomically mark an eligible attempt before inference, so parallel scheduled triggers cannot all pass and provider failures do not immediately retrigger.

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

Device discovery and the smoke test are local-only. The provider-neutral voice orchestrator also has a full simulated turn with fake providers:

```bash
.venv/bin/parakeet voice demo --pause-seconds 12
```

That demo fast-forwards the simulated pause; it does not wait 12 real seconds. It proves that after VAD sees speech, silence is passed onward so STT—not VAD—ends the turn. It uses no microphone, speaker, credentials, or network.

### Live voice loop

The real adapters are now implemented. Install PortAudio plus the project's audio/voice/model extras:

```bash
sudo apt install libportaudio2
.venv/bin/python -m pip install -e '.[dev,mcp,hardware,audio,voice,wakeword,vad]'
# The adapters force CPU ONNX inference; install the unused TFLite runtime only if a wheel exists.
.venv/bin/python -m pip install --no-deps 'openwakeword>=0.6,<1'
.venv/bin/python -m pip install 'tflite-runtime>=2.8,<3' || echo 'No TFLite wheel for this Pi/Python; ONNX inference remains enabled.'
```

The voice loop reads `~/.hermes/.env` (without overriding already-exported variables) and uses Hermes' existing `~/.hermes/config.yaml` through the installed `hermes` CLI. Set a wakeword model and choose a TTS provider. Example environment settings:

```bash
export PARAKEET_WAKEWORD_MODEL='<openWakeWord model name or local model path>'
export DEEPGRAM_API_KEY='<Deepgram key>'
export PARAKEET_TTS_PROVIDER='cartesia'  # piper | cartesia | google
export CARTESIA_API_KEY='<Cartesia key>'
export PARAKEET_CARTESIA_VOICE='<Cartesia voice ID>'
```

For Piper, set `PARAKEET_TTS_PROVIDER=piper` and `PARAKEET_PIPER_MODEL` to a model already installed locally. For Google TTS, set the provider to `google`, `GOOGLE_TTS_API_KEY`, and optionally `PARAKEET_GOOGLE_TTS_VOICE`. Input/output devices default to the OS-selected devices; optional `PARAKEET_AUDIO_INPUT` and `PARAKEET_AUDIO_OUTPUT` values select a discovered device by name or index. `PARAKEET_VAD_MODEL` can point to an alternate local Silero-compatible ONNX model. No device identity is baked into the runtime.

Start the live loop with:

```bash
.venv/bin/parakeet voice run
```

You can override settings per run: `--wakeword-model`, `--input-device`, `--output-device`, `--tts-provider`, `--eot-timeout-ms`, `--eot-threshold`, and `--conversation-idle-timeout-seconds`. A wakeword is required only to start a conversation. After each reply, capture resumes and the companion listens for the next user turn without another wakeword. Say `goodbye`, `that's all`, `I'm done`, or `stop listening` to end explicitly; otherwise the session closes after 120 seconds without new speech by default. Configure `PARAKEET_CONVERSATION_IDLE_TIMEOUT_SECONDS` or the CLI option from 15 to 900 seconds. On a conversation timeout/sign-off, the next turn requires the wakeword and in-memory Hermes turn history is cleared. Flux defaults to a 0.85 end-of-turn threshold and a 30-second silence timeout; the timeout is configurable up to 60 seconds. VAD gates the start of each STT stream, then the full audio—including pauses—is forwarded to Flux. The terminal reports elapsed progress while Flux finishes the turn, Hermes thinks, TTS prepares the reply, and audio plays; recoverable turn failures reset the audio stream and report the error instead of silently hanging. These UI-neutral status events are also the hook for a future screen. Capture is paused while Hermes and TTS respond to avoid microphone queue overflow; barge-in during a spoken response is not implemented yet. The voice loop is continuous until Ctrl+C. No audio is written to disk by this runtime, but the post-onset audio is streamed to Deepgram for transcription/turn detection.

**Pi-specific installation note:** openWakeWord's PyPI wheel can fail on some ARM64/Python combinations because of the optional TFLite runtime wheel. If `.[wakeword]` fails, try installing ONNX Runtime and the remaining openWakeWord runtime dependencies, then install its source without optional TFLite dependencies:

```bash
.venv/bin/python -m pip install onnxruntime scipy scikit-learn tqdm requests
.venv/bin/python -m pip install --no-deps 'openwakeword @ git+https://github.com/dscripka/openWakeWord.git'
```

The first use of a built-in openWakeWord model (for example `hey_jarvis`) downloads its ONNX model and shared features from the project's GitHub release into the openWakeWord package's model directory. This requires outbound access to GitHub and a writable project virtualenv. Alternatively, configure `PARAKEET_WAKEWORD_MODEL` to a local ONNX wakeword model path. The actual wakeword phrase/model, Deepgram account, Hermes provider credentials, and chosen TTS voice still need to be configured on the Pi. Keep credentials in `~/.hermes/.env` or the process environment, never in Git. Piper remains local and opt-in; models/audio are ignored by Git.

## Laptop-to-Pi workflow

Develop and run the test suite on x86_64 first. On the Pi, install the package in a dedicated venv from a clone/sync, then install `.[mcp,hardware]` and point Hermes at that venv. Do not sync `.local/workspace/data/companion.db`, caregiver-filled `LIFE_STORY.md`, logs, `.env`, or model files to Git. Backups and retention should be chosen by the caregiver; this prototype does not automatically upload or purge personal data.
