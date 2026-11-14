import contextlib
import io
import json
import tempfile
import unittest
from unittest.mock import patch

from ubiquitous_parakeet.audio_io import AudioDevice
from ubiquitous_parakeet.cli import main


class CliTests(unittest.TestCase):
    def test_audio_devices_command_uses_discovered_devices(self):
        devices = [
            AudioDevice(0, "USB mic", "ALSA", 1, 0, 48000, True, False),
            AudioDevice(1, "USB headphones", "ALSA", 0, 2, 48000, False, True),
        ]
        output = io.StringIO()
        with patch("ubiquitous_parakeet.cli.list_audio_devices", return_value=devices):
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["audio", "devices"]), 0)
        self.assertIn("USB mic", output.getvalue())
        self.assertIn("USB headphones", output.getvalue())
        self.assertIn("system default", output.getvalue())

    def test_init_and_explicit_absence_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["init", "--workspace", temporary]), 0)
            result = json.loads(output.getvalue().split("\nLocal workspace ready.")[0])
            self.assertTrue(result["database"].endswith("companion.db"))

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main([
                    "gate", "--workspace", temporary,
                    "--trigger", "proactive", "--presence", "absent",
                ])
            self.assertEqual(status, 2)
            decision = json.loads(output.getvalue())
            self.assertEqual(decision["reason"], "no_presence")


if __name__ == "__main__":
    unittest.main()
