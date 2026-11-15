import asyncio
import unittest

from ubiquitous_parakeet.audio_io import AudioFrame
from ubiquitous_parakeet.cli import _live_voice_status, main
from ubiquitous_parakeet.voice import PcmAudio, VoiceLoop, VoiceStatus
from ubiquitous_parakeet.voice_demo import run_demo


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
