"""Hardware abstraction: safe mocks on development hosts, optional Pi sensors."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class PresenceSensor(Protocol):
    backend: str

    def is_present(self) -> bool: ...

    def close(self) -> None: ...


def is_raspberry_pi(model_file: str | Path = "/proc/device-tree/model") -> bool:
    try:
        model = Path(model_file).read_text(errors="replace").replace("\x00", "").lower()
    except OSError:
        return False
    return "raspberry pi" in model


@dataclass
class MockPresenceSensor:
    present: bool = False
    backend: str = "mock"
    output: bool = True

    def is_present(self) -> bool:
        if self.output:
            print(f"[mock HAL] presence sensor -> {'present' if self.present else 'absent'}", file=sys.stderr)
        return self.present

    def close(self) -> None:
        return None


class GPIOZeroPresenceSensor:
    """Read a sensor's digital OUT pin; the electrical/pin choice stays configurable."""

    backend = "gpiozero"

    def __init__(self, pin: int, *, active_state: bool = True) -> None:
        try:
            from gpiozero import DigitalInputDevice
        except ImportError as error:
            raise RuntimeError(
                "GPIO presence needs the optional 'hardware' extra (pip install -e '.[hardware]')."
            ) from error
        self._device = DigitalInputDevice(pin, pull_up=False, active_state=active_state)

    def is_present(self) -> bool:
        return bool(self._device.is_active)

    def close(self) -> None:
        self._device.close()


def create_presence_sensor(
    *,
    mode: str | None = None,
    mock_present: bool = False,
    gpio_pin: int | None = None,
    interface: str | None = None,
    mock_output: bool = True,
) -> PresenceSensor:
    """Select mock/GPIO/UART from environment and Pi detection.

    ``auto`` uses a mock on non-Pi hosts. A detected Pi uses the configured
    ``PARAKEET_PRESENCE_INTERFACE`` (default: ``uart``). Real hardware never
    silently falls back to mock mode; missing configuration or I/O errors fail
    visibly so callers can keep model invocation blocked.
    """
    selected = (mode or os.environ.get("PARAKEET_HARDWARE", "auto")).lower()
    if selected not in {"auto", "mock", "real"}:
        raise ValueError("hardware mode must be 'auto', 'mock', or 'real'")
    if selected == "mock":
        return MockPresenceSensor(present=mock_present, output=mock_output)
    if selected == "auto" and not is_raspberry_pi():
        return MockPresenceSensor(present=mock_present, output=mock_output)

    selected_interface = (
        interface or os.environ.get("PARAKEET_PRESENCE_INTERFACE", "uart")
    ).lower()
    if selected_interface == "uart":
        from .ld2410_uart import LD2410UARTPresenceSensor

        port = os.environ.get("PARAKEET_LD2410_PORT", "/dev/serial0")
        baudrate = int(os.environ.get("PARAKEET_LD2410_BAUD", "256000"))
        read_timeout = float(os.environ.get("PARAKEET_LD2410_TIMEOUT", "1.0"))
        return LD2410UARTPresenceSensor(port, baudrate=baudrate, read_timeout=read_timeout)
    if selected_interface == "gpio":
        pin_value = gpio_pin if gpio_pin is not None else os.environ.get("PARAKEET_PRESENCE_GPIO")
        if pin_value is None:
            raise RuntimeError(
                "GPIO presence selected, but PARAKEET_PRESENCE_GPIO is unset. "
                "Choose the sensor's digital OUT pin after wiring is confirmed."
            )
        try:
            pin = int(pin_value)
        except (TypeError, ValueError) as error:
            raise ValueError("GPIO pin must be a numeric BCM pin") from error
        return GPIOZeroPresenceSensor(pin)
    raise ValueError("presence interface must be 'uart' or 'gpio'")
