#!/usr/bin/env bash
# fetch-fit.sh — download a Runalyze FIT file for the analyze-run skill
#
# Usage:
#   fetch-fit.sh <user>                    # latest running activity
#   fetch-fit.sh <user> <activity-id>      # specific numeric ID
#   fetch-fit.sh <user> <YYYY-MM-DD>       # first running activity on that date
#
# Output (stdout, tab-separated on one line):
#   <activity_id>\t<date>\t<title>\t<distance_km>\t<duration_sec>\t<dest_path>
#
# Then runs fit-analyzer on the downloaded file and prints its output.
# Exits non-zero on any error.

set -euo pipefail

API="https://runalyze.com/api/v1"

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
die() {
  echo "ERROR: $*" >&2
  exit 1
}

read_yaml_field() {
  local file="$1" field="$2"
  grep "^${field}:" "$file" | head -1 | sed "s/^${field}: *//" | tr -d '"' | tr -d "'"
}

# ---------------------------------------------------------------------------
# Step 0 — resolve user and config
# ---------------------------------------------------------------------------
USER_ARG="${1:-}"
ARG="${2:-}"

[[ -n "$USER_ARG" ]] || die "Usage: fetch-fit.sh <user> [activity-id|YYYY-MM-DD]"

CONFIG="$HOME/.marathon-coach/$USER_ARG/config.yaml"
[[ -f "$CONFIG" ]] || die "Config not found: $CONFIG"

TOKEN=$(read_yaml_field "$CONFIG" runalyze_token)
[[ -n "$TOKEN" ]] || die "runalyze_token not set in $CONFIG"

OUTPUT_DIR=$(read_yaml_field "$CONFIG" output_dir)
[[ -n "$OUTPUT_DIR" ]] || die "output_dir not set in $CONFIG"

FIT_DIR="$OUTPUT_DIR/Lauftagebuch/fit"

command -v fit-analyzer >/dev/null 2>&1 || die "fit-analyzer not found. Install it from https://github.com/k15r/fit-analyzer"

auth_curl() { curl -sf -H "token: $TOKEN" "$@"; }

# Parse "5476.0" seconds → "1:31:16"
fmt_duration() {
  local s=${1%.*} # strip decimal
  printf "%d:%02d:%02d" $((s / 3600)) $(((s % 3600) / 60)) $((s % 60))
}

# ---------------------------------------------------------------------------
# Step 1 — resolve activity
# ---------------------------------------------------------------------------
ACTIVITY_ID=""
DATE=""
TITLE=""
DISTANCE_KM=""
DURATION_SEC=""

if [[ "$ARG" =~ ^[0-9]+$ ]]; then
  # Numeric ID supplied directly
  ACTIVITY_ID="$ARG"
  INFO=$(auth_curl "$API/activity/$ACTIVITY_ID" | jq -r '[.id, .date_time, .title // "", .distance, .duration] | @tsv')
  IFS=$'\t' read -r ACTIVITY_ID RAW_DATE TITLE DISTANCE_KM DURATION_SEC <<<"$INFO"
  DATE="${RAW_DATE:0:10}"

elif [[ "$ARG" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  # Date string — find first running activity on that date
  LIST=$(auth_curl "$API/activity?itemsPerPage=20")
  ROW=$(echo "$LIST" | jq -r --arg d "$ARG" '
        .[]
        | select(
            (.date_time // "" | startswith($d)) and
            (.sport.name // "" | test("Laufen|Running"; "i")) and
            (.distance // 0 > 0.1)
          )
        | [.id, .date_time, .title // "", .distance, .duration]
        | @tsv
    ' | head -1)
  [[ -n "$ROW" ]] || die "No running activity found for date $ARG"
  IFS=$'\t' read -r ACTIVITY_ID RAW_DATE TITLE DISTANCE_KM DURATION_SEC <<<"$ROW"
  DATE="$ARG"

else
  # No argument — pick latest running activity (skip obviously broken placeholder)
  LIST=$(auth_curl "$API/activity?itemsPerPage=20")
  ROW=$(echo "$LIST" | jq -r '
        .[]
        | select(
            (.sport.name // "" | test("Laufen|Running"; "i")) and
            (.distance // 0 > 0.1) and
            (.date_time | startswith("203") | not)
          )
        | [.id, .date_time, .title // "", .distance, .duration]
        | @tsv
    ' | head -1)
  [[ -n "$ROW" ]] || die "No recent running activity found"
  IFS=$'\t' read -r ACTIVITY_ID RAW_DATE TITLE DISTANCE_KM DURATION_SEC <<<"$ROW"
  DATE="${RAW_DATE:0:10}"
fi

[[ -n "$ACTIVITY_ID" ]] || die "Could not resolve activity ID"
[[ -n "$DATE" ]] || die "Could not determine activity date"

# ---------------------------------------------------------------------------
# Step 2 — determine a temporary filename (Claude will rename after type is known)
# Use the Runalyze title if it hints at the type, otherwise fall back to "Laufen"
# ---------------------------------------------------------------------------
TYPE_HINT="Laufen"
TITLE_LOWER=$(echo "$TITLE" | tr '[:upper:]' '[:lower:]')
if [[ "$TITLE_LOWER" =~ jogging|regeneration|regen ]]; then
  TYPE_HINT="Jogging"
elif [[ "$TITLE_LOWER" =~ dauerlauf|dl ]]; then
  TYPE_HINT="Dauerlauf"
elif [[ "$TITLE_LOWER" =~ crescendo ]]; then
  TYPE_HINT="Crescendo"
elif [[ "$TITLE_LOWER" =~ intervall|it ]]; then
  TYPE_HINT="Intervall"
elif [[ "$TITLE_LOWER" =~ tempo|tdl ]]; then
  TYPE_HINT="Tempo"
elif [[ "$TITLE_LOWER" =~ trail ]]; then
  TYPE_HINT="Trail"
elif [[ "$TITLE_LOWER" =~ wettkampf|rennen|wk|race ]]; then
  TYPE_HINT="Rennen"
fi

DEST="$FIT_DIR/$DATE $TYPE_HINT.fit"

# ---------------------------------------------------------------------------
# Step 3 — download
# ---------------------------------------------------------------------------
mkdir -p "$FIT_DIR"

echo ">>> Downloading activity $ACTIVITY_ID ($DATE, ~${DISTANCE_KM} km) …" >&2
auth_curl "$API/activity/$ACTIVITY_ID/fit-original" -o "$DEST"

SIZE=$(wc -c <"$DEST" | tr -d ' ')
[[ "$SIZE" -gt 1000 ]] || die "Downloaded file too small ($SIZE bytes) — likely an error response"

echo ">>> Saved to: $DEST" >&2
echo ">>> Running fit-analyzer …" >&2
echo ""

# ---------------------------------------------------------------------------
# Step 4 — emit summary line then full fit-analyzer output
# ---------------------------------------------------------------------------
printf "ACTIVITY_ID=%s\tDATE=%s\tTITLE=%s\tDIST_KM=%s\tDUR_SEC=%s\tDEST=%s\n" \
  "$ACTIVITY_ID" "$DATE" "$TITLE" "$DISTANCE_KM" "$DURATION_SEC" "$DEST"

echo "---FIT-ANALYZER---"
fit-analyzer "$DEST"
