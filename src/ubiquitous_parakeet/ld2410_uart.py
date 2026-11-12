"""Read-only UART decoder for the LD2410 basic periodic report protocol.

The driver does not send configuration commands or enable engineering mode. The
normal report provides target state, distances, and energy values.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

REPORT_HEADER = bytes((0xF4, 0xF3, 0xF2, 0xF1))
REPORT_FOOTER = bytes((0xF8, 0xF7, 0xF6, 0xF5))
MIN_REPORT_PAYLOAD = 13
MAX_REPORT_PAYLOAD = 64


@dataclass(frozen=True)
class LD2410Report:
    target_state: int
    moving_distance_cm: int
    moving_energy: int
    stationary_distance_cm: int
    stationary_energy: int
    detection_distance_cm: int
    report_mode: int

    @property
    def present(self) -> bool:
        return self.target_state != 0


def parse_report_frame(frame: bytes) -> LD2410Report:
    """Validate and decode one complete LD2410 normal/engineering report frame."""
    if len(frame) < 6 + MIN_REPORT_PAYLOAD + 4:
        raise ValueError("LD2410 report frame is too short")
    if not frame.startswith(REPORT_HEADER) or not frame.endswith(REPORT_FOOTER):
        raise ValueError("Invalid LD2410 report header or footer")
    payload_length = int.from_bytes(frame[4:6], "little")
    if payload_length < MIN_REPORT_PAYLOAD or payload_length > MAX_REPORT_PAYLOAD:
        raise ValueError(f"Unsupported LD2410 report payload length: {payload_length}")
    if len(frame) != 6 + payload_length + len(REPORT_FOOTER):
        raise ValueError("LD2410 frame length does not match its length field")

    payload = frame[6:-4]
    if payload[0] not in (0x01, 0x02):
        raise ValueError("Unknown LD2410 report mode")
    if payload[1] != 0xAA or payload[-2:] != b"\x55\x00":
        raise ValueError("Invalid LD2410 report markers")
    target_state = payload[2]
    if target_state not in (0, 1, 2, 3):
        raise ValueError(f"Invalid LD2410 target state: {target_state}")

    return LD2410Report(
        target_state=target_state,
        moving_distance_cm=int.from_bytes(payload[3:5], "little"),
        moving_energy=payload[5],
        stationary_distance_cm=int.from_bytes(payload[6:8], "little"),
        stationary_energy=payload[8],
        detection_distance_cm=int.from_bytes(payload[9:11], "little"),
        report_mode=payload[0],
    )


class LD2410UARTPresenceSensor:
    """Open an LD2410 UART and return the next valid target-state report."""

    backend = "ld2410-uart"

    def __init__(
        self,
        port: str = "/dev/serial0",
        *,
        baudrate: int = 256000,
        read_timeout: float = 1.0,
    ) -> None:
        if read_timeout <= 0:
            raise ValueError("read_timeout must be positive")
        try:
            import serial
        except ImportError as error:
            raise RuntimeError(
                "LD2410 UART needs pyserial; install with pip install -e '.[hardware]'."
            ) from error
        try:
            self._serial = serial.Serial(
                port=port,
                baudrate=baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=min(0.1, read_timeout),
            )
        except (OSError, serial.SerialException) as error:
            raise RuntimeError(f"Could not open LD2410 UART {port!r}: {error}") from error
        self._read_timeout = read_timeout
        self._buffer = bytearray()

    def _take_report_from_buffer(self) -> LD2410Report | None:
        while True:
            start = self._buffer.find(REPORT_HEADER)
            if start < 0:
                # Preserve a partial header across reads.
                if len(self._buffer) > len(REPORT_HEADER) - 1:
                    del self._buffer[: -(len(REPORT_HEADER) - 1)]
                return None
            if start:
                del self._buffer[:start]
            if len(self._buffer) < 6:
                return None
            payload_length = int.from_bytes(self._buffer[4:6], "little")
            if not MIN_REPORT_PAYLOAD <= payload_length <= MAX_REPORT_PAYLOAD:
                del self._buffer[0]
                continue
            frame_length = 6 + payload_length + len(REPORT_FOOTER)
            if len(self._buffer) < frame_length:
                return None
            candidate = bytes(self._buffer[:frame_length])
            if not candidate.endswith(REPORT_FOOTER):
                del self._buffer[0]
                continue
            del self._buffer[:frame_length]
            try:
                return parse_report_frame(candidate)
            except ValueError:
                # Skip a malformed frame and continue searching for the next one.
                continue

    def read_report(self) -> LD2410Report:
        deadline = time.monotonic() + self._read_timeout
        while time.monotonic() < deadline:
            report = self._take_report_from_buffer()
            if report is not None:
                return report
            remaining = deadline - time.monotonic()
            chunk = self._serial.read(max(1, min(64, int(remaining * 256000 / 10))))
            if chunk:
                self._buffer.extend(chunk)
        raise TimeoutError(f"No valid LD2410 report received within {self._read_timeout:g}s")

    def is_present(self) -> bool:
        return self.read_report().present

    def close(self) -> None:
        self._serial.close()
