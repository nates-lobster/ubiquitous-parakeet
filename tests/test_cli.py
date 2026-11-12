import contextlib
import io
import json
import tempfile
import unittest

from ubiquitous_parakeet.cli import main


class CliTests(unittest.TestCase):
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
