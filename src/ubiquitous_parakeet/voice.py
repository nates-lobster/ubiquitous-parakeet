"""Provider-neutral orchestration for one wakeword-activated voice turn.

Audio frames passed into this layer must already be mono, signed 16-bit PCM at
``sample_rate``. Hardware capture, normalization, wakeword, VAD, transcription,
assistant, TTS, and playback are separate injectable adapters.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
import inspect
import time
from typing import AsyncIterator, Awaitable, Callable, Protocol, TypeVar

from .audio_io import AudioFrame


class WakeWordProvider(Protocol):
    """Local detector; ``detect`` must be fast and must not call a cloud API."""

    def detect(self, frame: AudioFrame) -> bool: ...


class VoiceActivityProvider(Protocol):
    """Speech/non-speech classifier. It does not decide when a turn has ended."""

    def is_speech(self, frame: AudioFrame) -> bool: ...

    def reset(self) -> None: ...


class StreamingTranscriptionProvider(Protocol):
    """Consumes audio through its own end-of-turn event and returns final text."""

    async def transcribe_turn(self, frames: AsyncIterator[AudioFrame]) -> str: ...


class AssistantProvider(Protocol):
    async def respond(self, text: str) -> str: ...


@dataclass(frozen=True)
class PcmAudio:
    pcm_s16le: bytes
    sample_rate: int
    channels: int = 1

    def __post_init__(self) -> None:
        if self.sample_rate < 1:
            raise ValueError("sample_rate must be positive")
        if self.channels < 1:
            raise ValueError("channels must be positive")
        if len(self.pcm_s16le) % (2 * self.channels):
            raise ValueError("PCM length is not aligned to the configured channel count")


class TextToSpeechProvider(Protocol):
    async def synthesize(self, text: str) -> PcmAudio: ...


class AudioOutputProvider(Protocol):
    async def play(self, audio: PcmAudio) -> None: ...


FrameReader = Callable[[], Awaitable[AudioFrame]]
T = TypeVar("T")


@dataclass(frozen=True)
class VoiceStatus:
    """UI-neutral voice-loop progress event for a terminal or future display."""

    phase: str
    elapsed_seconds: float = 0.0
    complete: bool = False
    failed: bool = False


VoiceStatusCallback = Callable[[VoiceStatus], Awaitable[None] | None]


class VoicePipelineError(RuntimeError):
    """A configured voice turn could not be completed."""


class VoiceTimeout(VoicePipelineError):
    """No wakeword or speech onset arrived before its configured timeout."""


@dataclass(frozen=True)
class VoiceTurnResult:
    transcript: str
    response: str
    audio: PcmAudio
    speech_audio_frames: int
    silence_audio_frames: int


class VoiceLoop:
    """Run wakeword → local speech gate → streaming STT → assistant → TTS → output.

    VAD only decides when to begin sending the post-wake audio to STT. Once
    speech begins, every subsequent frame—including silence—is forwarded so
    the transcription provider can make patient-friendly turn decisions.
    """

    def __init__(
        self,
        *,
        wakeword: WakeWordProvider,
        vad: VoiceActivityProvider,
        transcriber: StreamingTranscriptionProvider,
        assistant: AssistantProvider,
        tts: TextToSpeechProvider,
        output: AudioOutputProvider,
        sample_rate: int = 16000,
        pre_roll_frames: int = 4,
        pause_input: Callable[[], Awaitable[None]] | None = None,
        resume_input: Callable[[], Awaitable[None]] | None = None,
        status_callback: VoiceStatusCallback | None = None,
    ) -> None:
        if sample_rate < 1:
            raise ValueError("sample_rate must be positive")
        if pre_roll_frames < 0:
            raise ValueError("pre_roll_frames cannot be negative")
        if (pause_input is None) != (resume_input is None):
            raise ValueError("pause_input and resume_input must be supplied together")
        self.wakeword = wakeword
        self.vad = vad
        self.transcriber = transcriber
        self.assistant = assistant
        self.tts = tts
        self.output = output
        self.sample_rate = sample_rate
        self.pre_roll_frames = pre_roll_frames
        self.pause_input = pause_input
        self.resume_input = resume_input
        self.status_callback = status_callback

    async def _report_status(self, event: VoiceStatus) -> None:
        if self.status_callback is None:
            return
        try:
            result = self.status_callback(event)
            if inspect.isawaitable(result):
                await result
        except Exception:
            # A display/status sink must never break capture or a voice response.
            return

    async def _with_progress(self, phase: str, operation: Awaitable[T]) -> T:
        """Report elapsed progress once a second while a provider operation waits."""
        task = asyncio.ensure_future(operation)
        started = time.monotonic()
        await self._report_status(VoiceStatus(phase))
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=1.0)
                if not task.done():
                    await self._report_status(
                        VoiceStatus(phase, elapsed_seconds=time.monotonic() - started)
                    )
            result = await task
        except BaseException:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await self._report_status(
                VoiceStatus(
                    phase,
                    elapsed_seconds=time.monotonic() - started,
                    complete=True,
                    failed=True,
                )
            )
            raise
        await self._report_status(
            VoiceStatus(
                phase,
                elapsed_seconds=time.monotonic() - started,
                complete=True,
            )
        )
        return result

    def _validate_frame(self, frame: AudioFrame) -> None:
        if frame.sample_rate != self.sample_rate or frame.channels != 1:
            raise VoicePipelineError(
                "Voice providers require normalized mono audio at "
                f"{self.sample_rate} Hz; got {frame.sample_rate} Hz, {frame.channels} channel(s)."
            )
        if len(frame.pcm_s16le) % 2:
            raise VoicePipelineError("Input frame is not aligned 16-bit PCM.")

    async def _read_before_deadline(
        self, reader: FrameReader, deadline: float | None, phase: str
    ) -> AudioFrame:
        if deadline is None:
            return await reader()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise VoiceTimeout(f"Timed out waiting for {phase}.")
        try:
            return await asyncio.wait_for(reader(), timeout=remaining)
        except TimeoutError as error:
            raise VoiceTimeout(f"Timed out waiting for {phase}.") from error

    async def run_one_turn(
        self,
        read_frame: FrameReader,
        *,
        wake_timeout_seconds: float | None = None,
        speech_start_timeout_seconds: float = 30.0,
    ) -> VoiceTurnResult:
        """Wait for one wakeword and one patient-paced utterance, then answer it."""
        if wake_timeout_seconds is not None and wake_timeout_seconds <= 0:
            raise ValueError("wake_timeout_seconds must be positive or None")
        if speech_start_timeout_seconds <= 0:
            raise ValueError("speech_start_timeout_seconds must be positive")

        wake_deadline = (
            time.monotonic() + wake_timeout_seconds
            if wake_timeout_seconds is not None
            else None
        )
        await self._report_status(VoiceStatus("waiting_for_wakeword"))
        while True:
            frame = await self._read_before_deadline(read_frame, wake_deadline, "wakeword")
            self._validate_frame(frame)
            if self.wakeword.detect(frame):
                reset_wakeword = getattr(self.wakeword, "reset", None)
                if reset_wakeword is not None:
                    reset_wakeword()
                break

        await self._report_status(VoiceStatus("waiting_for_speech"))
        self.vad.reset()
        speech_deadline = time.monotonic() + speech_start_timeout_seconds
        preroll: deque[tuple[AudioFrame, bool]] = deque(
            maxlen=max(1, self.pre_roll_frames)
        )
        first_speech_frame: AudioFrame | None = None
        while first_speech_frame is None:
            frame = await self._read_before_deadline(
                read_frame, speech_deadline, "speech after wakeword"
            )
            self._validate_frame(frame)
            is_speech = self.vad.is_speech(frame)
            preroll.append((frame, is_speech))
            if is_speech:
                first_speech_frame = frame

        speech_frames = sum(1 for _, is_speech in preroll if is_speech)
        silence_frames = len(preroll) - speech_frames

        async def active_audio() -> AsyncIterator[AudioFrame]:
            nonlocal speech_frames, silence_frames
            # Include a short pre-roll so VAD onset latency does not clip a word.
            if self.pre_roll_frames:
                for buffered, _was_speech in preroll:
                    yield buffered
            else:
                yield first_speech_frame
            # The first speech frame is already in preroll when that is enabled.
            while True:
                next_audio = await read_frame()
                self._validate_frame(next_audio)
                is_speech = self.vad.is_speech(next_audio)
                if is_speech:
                    speech_frames += 1
                else:
                    silence_frames += 1
                # Deliberately send silence too: VAD is not the turn-end detector.
                yield next_audio

        frame_stream = active_audio()
        try:
            transcript = (
                await self._with_progress(
                    "transcribing", self.transcriber.transcribe_turn(frame_stream)
                )
            ).strip()
        finally:
            await frame_stream.aclose()

        if not transcript:
            raise VoicePipelineError("The transcription provider returned an empty turn.")

        input_paused = False
        try:
            if self.pause_input is not None:
                await self.pause_input()
                input_paused = True
            response = (
                await self._with_progress("thinking", self.assistant.respond(transcript))
            ).strip()
            if not response:
                raise VoicePipelineError("The assistant returned an empty response.")
            audio = await self._with_progress("preparing_audio", self.tts.synthesize(response))
            await self._with_progress("speaking", self.output.play(audio))
            return VoiceTurnResult(
                transcript=transcript,
                response=response,
                audio=audio,
                speech_audio_frames=speech_frames,
                silence_audio_frames=silence_frames,
            )
        finally:
            if input_paused and self.resume_input is not None:
                await self.resume_input()
