import asyncio
import base64
from collections import deque
import importlib.util
from io import BytesIO
from pathlib import Path
import sys
import tempfile
import types
import unittest
import wave
from unittest.mock import AsyncMock, Mock, patch

from ubiquitous_parakeet.audio_io import AudioFrame
from ubiquitous_parakeet.voice_adapters import (
    CartesiaTTS,
    DeepgramFluxSTT,
    GoogleCloudTTS,
    HermesCliAssistant,
    OpenWakeWordDetector,
    SileroVAD,
    _decode_wav,
)
from ubiquitous_parakeet.voice_runtime import NormalizedAudioReader, VoiceSettings


def make_wav(sample_rate=24000, samples=240):
    buffer = BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(bytes(samples * 2))
    return buffer.getvalue()


class StubCapture:
    def __init__(self, frames):
        self.frames = deque(frames)
        self.started = False
        self.closed = False

    def start(self):
        self.started = True
        self.closed = False
        return self

    def read(self, timeout=None):
        if not self.frames:
            raise TimeoutError("stub frame source exhausted")
        return self.frames.popleft()

    def close(self):
        self.closed = True
        self.started = False


class VoiceAdapterTests(unittest.IsolatedAsyncioTestCase):
    def test_provider_thresholds_and_turn_timeouts_are_validated(self):
        with self.assertRaisesRegex(ValueError, "Wakeword threshold"):
            OpenWakeWordDetector("hey_jarvis", threshold=0.0)
        with self.assertRaisesRegex(ValueError, "end-of-turn timeout"):
            DeepgramFluxSTT(api_key="test-key", eot_timeout_ms=100)
        with self.assertRaisesRegex(ValueError, "threshold"):
            DeepgramFluxSTT(api_key="test-key", eot_threshold=0.1)

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "optional NumPy audio extra not installed")
    def test_openwakeword_loads_configured_builtin_with_onnx_and_resets(self):
        package = types.ModuleType("openwakeword")
        package.MODELS = {"hey_jarvis": {}}
        package.__path__ = []
        model_module = types.ModuleType("openwakeword.model")
        utils_module = types.ModuleType("openwakeword.utils")
        fake_model = Mock()
        fake_model.predict.return_value = {"hey_jarvis": 0.8}
        model_module.Model = Mock(return_value=fake_model)
        utils_module.download_models = Mock()
        with patch.dict(
            sys.modules,
            {
                "openwakeword": package,
                "openwakeword.model": model_module,
                "openwakeword.utils": utils_module,
            },
        ):
            detector = OpenWakeWordDetector("hey_jarvis", threshold=0.7)
            frame = AudioFrame(bytes(2560), 16000, 1, 1.0)
            self.assertTrue(detector.detect(frame))
            detector.reset()
        utils_module.download_models.assert_called_once_with(model_names=["hey_jarvis"])
        model_module.Model.assert_called_once_with(
            wakeword_models=["hey_jarvis"], inference_framework="onnx", vad_threshold=0.0
        )
        fake_model.reset.assert_called_once()

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "optional NumPy audio extra not installed")
    def test_openwakeword_local_model_still_loads_shared_features(self):
        package = types.ModuleType("openwakeword")
        package.MODELS = {"hey_jarvis": {}}
        package.__path__ = []
        model_module = types.ModuleType("openwakeword.model")
        utils_module = types.ModuleType("openwakeword.utils")
        model_module.Model = Mock(return_value=Mock())
        utils_module.download_models = Mock()
        with tempfile.TemporaryDirectory() as directory:
            model_file = Path(directory) / "custom.onnx"
            model_file.write_bytes(b"test-model")
            with patch.dict(
                sys.modules,
                {
                    "openwakeword": package,
                    "openwakeword.model": model_module,
                    "openwakeword.utils": utils_module,
                },
            ):
                OpenWakeWordDetector(str(model_file))
        utils_module.download_models.assert_called_once_with(model_names=[])
        model_module.Model.assert_called_once_with(
            wakeword_models=[str(model_file)], inference_framework="onnx", vad_threshold=0.0
        )

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "optional NumPy audio extra not installed")
    def test_openwakeword_rejects_malformed_audio(self):
        package = types.ModuleType("openwakeword")
        package.MODELS = {"hey_jarvis": {}}
        package.__path__ = []
        model_module = types.ModuleType("openwakeword.model")
        utils_module = types.ModuleType("openwakeword.utils")
        model_module.Model = Mock(return_value=Mock())
        utils_module.download_models = Mock()
        with patch.dict(
            sys.modules,
            {
                "openwakeword": package,
                "openwakeword.model": model_module,
                "openwakeword.utils": utils_module,
            },
        ):
            detector = OpenWakeWordDetector("hey_jarvis")
        with self.assertRaisesRegex(ValueError, "16 kHz mono"):
            detector.detect(AudioFrame(bytes(1280), 8000, 1, 1.0))

    def test_wav_decoder_checks_format_and_returns_raw_pcm(self):
        audio = _decode_wav(make_wav(sample_rate=22050, samples=128))
        self.assertEqual(audio.sample_rate, 22050)
        self.assertEqual(audio.channels, 1)
        self.assertEqual(len(audio.pcm_s16le), 256)

    async def test_cartesia_adapter_requests_pcm_wav_and_decodes_response(self):
        wav_data = make_wav(sample_rate=24000)
        with patch(
            "ubiquitous_parakeet.voice_adapters._post_bytes",
            new=AsyncMock(return_value=wav_data),
        ) as post:
            adapter = CartesiaTTS(api_key="test-key", voice="voice-id")
            audio = await adapter.synthesize("Hello there")
        self.assertEqual(audio.sample_rate, 24000)
        payload = post.call_args.args[1]
        self.assertEqual(payload["transcript"], "Hello there")
        self.assertEqual(payload["output_format"]["encoding"], "pcm_s16le")
        self.assertEqual(post.call_args.args[2]["Authorization"], "Bearer test-key")

    async def test_google_adapter_decodes_api_wav_response(self):
        wav_data = make_wav(sample_rate=24000)
        with patch(
            "ubiquitous_parakeet.voice_adapters._post_json",
            new=AsyncMock(return_value={"audioContent": base64.b64encode(wav_data).decode()}),
        ) as post:
            audio = await GoogleCloudTTS(api_key="test-key").synthesize("Hello")
        self.assertEqual(audio.sample_rate, 24000)
        self.assertEqual(post.call_args.args[1]["input"]["text"], "Hello")

    async def test_flux_adapter_streams_frames_and_uses_turn_event(self):
        sent = []
        urls = []

        class Socket:
            async def send(self, message):
                sent.append(message)

            async def recv(self):
                await asyncio.sleep(0.01)
                return '{"type":"TurnInfo","event":"EndOfTurn","transcript":"I need a moment."}'

        class Context:
            async def __aenter__(self):
                return Socket()

            async def __aexit__(self, *_args):
                return None

        def connect(uri, **kwargs):
            urls.append((uri, kwargs))
            return Context()

        async def frames():
            yield AudioFrame(bytes(2560), 16000, 1, 1.0)
            await asyncio.sleep(1)

        fake_websockets = types.SimpleNamespace(connect=connect)
        with patch.dict(sys.modules, {"websockets": fake_websockets}):
            result = await DeepgramFluxSTT(
                api_key="test-key", eot_timeout_ms=30000
            ).transcribe_turn(frames())
        self.assertEqual(result, "I need a moment.")
        self.assertEqual(sent, [bytes(2560)])
        self.assertIn("eot_timeout_ms=30000", urls[0][0])
        self.assertEqual(urls[0][1]["additional_headers"]["Authorization"], "Token test-key")

    async def test_hermes_adapter_uses_one_shot_and_keeps_short_history(self):
        class Process:
            returncode = 0

            async def communicate(self):
                return b"A kind reply.", b""

        with patch("ubiquitous_parakeet.voice_adapters.shutil.which", return_value="/usr/bin/hermes"):
            with patch(
                "ubiquitous_parakeet.voice_adapters.asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=Process()),
            ) as run:
                assistant = HermesCliAssistant()
                self.assertEqual(await assistant.respond("Hi"), "A kind reply.")
                args = run.call_args.args
                self.assertEqual(args[:2], ("/usr/bin/hermes", "-z"))
                self.assertIn("User: Hi", args[2])
                self.assertEqual(len(assistant.history), 1)

    def test_voice_settings_require_explicit_wakeword_and_accept_provider_switch(self):
        with patch.dict(
            "os.environ",
            {
                "DEEPGRAM_API_KEY": "dg-test",
                "PARAKEET_TTS_PROVIDER": "cartesia",
                "PARAKEET_CONVERSATION_IDLE_TIMEOUT_SECONDS": "240",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "Choose a wakeword"):
                VoiceSettings.from_environment()
            settings = VoiceSettings.from_environment(wakeword_model="hey_jarvis")
        self.assertEqual(settings.tts_provider, "cartesia")
        self.assertEqual(settings.flux_eot_timeout_ms, 30000)
        self.assertEqual(settings.conversation_idle_timeout_seconds, 240.0)

    @unittest.skipUnless(
        importlib.util.find_spec("numpy"),
        "optional NumPy audio extra not installed",
    )
    def test_silero_vad_uses_streaming_onnx_state_and_only_signals_speech(self):
        import numpy as np

        class Session:
            def __init__(self):
                self.probabilities = deque([0.1, 0.1, 0.9, 0.1, 0.1, 0.1, 0.1])
                self.inputs = []

            def run(self, _outputs, inputs):
                self.inputs.append(inputs)
                return [
                    np.asarray([[self.probabilities.popleft()]], dtype=np.float32),
                    inputs["h"] + 1,
                    inputs["c"] + 1,
                ]

        session = Session()

        class Options:
            inter_op_num_threads = 0
            intra_op_num_threads = 0

        class Runtime:
            SessionOptions = Options

            @staticmethod
            def InferenceSession(*_args, **_kwargs):
                return session

        silence = AudioFrame(np.zeros(1280, dtype="<i2").tobytes(), 16000, 1, 0.0)
        with tempfile.TemporaryDirectory() as directory:
            model_file = Path(directory) / "vad.onnx"
            model_file.write_bytes(b"fake-onx")
            with patch.dict(sys.modules, {"onnxruntime": Runtime}):
                detector = SileroVAD(model_path=model_file)
                self.assertFalse(detector.is_speech(silence))
                # The trailing 320 samples from the prior 80 ms frame are retained.
                self.assertTrue(detector.is_speech(silence))
                self.assertEqual(len(session.inputs), 5)
                self.assertEqual(session.inputs[0]["input"].shape, (1, 480))
                self.assertEqual(session.inputs[1]["h"][0, 0, 0], 1.0)
                detector.reset()
                self.assertFalse(detector.is_speech(silence))
                self.assertEqual(session.inputs[-2]["h"][0, 0, 0], 0.0)

    @unittest.skipUnless(
        importlib.util.find_spec("numpy") and importlib.util.find_spec("soxr"),
        "optional numpy/soxr audio extra not installed",
    )
    async def test_normalized_reader_resamples_48khz_to_16khz(self):
        import numpy as np

        frames = [
            AudioFrame(np.full(3840, 1200, dtype="<i2").tobytes(), 48000, 1, 0.0),
            AudioFrame(np.full(3840, 1200, dtype="<i2").tobytes(), 48000, 1, 0.08),
            AudioFrame(np.full(3840, 1200, dtype="<i2").tobytes(), 48000, 1, 0.16),
        ]
        reader = NormalizedAudioReader(StubCapture(frames))
        reader.start()
        output = await reader.read_frame()
        self.assertEqual(output.sample_rate, 16000)
        self.assertEqual(len(output.pcm_s16le), 2560)
        self.assertGreater(max(abs(value) for value in np.frombuffer(output.pcm_s16le, dtype="<i2")), 500)
        await reader.close()

    async def test_normalized_reader_frames_native_16khz_input_without_resampler(self):
        frames = [
            AudioFrame(bytes(960), 16000, 1, 0.0),
            AudioFrame(bytes(960), 16000, 1, 0.03),
            AudioFrame(bytes(640), 16000, 1, 0.06),
        ]
        source = StubCapture(frames)
        reader = NormalizedAudioReader(source)
        reader.start()
        output = await reader.read_frame()
        self.assertEqual(output.sample_rate, 16000)
        self.assertEqual(output.channels, 1)
        self.assertEqual(len(output.pcm_s16le), 2560)
        await reader.pause()
        self.assertTrue(source.closed)
        await reader.resume()
        self.assertTrue(source.started)
        await reader.close()


if __name__ == "__main__":
    unittest.main()
