from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ubiquitous_parakeet.audio_io import (
    AudioDevice,
    _resolve_device,
    AudioInputStream,
    AudioPlayback,
    capture_and_playback_test,
    format_audio_devices,
    list_audio_devices,
)


class FakeRawStream:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.writes = []
        self.started = False
        self.closed = False

    def start(self):
        self.started = True
        callback = self.kwargs.get("callback")
        if callback:
            frames = self.kwargs["blocksize"]
            callback(bytes(frames * self.kwargs["channels"] * 2), frames, None, None)

    def write(self, data):
        self.writes.append(data)

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True


class FakeSoundDevice:
    def __init__(self, defaults=(0, 0)):
        self.default = SimpleNamespace(device=defaults)
        self.devices = [
            {
                "name": "USB Headset",
                "hostapi": 0,
                "max_input_channels": 1,
                "max_output_channels": 2,
                "default_samplerate": 48000,
            },
            {
                "name": "Pi Analog Output",
                "hostapi": 0,
                "max_input_channels": 0,
                "max_output_channels": 2,
                "default_samplerate": 44100,
            },
        ]
        self.host_apis = [{"name": "ALSA"}]
        self.recorded = []
        self.played = []
        self.raw_inputs = []
        self.raw_outputs = []

    def RawInputStream(self, **kwargs):
        stream = FakeRawStream(**kwargs)
        self.raw_inputs.append(stream)
        return stream

    def RawOutputStream(self, **kwargs):
        stream = FakeRawStream(**kwargs)
        self.raw_outputs.append(stream)
        return stream

    def query_devices(self):
        return self.devices

    def query_hostapis(self):
        return self.host_apis

    def rec(self, frames, **kwargs):
        self.recorded.append((frames, kwargs))
        return object()

    def play(self, recording, **kwargs):
        self.played.append((recording, kwargs))

    def wait(self):
        return None


class AudioIoTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeSoundDevice()
        self.import_patch = patch(
            "ubiquitous_parakeet.audio_io.importlib.import_module",
            return_value=self.fake,
        )
        self.import_patch.start()
        self.addCleanup(self.import_patch.stop)

    def test_device_discovery_marks_current_defaults(self):
        devices = list_audio_devices()
        self.assertEqual(devices[0].name, "USB Headset")
        self.assertTrue(devices[0].is_default_input)
        self.assertTrue(devices[0].is_default_output)
        self.assertFalse(devices[1].is_default_input)
        self.assertIn("USB Headset", format_audio_devices(devices))
        self.assertIn("Pi Analog Output", format_audio_devices(devices))

    def test_selection_follows_system_default_or_name_not_fixed_device(self):
        devices = list_audio_devices()
        selected = _resolve_device(devices, "input", None)
        self.assertEqual(selected.index, 0)
        self.assertEqual(_resolve_device(devices, "output", "Analog").index, 1)

    def test_uses_only_available_device_if_no_system_default(self):
        devices = [
            AudioDevice(
                index=3,
                name="Only microphone",
                host_api="ALSA",
                input_channels=1,
                output_channels=0,
                default_sample_rate=16000,
            )
        ]
        self.assertEqual(_resolve_device(devices, "input", None).index, 3)

    def test_no_default_with_multiple_devices_requires_selection(self):
        devices = list_audio_devices()
        devices = [replace(device, is_default_input=False) for device in devices]
        devices[1] = replace(devices[1], input_channels=1)
        with self.assertRaisesRegex(RuntimeError, "multiple devices"):
            _resolve_device(devices, "input", None)

    def test_capture_and_playback_uses_native_input_rate_and_saves_nothing(self):
        result = capture_and_playback_test(duration_seconds=0.5)
        self.assertEqual(result.sample_rate, 48000)
        self.assertEqual(result.input_device.name, "USB Headset")
        self.assertEqual(result.output_device.name, "USB Headset")
        frames, kwargs = self.fake.recorded[0]
        self.assertEqual(frames, 24000)
        self.assertEqual(kwargs["device"], 0)
        self.assertEqual(self.fake.played[0][1]["device"], 0)

    def test_capture_only_does_not_require_or_use_output(self):
        self.fake.default = SimpleNamespace(device=(0, -1))
        result = capture_and_playback_test(duration_seconds=0.1, playback=False)
        self.assertIsNone(result.output_device)
        self.assertEqual(self.fake.played, [])

    def test_input_stream_yields_timestamped_pcm_frames_from_default_device(self):
        with AudioInputStream(block_duration_ms=20) as stream:
            frame = stream.read(timeout=0.1)
            self.assertEqual(stream.selected_device.name, "USB Headset")
            self.assertEqual(frame.sample_rate, 48000)
            self.assertEqual(frame.frame_count, 960)
            self.assertEqual(len(frame.pcm_s16le), 1920)
            self.assertGreater(frame.captured_at_monotonic, 0)
        self.assertTrue(self.fake.raw_inputs[0].closed)

    def test_input_stream_fails_loudly_when_consumer_falls_behind(self):
        stream = AudioInputStream(queue_depth=1).start()
        raw_stream = self.fake.raw_inputs[0]
        callback = raw_stream.kwargs["callback"]
        callback(bytes(960), 480, None, None)
        try:
            with self.assertRaisesRegex(RuntimeError, "queue overflowed"):
                stream.read(timeout=0.1)
        finally:
            stream.close()

    def test_playback_writes_pcm_to_selected_device(self):
        with AudioPlayback(sample_rate=24000, device="Analog") as playback:
            playback.write(bytes(16))
            self.assertEqual(playback.selected_device.name, "Pi Analog Output")
        self.assertEqual(self.fake.raw_outputs[0].writes, [bytes(16)])
        self.assertTrue(self.fake.raw_outputs[0].closed)

    def test_playback_rejects_misaligned_pcm(self):
        with AudioPlayback(sample_rate=24000, channels=2) as playback:
            with self.assertRaisesRegex(ValueError, "multiple of 4"):
                playback.write(bytes(2))

    def test_invalid_duration_fails_before_opening_audio(self):
        with self.assertRaisesRegex(ValueError, "duration_seconds"):
            capture_and_playback_test(duration_seconds=0)


if __name__ == "__main__":
    unittest.main()
