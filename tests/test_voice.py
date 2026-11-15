import asyncio
import contextlib
import io
import unittest
from unittest.mock import patch

from ubiquitous_parakeet.audio_io import AudioFrame
from ubiquitous_parakeet.cli import _live_voice_status, main
from ubiquitous_parakeet.voice import (
    PcmAudio,
    VoiceLoop,
    VoiceStatus,
    VoiceTimeout,
    VoiceTurnResult,
    is_conversation_end,
)
from ubiquitous_parakeet.voice_demo import run_demo
from ubiquitous_parakeet.voice_runtime import VoiceSettings, run_voice_service


class VoicePipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_flow_preserves_silence_for_turn_detector(self):
        result, transcriber, output = await run_demo(pause_seconds=12.0)
        self.assertEqual(result.transcript, "Could you tell me what we planned for today?")
        self.assertIn("I heard you ask", result.response)
        self.assertEqual(transcriber.silence_received, 150)
        self.assertGreaterEqual(transcriber.frames_received, 154)
        self.assertEqual(len(output.played), 1)
        self.assertEqual(output.played[0].sample_rate, 16000)

    async def test_demo_pause_is_configurable(self):
        _result, transcriber, _output = await run_demo(pause_seconds=0.16)
        self.assertEqual(transcriber.silence_received, 2)

    async def test_demo_rejects_invalid_pause(self):
        with self.assertRaisesRegex(ValueError, "pause_seconds"):
            await run_demo(pause_seconds=0)


class VoicePauseResumeTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_loop_pauses_mic_and_reports_progress_during_llm_wait(self):
        events = []
        statuses = []
        frames = iter(
            [
                AudioFrame(bytes(2560), 16000, 1, 0.0),
                AudioFrame(bytes(2560), 16000, 1, 0.08),
            ]
        )

        async def read_frame():
            return next(frames)

        class Wake:
            def detect(self, _frame):
                return True

        class VAD:
            def reset(self):
                return None

            def is_speech(self, _frame):
                return True

        class STT:
            async def transcribe_turn(self, audio_frames):
                await anext(audio_frames)
                return "Hello"

        class Assistant:
            async def respond(self, _text):
                events.append("assistant")
                await asyncio.sleep(1.05)
                return "Hi there."

        class TTS:
            async def synthesize(self, _text):
                events.append("tts")
                return PcmAudio(bytes(2), 16000)

        class Speaker:
            async def play(self, _audio):
                events.append("speaker")

        async def pause():
            events.append("pause-input")

        async def resume():
            events.append("resume-input")

        pipeline = VoiceLoop(
            wakeword=Wake(), vad=VAD(), transcriber=STT(),
            assistant=Assistant(), tts=TTS(), output=Speaker(),
            pause_input=pause, resume_input=resume, pre_roll_frames=0,
            status_callback=statuses.append,
        )
        result = await pipeline.run_one_turn(read_frame)
        self.assertEqual(result.response, "Hi there.")
        self.assertEqual(events, ["pause-input", "assistant", "tts", "speaker", "resume-input"])
        self.assertTrue(any(
            status.phase == "thinking" and not status.complete and status.elapsed_seconds > 0
            for status in statuses
        ))
        self.assertTrue(any(status.phase == "speaking" and status.complete for status in statuses))

    async def test_active_conversation_skips_wakeword_and_honors_signoff(self):
        statuses = []
        frames = iter([AudioFrame(bytes(2560), 16000, 1, 1.0)])

        async def read_frame():
            return next(frames)

        class Wake:
            calls = 0

            def detect(self, _frame):
                self.calls += 1
                return False

        class VAD:
            def reset(self):
                return None

            def is_speech(self, _frame):
                return True

        class STT:
            async def transcribe_turn(self, audio_frames):
                await anext(audio_frames)
                return "Okay, goodbye."

        class Assistant:
            async def respond(self, _text):
                return "Goodbye."

        class TTS:
            async def synthesize(self, _text):
                return PcmAudio(bytes(2), 16000)

        class Speaker:
            async def play(self, _audio):
                return None

        wake = Wake()
        pipeline = VoiceLoop(
            wakeword=wake, vad=VAD(), transcriber=STT(), assistant=Assistant(),
            tts=TTS(), output=Speaker(), pre_roll_frames=0,
            status_callback=statuses.append,
        )
        result = await pipeline.run_one_turn(read_frame, require_wakeword=False)
        self.assertEqual(wake.calls, 0)
        self.assertTrue(result.conversation_ended)
        self.assertTrue(any(status.phase == "waiting_for_next_turn" for status in statuses))
        self.assertTrue(is_conversation_end("That's all, goodbye!"))
        self.assertFalse(is_conversation_end("I don't want to say goodbye yet"))


class VoiceConversationServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_service_keeps_turns_active_until_signoff_then_requires_wakeword(self):
        calls = []
        reader_events = []

        class Reader:
            def start(self):
                reader_events.append("start")

            async def pause(self):
                reader_events.append("pause")

            async def resume(self):
                reader_events.append("resume")

            async def close(self):
                reader_events.append("close")

            async def read_frame(self):
                raise AssertionError("fake loop does not consume capture frames")

        class Assistant:
            resets = 0

            def reset_conversation(self):
                self.resets += 1

        class Loop:
            assistant = Assistant()

            async def run_one_turn(self, _reader, **kwargs):
                calls.append(kwargs["require_wakeword"])
                if len(calls) == 4:
                    raise StopService
                return VoiceTurnResult(
                    "test turn", "test response", PcmAudio(bytes(2), 16000), 1, 0,
                    conversation_ended=(len(calls) == 3),
                )

        class StopService(Exception):
            pass

        loop = Loop()
        with patch(
            "ubiquitous_parakeet.voice_runtime.build_voice_loop",
            return_value=(Reader(), loop),
        ):
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(StopService):
                    await run_voice_service(VoiceSettings(wakeword_model="test"))

        self.assertEqual(calls, [True, False, False, True])
        self.assertEqual(loop.assistant.resets, 1)
        self.assertEqual(reader_events[0], "start")
        self.assertEqual(reader_events[-1], "close")

    async def test_idle_conversation_timeout_returns_to_wakeword_mode(self):
        calls = []
        reset_count = []

        class Reader:
            async def pause(self):
                return None

            async def resume(self):
                return None

            async def close(self):
                return None

            def start(self):
                return None

            async def read_frame(self):
                return AudioFrame(bytes(2560), 16000, 1, 0.0)

        class Assistant:
            def reset_conversation(self):
                reset_count.append(True)

        class StopService(Exception):
            pass

        class Loop:
            assistant = Assistant()

            async def run_one_turn(self, _reader, **kwargs):
                calls.append(kwargs["require_wakeword"])
                if len(calls) == 1:
                    return VoiceTurnResult(
                        "hello", "Hi.", PcmAudio(bytes(2), 16000), 1, 0
                    )
                if len(calls) == 2:
                    raise VoiceTimeout("next conversational turn")
                raise StopService

        with patch(
            "ubiquitous_parakeet.voice_runtime.build_voice_loop",
            return_value=(Reader(), Loop()),
        ):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(StopService):
                    await run_voice_service(VoiceSettings(wakeword_model="test"))
        self.assertEqual(calls, [True, False, True])
        self.assertEqual(reset_count, [True])


class VoiceCliTests(unittest.TestCase):
    def test_live_status_reporter_shows_phase_and_elapsed_time(self):
        from contextlib import redirect_stderr
        from io import StringIO

        output = StringIO()
        reporter = _live_voice_status()
        with redirect_stderr(output):
            reporter(VoiceStatus("thinking"))
            reporter(VoiceStatus("thinking", elapsed_seconds=2.5))
            reporter(VoiceStatus("thinking", elapsed_seconds=3.0, complete=True))
        self.assertIn("Thinking with Hermes", output.getvalue())
        self.assertIn("3.0s", output.getvalue())

    def test_voice_demo_runs_fake_pipeline_and_explains_no_hardware_was_used(self):
        from contextlib import redirect_stdout
        from io import StringIO

        output = StringIO()
        with redirect_stdout(output):
            status = main(["voice", "demo", "--pause-seconds", "0.16"])
        self.assertEqual(status, 0)
        self.assertIn("no microphone, network, or real speaker", output.getvalue())
        self.assertIn("2 silence frames", output.getvalue())


if __name__ == "__main__":
    unittest.main()
