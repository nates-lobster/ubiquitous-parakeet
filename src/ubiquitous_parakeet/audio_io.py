"""Portable audio-device discovery and explicit microphone/speaker smoke tests.

Devices are resolved at runtime through PortAudio, so the audio path is not tied
to a particular USB device, ALSA card number, or Raspberry Pi connector.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import importlib
import queue
import threading
import time
from typing import Any, Literal


Direction = Literal["input", "output"]


@dataclass(frozen=True)
class AudioDevice:
    index: int
    name: str
    host_api: str
    input_channels: int
    output_channels: int
    default_sample_rate: int
    is_default_input: bool = False
    is_default_output: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AudioTestResult:
    input_device: AudioDevice
    output_device: AudioDevice | None
    sample_rate: int
    duration_seconds: float
    playback: bool


@dataclass(frozen=True)
class AudioFrame:
    """One captured frame of signed 16-bit little-endian PCM."""

    pcm_s16le: bytes
    sample_rate: int
    channels: int
    captured_at_monotonic: float
    status: str | None = None

    @property
    def frame_count(self) -> int:
        return len(self.pcm_s16le) // (2 * self.channels)


def _sounddevice() -> Any:
    try:
        return importlib.import_module("sounddevice")
    except ImportError as error:
        raise RuntimeError(
            "Audio I/O needs the optional audio extra. Install it with "
            "'.venv/bin/python -m pip install -e \".[audio]\"'. On Raspberry Pi OS, "
            "also install the system PortAudio library (for example, "
            "'sudo apt install libportaudio2')."
        ) from error


def _default_indices(sd: Any) -> tuple[int | None, int | None]:
    try:
        values = sd.default.device
    except (AttributeError, TypeError):
        return None, None
    if not isinstance(values, (tuple, list)) or len(values) < 2:
        return None, None

    def normalize(value: Any) -> int | None:
        try:
            index = int(value)
        except (TypeError, ValueError):
            return None
        return index if index >= 0 else None

    return normalize(values[0]), normalize(values[1])


def list_audio_devices() -> list[AudioDevice]:
    """Return discovered PortAudio devices and mark the current system defaults."""
    sd = _sounddevice()
    try:
        rows = sd.query_devices()
        host_apis = sd.query_hostapis()
        default_input, default_output = _default_indices(sd)
    except Exception as error:
        raise RuntimeError(f"Could not enumerate audio devices: {error}") from error
    devices: list[AudioDevice] = []
    for index, row in enumerate(rows):
        host_api_index = int(row.get("hostapi", -1))
        host_api = (
            str(host_apis[host_api_index].get("name", "unknown"))
            if 0 <= host_api_index < len(host_apis)
            else "unknown"
        )
        devices.append(
            AudioDevice(
                index=index,
                name=str(row.get("name", f"Audio device {index}")),
                host_api=host_api,
                input_channels=int(row.get("max_input_channels", 0)),
                output_channels=int(row.get("max_output_channels", 0)),
                default_sample_rate=int(float(row.get("default_samplerate", 0))),
                is_default_input=index == default_input,
                is_default_output=index == default_output,
            )
        )
    return devices


def _resolve_device(
    devices: list[AudioDevice], direction: Direction, selection: str | int | None
) -> AudioDevice:
    channel_count = (
        (lambda device: device.input_channels)
        if direction == "input"
        else (lambda device: device.output_channels)
    )
    candidates = [device for device in devices if channel_count(device) > 0]
    if not candidates:
        raise RuntimeError(f"No audio {direction} devices were found.")

    if selection is None or (
        isinstance(selection, str) and selection.strip().lower() in {"", "default", "auto"}
    ):
        default_flag = "is_default_input" if direction == "input" else "is_default_output"
        default_device = next(
            (device for device in candidates if getattr(device, default_flag)), None
        )
        if default_device is not None:
            return default_device
        if len(candidates) == 1:
            return candidates[0]
        choices = "; ".join(f"{device.index}: {device.name}" for device in candidates)
        raise RuntimeError(
            f"No system-default audio {direction} is configured and multiple devices are available. "
            f"Choose one by index or name: {choices}"
        )

    if isinstance(selection, int) or (
        isinstance(selection, str) and selection.strip().isdigit()
    ):
        index = int(selection)
        match = next((device for device in candidates if device.index == index), None)
        if match is None:
            raise ValueError(
                f"Audio {direction} device index {index} is unavailable or lacks {direction} channels."
            )
        return match

    name = str(selection).strip().casefold()
    exact = [device for device in candidates if device.name.casefold() == name]
    matches = exact or [device for device in candidates if name in device.name.casefold()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"No audio {direction} device matches {selection!r}.")
    choices = "; ".join(f"{device.index}: {device.name}" for device in matches)
    raise ValueError(f"Audio {direction} device name {selection!r} is ambiguous: {choices}")


def resolve_audio_device(
    direction: Direction, selection: str | int | None = None
) -> AudioDevice:
    """Resolve an optional index/name; by default, follow the current OS default."""
    return _resolve_device(list_audio_devices(), direction, selection)


class AudioInputStream:
    """Read device-independent mono/stereo PCM frames from a discovered input.

    The callback only copies frames into a bounded queue; wakeword/VAD/STT work
    belongs in the consumer. Queue overflow is reported rather than silently
    dropping audio and corrupting a turn.
    """

    def __init__(
        self,
        *,
        device: str | int | None = None,
        sample_rate: int | None = None,
        channels: int = 1,
        block_duration_ms: int = 30,
        queue_depth: int = 50,
    ) -> None:
        if channels < 1:
            raise ValueError("channels must be at least 1")
        if sample_rate is not None and not 8000 <= sample_rate <= 96000:
            raise ValueError("sample_rate must be between 8000 and 96000 Hz")
        if not 5 <= block_duration_ms <= 200:
            raise ValueError("block_duration_ms must be between 5 and 200")
        if queue_depth < 1:
            raise ValueError("queue_depth must be at least 1")
        self.device_selection = device
        self.sample_rate = sample_rate
        self.channels = channels
        self.block_duration_ms = block_duration_ms
        self._frames: queue.Queue[AudioFrame] = queue.Queue(maxsize=queue_depth)
        self._overflowed = threading.Event()
        self._stream: Any = None
        self.selected_device: AudioDevice | None = None

    def start(self) -> "AudioInputStream":
        if self._stream is not None:
            return self
        sd = _sounddevice()
        self.selected_device = _resolve_device(
            list_audio_devices(), "input", self.device_selection
        )
        self.sample_rate = self.sample_rate or self.selected_device.default_sample_rate
        if self.sample_rate <= 0:
            raise RuntimeError(
                f"The selected input device {self.selected_device.name!r} has no reported "
                "default sample rate; pass sample_rate explicitly."
            )
        block_size = max(1, round(self.sample_rate * self.block_duration_ms / 1000))

        def on_audio(indata: Any, frames: int, _time_info: Any, status: Any) -> None:
            frame = AudioFrame(
                pcm_s16le=bytes(indata),
                sample_rate=int(self.sample_rate),
                channels=self.channels,
                captured_at_monotonic=time.monotonic(),
                status=str(status) if status else None,
            )
            try:
                self._frames.put_nowait(frame)
            except queue.Full:
                self._overflowed.set()

        stream = None
        try:
            stream = sd.RawInputStream(
                device=self.selected_device.index,
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="int16",
                blocksize=block_size,
                callback=on_audio,
            )
            stream.start()
        except Exception as error:
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
            raise RuntimeError(f"Could not start audio input: {error}") from error
        self._stream = stream
        return self

    def read(self, timeout: float | None = None) -> AudioFrame:
        if self._stream is None:
            raise RuntimeError("Audio input is not started; call start() first.")
        if self._overflowed.is_set():
            raise RuntimeError(
                "Audio input queue overflowed; the consumer is not keeping up. "
                "Close and restart the stream rather than using a partial turn."
            )
        try:
            return self._frames.get(timeout=timeout)
        except queue.Empty as error:
            raise TimeoutError("Timed out waiting for an audio frame.") from error

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()

    def __enter__(self) -> "AudioInputStream":
        return self.start()

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        self.close()


class AudioPlayback:
    """Play signed 16-bit little-endian PCM to a selected/default output device."""

    def __init__(
        self,
        *,
        sample_rate: int,
        device: str | int | None = None,
        channels: int = 1,
    ) -> None:
        if not 8000 <= sample_rate <= 96000:
            raise ValueError("sample_rate must be between 8000 and 96000 Hz")
        if channels < 1:
            raise ValueError("channels must be at least 1")
        self.sample_rate = sample_rate
        self.device_selection = device
        self.channels = channels
        self.selected_device: AudioDevice | None = None
        self._stream: Any = None

    def start(self) -> "AudioPlayback":
        if self._stream is not None:
            return self
        sd = _sounddevice()
        self.selected_device = _resolve_device(
            list_audio_devices(), "output", self.device_selection
        )
        stream = None
        try:
            stream = sd.RawOutputStream(
                device=self.selected_device.index,
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="int16",
            )
            stream.start()
        except Exception as error:
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
            raise RuntimeError(f"Could not start audio output: {error}") from error
        self._stream = stream
        return self

    def write(self, pcm_s16le: bytes) -> None:
        if self._stream is None:
            raise RuntimeError("Audio output is not started; call start() first.")
        bytes_per_frame = 2 * self.channels
        if len(pcm_s16le) % bytes_per_frame:
            raise ValueError(
                f"PCM byte length must be a multiple of {bytes_per_frame} "
                "for the configured channel count."
            )
        try:
            self._stream.write(pcm_s16le)
        except Exception as error:
            raise RuntimeError(f"Audio playback failed: {error}") from error

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()

    def __enter__(self) -> "AudioPlayback":
        return self.start()

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        self.close()


def capture_and_playback_test(
    *,
    duration_seconds: float = 3.0,
    input_device: str | int | None = None,
    output_device: str | int | None = None,
    sample_rate: int | None = None,
    playback: bool = True,
) -> AudioTestResult:
    """Record a short sample in memory and optionally play it back; save no audio."""
    if not 0 < duration_seconds <= 60:
        raise ValueError("duration_seconds must be greater than 0 and no more than 60")
    if sample_rate is not None and not 8000 <= sample_rate <= 96000:
        raise ValueError("sample_rate must be between 8000 and 96000 Hz")

    sd = _sounddevice()
    devices = list_audio_devices()
    selected_input = _resolve_device(devices, "input", input_device)
    selected_output = _resolve_device(devices, "output", output_device) if playback else None
    rate = sample_rate or selected_input.default_sample_rate
    if rate <= 0:
        raise RuntimeError(
            f"The selected input device {selected_input.name!r} has no reported default sample rate. "
            "Pass --sample-rate explicitly."
        )

    frames = round(duration_seconds * rate)
    try:
        recording = sd.rec(
            frames,
            samplerate=rate,
            channels=1,
            dtype="float32",
            device=selected_input.index,
        )
        sd.wait()
        if selected_output is not None:
            sd.play(recording, samplerate=rate, device=selected_output.index)
            sd.wait()
    except Exception as error:
        raise RuntimeError(f"Audio test failed: {error}") from error

    return AudioTestResult(
        input_device=selected_input,
        output_device=selected_output,
        sample_rate=rate,
        duration_seconds=duration_seconds,
        playback=playback,
    )


def format_audio_devices(devices: list[AudioDevice]) -> str:
    """Human-readable device list for the Pi terminal."""
    lines = ["Audio input devices:"]
    inputs = [device for device in devices if device.input_channels > 0]
    if inputs:
        for device in inputs:
            default = " (system default)" if device.is_default_input else ""
            lines.append(
                f"  [{device.index}] {device.name} — {device.host_api}, "
                f"{device.input_channels} input channel(s), "
                f"reported rate {device.default_sample_rate} Hz{default}"
            )
    else:
        lines.append("  (none found)")

    lines.append("Audio output devices:")
    outputs = [device for device in devices if device.output_channels > 0]
    if outputs:
        for device in outputs:
            default = " (system default)" if device.is_default_output else ""
            lines.append(
                f"  [{device.index}] {device.name} — {device.host_api}, "
                f"{device.output_channels} output channel(s), "
                f"reported rate {device.default_sample_rate} Hz{default}"
            )
    else:
        lines.append("  (none found)")
    return "\n".join(lines)
