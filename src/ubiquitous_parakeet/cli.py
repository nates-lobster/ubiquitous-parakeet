"""Command-line entrypoints for local setup and safe deterministic checks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

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

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
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
