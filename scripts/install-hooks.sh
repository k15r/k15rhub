#!/usr/bin/env bash
# Install git hooks from scripts/ into .git/hooks/
# Run once after cloning: bash scripts/install-hooks.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOKS_DIR="$ROOT/.git/hooks"

install_hook() {
  local name="$1"
  local src="$ROOT/scripts/$name"
  local dst="$HOOKS_DIR/$name"

  cp "$src" "$dst"
  chmod +x "$dst"
  echo "Installed $name"
}

install_hook pre-push

echo "Done. Hooks installed in $HOOKS_DIR"
