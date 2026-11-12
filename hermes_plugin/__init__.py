"""NousResearch Hermes Agent plugin: invoke the local gate before inference."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _block(message: str) -> dict[str, str]:
    return {"action": "block", "message": message}


def _make_gate_hook(ctx: Any):
    command = str(ctx.get_config("command", default="parakeet"))
    workspace = str(
        Path(str(ctx.get_config("workspace", default="~/.hermes/workspaces/alzheimers_buddy")))
        .expanduser()
    )
    hardware_mode = str(ctx.get_config("hardware_mode", default="auto"))
    mock_present = bool(ctx.get_config("mock_present", default=False))
    marker = str(ctx.get_config("trigger_marker", default="[PARAKEET:PROACTIVE]")).strip()
    uart_port = str(ctx.get_config("uart_port", default="/dev/serial0"))
    uart_baud = str(ctx.get_config("uart_baud", default=256000))
    sensor_timeout = str(ctx.get_config("sensor_timeout", default=1.0))
    timeout_seconds = int(ctx.get_config("timeout_seconds", default=3))
    if timeout_seconds < 1 or timeout_seconds > 10:
        timeout_seconds = 3

    def gate_before_inference(user_message: str = "", **kwargs: Any) -> dict[str, str] | None:
        """Block before LLM inference whenever local eligibility is false/unknown."""
        message = str(user_message or "")
        trigger_type = "proactive" if marker and message.lstrip().startswith(marker) else "reactive"
        # Pass only basic process configuration plus the sensor settings, not
        # Hermes/provider credentials or unrelated inherited secrets.
        environment = {
            key: os.environ[key]
            for key in ("HOME", "PATH", "LANG", "LC_ALL", "LC_CTYPE")
            if key in os.environ
        }
        environment["PARAKEET_PRESENCE_INTERFACE"] = "uart"
        environment["PARAKEET_LD2410_PORT"] = uart_port
        environment["PARAKEET_LD2410_BAUD"] = uart_baud
        environment["PARAKEET_LD2410_TIMEOUT"] = sensor_timeout
        argv = [
            command,
            "gate",
            "--workspace",
            workspace,
            "--trigger",
            trigger_type,
            "--presence",
            "auto",
            "--hardware-mode",
            hardware_mode,
        ]
        if trigger_type == "proactive":
            argv.append("--reserve-proactive")
        if mock_present:
            argv.append("--mock-present")
        try:
            result = subprocess.run(
                argv,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env=environment,
            )
        except subprocess.TimeoutExpired:
            return _block("Local presence check timed out; no prompt inference request was made.")
        except Exception as error:
            logger.error("Local companion gate could not run: %s", error)
            return _block("Local presence check failed; no prompt inference request was made.")

        try:
            decision = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError):
            logger.error("Local companion gate returned invalid output (exit %s)", result.returncode)
            return _block("Local presence check was unavailable; no prompt inference request was made.")

        if result.returncode == 0 and decision.get("allowed") is True:
            return None
        reason = str(decision.get("reason", "gate_unavailable"))
        if reason == "cooldown":
            remaining = int(decision.get("cooldown_remaining_seconds", 0))
            return _block(
                f"A proactive check-in is in its local cooldown ({remaining} seconds remain); "
                "no prompt inference request was made."
            )
        return _block(f"Local companion gate denied this turn ({reason}); no prompt inference request was made.")

    return gate_before_inference


def register(ctx: Any) -> None:
    """Register the documented Hermes pre_llm_call directive hook."""
    ctx.register_hook("pre_llm_call", _make_gate_hook(ctx))
