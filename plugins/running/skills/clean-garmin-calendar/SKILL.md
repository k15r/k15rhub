---
name: clean-garmin-calendar
description: >-
  Removes all scheduled workouts from Garmin Connect for a given user, from today
  onward. Optionally also deletes the workout definitions from the library. Use this
  to clear the Garmin calendar before a full re-sync, or when a training plan changes
  significantly. Requires garmin_email in config.
argument-hint: "[user=<name>] [--date <YYYY-MM-DD>] [--library]"
allowed-tools:
  - Read(~/.marathon-coach/**)
  - Read(~/.garminconnect/**)
  - Write(~/.garminconnect/**)
  - Bash(uv run --script:*)
---

# Clean Garmin Calendar

Removes all scheduled workouts from the Garmin Connect calendar from today onward.

**User arguments:** `$ARGUMENTS`

> **Version:** `running v0.10.10` — output this line to the user as the very first thing when this skill is invoked, before doing anything else. Keep it in sync with the plugin version.

- `user=<name>` *(optional)* — which user's config to use
- `--date <YYYY-MM-DD>` *(optional)* — only clean this single day (default: today through race_date)
- `--library` *(optional)* — also delete the workout definitions from the Garmin library (not just the calendar entries)

---

## Step 0 — Resolve user

Check `$ARGUMENTS` for `user=<name>`, then list `~/.marathon-coach/` subdirs, use the only one or ask if multiple.

Set `CONFIG=~/.marathon-coach/<USER>/config.yaml`. Read `garmin_email` from config.

If `garmin_email` is not set, inform the user and stop.

---

## Step 1 — Confirm

Tell the user what will be deleted and ask for confirmation before proceeding. Use the user's configured language (`language` field in config — `de` or `en`):

- German: *"Dies löscht alle geplanten Garmin-Trainingseinheiten ab heute für **<USER>**. [Falls --library: Auch die Einheitendefinitionen aus der Garmin-Bibliothek werden gelöscht.] Fortfahren? (ja/nein)"*
- English: *"This will remove all scheduled Garmin workouts from today onward for **<USER>**. [If --library: It will also delete the workout definitions from your Garmin library.] Continue? (yes/no)"*

Wait for explicit confirmation. Abort if anything other than yes/y/ja/j.

---

## Step 2 — Run cleanup script

```bash
uv run --script <skill-dir>/../analyze-activity/garmin.py --user $USER calendar clean [--date <YYYY-MM-DD>] [--library]
```

The command:

1. Without `--date`: reads `race_date` from config to determine coverage end. Fetches scheduled workouts for all months from today through `race_date` (at least 4 months ahead if `race_date` is not set or already past), filtered to entries whose date ≥ today.
2. With `--date <YYYY-MM-DD>`: only that single day's scheduled workouts are removed.
3. Calls `unschedule_workout(scheduled_id)` for each
4. If `--library` was passed: collects the unique `workoutId` values and calls `delete_workout(workout_id)` for each

---

## Step 3 — Report

After the script finishes, report:

- How many calendar entries were removed and across which date range
- How many workout definitions were deleted from the library (if `--library`)
- Suggest running `/sync-garmin` to push the current plan back to the calendar
