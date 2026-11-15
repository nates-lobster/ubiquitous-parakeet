"""Real-device runtime assembly for the provider-neutral voice loop."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import sys
import threading
from typing import Any

from .audio_io import AudioFrame, AudioInputStream, AudioPlayback, resolve_audio_device
from .voice import PcmAudio, VoiceLoop, VoicePipelineError, VoiceStatusCallback, VoiceTimeout


@dataclass(frozen=True)
class VoiceSettings:
    wakeword_model: str
    wakeword_threshold: float = 0.55
    vad_threshold: float = 0.5
    vad_model: str | None = None
    input_device: str | None = None
    output_device: str | None = None
    tts_provider: str = "piper"
    piper_model: str | None = None
    cartesia_api_key: str | None = None
    cartesia_voice: str | None = None
    cartesia_model: str = "sonic-3.6"
    google_api_key: str | None = None
    google_voice: str = "en-US-Standard-C"
    deepgram_api_key: str | None = None
    flux_eot_threshold: float = 0.85
    flux_eot_timeout_ms: int = 30000
    conversation_idle_timeout_seconds: float = 120.0
    hermes_executable: str = "hermes"

    @classmethod
    def from_environment(
        cls,
        *,
        wakeword_model: str | None = None,
        input_device: str | None = None,
        output_device: str | None = None,
        tts_provider: str | None = None,
        eot_threshold: float | None = None,
        eot_timeout_ms: int | None = None,
        conversation_idle_timeout_seconds: float | None = None,
    ) -> "VoiceSettings":
        try:
            from dotenv import load_dotenv

            load_dotenv(Path.home() / ".hermes" / ".env", override=False)
        except ImportError:
            pass
        model = wakeword_model or os.environ.get("PARAKEET_WAKEWORD_MODEL")
        if not model:
            raise ValueError(
                "Choose a wakeword model/path with --wakeword-model or "
                "PARAKEET_WAKEWORD_MODEL; no phrase is hard-coded."
            )
        try:
            threshold = float(os.environ.get("PARAKEET_WAKEWORD_THRESHOLD", "0.55"))
            vad_threshold = float(os.environ.get("PARAKEET_VAD_THRESHOLD", "0.5"))
            flux_threshold = (
                eot_threshold
                if eot_threshold is not None
                else float(os.environ.get("PARAKEET_FLUX_EOT_THRESHOLD", "0.85"))
            )
            flux_timeout = (
                eot_timeout_ms
                if eot_timeout_ms is not None
                else int(os.environ.get("PARAKEET_FLUX_EOT_TIMEOUT_MS", "30000"))
            )
            conversation_timeout = (
                conversation_idle_timeout_seconds
                if conversation_idle_timeout_seconds is not None
                else float(os.environ.get("PARAKEET_CONVERSATION_IDLE_TIMEOUT_SECONDS", "120"))
            )
        except ValueError as error:
            raise ValueError("Voice thresholds/timeouts must be valid numbers") from error
        if not 0.0 < threshold <= 1.0 or not 0.0 < vad_threshold <= 1.0:
            raise ValueError("Wakeword and VAD thresholds must be between 0 and 1")
        if not 0.5 <= flux_threshold <= 1.0:
            raise ValueError("Flux end-of-turn threshold must be between 0.5 and 1.0")
        if not 500 <= flux_timeout <= 60000:
            raise ValueError("Flux end-of-turn timeout must be between 500 and 60000 ms")
        if not 15 <= conversation_timeout <= 900:
            raise ValueError("Conversation idle timeout must be between 15 and 900 seconds")

        provider = (tts_provider or os.environ.get("PARAKEET_TTS_PROVIDER", "piper")).lower()
        if provider not in {"piper", "cartesia", "google"}:
            raise ValueError("TTS provider must be piper, cartesia, or google")
        return cls(
            wakeword_model=model,
            wakeword_threshold=threshold,
            vad_threshold=vad_threshold,
            vad_model=os.environ.get("PARAKEET_VAD_MODEL"),
            input_device=input_device or os.environ.get("PARAKEET_AUDIO_INPUT") or None,
            output_device=output_device or os.environ.get("PARAKEET_AUDIO_OUTPUT") or None,
            tts_provider=provider,
            piper_model=os.environ.get("PARAKEET_PIPER_MODEL"),
            cartesia_api_key=os.environ.get("CARTESIA_API_KEY"),
            cartesia_voice=os.environ.get("PARAKEET_CARTESIA_VOICE"),
            cartesia_model=os.environ.get("PARAKEET_CARTESIA_MODEL", "sonic-3.6"),
            google_api_key=os.environ.get("GOOGLE_TTS_API_KEY"),
            google_voice=os.environ.get("PARAKEET_GOOGLE_TTS_VOICE", "en-US-Standard-C"),
            deepgram_api_key=os.environ.get("DEEPGRAM_API_KEY"),
            flux_eot_threshold=flux_threshold,
            flux_eot_timeout_ms=flux_timeout,
            conversation_idle_timeout_seconds=conversation_timeout,
            hermes_executable=os.environ.get("HERMES_EXECUTABLE", "hermes"),
        )


class NormalizedAudioReader:
    """Capture through the selected system input and emit 16 kHz mono, 80 ms PCM.

    Resampling is stateful to avoid chunk-boundary clicks and timing drift.
    Capture is paused while Hermes and TTS respond, then restarted before the
    next wakeword wait so a slow reply cannot overrun the microphone queue.
    """

    target_rate = 16000
    samples_per_frame = 1280
    bytes_per_frame = samples_per_frame * 2

    def __init__(self, source: AudioInputStream) -> None:
        self.source = source
        self._lock = threading.RLock()
        self._buffer = bytearray()
        self._buffer_timestamp: float | None = None
        self._input_rate: int | None = None
        self._resampler: Any = None

    def start(self) -> None:
        with self._lock:
            self._reset_conversion()
            self.source.start()

    def _reset_conversion(self) -> None:
        self._buffer.clear()
        self._buffer_timestamp = None
        self._input_rate = None
        self._resampler = None

    def _convert(self, frame: AudioFrame) -> bytes:
        if frame.channels != 1:
            raise RuntimeError(
                f"Voice input expects mono audio; selected stream supplied {frame.channels} channels."
            )
        if self._input_rate is None:
            self._input_rate = frame.sample_rate
            if self._input_rate != self.target_rate:
                try:
                    import numpy as np
                    import soxr
                except ImportError as error:
                    raise RuntimeError(
                        "Resampling a non-16-kHz microphone requires NumPy and SoXR. "
                        "Install the project 'voice' extra."
                    ) from error
                self._np = np
                self._resampler = soxr.ResampleStream(
                    self._input_rate,
                    self.target_rate,
                    1,
                    dtype="int16",
                    quality="HQ",
                )
        elif frame.sample_rate != self._input_rate:
            raise RuntimeError("Audio input sample rate changed during a voice session.")

        if self._resampler is None:
            return frame.pcm_s16le
        samples = self._np.frombuffer(frame.pcm_s16le, dtype="<i2")
        return self._resampler.resample_chunk(samples, last=False).astype("<i2", copy=False).tobytes()

    def _read_sync(self) -> AudioFrame:
        with self._lock:
            while len(self._buffer) < self.bytes_per_frame:
                incoming = self.source.read(timeout=1.0)
                converted = self._convert(incoming)
                if converted:
                    if not self._buffer:
                        self._buffer_timestamp = incoming.captured_at_monotonic
                    self._buffer.extend(converted)
            pcm = bytes(self._buffer[: self.bytes_per_frame])
            del self._buffer[: self.bytes_per_frame]
            timestamp = self._buffer_timestamp or 0.0
            if self._buffer_timestamp is not None:
                self._buffer_timestamp += self.samples_per_frame / self.target_rate
            return AudioFrame(
                pcm_s16le=pcm,
                sample_rate=self.target_rate,
                channels=1,
                captured_at_monotonic=timestamp,
            )

    async def read_frame(self) -> AudioFrame:
        return await asyncio.to_thread(self._read_sync)

    async def pause(self) -> None:
        def suspend() -> None:
            with self._lock:
                self.source.close()
                self._reset_conversion()

        await asyncio.to_thread(suspend)

    async def resume(self) -> None:
        await asyncio.to_thread(self.start)

    async def close(self) -> None:
        await self.pause()


class SoundDeviceOutput:
    """Route generated PCM to the current/default output device, without pinning it."""

    def __init__(self, device: str | None = None) -> None:
        self.device = device

    def _play_sync(self, audio: PcmAudio) -> None:
        selected = resolve_audio_device("output", self.device)
        output_rate = selected.default_sample_rate or audio.sample_rate
        pcm = audio.pcm_s16le
        if audio.sample_rate != output_rate:
            try:
                import numpy as np
                import soxr
            except ImportError as error:
                raise RuntimeError(
                    "Resampling TTS output requires NumPy and SoXR; install the project 'voice' extra."
                ) from error
            samples = np.frombuffer(pcm, dtype="<i2")
            pcm = soxr.resample(samples, audio.sample_rate, output_rate, quality="HQ").astype(
                "<i2", copy=False
            ).tobytes()
        with AudioPlayback(
            sample_rate=output_rate,
            device=selected.index,
            channels=audio.channels,
        ) as speaker:
            speaker.write(pcm)

    async def play(self, audio: PcmAudio) -> None:
        await asyncio.to_thread(self._play_sync, audio)


def build_voice_loop(
    settings: VoiceSettings,
    *,
    status_callback: VoiceStatusCallback | None = None,
) -> tuple[NormalizedAudioReader, VoiceLoop]:
    """Validate configuration and devices before loading/downloading provider models."""
    from .voice_adapters import (
        CartesiaTTS,
        DeepgramFluxSTT,
        GoogleCloudTTS,
        HermesCliAssistant,
        OpenWakeWordDetector,
        PiperTTS,
        SileroVAD,
    )

    if not settings.deepgram_api_key:
        raise ValueError("Set DEEPGRAM_API_KEY before starting the live voice loop.")
    selected_input = resolve_audio_device("input", settings.input_device)
    selected_output = resolve_audio_device("output", settings.output_device)

    if settings.tts_provider == "piper":
        if not settings.piper_model:
            raise ValueError(
                "Set PARAKEET_PIPER_MODEL to a local Piper .onnx model, or choose another TTS provider."
            )
        tts = PiperTTS(settings.piper_model)
    elif settings.tts_provider == "cartesia":
        if not settings.cartesia_api_key or not settings.cartesia_voice:
            raise ValueError(
                "Cartesia requires CARTESIA_API_KEY and PARAKEET_CARTESIA_VOICE."
            )
        tts = CartesiaTTS(
            api_key=settings.cartesia_api_key,
            voice=settings.cartesia_voice,
            model=settings.cartesia_model,
        )
    elif settings.tts_provider == "google":
        if not settings.google_api_key:
            raise ValueError("Google TTS requires GOOGLE_TTS_API_KEY.")
        tts = GoogleCloudTTS(
            api_key=settings.google_api_key,
            voice=settings.google_voice,
        )
    else:
        raise ValueError("TTS provider must be piper, cartesia, or google")

    assistant = HermesCliAssistant(executable=settings.hermes_executable)
    transcriber = DeepgramFluxSTT(
        api_key=settings.deepgram_api_key,
        eot_threshold=settings.flux_eot_threshold,
        eot_timeout_ms=settings.flux_eot_timeout_ms,
    )
    wakeword = OpenWakeWordDetector(
        settings.wakeword_model, threshold=settings.wakeword_threshold
    )
    vad = SileroVAD(threshold=settings.vad_threshold, model_path=settings.vad_model)

    capture = AudioInputStream(
        device=selected_input.index,
        channels=1,
        block_duration_ms=30,
    )
    reader = NormalizedAudioReader(capture)
    loop = VoiceLoop(
        wakeword=wakeword,
        vad=vad,
        transcriber=transcriber,
        assistant=assistant,
        tts=tts,
        output=SoundDeviceOutput(selected_output.index),
        sample_rate=reader.target_rate,
        pre_roll_frames=4,
        pause_input=reader.pause,
        resume_input=reader.resume,
        status_callback=status_callback,
    )
    return reader, loop


async def run_voice_service(
    settings: VoiceSettings,
    *,
    status_callback: VoiceStatusCallback | None = None,
) -> None:
    reader, loop = build_voice_loop(settings, status_callback=status_callback)
    reader.start()
    conversation_active = False
    print("Listening locally for the configured wakeword. Press Ctrl+C to stop.")
    try:
        while True:
            try:
                result = await loop.run_one_turn(
                    reader.read_frame,
                    require_wakeword=not conversation_active,
                    speech_start_timeout_seconds=(
                        settings.conversation_idle_timeout_seconds
                        if conversation_active
                        else 60.0
                    ),
                )
            except VoiceTimeout as error:
                if conversation_active:
                    print(
                        f"Conversation idle for {settings.conversation_idle_timeout_seconds:g}s; "
                        "say the wakeword to start again.",
                        file=sys.stderr,
                    )
                    conversation_active = False
                    reset = getattr(loop.assistant, "reset_conversation", None)
                    if reset is not None:
                        reset()
                else:
                    print(f"No speech after wakeword ({error}); listening again.", file=sys.stderr)
                await reader.pause()
                await reader.resume()
                continue
            except (VoicePipelineError, RuntimeError, OSError, ValueError) as error:
                print(
                    f"Voice turn failed: {error}. Resetting audio and listening again.",
                    file=sys.stderr,
                )
                await reader.pause()
                await reader.resume()
                continue
            print(f"Heard: {result.transcript}")
            print(f"Replied: {result.response}")
            if result.conversation_ended:
                conversation_active = False
                reset = getattr(loop.assistant, "reset_conversation", None)
                if reset is not None:
                    reset()
                print("Conversation ended; say the wakeword when you want to talk again.", file=sys.stderr)
            else:
                conversation_active = True
    finally:
        await reader.close()
