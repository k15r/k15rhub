#!/usr/bin/env bash
# fetch-recent-runs.sh — download recent FIT files and run fit-analyzer for the marathon-coach skill
#
# Reads ~/.marathon-coach/config.yaml for the Runalyze token and output_dir.
# If no token is configured, prints NO_TOKEN and exits 0.
# If no config exists, prints NO_CONFIG and exits 0.
#
# Usage:
#   fetch-recent-runs.sh [count]   # default: 5 most recent running activities
#
# Output (stdout) per activity:
#   ---ACTIVITY---
#   ACTIVITY_ID=<id>	DATE=<YYYY-MM-DD>	TITLE=<title>	DIST_KM=<km>	DUR_SEC=<s>	DEST=<path>
#   ---FIT-ANALYZER---
#   <fit-analyzer YAML output>
#
# Exits non-zero on API or tool errors.

set -euo pipefail

CONFIG="$HOME/.marathon-coach/config.yaml"
API="https://runalyze.com/api/v1"
COUNT="${1:-5}"

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
die() { echo "ERROR: $*" >&2; exit 1; }

read_yaml_field() {
    local file="$1" field="$2"
    grep "^${field}:" "$file" | head -1 | sed "s/^${field}: *//" | tr -d '"' | tr -d "'"
}

# ---------------------------------------------------------------------------
# Check prerequisites
# ---------------------------------------------------------------------------
[[ -f "$CONFIG" ]] || { echo "NO_CONFIG"; exit 0; }

TOKEN=$(read_yaml_field "$CONFIG" runalyze_token)
[[ -n "$TOKEN" ]] || { echo "NO_TOKEN"; exit 0; }

OUTPUT_DIR=$(read_yaml_field "$CONFIG" output_dir)
[[ -n "$OUTPUT_DIR" ]] || die "output_dir not set in $CONFIG"

FIT_DIR="$OUTPUT_DIR/Lauftagebuch/fit"

command -v jq          >/dev/null 2>&1 || die "jq not found. Install it (e.g. brew install jq)"
command -v fit-analyzer >/dev/null 2>&1 || die "fit-analyzer not found. Install from https://github.com/k15r/fit-analyzer"

auth_curl() { curl -sf -H "token: $TOKEN" "$@"; }

# ---------------------------------------------------------------------------
# Resolve N most recent running activities
# ---------------------------------------------------------------------------
LIST=$(auth_curl "$API/activity?itemsPerPage=50")

ACTIVITIES=$(echo "$LIST" | jq -r --argjson n "$COUNT" '[
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
        duration_sec: (.duration // 0)
      }
] | .[0:$n] | .[]
| [.id, .date, .title, .distance_km, .duration_sec] | @tsv')

[[ -n "$ACTIVITIES" ]] || die "No recent running activities found in Runalyze"

mkdir -p "$FIT_DIR"

# ---------------------------------------------------------------------------
# For each activity: download FIT, run fit-analyzer, emit structured output
# ---------------------------------------------------------------------------
while IFS=$'\t' read -r ACTIVITY_ID DATE TITLE DISTANCE_KM DURATION_SEC; do
    # Derive type hint from title (mirrors fetch-fit.sh logic)
    TYPE_HINT="Laufen"
    TITLE_LOWER=$(echo "$TITLE" | tr '[:upper:]' '[:lower:]')
    if   [[ "$TITLE_LOWER" =~ jogging|regeneration|regen ]]; then TYPE_HINT="Jogging"
    elif [[ "$TITLE_LOWER" =~ dauerlauf|dl ]];                then TYPE_HINT="Dauerlauf"
    elif [[ "$TITLE_LOWER" =~ crescendo ]];                   then TYPE_HINT="Crescendo"
    elif [[ "$TITLE_LOWER" =~ intervall|it ]];                then TYPE_HINT="Intervall"
    elif [[ "$TITLE_LOWER" =~ tempo|tdl ]];                   then TYPE_HINT="Tempo"
    elif [[ "$TITLE_LOWER" =~ trail ]];                       then TYPE_HINT="Trail"
    elif [[ "$TITLE_LOWER" =~ wettkampf|rennen|wk|race ]];    then TYPE_HINT="Rennen"
    fi

    DEST="$FIT_DIR/$DATE $TYPE_HINT.fit"

    echo ">>> Downloading activity $ACTIVITY_ID ($DATE, ~${DISTANCE_KM} km) …" >&2
    auth_curl "$API/activity/$ACTIVITY_ID/fit-original" -o "$DEST"

    SIZE=$(wc -c < "$DEST" | tr -d ' ')
    if [[ "$SIZE" -le 1000 ]]; then
        echo "WARN: activity $ACTIVITY_ID download too small ($SIZE bytes), skipping" >&2
        rm -f "$DEST"
        continue
    fi

    echo "---ACTIVITY---"
    printf "ACTIVITY_ID=%s\tDATE=%s\tTITLE=%s\tDIST_KM=%s\tDUR_SEC=%s\tDEST=%s\n" \
        "$ACTIVITY_ID" "$DATE" "$TITLE" "$DISTANCE_KM" "$DURATION_SEC" "$DEST"
    echo "---FIT-ANALYZER---"
    fit-analyzer "$DEST"

done <<< "$ACTIVITIES"
