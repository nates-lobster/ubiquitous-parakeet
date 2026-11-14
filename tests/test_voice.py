import unittest

from ubiquitous_parakeet.cli import main
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


class VoiceCliTests(unittest.TestCase):
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
