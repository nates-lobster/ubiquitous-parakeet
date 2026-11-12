"""Explicit local Piper CLI adapter; never downloads models or plays audio implicitly."""

from __future__ import annotations

import asyncio
from pathlib import Path


async def synthesize(
    text: str,
    *,
    model: str | Path,
    output_file: str | Path,
    executable: str = "piper",
) -> Path:
    if not text.strip():
        raise ValueError("text must not be empty")
    model_path = Path(model).expanduser()
    output_path = Path(output_file).expanduser()
    if not model_path.is_file():
        raise FileNotFoundError(f"Piper model does not exist: {model_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    process = await asyncio.create_subprocess_exec(
        executable,
        "--model",
        str(model_path),
        "--output_file",
        str(output_path),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await process.communicate(text.encode("utf-8"))
    if process.returncode:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Piper exited with status {process.returncode}: {detail}")
    return output_path


async def speak(
    text: str,
    *,
    model: str | Path,
    output_file: str | Path,
    player: str = "aplay",
    executable: str = "piper",
) -> Path:
    """Synthesize, then explicitly play through a local ALSA command."""
    wav_path = await synthesize(
        text, model=model, output_file=output_file, executable=executable
    )
    process = await asyncio.create_subprocess_exec(
        player,
        str(wav_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await process.communicate()
    if process.returncode:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Audio player exited with status {process.returncode}: {detail}")
    return wav_path
