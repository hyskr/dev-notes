#!/usr/bin/env bash
set -euo pipefail

PLUGIN_NAME="ai-development-journal"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VAULT_PATH="${AI_DEV_JOURNAL_VAULT:-}"
SKIP_CODEX=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --vault)
      VAULT_PATH="${2:?--vault requires a path}"
      shift 2
      ;;
    --skip-codex)
      SKIP_CODEX=1
      shift
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 2
      ;;
  esac
done

command -v python3 >/dev/null 2>&1 || {
  echo "Python 3 is required." >&2
  exit 1
}

INSTALL_ARGS=(install --source "$SCRIPT_DIR")
if [ -n "$VAULT_PATH" ]; then
  INSTALL_ARGS+=(--vault "$VAULT_PATH")
fi

INSTALL_RESULT="$(python3 "$SCRIPT_DIR/scripts/manage_install.py" "${INSTALL_ARGS[@]}")"
echo "$INSTALL_RESULT" | python3 -m json.tool

MARKETPLACE_NAME="$(printf '%s' "$INSTALL_RESULT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["marketplace"])')"
INSTALLED_PLUGIN="$(printf '%s' "$INSTALL_RESULT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["plugin"])')"

if [ "$SKIP_CODEX" -eq 0 ] && command -v codex >/dev/null 2>&1; then
  codex plugin add "$PLUGIN_NAME@$MARKETPLACE_NAME"
else
  echo "Codex CLI not found or skipped; marketplace entry was still created."
fi

python3 "$INSTALLED_PLUGIN/scripts/journal.py" doctor || true

echo
echo "Dev Notes installed."
echo "Claude Code hooks were merged without replacing existing hooks."
echo "Codex uses plugin-bundled hooks, so the existing notify command was not changed."
echo "Open a new Codex task and run /hooks once to review and trust the new hook definition."
