"""Deterministic, no-device/no-network exercise of the complete voice loop."""

from __future__ import annotations

import math
from typing import AsyncIterator

from .audio_io import AudioFrame
from .voice import PcmAudio, VoiceLoop, VoiceTurnResult


FRAME_MS = 80
SAMPLE_RATE = 16000
SAMPLES_PER_FRAME = SAMPLE_RATE * FRAME_MS // 1000


def _frame(speech: bool, index: int) -> AudioFrame:
    sample = 1400 if speech else 0
    pcm = sample.to_bytes(2, "little", signed=True) * SAMPLES_PER_FRAME
    return AudioFrame(
        pcm_s16le=pcm,
        sample_rate=SAMPLE_RATE,
        channels=1,
        captured_at_monotonic=float(index),
    )


class DemoWakeWord:
    def __init__(self) -> None:
        self.calls = 0

    def detect(self, _frame: AudioFrame) -> bool:
        self.calls += 1
        return self.calls == 2


class DemoVAD:
    def is_speech(self, frame: AudioFrame) -> bool:
        return int.from_bytes(frame.pcm_s16le[:2], "little", signed=True) != 0

    def reset(self) -> None:
        return None


class DemoTranscriber:
    """Pretend a remote turn detector returns after the configured quiet frames."""

    def __init__(self, silence_frames: int) -> None:
        self.silence_frames = silence_frames
        self.frames_received = 0
        self.silence_received = 0

    async def transcribe_turn(self, frames: AsyncIterator[AudioFrame]) -> str:
        heard_speech = False
        async for frame in frames:
            self.frames_received += 1
            is_speech = int.from_bytes(frame.pcm_s16le[:2], "little", signed=True) != 0
            if is_speech:
                heard_speech = True
                self.silence_received = 0
            elif heard_speech:
                self.silence_received += 1
                if self.silence_received >= self.silence_frames:
                    return "Could you tell me what we planned for today?"
        raise RuntimeError("Demo audio ended before the simulated end-of-turn event.")


class DemoAssistant:
    async def respond(self, text: str) -> str:
        return f"I heard you ask: {text}"


class DemoTTS:
    async def synthesize(self, _text: str) -> PcmAudio:
        # A short silent PCM payload exercises the output contract without noise.
        return PcmAudio(pcm_s16le=bytes(SAMPLE_RATE // 2), sample_rate=SAMPLE_RATE)


class DemoOutput:
    def __init__(self) -> None:
        self.played: list[PcmAudio] = []

    async def play(self, audio: PcmAudio) -> None:
        self.played.append(audio)


async def run_demo(pause_seconds: float = 12.0) -> tuple[VoiceTurnResult, DemoTranscriber, DemoOutput]:
    if not 0.08 <= pause_seconds <= 60:
        raise ValueError("pause_seconds must be between 0.08 and 60")
    pause_frames = max(1, math.ceil(pause_seconds * 1000 / FRAME_MS))
    frames = [
        _frame(False, 0),
        _frame(False, 1),  # demo wakeword fires after this frame
        _frame(False, 2),  # brief post-wake pause
        _frame(True, 3),
        _frame(True, 4),
        _frame(True, 5),
        *(_frame(False, 6 + index) for index in range(pause_frames)),
    ]
    frame_iter = iter(frames)

    async def read_frame() -> AudioFrame:
        try:
            return next(frame_iter)
        except StopIteration as error:
            raise RuntimeError("Demo source exhausted.") from error

    transcriber = DemoTranscriber(pause_frames)
    output = DemoOutput()
    loop = VoiceLoop(
        wakeword=DemoWakeWord(),
        vad=DemoVAD(),
        transcriber=transcriber,
        assistant=DemoAssistant(),
        tts=DemoTTS(),
        output=output,
        sample_rate=SAMPLE_RATE,
        pre_roll_frames=3,
    )
    result = await loop.run_one_turn(read_frame, speech_start_timeout_seconds=2)
    return result, transcriber, output
