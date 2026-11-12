"""Provision a private runtime workspace from safe, non-personal templates."""

from __future__ import annotations

import shutil
from pathlib import Path

from .database import initialize_database

TEMPLATE_DIR = Path(__file__).with_name("templates")


def initialize_workspace(workspace_path: str | Path) -> dict[str, object]:
    """Create the requested layout; never overwrite user-edited biography or logs."""
    root = Path(workspace_path).expanduser().resolve()
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "plugins").mkdir(parents=True, exist_ok=True)
    created: list[str] = []
    for name in ("SOUL.md", "LIFE_STORY.md", "TODAY_LOG.md", "AGENTS.md"):
        target = root / name
        if not target.exists():
            shutil.copyfile(TEMPLATE_DIR / name, target)
            created.append(name)

    # Keep the workspace layout discoverable while keeping implementation in one package.
    plugin_templates = {
        "hardware_mcp.py": (
            '"""Workspace launcher for local MCP tools."""\n'
            "from ubiquitous_parakeet.mcp_server import main\n\n"
            'if __name__ == "__main__":\n'
            "    main()\n"
        ),
        "tts_piper.py": (
            '"""Entrypoint reference for local Piper TTS; see the project docs."""\n'
            "from ubiquitous_parakeet.tts_piper import synthesize, speak\n"
        ),
    }
    for name, content in plugin_templates.items():
        target = root / "plugins" / name
        if not target.exists():
            target.write_text(content, encoding="utf-8")
            created.append(f"plugins/{name}")

    database_path = initialize_database(root / "data" / "companion.db")
    return {"workspace": str(root), "created": created, "database": str(database_path)}
