import json
import subprocess
import unittest
from unittest.mock import patch

from hermes_plugin import register


class FakeContext:
    def __init__(self, settings=None):
        self.settings = settings or {}
        self.hooks = {}

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_hook(self, name, callback):
        self.hooks[name] = callback


class HermesPluginTests(unittest.TestCase):
    def setUp(self):
        self.ctx = FakeContext({
            "command": "/tmp/venv/bin/parakeet",
            "workspace": "/tmp/buddy",
            "hardware_mode": "mock",
        })
        register(self.ctx)
        self.hook = self.ctx.hooks["pre_llm_call"]

    def test_allows_when_local_gate_says_allowed(self):
        completed = subprocess.CompletedProcess([], 0, json.dumps({"allowed": True}), "")
        with patch("hermes_plugin.subprocess.run", return_value=completed) as run:
            self.assertIsNone(self.hook(user_message="Hello", platform="cli"))
        argv = run.call_args.args[0]
        self.assertIn("reactive", argv)
        self.assertEqual(run.call_args.kwargs["timeout"], 3)
        self.assertEqual(run.call_args.kwargs["env"]["PARAKEET_PRESENCE_INTERFACE"], "uart")

    def test_proactive_marker_routes_through_cooldown_and_blocks(self):
        decision = {"allowed": False, "reason": "cooldown", "cooldown_remaining_seconds": 90}
        completed = subprocess.CompletedProcess([], 2, json.dumps(decision), "")
        with patch("hermes_plugin.subprocess.run", return_value=completed) as run:
            blocked = self.hook(user_message="  [PARAKEET:PROACTIVE] gentle check-in")
        self.assertIn("cooldown", blocked["message"])
        self.assertIn("proactive", run.call_args.args[0])
        self.assertIn("--reserve-proactive", run.call_args.args[0])

    def test_gate_errors_fail_closed(self):
        with patch("hermes_plugin.subprocess.run", side_effect=OSError("missing CLI")):
            blocked = self.hook(user_message="hello")
        self.assertEqual(blocked["action"], "block")
        self.assertIn("no prompt inference request", blocked["message"])


if __name__ == "__main__":
    unittest.main()
