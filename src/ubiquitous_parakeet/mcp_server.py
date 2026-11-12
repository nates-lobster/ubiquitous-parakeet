"""Optional stdio MCP tools for a compatible Hermes/MCP host.

These tools expose local state and a gate decision. They do not call a model API;
Hermes must enforce the gate in its pre-inference/orchestration path to guarantee it.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .database import record_interaction, record_vital
from .gate import check_eligibility
from .hardware import create_presence_sensor
from .workspace import initialize_workspace


def main() -> None:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as error:
        raise SystemExit("Install MCP support with: pip install -e '.[mcp]'") from error

    parser = argparse.ArgumentParser(prog="parakeet-mcp")
    parser.add_argument("--workspace", default=".local/workspace")
    parser.add_argument("--hardware-mode", choices=("auto", "mock", "real"), default=None)
    parser.add_argument("--mock-present", action="store_true", help="simulate presence in mock mode")
    parser.add_argument("--gpio-pin", type=int, default=None)
    args = parser.parse_args()

    workspace = Path(args.workspace).expanduser().resolve()
    initialize_workspace(workspace)
    database = workspace / "data" / "companion.db"
    sensor = create_presence_sensor(
        mode=args.hardware_mode,
        mock_present=args.mock_present,
        gpio_pin=args.gpio_pin,
    )
    server = FastMCP("ubiquitous-parakeet")

    @server.tool()
    def presence_status() -> dict[str, object]:
        """Read the local presence sensor. Mock mode is explicitly identified."""
        return {"backend": sensor.backend, "present": sensor.is_present()}

    @server.tool()
    def check_in_eligibility(trigger_type: str) -> dict[str, object]:
        """Apply local presence and cooldown rules; does not invoke any model."""
        present = sensor.is_present()
        return check_eligibility(
            database, trigger_type=trigger_type, present=present
        ).to_dict()

    @server.tool()
    def record_interaction_event(
        trigger_type: str,
        topic_category: str,
        engagement_score: int | None = None,
    ) -> dict[str, int]:
        """Record a completed interaction locally; engagement is optional (0 to 5)."""
        event_id = record_interaction(
            database,
            trigger_type=trigger_type,
            topic_category=topic_category,
            engagement_score=engagement_score,
        )
        return {"id": event_id}

    @server.tool()
    def record_local_metric(metric_type: str, value: str) -> dict[str, int]:
        """Record a locally supplied metric. This does not verify intake or health."""
        event_id = record_vital(database, metric_type=metric_type, value=value)
        return {"id": event_id}

    try:
        server.run(transport="stdio")
    finally:
        sensor.close()


if __name__ == "__main__":
    main()
