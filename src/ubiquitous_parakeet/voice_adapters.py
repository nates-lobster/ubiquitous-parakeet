"""Optional live speech providers used by the voice runtime.

Each class implements one provider-neutral interface. Credentials are read from
process environment by ``VoiceSettings`` and are never written to the workspace.
"""

from __future__ import annotations

import asyncio
import base64
from collections import deque
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import wave
from typing import Any, AsyncIterator

from .audio_io import AudioFrame
from .voice import PcmAudio, VoicePipelineError


class OpenWakeWordDetector:
    """Local openWakeWord inference for one configured model/phrase."""

    def __init__(self, model: str, *, threshold: float = 0.55) -> None:
        if not model.strip():
            raise ValueError("A wakeword model name or path is required.")
        if not 0.0 < threshold <= 1.0:
            raise ValueError("Wakeword threshold must be between 0 and 1")
        try:
            import numpy as np
            import openwakeword
            from openwakeword.model import Model
            from openwakeword.utils import download_models
        except ImportError as error:
            raise RuntimeError(
                "Wakeword detection needs openwakeword, NumPy, and ONNX Runtime. Install the optional "
                "'wakeword' extra; on ARM64, see the Pi installation note in the docs."
            ) from error
        self._np = np
        try:
            local_model = Path(model).expanduser()
            is_local_model = local_model.is_file()
            model_selection = str(local_model) if is_local_model else model
            if not is_local_model:
                normalized_name = model.replace(" ", "_")
                if normalized_name not in openwakeword.MODELS:
                    raise ValueError(
                        "Unknown built-in openWakeWord model. Provide one of "
                        f"{', '.join(openwakeword.MODELS)} or an existing local model path."
                    )
                download_models(model_names=[normalized_name])
            else:
                # Custom wakeword files still need openWakeWord's shared feature models.
                download_models(model_names=[])
            self._model = Model(
                wakeword_models=[model_selection],
                inference_framework="onnx",
                vad_threshold=0.0,
            )
        except Exception as error:
            raise RuntimeError(f"Could not load wakeword model {model!r}: {error}") from error
        self.model_name = model
        self.threshold = threshold

    def detect(self, frame: AudioFrame) -> bool:
        if frame.sample_rate != 16000 or frame.channels != 1:
            raise ValueError("openWakeWord input must be 16 kHz mono PCM.")
        samples = self._np.frombuffer(frame.pcm_s16le, dtype="<i2")
        scores = self._model.predict(samples)
        return any(float(score) >= self.threshold for score in scores.values())

    def reset(self) -> None:
        reset = getattr(self._model, "reset", None)
        if reset is not None:
            reset()


class SileroVAD:
    """Local Silero speech classifier; it signals speech, never turn completion."""

    sample_rate = 16000
    window_samples = 480  # 30 ms, as expected by the bundled Silero ONNX model.

    def __init__(self, *, threshold: float = 0.5, model_path: str | Path | None = None) -> None:
        if not 0.0 < threshold <= 1.0:
            raise ValueError("Silero VAD threshold must be between 0 and 1")
        try:
            import numpy as np
            import onnxruntime as ort
        except ImportError as error:
            raise RuntimeError(
                "Silero VAD needs NumPy and ONNX Runtime. Install the project's 'vad' extra."
            ) from error
        if model_path is None:
            try:
                import openwakeword
                from openwakeword.utils import download_models

                model_path = openwakeword.VAD_MODELS["silero_vad"]["model_path"]
                if not Path(model_path).is_file():
                    download_models(model_names=[])
            except ImportError as error:
                raise RuntimeError(
                    "Silero VAD needs the openWakeWord model-data package. Install the 'wakeword' "
                    "extra and its no-dependencies openwakeword package, or set PARAKEET_VAD_MODEL."
                ) from error
        model_file = Path(model_path).expanduser()
        if not model_file.is_file():
            raise FileNotFoundError(f"Silero VAD ONNX model does not exist: {model_file}")
        self._np = np
        self._session = None
        try:
            options = ort.SessionOptions()
            options.inter_op_num_threads = 1
            options.intra_op_num_threads = 1
            self._session = ort.InferenceSession(
                str(model_file),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
        except Exception as error:
            raise RuntimeError(f"Could not load the Silero VAD ONNX model: {error}") from error
        self.threshold = threshold
        self._sample_buffer = np.empty(0, dtype=np.int16)
        self._h = np.zeros((2, 1, 64), dtype=np.float32)
        self._c = np.zeros((2, 1, 64), dtype=np.float32)
        self._sample_rate = np.asarray(self.sample_rate, dtype=np.int64)

    def is_speech(self, frame: AudioFrame) -> bool:
        if frame.sample_rate != self.sample_rate or frame.channels != 1:
            raise ValueError("Silero VAD input must be 16 kHz mono PCM.")
        chunk = self._np.frombuffer(frame.pcm_s16le, dtype="<i2")
        if chunk.size:
            self._sample_buffer = self._np.concatenate((self._sample_buffer, chunk))
        speech = False
        while self._sample_buffer.size >= self.window_samples:
            samples = self._sample_buffer[: self.window_samples].astype(self._np.float32) / 32767.0
            self._sample_buffer = self._sample_buffer[self.window_samples :]
            result, self._h, self._c = self._session.run(
                None,
                {
                    "input": samples.reshape(1, -1),
                    "h": self._h,
                    "c": self._c,
                    "sr": self._sample_rate,
                },
            )
            probability = float(self._np.asarray(result).reshape(-1)[0])
            speech = speech or probability >= self.threshold
        return speech

    def reset(self) -> None:
        self._sample_buffer = self._np.empty(0, dtype=self._np.int16)
        self._h.fill(0)
        self._c.fill(0)


class DeepgramFluxSTT:
    """Deepgram Flux v2 streaming STT and native end-of-turn detection."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "flux-general-en",
        eot_threshold: float = 0.85,
        eot_timeout_ms: int = 30000,
        response_timeout_seconds: float = 90.0,
    ) -> None:
        if not api_key:
            raise ValueError("Deepgram API key is required.")
        if not 0.5 <= eot_threshold <= 1.0:
            raise ValueError("Deepgram end-of-turn threshold must be between 0.5 and 1.0")
        if not 500 <= eot_timeout_ms <= 60000:
            raise ValueError("Deepgram end-of-turn timeout must be between 500 and 60000 ms")
        if response_timeout_seconds <= 0:
            raise ValueError("Deepgram response timeout must be positive")
        self.api_key = api_key
        self.model = model
        self.eot_threshold = eot_threshold
        self.eot_timeout_ms = eot_timeout_ms
        self.response_timeout_seconds = response_timeout_seconds

    async def transcribe_turn(self, frames: AsyncIterator[AudioFrame]) -> str:
        try:
            import websockets
        except ImportError as error:
            raise RuntimeError("Deepgram Flux streaming needs the optional 'voice' extra.") from error

        query = urllib.parse.urlencode(
            {
                "model": self.model,
                "encoding": "linear16",
                "sample_rate": "16000",
                "eot_threshold": str(self.eot_threshold),
                "eot_timeout_ms": str(self.eot_timeout_ms),
            }
        )
        uri = f"wss://api.deepgram.com/v2/listen?{query}"
        try:
            async with websockets.connect(
                uri,
                additional_headers={"Authorization": f"Token {self.api_key}"},
                max_size=None,
                ping_interval=20,
                ping_timeout=20,
                open_timeout=15,
            ) as socket:
                async def send_audio() -> None:
                    async for frame in frames:
                        if frame.sample_rate != 16000 or frame.channels != 1:
                            raise VoicePipelineError(
                                "Deepgram Flux requires normalized 16 kHz mono PCM."
                            )
                        await socket.send(frame.pcm_s16le)

                sender = asyncio.create_task(send_audio())
                try:
                    while True:
                        raw = await asyncio.wait_for(
                            socket.recv(), timeout=self.response_timeout_seconds
                        )
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8", errors="replace")
                        try:
                            message = json.loads(raw)
                        except (json.JSONDecodeError, TypeError):
                            continue
                        if message.get("type") == "Error":
                            raise VoicePipelineError(
                                f"Deepgram Flux error: {message.get('description') or message.get('message') or 'unknown error'}"
                            )
                        if (
                            message.get("type") == "TurnInfo"
                            and message.get("event") == "EndOfTurn"
                        ):
                            return str(message.get("transcript", ""))
                        if sender.done():
                            exception = sender.exception()
                            if exception is not None:
                                raise VoicePipelineError(
                                    f"Audio stream ended before Flux completed a turn: {exception}"
                                ) from exception
                            raise VoicePipelineError(
                                "Audio stream ended before Deepgram Flux emitted EndOfTurn."
                            )
                finally:
                    if not sender.done():
                        sender.cancel()
                    await asyncio.gather(sender, return_exceptions=True)
        except asyncio.TimeoutError as error:
            raise VoicePipelineError(
                "Timed out waiting for Deepgram Flux end-of-turn; check the connection and timeout setting."
            ) from error
        except VoicePipelineError:
            raise
        except Exception as error:
            raise VoicePipelineError(f"Deepgram Flux connection failed: {error}") from error


class HermesCliAssistant:
    """Call the configured Hermes agent in one-shot mode, retaining recent turns."""

    def __init__(
        self,
        *,
        executable: str = "hermes",
        max_history_turns: int = 4,
        timeout_seconds: float = 180.0,
    ) -> None:
        resolved = shutil.which(executable)
        if resolved is None:
            raise RuntimeError(
                f"Could not find Hermes executable {executable!r}; keep Hermes installed outside the project venv and ensure it is on PATH."
            )
        self.executable = resolved
        self.history: deque[tuple[str, str]] = deque(maxlen=max_history_turns)
        self.timeout_seconds = timeout_seconds

    async def respond(self, text: str) -> str:
        lines = [
            "Respond as the configured companion persona. Keep the answer concise, natural to speak aloud, and avoid markdown formatting.",
        ]
        for user_text, assistant_text in self.history:
            lines.extend((f"User: {user_text}", f"Assistant: {assistant_text}"))
        lines.append(f"User: {text}")
        prompt = "\n\n".join(lines)
        try:
            process = await asyncio.create_subprocess_exec(
                self.executable,
                "-z",
                prompt,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=self.timeout_seconds
                )
            except asyncio.TimeoutError as error:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.communicate()
                raise VoicePipelineError(
                    "Hermes did not return a response before the voice timeout."
                ) from error
        except VoicePipelineError:
            raise
        except OSError as error:
            raise VoicePipelineError(f"Could not start Hermes: {error}") from error
        response = stdout.decode("utf-8", errors="replace").strip()
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise VoicePipelineError(
                f"Hermes exited with status {process.returncode}: {detail or 'no diagnostic'}"
            )
        if response:
            self.history.append((text, response))
        return response


def _decode_wav(pcm_wav: bytes) -> PcmAudio:
    try:
        with wave.open(BytesIO(pcm_wav), "rb") as source:
            if source.getcomptype() != "NONE" or source.getsampwidth() != 2:
                raise VoicePipelineError("TTS must return uncompressed 16-bit PCM WAV audio.")
            channels = source.getnchannels()
            sample_rate = source.getframerate()
            pcm = source.readframes(source.getnframes())
    except (wave.Error, EOFError) as error:
        raise VoicePipelineError(f"TTS provider returned invalid WAV audio: {error}") from error
    return PcmAudio(pcm_s16le=pcm, sample_rate=sample_rate, channels=channels)


class PiperTTS:
    """Local Piper CLI synthesis; model path is explicit and no model is downloaded."""

    def __init__(self, model: str | Path, *, executable: str = "piper") -> None:
        self.model = Path(model).expanduser()
        self.executable = executable
        if not self.model.is_file():
            raise FileNotFoundError(f"Piper model does not exist: {self.model}")

    async def synthesize(self, text: str) -> PcmAudio:
        from .tts_piper import synthesize

        with tempfile.TemporaryDirectory(prefix="companion-tts-") as temporary:
            output = Path(temporary) / "speech.wav"
            path = await synthesize(
                text,
                model=self.model,
                output_file=output,
                executable=self.executable,
            )
            return await asyncio.to_thread(_decode_wav, path.read_bytes())


class CartesiaTTS:
    """Cartesia synchronous bytes endpoint, normalized to PCM WAV for playback."""

    def __init__(
        self,
        *,
        api_key: str,
        voice: str,
        model: str = "sonic-3.6",
        api_version: str = "2025-04-16",
        sample_rate: int = 24000,
    ) -> None:
        if not api_key or not voice:
            raise ValueError("Cartesia requires an API key and voice ID.")
        self.api_key = api_key
        self.voice = voice
        self.model = model
        self.api_version = api_version
        self.sample_rate = sample_rate

    async def synthesize(self, text: str) -> PcmAudio:
        payload = {
            "model_id": self.model,
            "transcript": text,
            "voice": self.voice,
            "output_format": {
                "container": "wav",
                "encoding": "pcm_s16le",
                "sample_rate": self.sample_rate,
            },
        }
        data = await _post_bytes(
            "https://api.cartesia.ai/tts/bytes",
            payload,
            {
                "Authorization": f"Bearer {self.api_key}",
                "Cartesia-Version": self.api_version,
                "Content-Type": "application/json",
            },
        )
        return await asyncio.to_thread(_decode_wav, data)


class GoogleCloudTTS:
    """Google Cloud Text-to-Speech REST adapter using an API key."""

    def __init__(
        self,
        *,
        api_key: str,
        voice: str = "en-US-Standard-C",
        sample_rate: int = 24000,
    ) -> None:
        if not api_key:
            raise ValueError("Google Cloud TTS API key is required.")
        self.api_key = api_key
        self.voice = voice
        self.sample_rate = sample_rate

    async def synthesize(self, text: str) -> PcmAudio:
        payload = {
            "input": {"text": text},
            "voice": {"languageCode": "en-US", "name": self.voice},
            "audioConfig": {
                "audioEncoding": "LINEAR16",
                "sampleRateHertz": self.sample_rate,
            },
        }
        url = "https://texttospeech.googleapis.com/v1/text:synthesize?" + urllib.parse.urlencode(
            {"key": self.api_key}
        )
        body = await _post_json(url, payload, {"Content-Type": "application/json"})
        try:
            audio = base64.b64decode(body["audioContent"], validate=True)
        except (KeyError, ValueError, TypeError) as error:
            raise VoicePipelineError("Google TTS response did not contain valid audioContent.") from error
        return await asyncio.to_thread(_decode_wav, audio)


async def _post_json(
    url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float = 60.0
) -> dict[str, Any]:
    body = await _post_bytes(url, payload, headers, timeout=timeout)
    try:
        return json.loads(body)
    except json.JSONDecodeError as error:
        raise VoicePipelineError("TTS provider returned invalid JSON.") from error


async def _post_bytes(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    *,
    timeout: float = 60.0,
) -> bytes:
    encoded = json.dumps(payload).encode("utf-8")

    def request() -> bytes:
        req = urllib.request.Request(url, data=encoded, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            detail = error.read(1024).decode("utf-8", errors="replace")
            raise VoicePipelineError(
                f"TTS provider returned HTTP {error.code}: {detail}"
            ) from error
        except urllib.error.URLError as error:
            raise VoicePipelineError(f"Could not reach the TTS provider: {error.reason}") from error

    return await asyncio.to_thread(request)
