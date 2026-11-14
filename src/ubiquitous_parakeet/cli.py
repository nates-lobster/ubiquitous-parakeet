"""Command-line entrypoints for local setup and safe deterministic checks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .audio_io import capture_and_playback_test, format_audio_devices, list_audio_devices
from .database import initialize_database
from .gate import check_eligibility, reserve_proactive_if_eligible
from .hardware import create_presence_sensor
from .workspace import initialize_workspace

DEFAULT_DEV_WORKSPACE = ".local/workspace"


def _workspace_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--workspace",
        default=DEFAULT_DEV_WORKSPACE,
        help=f"private runtime workspace (default: {DEFAULT_DEV_WORKSPACE})",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="parakeet", description="Local-first companion prototype tools")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="create templates, plugins folder, and SQLite database")
    _workspace_arg(init)

    db = commands.add_parser("db-init", help="initialize the local SQLite schema")
    _workspace_arg(db)

    hardware = commands.add_parser("hardware-status", help="read presence through the selected HAL")
    hardware.add_argument("--hardware-mode", choices=("auto", "mock", "real"), default=None)
    hardware.add_argument("--mock-present", action="store_true", help="simulate presence in mock mode")
    hardware.add_argument("--gpio-pin", type=int, help="BCM pin connected to a sensor's digital OUT")
    hardware.add_argument("--sensor-interface", choices=("uart", "gpio"), default=None)

    gate = commands.add_parser("gate", help="evaluate local call eligibility; no model is called")
    _workspace_arg(gate)
    gate.add_argument("--trigger", choices=("proactive", "reactive"), required=True)
    gate.add_argument(
        "--presence", choices=("auto", "present", "absent"), default="auto",
        help="auto reads the HAL; present/absent are explicit test inputs",
    )
    gate.add_argument("--hardware-mode", choices=("auto", "mock", "real"), default=None)
    gate.add_argument("--mock-present", action="store_true", help="simulate presence in mock mode")
    gate.add_argument("--gpio-pin", type=int, help="BCM pin connected to a sensor's digital OUT")
    gate.add_argument("--sensor-interface", choices=("uart", "gpio"), default=None)
    gate.add_argument("--cooldown-minutes", type=int, default=45)
    gate.add_argument(
        "--reserve-proactive", action="store_true",
        help="atomically reserve this proactive attempt before model invocation",
    )

    audio = commands.add_parser("audio", help="discover and test system audio devices")
    audio_commands = audio.add_subparsers(dest="audio_command", required=True)
    audio_commands.add_parser("devices", help="list discovered input/output devices")
    audio_test = audio_commands.add_parser(
        "test", help="record a short sample in memory and optionally play it back"
    )
    audio_test.add_argument("--duration", type=float, default=3.0, help="recording length in seconds (max 60)")
    audio_test.add_argument("--input-device", help="device index or unique name; defaults to the OS input device")
    audio_test.add_argument("--output-device", help="device index or unique name; defaults to the OS output device")
    audio_test.add_argument("--sample-rate", type=int, help="override the input device's default sample rate")
    audio_test.add_argument("--no-playback", action="store_true", help="test capture only")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "audio":
        try:
            if args.audio_command == "devices":
                print(format_audio_devices(list_audio_devices()))
                return 0
            print(
                f"Recording for {args.duration:g} seconds; speak after the prompt. "
                "The sample stays in memory and is not saved."
            )
            result = capture_and_playback_test(
                duration_seconds=args.duration,
                input_device=args.input_device,
                output_device=args.output_device,
                sample_rate=args.sample_rate,
                playback=not args.no_playback,
            )
            output = (
                f" to [{result.output_device.index}] {result.output_device.name}"
                if result.output_device is not None
                else " (playback skipped)"
            )
            print(
                f"Audio test complete: captured {result.duration_seconds:g}s at "
                f"{result.sample_rate} Hz from [{result.input_device.index}] "
                f"{result.input_device.name}{output}."
            )
            return 0
        except (RuntimeError, ValueError) as error:
            print(f"Audio error: {error}", file=sys.stderr)
            return 2

    if args.command == "init":
        result = initialize_workspace(args.workspace)
        print(json.dumps(result, indent=2))
        print("Local workspace ready. Personal data and the database are git-ignored by default.")
        return 0

    if args.command == "db-init":
        database = initialize_database(Path(args.workspace).expanduser() / "data" / "companion.db")
        print(f"SQLite schema ready: {database}")
        return 0

    if args.command == "hardware-status":
        sensor = create_presence_sensor(
            mode=args.hardware_mode,
            mock_present=args.mock_present,
            gpio_pin=args.gpio_pin,
            interface=args.sensor_interface,
        )
        try:
            print(json.dumps({"backend": sensor.backend, "present": sensor.is_present()}))
        finally:
            sensor.close()
        return 0

    if args.command == "gate":
        database = initialize_database(Path(args.workspace).expanduser() / "data" / "companion.db")
        if args.presence == "present":
            present = True
        elif args.presence == "absent":
            present = False
        else:
            sensor = create_presence_sensor(
                mode=args.hardware_mode,
                mock_present=args.mock_present,
                gpio_pin=args.gpio_pin,
                interface=args.sensor_interface,
            )
            try:
                present = sensor.is_present()
            finally:
                sensor.close()
        if args.reserve_proactive:
            if args.trigger != "proactive":
                raise SystemExit("--reserve-proactive can only be used with --trigger proactive")
            decision = reserve_proactive_if_eligible(
                database,
                present=present,
                cooldown_minutes=args.cooldown_minutes,
            )
        else:
            decision = check_eligibility(
                database,
                trigger_type=args.trigger,
                present=present,
                cooldown_minutes=args.cooldown_minutes,
            )
        print(json.dumps(decision.to_dict(), indent=2))
        return 0 if decision.allowed else 2

    return 2
