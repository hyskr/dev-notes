#!/usr/bin/env bash
set -euo pipefail

PLUGIN_NAME="ai-development-journal"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PURGE_STATE=0

if [ "${1:-}" = "--purge-state" ]; then
  PURGE_STATE=1
elif [ "$#" -gt 0 ]; then
  echo "Usage: $0 [--purge-state]" >&2
  exit 2
fi

if command -v codex >/dev/null 2>&1; then
  codex plugin remove "$PLUGIN_NAME@personal" >/dev/null 2>&1 || true
fi

ARGS=(uninstall)
if [ "$PURGE_STATE" -eq 1 ]; then
  ARGS+=(--purge-state)
fi

python3 "$SCRIPT_DIR/scripts/manage_install.py" "${ARGS[@]}" | python3 -m json.tool
echo "Journal Markdown files in Obsidian were left untouched."
