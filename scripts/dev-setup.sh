#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
PYTHON_BIN="${PYTHON:-python3}"
WITH_MCP=false
WITH_HARDWARE=false
WITH_AUDIO=false
WITH_VOICE=false
WITH_WAKEWORD=false
WITH_VAD=false

for option in "$@"; do
  case "$option" in
    --mcp) WITH_MCP=true ;;
    --hardware) WITH_HARDWARE=true ;;
    --audio) WITH_AUDIO=true ;;
    --voice) WITH_VOICE=true ;;
    --wakeword) WITH_WAKEWORD=true ;;
    --vad) WITH_VAD=true ;;
    *) echo "Usage: $0 [--mcp] [--hardware] [--audio] [--voice] [--wakeword] [--vad]" >&2; exit 2 ;;
  esac
done

"$PYTHON_BIN" -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10+ is required"'
"$PYTHON_BIN" -m venv .venv
EXTRAS=".[dev"
if [[ "$WITH_MCP" == true ]]; then EXTRAS+=",mcp"; fi
if [[ "$WITH_HARDWARE" == true ]]; then EXTRAS+=",hardware"; fi
if [[ "$WITH_AUDIO" == true ]]; then EXTRAS+=",audio"; fi
if [[ "$WITH_VOICE" == true ]]; then EXTRAS+=",voice"; fi
if [[ "$WITH_WAKEWORD" == true ]]; then EXTRAS+=",wakeword"; fi
if [[ "$WITH_VAD" == true ]]; then EXTRAS+=",vad"; fi
EXTRAS+="]"
.venv/bin/python -m pip install -e "$EXTRAS"
if [[ "$WITH_WAKEWORD" == true || "$WITH_VAD" == true ]]; then
  # Force ONNX inference; avoid openWakeWord's optional TFLite runtime wheel,
  # which is not published for every Raspberry Pi/Python combination.
  .venv/bin/python -m pip install --no-deps 'openwakeword>=0.6,<1'
  if ! .venv/bin/python -m pip install 'tflite-runtime>=2.8,<3'; then
    echo "TFLite runtime wheel unavailable; the companion forces ONNX CPU inference, so this optional backend is not used." >&2
  fi
fi
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/parakeet init --workspace .local/workspace
printf '\nReady. Activate with: source .venv/bin/activate\n'
