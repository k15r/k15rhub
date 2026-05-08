#!/usr/bin/env bash
# fetch-recent-runs.sh — fetch recent running activities for the marathon-coach skill
#
# Reads ~/.marathon-coach/config.yaml for the Runalyze token.
# If no token is configured, prints NO_TOKEN and exits 0.
#
# Output (stdout):
#   FETCH_MODE=runalyze\tCOUNT=<n>\tDATE_RANGE=<from>..<to>
#   ---RUNS---
#   <JSON array of activities>
#
# Exits non-zero on API or parse errors.

set -euo pipefail

CONFIG="$HOME/.marathon-coach/config.yaml"
API="https://runalyze.com/api/v1"

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
die() { echo "ERROR: $*" >&2; exit 1; }

read_yaml_field() {
    local file="$1" field="$2"
    grep "^${field}:" "$file" | head -1 | sed "s/^${field}: *//" | tr -d '"' | tr -d "'"
}

# ---------------------------------------------------------------------------
# Check config
# ---------------------------------------------------------------------------
[[ -f "$CONFIG" ]] || { echo "NO_CONFIG"; exit 0; }

TOKEN=$(read_yaml_field "$CONFIG" runalyze_token)
[[ -n "$TOKEN" ]] || { echo "NO_TOKEN"; exit 0; }

auth_curl() { curl -sf -H "token: $TOKEN" "$@"; }

command -v jq >/dev/null 2>&1 || die "jq not found. Install it (e.g. brew install jq)"

# ---------------------------------------------------------------------------
# Fetch last 10 running activities
# ---------------------------------------------------------------------------
LIST=$(auth_curl "$API/activity?itemsPerPage=20")

RUNS=$(echo "$LIST" | jq '[
    .[]
    | select(
        (.sport.category // "" == "running") and
        (.distance // 0 > 0.1) and
        (.date_time | startswith("203") | not)
      )
    | {
        id: .id,
        date: (.date_time // "" | .[0:10]),
        title: (.title // ""),
        distance_km: (.distance // 0),
        duration_sec: (.duration // 0),
        avg_pace_sec_km: (
            if (.distance // 0) > 0
            then ((.duration // 0) / (.distance // 1)) | floor
            else null
            end
        ),
        avg_hr: (.avg_heart_rate // null)
      }
] | .[0:10]')

COUNT=$(echo "$RUNS" | jq 'length')
[[ "$COUNT" -gt 0 ]] || die "No recent running activities found in Runalyze"

DATE_FROM=$(echo "$RUNS" | jq -r '.[-1].date // ""')
DATE_TO=$(echo "$RUNS"   | jq -r '.[0].date // ""')

printf "FETCH_MODE=runalyze\tCOUNT=%s\tDATE_RANGE=%s..%s\n" "$COUNT" "$DATE_FROM" "$DATE_TO"
echo "---RUNS---"
echo "$RUNS"
