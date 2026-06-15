---
name: analyze-activity
description: >-
  Fetches the latest (or a specified) activity from Runalyze, downloads the original FIT file,
  analyzes it with fit-analyzer, writes a standardized Lauftagebuch entry to the Zettelkasten,
  and triggers adaptive weekly plan adjustment via the marathon-coach agent. Works for any sport
  type (running, cycling, swimming, strength, etc.). Use this skill after any activity to
  document it and keep the training plan current.
argument-hint: "[user=<name>] [optional: Runalyze activity ID or date YYYY-MM-DD — default: latest activity]"
allowed-tools:
  - Edit(./**)
  - Write(./**)
  - Read(~/.marathon-coach/**)
  - Edit(~/.marathon-coach/**)
  - Write(~/.marathon-coach/**)
---

# Analyze Activity

**User arguments:** `$ARGUMENTS`

- `user=<name>` *(optional)* — which user's config to use
- Remaining argument: optional Runalyze activity ID or date (YYYY-MM-DD). Default: fetch the latest activity.

---

## Step 0 — Resolve user

1. Check if `$ARGUMENTS` starts with `user=<name>` — if so, extract `<name>` as `USER` and strip it from the remaining arguments (pass the rest as the activity argument in Step 1).
2. If no `user=` argument:
   a. List all subdirectories of `~/.marathon-coach/` that contain a `config.yaml`.
   b. Exactly one found → use it without asking.
   c. More than one found → ask: *"Für welchen User? [<list>]"* and wait for the answer.
   d. None found → inform the user that no config exists yet and suggest running `/marathon-coach` first.
3. Also check for a legacy flat config at `~/.marathon-coach/config.yaml` (no subdirectory). If found and no user-subdirectories exist, treat it as `USER=default` and note that migration to `~/.marathon-coach/default/config.yaml` is recommended.

Set `CONFIG_DIR=~/.marathon-coach/<USER>/` and `CONFIG=<CONFIG_DIR>/config.yaml` for all subsequent steps.

---

## Step 1 — Fetch activity and FIT file

Read `output_dir` and `current_plan` from `$CONFIG`.

Run the fetch script (located in the same directory as this skill file):

```bash
bash <skill-dir>/fetch-fit.sh $USER $ACTIVITY_ARGUMENT
```

Where `$ACTIVITY_ARGUMENT` is the remaining argument after stripping `user=<name>` (may be empty → latest activity).

The script checks that `fit-analyzer` is installed and exits with an error if not — install it from https://github.com/k15r/fit-analyzer

The source is auto-detected from config:

- `garmin_email` set → uses Garmin Connect via `fetch-fit-garmin.py` (requires `uv`; dependencies are installed automatically on first run via PEP 723 inline metadata)
- `runalyze_token` set → uses Runalyze API directly

- No argument → latest activity (any sport)
- Numeric ID → that specific activity
- `YYYY-MM-DD` → first activity on that date

**Output format — first line:**

```text
ACTIVITY_ID=<id>	DATE=<YYYY-MM-DD>	TITLE=<title>	DIST_KM=<km>	DUR_SEC=<s>	DEST=<path>
```

Followed by `---FIT-ANALYZER---` and the full fit-analyzer YAML output.

---

## Step 2 — Parse fit-analyzer output and determine sport

From the fit-analyzer output, read the `sport` or `activity_type` field from the session record to determine the sport. Map to one of:

- **running** — `running`, `Laufen`, `Jogging`, `trail_running`, or any run variant
- **cycling** — `cycling`, `Radfahren`, `bike`, `indoor_cycling`
- **swimming** — `swimming`, `Schwimmen`
- **strength** — `training`, `Kraft`, `gym`, `strength_training`, `yoga`
- **other** — anything else

If no sport field is present, infer from the DEST filename type hint.

### Fields to extract for all sports

From `session` record:

- `total_distance` → km (÷1000, 2 decimal places); may be 0 for strength/yoga
- `total_elapsed_time` → total time incl. pauses (format as H:MM:SS)
- `total_timer_time` → active time
- `avg_heart_rate`, `max_heart_rate`
- `total_calories`
- `training_effect` (aerobic TE, 0.0–5.0; omit if absent)

### Additional fields for running

- `avg_cadence` → **multiply ×2** (fit-analyzer reports per-leg)
- `avg_step_length` → mm
- `avg_vertical_oscillation` → mm
- `avg_stance_time` → ms
- `avg_vertical_ratio` → %
- `total_ascent`, `total_descent` → m
- `height_profile.sparkline` → ASCII bar chart
- `height_profile.min_elevation`, `height_profile.max_elevation` → m

From `lap` records (running and cycling):

- List all laps with: lap number, `total_distance` (km), `avg_pace` or `avg_speed`, `avg_heart_rate`
- Running: detect "Uhr nicht gestoppt" laps (distance < 0.05 km AND pace > 10:00) → exclude from effective pace
- Calculate effective pace (running) or avg speed (cycling) from valid laps only

---

## Step 3 — Determine activity type and context

Read `current_plan` from `$CONFIG`. If set, load the plan index and current week file.

### Activity type mapping

**Running types** (used in filename and entry heading):

- `Jogging` — easy recovery, pace ≥ 5:40
- `Dauerlauf` — aerobic base, pace 5:00–5:35
- `Crescendo` — progressive long run
- `Rennen` — race
- `Trail` — off-road
- `Intervall` — interval training
- `Tempo` — threshold/tempo run
- `Laufen` — unspecified run

**Non-running types:**

- `Radfahren` — any cycling
- `Schwimmen` — swimming
- `Kraft` — strength training, gym, yoga
- `<Runalyze title>` — anything else (use the title as-is, sanitised)

### File naming convention

- Plan run: `YYYY-MM-DD <Type> W<N> <Day>.md`
- Free run or non-running: `YYYY-MM-DD <Type>.md`

**Week abbreviation:** Mo, Di, Mi, Do, Fr, Sa, So

---

## Step 4 — Read the plan for Soll values

Only for **running** activities when `current_plan` is set and a matching day/type is found in the week file:

From the weekly plan file, find the matching workout entry (by day/type) and note:

- Target duration (Soll minutes)
- Target pace (Soll min/km) → derive target distance as Soll_min ÷ Soll_pace_min_per_km
- Any special workout structure

For non-running activities, skip Soll comparison entirely.

---

## Step 5 — Write the Lauftagebuch entry

Create the note at:

```text
<output_dir>/Lauftagebuch/YYYY-MM/<filename>.md
```

Create the directory if it does not exist.

### Running entry template

~~~markdown
---
tags: [sport, lauf, <type_lowercase>, lauftagebuch]
date: YYYY-MM-DD
sport: running
distanz_km: X.XX
dauer: "H:MM:SS"
pace: "M:SS"
hf_avg: XXX
hf_max: XXX
hoehenmeter_auf: XX
hoehenmeter_ab: XX
kalorien: XXXX
training_effect: X.X
typ: <type_lowercase>
planwoche: W<N>
plan: "<current_plan>"
isowoche: KW<NN>
---

# <Type> – DD.MM.YYYY (<Context>)

## Kennzahlen

| | | Soll |
| --- | --- | --- |
| Distanz | X,XX km | ~X,X km (Y') |
| Zeit | H:MM:SS (inkl. Pause) | Y' |
| Pace | M:SS min/km (eff.) | M:SS min/km |
| HF Ø / max | XXX / XXX bpm | — |
| Höhenmeter | +XX m / −XX m | — |
| Kalorien | XXXX kcal | — |
| Training Effect | X.X | — |

> <Erklärung von Pausen/Anomalien in den Laps, falls vorhanden.>

## Höhenprofil

```text
<sparkline>
XXXm                                                                                        XXXm
```

- Km 0–X: <Beschreibung>

## Laufqualität

| | |
| --- | --- |
| Kadenz Ø | XXX spm |
| Schrittlänge Ø | XXXX mm |
| Vertikale Oszillation | XX,X mm |
| Stance Time | XXX,X ms |
| Vertical Ratio | X,XX % |

## Verlauf

| Laps | Pace | HF Ø |
| --- | --- | --- |
| <lap ranges> | M:SS–M:SS | XXX–XXX |

<1-2 Sätze über Gleichmäßigkeit, Pace-Spanne, HF-Kontrolle>

## Reflexion

**Was gut lief:**
- <Punkte>

**Was aufgefallen ist:**
- <Punkte, falls vorhanden>

## Kontext

- [[<current_plan>/W<N> – DD.MM–DD.MM|Woche <N>]] — <Wochentag> <Workout-Name>
- FIT-File: [[fit/<filename>.fit|<filename>.fit]]
~~~

### Cycling entry template

~~~markdown
---
tags: [sport, radfahren, lauftagebuch]
date: YYYY-MM-DD
sport: cycling
distanz_km: X.XX
dauer: "H:MM:SS"
hf_avg: XXX
hf_max: XXX
kalorien: XXXX
training_effect: X.X
typ: radfahren
isowoche: KW<NN>
---

# Radfahren – DD.MM.YYYY

## Kennzahlen

| | |
| --- | --- |
| Distanz | X,XX km |
| Zeit | H:MM:SS |
| Ø Geschwindigkeit | XX,X km/h |
| HF Ø / max | XXX / XXX bpm |
| Kalorien | XXXX kcal |
| Training Effect | X.X |

## Verlauf

| Abschnitt | km/h | HF Ø |
| --- | --- | --- |
| <sections> | XX–XX | XXX–XXX |

## Reflexion

**Was gut lief:**
- <Punkte>

**Was aufgefallen ist:**
- <Punkte, falls vorhanden>

## Kontext

- FIT-File: [[fit/<filename>.fit|<filename>.fit]]
~~~

### Strength/yoga entry template

~~~markdown
---
tags: [sport, kraft, lauftagebuch]
date: YYYY-MM-DD
sport: strength
dauer: "H:MM:SS"
hf_avg: XXX
kalorien: XXXX
typ: kraft
isowoche: KW<NN>
---

# Kraft – DD.MM.YYYY

## Kennzahlen

| | |
| --- | --- |
| Dauer | H:MM:SS |
| HF Ø | XXX bpm |
| Kalorien | XXXX kcal |

## Reflexion

**Übungen / Fokus:**
- <Punkte>

**Was aufgefallen ist:**
- <Punkte, falls vorhanden>

## Kontext

- FIT-File: [[fit/<filename>.fit|<filename>.fit]]
~~~

### Template notes

- Omit `Soll` column for free runs and all non-running activities
- Omit `planwoche` and `plan` frontmatter fields for free runs and non-running
- Omit `Laufqualität` section if running dynamics are missing
- Omit `Höhenprofil` section if elevation data is absent
- Omit `> Erklärung...` blockquote if no anomalies exist
- German locale: comma as decimal separator (5,27 not 5.27)
- Cadence ×2 for running

---

## Step 6 — Update the Lauftagebuch index

The index lives at `<output_dir>/Lauftagebuch/Lauftagebuch.md`. Append a row in chronological order:

```markdown
| [[YYYY-MM/<filename without .md>]] | <Type> | <sport> | XX,XX km | M:SS | XXX | X.X |
```

For non-running activities where pace is not applicable, use `—` for the pace cell. If the index table header does not yet have a `Sport` column, add it.

The index table header should be:

```markdown
| Eintrag | Typ | Sport | Distanz | Pace | HF Ø | TE |
| --- | --- | --- | --- | --- | --- | --- |
```

---

## Step 7 — Back-link the plan entry

**Only for running activities** where `current_plan` is set and a matching plan session was found:

Open the week file and write a wiki-link into the `Log` column of the matching day row:

```markdown
| Mi | DD.MM  | Dauerlauf (5:15) |  | [[Lauftagebuch/YYYY-MM/2026-05-14 Dauerlauf W10 Mi\|✓]] |
```

Do not modify any other rows or content in the file.

---

## Step 8 — Coaching assessment

After writing the entry, provide a brief inline coaching assessment (not written to any file):

- Was effort appropriate for the activity type and plan context?
- How does it fit the training week?
- Any patterns worth noting (HR drift, fatigue, form signals)?
- Concrete suggestion for the next session if relevant

For non-running activities, focus on cross-training value and recovery impact.

---

## Step 9 — Trigger adaptive week adjustment

Only execute this step if `current_plan` is set in `$CONFIG`. Skip silently if no plan exists.

Locate the current week file and the next week file:

1. Read `<output_dir>/<Race-Type-Folder>/<current_plan>/` and list all week files (`W<N> – *.md`, excluding `.bak.` files).
2. Identify the **current week file**: the one whose date range contains today.
3. Identify the **next week file**: the immediately following one, if it exists.
4. Read all Lauftagebuch entries from the last 7 days: scan `<output_dir>/Lauftagebuch/` for entries with `date:` frontmatter within the last 7 days. Read them in full, newest first.
5. Read the 2 prior week files for load trajectory.

Invoke the `marathon-coach` agent with:

> **ACTION:** `adapt-week`
>
> **TODAY:** `<YYYY-MM-DD>`
>
> **CONFIG:**
> ```yaml
> <full contents of $CONFIG>
> ```
>
> **CURRENT_WEEK_FILE** (`<W<N> – DD.MM–DD.MM.md>`)**:**
> <full raw markdown of the current week file>
>
> **NEXT_WEEK_FILE** (`<W<N+1> – DD.MM–DD.MM.md | none>`)**:**
> <full raw markdown of the next week file, or "none">
>
> **TAGEBUCH_LAST_7_DAYS:**
> <raw markdown of all Lauftagebuch entries from the last 7 days, newest first>
>
> **PRIOR_WEEKS:**
> <raw markdown of the 2 prior week files>

After the agent responds, parse its output for `REWRITE_YAML:` and `REWRITE_FILE:` blocks:

```text
REWRITE_YAML: <full path to .yaml>
BACKUP_AS: <path with .bak.YYYY-MM-DD before .yaml>
---
<complete new YAML content>
---

REWRITE_FILE: <full path to .md>
BACKUP_AS: <path with .bak.YYYY-MM-DD before .md>
---
<complete new markdown content>
---
```

For each block (YAML and markdown alike):
1. Copy the current file to the `BACKUP_AS` path.
2. Overwrite the original file with the new content.

Then parse `CHANGED_DATES: <comma-separated dates or "none">` from the agent response.

If `garmin_email` is set in `$CONFIG` and `CHANGED_DATES` is not "none", delete and re-upload Garmin workouts for each changed date that is strictly in the future (tomorrow or later):

```bash
# For each changed date > today:
uv run --script <skill-dir>/push-workouts-garmin.py $USER --delete-date <YYYY-MM-DD>
uv run --script <skill-dir>/push-workouts-garmin.py $USER --week <rewritten-week-file-path>
```

Run push-workouts silently — if it fails, log the error but do not block the user.

Then display the agent's `COACHING_NOTE:` to the user.

---

## Key rules and reminders

- **Cadence ×2**: fit-analyzer reports per-leg cadence for running. Always multiply by 2.
- **Effective pace**: exclude "Uhr nicht gestoppt" and pause laps from pace calculation.
- **FIT original**: always download via `/fit-original` endpoint.
- **German locale**: comma as decimal separator.
- **Soll distance**: calculate as `Soll_minutes / Soll_pace_per_km`.
- **current_plan**: always read from `$CONFIG` — never hardcode a plan name.
- **adapt-week**: skip if `current_plan` is empty; never block the user waiting for agent output if the agent fails — log the error and continue.
