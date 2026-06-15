---
name: sync-garmin
description: >-
  Syncs the current training plan to Garmin Connect — uploads structured workouts and
  schedules them on the correct dates. Deletes and replaces any existing workouts for
  future sessions. Use this after manually editing a week YAML, after adapt-week, or
  any time the plan and Garmin calendar are out of sync. Requires garmin_email in config.
argument-hint: "[user=<name>] [week | plan] [optional: path to week YAML or plan dir]"
allowed-tools:
  - Read(./**)
  - Read(~/.marathon-coach/**)
  - Read(~/.garminconnect/**)
  - Write(~/.garminconnect/**)
---

# Sync Garmin

Pushes the current training plan's future sessions to Garmin Connect as structured
workouts (with pace targets, intervals, recovery steps). Deletes any previously
uploaded workouts for the same dates before uploading the new versions.

**User arguments:** `$ARGUMENTS`

- `user=<name>` *(optional)* — which user's config to use
- `week` *(optional)* — sync only the current week; optionally followed by a path to a specific week YAML
- `plan` *(optional)* — sync all future weeks in the active plan (default when no scope given)
- Path argument *(optional)* — explicit path to a week YAML or plan directory

---

## Step 0 — Resolve user

Same as `marathon-coach` Step 0: check `$ARGUMENTS` for `user=<name>`, then list
`~/.marathon-coach/` subdirs, use the only one or ask if multiple.

Set `CONFIG=~/.marathon-coach/<USER>/config.yaml`.

---

## Step 1 — Resolve scope

Read `current_plan`, `output_dir`, `race_type`, and `garmin_email` from `$CONFIG`.

If `garmin_email` is not set, inform the user and stop:
> "Garmin sync requires `garmin_email` in `~/.marathon-coach/<USER>/config.yaml`.
> Add it and run `/marathon-coach` once interactively to create the token cache."

Determine scope from remaining arguments:

- `week <path>` → sync that specific week YAML
- `week` (no path) → find the current week YAML (whose `dates.start`–`dates.end` contains today)
- `plan <path>` → sync all future weeks in that plan directory
- `plan` or no argument → derive plan directory from `current_plan` and `race_type`:
  `<output_dir>/<Race-Type-Folder>/<current_plan>/`

If `current_plan` is empty and no path was provided, inform the user and stop.

---

## Step 2 — Push to Garmin

The `push-workouts-garmin.py` script lives alongside `analyze-activity` in the plugin.

**For a single week:**

1. Read the week YAML to identify all non-rest session dates that are strictly after today.
2. For each such date, delete any previously scheduled workout:

```bash
uv run --script <skill-dir>/../analyze-activity/push-workouts-garmin.py $USER --delete-date <YYYY-MM-DD>
```

3. Upload and schedule the full week:

```bash
uv run --script <skill-dir>/../analyze-activity/push-workouts-garmin.py $USER --week <week-yaml-path>
```

The `--week` command only uploads sessions whose date is today or later — past sessions in the file are skipped automatically.

**For the full plan:**

```bash
uv run --script <skill-dir>/../analyze-activity/push-workouts-garmin.py $USER --plan <plan-dir-path>
```

For plan mode: the script skips weeks whose `dates.end` is before today and sessions whose date is not strictly after today.

---

## Step 3 — Report

After the script finishes, report to the user:

- How many workouts were uploaded and on which dates
- Any errors or skipped sessions
- Reminder that Garmin Connect calendar may take a moment to sync to the watch
