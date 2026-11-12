import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ubiquitous_parakeet.database import record_interaction
from ubiquitous_parakeet.gate import check_eligibility, reserve_proactive_if_eligible
from ubiquitous_parakeet.orchestrator import invoke_if_eligible


class GateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "companion.db"
        self.now = datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp.cleanup()

    def test_absence_denies_proactive_and_reactive(self):
        for trigger in ("proactive", "reactive"):
            with self.subTest(trigger=trigger):
                decision = check_eligibility(self.db, trigger_type=trigger, present=False)
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.reason, "no_presence")

    def test_cooldown_blocks_until_45_minutes_and_reactive_bypasses_it(self):
        record_interaction(
            self.db, trigger_type="proactive", topic_category="routine",
            timestamp=self.now - timedelta(minutes=20),
        )
        proactive = check_eligibility(
            self.db, trigger_type="proactive", present=True, now=self.now
        )
        self.assertFalse(proactive.allowed)
        self.assertEqual(proactive.reason, "cooldown")
        self.assertEqual(proactive.cooldown_remaining_seconds, 25 * 60)
        reactive = check_eligibility(
            self.db, trigger_type="reactive", present=True, now=self.now
        )
        self.assertTrue(reactive.allowed)

    def test_expired_cooldown_allows_proactive(self):
        record_interaction(
            self.db, trigger_type="proactive", topic_category="routine",
            timestamp=self.now - timedelta(minutes=46),
        )
        decision = check_eligibility(
            self.db, trigger_type="proactive", present=True, now=self.now
        )
        self.assertTrue(decision.allowed)

    def test_proactive_reservation_starts_cooldown_once(self):
        first = reserve_proactive_if_eligible(
            self.db, present=True, now=self.now
        )
        self.assertTrue(first.allowed)
        self.assertTrue(first.reserved)
        second = reserve_proactive_if_eligible(
            self.db, present=True, now=self.now + timedelta(seconds=1)
        )
        self.assertFalse(second.allowed)
        self.assertEqual(second.reason, "cooldown")
        self.assertEqual(second.cooldown_remaining_seconds, 44 * 60 + 59)

    def test_denied_invocation_never_runs_agent_callback(self):
        called = False

        async def callback():
            nonlocal called
            called = True
            return "should not happen"

        result = asyncio.run(invoke_if_eligible(
            self.db, trigger_type="proactive", present=False, invoke_agent=callback
        ))
        self.assertFalse(result.decision.allowed)
        self.assertIsNone(result.value)
        self.assertFalse(called)

    def test_allowed_invocation_runs_agent_callback(self):
        async def callback():
            return "mock response"

        result = asyncio.run(invoke_if_eligible(
            self.db, trigger_type="reactive", present=True, invoke_agent=callback
        ))
        self.assertTrue(result.decision.allowed)
        self.assertEqual(result.value, "mock response")


if __name__ == "__main__":
    unittest.main()
