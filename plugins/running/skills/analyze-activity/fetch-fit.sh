#!/usr/bin/env bash
# fetch-fit.sh — download a FIT file for the analyze-activity skill via Garmin Connect.
#
# Usage:
#   fetch-fit.sh <user>                    # latest activity
#   fetch-fit.sh <user> <activity-id>      # specific numeric ID
#   fetch-fit.sh <user> <YYYY-MM-DD>       # first activity on that date
#   fetch-fit.sh <user> --list [<count>]   # list recent activities (default 20)
#
# Output (stdout):
#   ACTIVITY_ID=<id>\tDATE=<YYYY-MM-DD>\tTITLE=<title>\tDIST_KM=<km>\tDUR_SEC=<s>\tDEST=<path>
#   ---FIT-ANALYZER---
#   <fit-analyzer output>
#
# Requires: uv (https://astral.sh/uv), garmin_email in config

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

USER_ARG="${1:-}"

[[ -n "$USER_ARG" ]] || { echo "ERROR: Usage: fetch-fit.sh <user> [activity-id|YYYY-MM-DD|--list [count]]" >&2; exit 1; }

command -v uv >/dev/null 2>&1 || { echo "ERROR: uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2; exit 1; }

exec uv run --script "$SCRIPT_DIR/fetch-fit-garmin.py" "$USER_ARG" "${@:2}"
