import tempfile
import unittest
from pathlib import Path

from ubiquitous_parakeet.hardware import (
    MockPresenceSensor,
    create_presence_sensor,
    is_raspberry_pi,
)


class HardwareTests(unittest.TestCase):
    def test_mock_sensor_is_deterministic_and_defaults_absent(self):
        sensor = create_presence_sensor(mode="mock", mock_output=False)
        self.assertIsInstance(sensor, MockPresenceSensor)
        self.assertFalse(sensor.is_present())
        present_sensor = create_presence_sensor(mode="mock", mock_present=True, mock_output=False)
        self.assertTrue(present_sensor.is_present())

    def test_pi_detection_uses_model_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            model = Path(temporary) / "model"
            model.write_text("Raspberry Pi 4 Model B Rev 1.5\x00", encoding="utf-8")
            self.assertTrue(is_raspberry_pi(model))
            model.write_text("Generic x86_64 laptop", encoding="utf-8")
            self.assertFalse(is_raspberry_pi(model))

    def test_real_gpio_mode_requires_explicit_pin(self):
        with self.assertRaisesRegex(RuntimeError, "PRESENCE_GPIO"):
            create_presence_sensor(mode="real", interface="gpio", gpio_pin=None)


if __name__ == "__main__":
    unittest.main()
