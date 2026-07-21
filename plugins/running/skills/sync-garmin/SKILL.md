---
name: sync-garmin
description: >-
  Syncs the current training plan to Garmin Connect — uploads structured workouts and
  schedules them on the correct dates. Deletes and replaces any existing workouts for
  future sessions. Use this after manually editing a week YAML, after adapt-week, or
  any time the plan and Garmin calendar are out of sync. Requires garmin_email in config.
argument-hint: "[user=<name>] [week | plan | migrate] [optional: path to week YAML or plan dir]"
allowed-tools:
  - Read(./**)
  - Read(~/.marathon-coach/**)
  - Read(~/.garminconnect/**)
  - Write(~/.garminconnect/**)
  - Bash(uv run --script:*)
---

# Sync Garmin

Pushes the current training plan's future sessions to Garmin Connect as structured
workouts (with pace targets, intervals, recovery steps). Deletes any previously
uploaded workouts for the same dates before uploading the new versions.

**User arguments:** `$ARGUMENTS`

> **Version:** `running v0.10.7` — output this line to the user as the very first thing when this skill is invoked, before doing anything else. Keep it in sync with the plugin version.

- `user=<name>` *(optional)* — which user's config to use
- `week` *(optional)* — sync only the next 7 days (default when no scope given); optionally followed by a path to a specific week YAML
- `plan` *(optional)* — sync all future weeks in the active plan
- `migrate` *(optional)* — backfill `strength` sub-blocks from markdown Kraft/Stabi cells into week YAMLs, then offer to sync; optionally followed by a path to a plan directory
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
> Add it and run `/analyze-activity` once interactively to create the token cache."

Determine scope from remaining arguments:

- `week <path>` → sync that specific week YAML
- `week` (no path) or no argument → sync the next 7 days: find the current week YAML (whose `dates.start`–`dates.end` contains today); if today's date is within the last 2 days of that week, also include the next week YAML so the full 7-day window is covered. If today falls outside all week ranges, inform the user and stop.
- `plan <path>` → sync all future weeks in that plan directory
- `plan` → derive plan directory from `current_plan` and `race_type`:
  `<output_dir>/<Race-Type-Folder>/<current_plan>/`
- `migrate <path>` → run the migration script (see Step 1b), then ask the user whether to also sync
- `migrate` (no path) → derive plan directory as for `plan`, then same as above

If `current_plan` is empty and no path was provided, inform the user and stop.

---

## Step 1b — Migrate (only when scope = `migrate`)

Run the migration script in dry-run mode first and show the user what would change:

```bash
uv run --script <skill-dir>/../analyze-activity/garmin.py migrate strength <plan-dir> --dry-run
```

If there is nothing to migrate, tell the user and stop.

If there are sessions to migrate, show the dry-run output and ask the user to confirm before applying:

```bash
uv run --script <skill-dir>/../analyze-activity/garmin.py migrate strength <plan-dir>
```

After a successful migration, remind the user:

- The `exercises` lists are empty — they should fill them in manually or run `/marathon-coach update` to have the coach rewrite the weeks with proper exercise details.
- Once exercises are filled in, run `/sync-garmin plan` to upload the strength workouts to Garmin.

Do **not** automatically proceed to Step 2 after migration — stop here and let the user decide.

---

## Step 2 — Push to Garmin

The `garmin.py` CLI lives alongside `analyze-activity` in the plugin.
Pass `--help` to any subcommand for a full argument reference, e.g.:

```bash
uv run --script <skill-dir>/../analyze-activity/garmin.py training --help
uv run --script <skill-dir>/../analyze-activity/garmin.py training push --help
```

**For a single week (or the default 7-day scope, which may cover two week YAMLs):**

For each week YAML in scope:

1. Read the week YAML to identify all non-rest session dates that are strictly after today **and within the next 7 days** (the same horizon enforced by `training push`).
2. For each such date, delete any previously scheduled workout:

```bash
uv run --script <skill-dir>/../analyze-activity/garmin.py --user $USER training delete <YYYY-MM-DD>
```

3. Upload and schedule the full week:

```bash
uv run --script <skill-dir>/../analyze-activity/garmin.py --user $USER training push <week-yaml-path>
```

The `training push` command only uploads sessions whose date is strictly after today — today and past sessions are skipped automatically.

**For the full plan:**

```bash
uv run --script <skill-dir>/../analyze-activity/garmin.py --user $USER plan push <plan-dir-path>
```

For plan mode: the command skips weeks whose `dates.end` is before today and sessions whose date is not strictly after today.

---

## Step 3 — Report

After the script finishes, report to the user:

- How many workouts were uploaded and on which dates
- Any errors or skipped sessions
- Reminder that Garmin Connect calendar may take a moment to sync to the watch
