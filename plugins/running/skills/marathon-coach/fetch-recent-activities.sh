#!/usr/bin/env bash
# fetch-recent-activities.sh — download recent FIT files for the marathon-coach skill via Garmin Connect.
#
# If no config exists, prints NO_CONFIG and exits 0.
# If garmin_email is not set in config, prints NO_TOKEN and exits 0.
#
# Usage:
#   fetch-recent-activities.sh <user> [count]   # default: 5 most recent activities
#
# Requires: uv (https://astral.sh/uv), garmin_email in config

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GARMIN_FETCH="$(dirname "$SCRIPT_DIR")/analyze-activity/fetch-fit-garmin.py"

USER_ARG="${1:-}"
COUNT="${2:-5}"

[[ -n "$USER_ARG" ]] || { echo "ERROR: Usage: fetch-recent-activities.sh <user> [count]" >&2; exit 1; }

CONFIG="$HOME/.marathon-coach/$USER_ARG/config.yaml"
[[ -f "$CONFIG" ]] || { echo "NO_CONFIG"; exit 0; }

read_yaml_field() {
    local file="$1" field="$2"
    grep "^${field}:" "$file" | head -1 | sed "s/^${field}: *//" | tr -d '"' | tr -d "'"
}

GARMIN_EMAIL=$(read_yaml_field "$CONFIG" garmin_email)
[[ -n "$GARMIN_EMAIL" ]] || { echo "NO_TOKEN"; exit 0; }

command -v uv >/dev/null 2>&1 || { echo "ERROR: uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2; exit 1; }
[[ -f "$GARMIN_FETCH" ]] || { echo "ERROR: fetch-fit-garmin.py not found at $GARMIN_FETCH" >&2; exit 1; }

exec uv run --script "$GARMIN_FETCH" "$USER_ARG" --batch "$COUNT"
