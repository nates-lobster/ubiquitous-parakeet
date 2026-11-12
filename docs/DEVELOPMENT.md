# Laptop development environment (Arch Linux)

## What this sets up

- A project-local `.venv` (no system Python packages are modified).
- The Python package and test dependencies.
- A disposable development workspace at `.local/workspace/`, including the Markdown templates, plugins directory, and `data/companion.db`.
- A mock sensor on non-Raspberry-Pi systems. It defaults to **absent**, so test runs cannot accidentally start proactive behavior.

No cloud credentials, audio models, GPIO permissions, or hardware are needed for the laptop workflow.

## Setup

From the repository root:

```bash
./scripts/dev-setup.sh
source .venv/bin/activate
```

For the optional MCP server as well:

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

# Explicit presence inputs make gating tests deterministic; these never call a model.
parakeet gate --workspace .local/workspace --trigger proactive --presence absent
parakeet gate --workspace .local/workspace --trigger proactive --presence present
parakeet gate --workspace .local/workspace --trigger reactive --presence present
python -m unittest discover -s tests -v
```

A denied gate exits with status 2 and prints a JSON reason. Reactive speech bypasses the proactive cooldown, but not an absent-presence result. The default cooldown is 45 minutes. Only a successfully recorded **proactive** interaction starts that cooldown.

## Optional laptop hardware/audio work

`hardware-status` in `auto` mode chooses the mock except on a detected Raspberry Pi. You can force `--hardware-mode mock` on any machine. Real GPIO requires the optional extra, a confirmed sensor output pin, and explicit configuration:

```bash
.venv/bin/python -m pip install -e '.[hardware]'
export PARAKEET_HARDWARE=real
export PARAKEET_PRESENCE_GPIO='<confirmed BCM pin>'
parakeet hardware-status
```

Do not guess the LD2410 pinout, output polarity, or voltage. Raspberry Pi GPIO is 3.3 V logic; confirm the specific sensor board's OUT signal is electrically safe before connecting it. The current HAL reads a **digital presence OUT signal** through gpiozero; it does not yet parse LD2410 UART telemetry.

Piper is also opt-in: install Piper separately, obtain a model through your preferred source, and configure its model path yourself. Models/audio are ignored by Git. The adapter does not download or play anything unless explicitly called.

## Laptop-to-Pi workflow

Develop and run the test suite on x86_64 first. On a Pi, install the package in its own venv from a clone/sync, install `.[hardware]`, and pass the deployed workspace path to `parakeet-mcp`. Do not sync `.local/workspace/data/companion.db`, caregiver-filled `LIFE_STORY.md`, logs, `.env`, or model files to Git. Backups and retention should be chosen by the caregiver; this prototype does not automatically upload or purge personal data.
