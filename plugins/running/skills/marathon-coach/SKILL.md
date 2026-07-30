---
name: marathon-coach
description: >-
  Running coach for any race distance. Creates and updates training plans based on
  the user's fitness level, goals, and recent activity history from Garmin Connect.
  Adapts to any experience level and race type (5k, 10k,
  half-marathon, marathon, ultramarathon). Supports onboarding for new users.
argument-hint: "[user=<name>] [race=<type>] [new | update | status | sync | regen-strength | <coaching question>]"
allowed-tools:
  - Read(./**)
  - Edit(./**)
  - Write(./**)
  - Read(~/.marathon-coach/**)
  - Edit(~/.marathon-coach/**)
  - Write(~/.marathon-coach/**)
  - Read(~/.garminconnect/**)
  - Bash(uv run --script:*)
  - Bash(fit-analyzer:*)
---

# Marathon Coach

> **Version:** `running v0.10.12` — output this line to the user as the very first thing when this skill is invoked, before doing anything else. Keep it in sync with the plugin version.

**User arguments:** `$ARGUMENTS`

- `user=<name>` *(optional)* — which user's config to use
- `race=<type>` *(optional)* — override the race type for this session (e.g. `race=10k`, `race=half-marathon`, `race=50k`); overrides `race_type` in config without permanently changing it
- `new` — create a new training plan
- `update` — adjust an existing plan (schedule change, race result, injury)
- `status` — assess current training state
- `sync` — push current plan's future sessions to Garmin Connect (delete and replace)
- `regen-strength` — rewrite all strength blocks in one or more week files using valid Garmin exercise keys; re-uploads affected dates
- Free text → coaching question, adjustment, or analysis

---

## Step 0 — Resolve user

1. Check if `$ARGUMENTS` starts with `user=<name>` — if so, extract `<name>` as `USER` and strip it from the remaining arguments (pass the rest as `$ACTION`).
2. If no `user=` argument:
   a. List all subdirectories of `~/.marathon-coach/` that contain a `config.yaml`.
   b. Exactly one found → use it without asking.
   c. More than one found → ask: *"Für welchen User? [<list>]"* and wait for the answer.
   d. None found → set `USER` to a name the user provides during onboarding (Step 1).
3. Also check for a legacy flat config at `~/.marathon-coach/config.yaml` (no subdirectory). If found and no user-subdirectories exist, treat it as `USER=default` and note that migration to `~/.marathon-coach/default/config.yaml` is recommended.

Set `CONFIG_DIR=~/.marathon-coach/<USER>/` and `CONFIG=<CONFIG_DIR>/config.yaml` for all subsequent steps.

---

## Step 0b — Normalise arguments

After resolving the user, parse the remaining arguments:

1. If `race=<type>` appears, extract it and set `$RACE_TYPE_OVERRIDE = <type>`. Remove it from the remaining string.
2. From what remains, identify `$ACTION`:
   - `new`, `update`, `status`, `sync`, or `regen-strength` → use as-is
   - Empty or blank → set `$ACTION = status`
   - Anything else → treat as a free-text coaching question

If `$RACE_TYPE_OVERRIDE` is set and `$CONFIG` already exists with a different `race_type`, tell the user which value is being used for this session and offer to update the config permanently. Do not rewrite the config without confirmation. Pass `$RACE_TYPE_OVERRIDE` to the agent as `race_type_override:` appended to the CONFIG block.

---

## Step 1 — Onboarding (first-time setup)

Check whether `$CONFIG` exists.

**If it does not exist**, run interactive onboarding — ask the user in one message for:

1. **Name** — how to address them (pre-fill with `USER` if derived from directory name)
2. **Language** — `de` (German) or `en` (English)
3. **Race type** — e.g. `10k`, `half-marathon`, `marathon`, `50k`
4. **Race date** — target race date (YYYY-MM-DD)
5. **Goal time** — HH:MM, or a descriptor like "finish", "sub-4h", "BQ"
6. **Weekly hours** — max training hours per week
7. **Experience level** — `beginner` / `intermediate` / `advanced`
   - beginner: first race at this distance / running < 2 years / < 30 km/week
   - intermediate: 1–3 races at this distance / 30–60 km/week
   - advanced: multiple races at this distance / 60+ km/week
8. **Notes / constraints** — injuries, constraints, preferences (may be empty)
9. **Output directory** — base path where plan files will be written
10. **Garmin Connect email** — your Garmin Connect login email (requires `uv`; tokens stored in `~/.garminconnect/` after first interactive login; password is never saved)

Create `$CONFIG_DIR` if it does not exist, then write `$CONFIG`:

```yaml
name: <name>
language: <de|en>
output_dir: <output_dir>
garmin_email: "<email>"
race_type: <e.g. marathon, half-marathon, 10k, 50k>
race_date: <YYYY-MM-DD>
goal_time: "<HH:MM or descriptor>"
weekly_hours: <number>
experience: <beginner|intermediate|advanced>
notes: "<free text>"
current_plan: ""
```

Confirm the file was written, then continue to Step 2.

---

## Step 2 — Gather activity history

Read `$CONFIG` and parse `output_dir` and `current_plan`.

### 2a — Lauftagebuch (primary source)

Check whether `<output_dir>/Lauftagebuch/lauftagebuch.yaml` exists (written by the `analyze-activity` skill).

If it exists: read the last 14 entries from the `entries` list. This is the structured source of truth for activities.

Also check whether `<output_dir>/Gesundheitstagebuch/gesundheitstagebuch.yaml` exists. If it exists: read the last 14 entries from the `entries` list.

### 2b — Fallback

If no Lauftagebuch exists, run:

```bash
bash <skill-dir>/fetch-recent-activities.sh $USER [count]   # default: 5
```

- `NO_CONFIG` → jump back to Step 1
- `NO_TOKEN` → `garmin_email` not set in config; run onboarding and add it, then log in once interactively with `/analyze-activity`
- Non-zero exit with an auth error → Garmin token cache missing; tell the user to run `/analyze-activity` once interactively to create it, then retry
- Otherwise: collect the full script output (all `---ACTIVITY--- / ---FIT-ANALYZER---` blocks)

---

## Step 3 — Gather plan context

Read `current_plan` from `$CONFIG`.

If `current_plan` is set, look for plan files under `<output_dir>/<race-type-folder>/<current_plan>/` where `<race-type-folder>` is derived from `race_type` in config (or `$RACE_TYPE_OVERRIDE` if set) by title-casing each hyphen-separated word (e.g. `marathon` → `Marathon`, `half-marathon` → `Half-Marathon`, `10k` → `10k`). Hyphens are preserved — do not replace them with spaces.

If `current_plan` is empty or the directory does not exist: scan `<output_dir>/` for subdirectories that contain a directory matching the slug pattern `<race-type>-<YYYY-MM-DD>`. If multiple are found, ask the user which one to use. If none are found, treat plan context as absent. Skip this scan entirely when ACTION is `new`.

If a plan is found: read the current week YAML, the 2 prior week YAMLs, and the plan index `.md` file. Collect their raw content.

---

## Step 4 — Delegate to the marathon-coach agent

Invoke the `marathon-coach` agent with the following prompt, substituting all collected data:

> You are the marathon-coach agent.
>
> **ACTION:** `$ACTION`
>
> **TODAY:** `<YYYY-MM-DD>`
>
> **CONFIG:**
> ```yaml
> <full contents of $CONFIG>
> <if $RACE_TYPE_OVERRIDE is set, append:>
> race_type_override: <$RACE_TYPE_OVERRIDE>
> ```
>
> **ACTIVITY_HISTORY** (last 14 entries, newest first)**:**
> ```yaml
> <entries list from lauftagebuch.yaml, last 14 items>
> ```
> *(If no lauftagebuch.yaml exists, pass the manually provided run summary as free text under this heading)*
>
> **HEALTH_HISTORY** (last 14 days, newest first)**:**
> ```yaml
> <entries list from gesundheitstagebuch.yaml, last 14 items, or "none">
> ```
>
> **PLAN_CONTEXT** (`<found | none>`)**:**
> ```yaml
> <full YAML content of current week + 2 prior week files, separated by "---",
>  or "none" if no plan exists or ACTION is "new">
> ```
>
> Proceed according to your instructions. When you create or activate a plan, return the plan slug on its own line as: `PLAN_SLUG: <slug>`
>
> Report back when done.

After the agent responds: if the response contains a `PLAN_SLUG: <slug>` line, update `current_plan` in `$CONFIG` to that value by rewriting the `current_plan:` field.

If `garmin_email` is set in `$CONFIG` and ACTION was `new` or `update`, push the full plan to Garmin Connect:

```bash
uv run --script <skill-dir>/../analyze-activity/garmin.py --user $USER plan push <output_dir>/<Race-Type-Folder>/<plan-slug>/
```

This uploads and schedules sessions within the next 7 days as structured workouts. Run silently in the background — if it fails, log the error but do not block the user. Inform the user that only the next 7 days are pushed to Garmin; run `/sync-garmin` each week to keep the calendar current.

If ACTION is `sync`, skip the agent entirely and go directly to the Garmin push:

1. Read `current_plan`, `output_dir`, and `garmin_email` from `$CONFIG`. If `current_plan` is empty, inform the user that no active plan is set. If `garmin_email` is not set, inform the user that Garmin sync requires `garmin_email` in the config.
2. Derive the plan directory: `<output_dir>/<Race-Type-Folder>/<current_plan>/`
3. Run:

```bash
uv run --script <skill-dir>/../analyze-activity/garmin.py --user $USER plan push <plan-dir>/
```

This pushes all sessions within the next 7 days, replacing any previously scheduled workouts for those dates. Report how many workouts were pushed and on which dates.

If ACTION is `regen-strength`:

1. Resolve the plan directory and identify the week files to process:
   - If a path argument follows `regen-strength`, use it as an explicit week YAML path or plan directory.
   - Otherwise, default to the full current plan directory.

2. Collect the list of week YAML paths to regenerate. Also read any `notes` / equipment information from `$CONFIG`.

3. Invoke the `marathon-coach` agent with ACTION = `regen-strength`, passing:
   - **WEEK_FILES:** newline-separated list of absolute YAML paths
   - **EQUIPMENT:** value of `notes` field from config (or empty)
   - **CONFIG**, **TODAY** as usual

4. Parse `REWRITE_YAML` / `REWRITE_FILE` blocks from the agent response and apply them (same logic as `adapt-week`).

5. Parse `REGEN_DATES`. For each date in the list, delete and re-upload the Garmin workout:

```bash
uv run --script <skill-dir>/../analyze-activity/garmin.py --user $USER training delete <date>
uv run --script <skill-dir>/../analyze-activity/garmin.py --user $USER training push <week-yaml-path>
```

6. Report which weeks were updated, which dates were re-synced to Garmin, and the agent's `COACHING_NOTE`.
