#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
DEST="$HERMES_HOME/plugins/ubiquitous-parakeet"

if [[ -e "$DEST" ]]; then
  echo "Refusing to overwrite existing plugin at $DEST" >&2
  echo "Back it up or remove it deliberately, then rerun this installer." >&2
  exit 1
fi

mkdir -p "$HERMES_HOME/plugins"
cp -R "$ROOT/hermes_plugin" "$DEST"
echo "Installed Hermes plugin files at: $DEST"
echo "Next: run 'hermes plugins enable ubiquitous-parakeet' in the same HERMES_HOME."
echo "Review the plugin and configure its CLI/workspace settings before enabling it."
if [[ -e "$HERMES_HOME/SOUL.md" ]]; then
  echo "Existing $HERMES_HOME/SOUL.md was preserved. Review/merge the workspace SOUL.md manually."
else
  echo "Copy the reviewed workspace SOUL.md to $HERMES_HOME/SOUL.md to load persona rules."
fi
