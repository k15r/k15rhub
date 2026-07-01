---
name: analyze-activity
description: >-
  Fetches the latest (or a specified) activity from Garmin Connect, downloads
  the original FIT file, analyzes it with fit-analyzer, writes a standardized Lauftagebuch
  entry (markdown + YAML) to the Zettelkasten, and triggers adaptive weekly plan adjustment
  via the marathon-coach agent. Works for any sport type (running, cycling, swimming,
  strength, etc.). Use this skill after any activity to document it and keep the training
  plan current. Pass `list` or `list <N>` to browse recent activities without downloading.
argument-hint: "[user=<name>] [list [<count>] | activity ID | YYYY-MM-DD]"
allowed-tools:
  - Read(./**)
  - Edit(./**)
  - Write(./**)
  - Read(~/.marathon-coach/**)
  - Edit(~/.marathon-coach/**)
  - Write(~/.marathon-coach/**)
---

# Analyze Activity

**User arguments:** `$ARGUMENTS`

- `user=<name>` *(optional)* — which user's config to use
- `list [<count>]` — list recent activities without downloading (default 20); stop after displaying the table
- Remaining argument: optional activity ID or date (YYYY-MM-DD). Default: fetch the latest activity.

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

If the remaining argument (after stripping `user=<name>`) starts with `list`, run in list mode:

```bash
bash <skill-dir>/fetch-fit.sh $USER --list [<count>]
```

Display the printed table to the user and **stop — do not proceed to Steps 2–9**. The user can then re-invoke the skill with a specific activity ID or date from the table.

Otherwise, run the fetch script to download an activity:

```bash
bash <skill-dir>/fetch-fit.sh $USER $ACTIVITY_ARGUMENT
```

Where `$ACTIVITY_ARGUMENT` is the remaining argument after stripping `user=<name>` (may be empty → latest activity).

The script checks that `fit-analyzer` is installed and exits with an error if not — install it from https://github.com/k15r/fit-analyzer

The source is Garmin Connect via `fetch-fit-garmin.py` (requires `uv`; dependencies are installed automatically on first run via PEP 723 inline metadata). After downloading the activity, the script automatically fetches health summaries for all days since the last health sync up to yesterday (complete data), and re-fetches today if a partial entry already exists.

- No argument → latest activity (any sport)
- Numeric ID → that specific activity
- `YYYY-MM-DD` → first activity on that date

**Output format — first line:**

```text
ACTIVITY_ID=<id>	DATE=<YYYY-MM-DD>	TITLE=<title>	DIST_KM=<km>	DUR_SEC=<s>	DEST=<path>
```

Followed by `---FIT-ANALYZER---` and the full fit-analyzer YAML output.

Optionally followed by `---WEATHER---` and a `weather:` YAML block with ambient temperature data from Open-Meteo (present when GPS track points were available in the FIT file).

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

Read `current_plan` from `$CONFIG`. If set, load the current week YAML.

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
- `<activity title>` — anything else (use the title as-is, sanitised)

### File naming convention

- Plan run: `YYYY-MM-DD <Type> W<N> <Day>.md`
- Free run or non-running: `YYYY-MM-DD <Type>.md`

**Week abbreviation:** Mo, Di, Mi, Do, Fr, Sa, So

---

## Step 4 — Read the plan for Soll values

Only for **running** activities when `current_plan` is set and a matching session is found in the current week YAML:

Read the session whose `date` matches today from the week YAML and extract:

- `soll_pace` → target pace (M:SS–M:SS per km)
- `duration_min` → target duration in minutes
- `distance_km` → target distance (if specified); otherwise derive as `duration_min ÷ pace_midpoint_min_per_km`
- Any special workout structure (`type`, `reps`, `effort_min`, etc.)

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
| Temperatur Ø / max | XX,X / XX,X °C | — |
| Gefühlte Temp. Ø / max | XX,X / XX,X °C | — |
| Luftfeuchtigkeit Ø | XX % | — |

> Temperatur-Zeilen nur einfügen wenn `---WEATHER---`-Block vorhanden. Werte aus `weather.avg_temp_c`, `weather.max_temp_c`, `weather.avg_apparent_temp_c`, `weather.max_apparent_temp_c`, `weather.avg_humidity_pct`.

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

### Activity YAML (sibling file)

Alongside each `.md` entry, write a sibling `.yaml` with the same base name. This is the structured source of truth read by the marathon-coach agent — always write it, even if the `.md` is the primary display.

```yaml
date: "YYYY-MM-DD"
type: <type_lowercase>        # jogging, dauerlauf, intervall, tempo, laufen, radfahren, kraft, …
sport: <running|cycling|strength|other>
activity_id: "<id>"
source: garmin
title: "<Garmin activity title>"
distance_km: X.XX             # 0 for strength/yoga
duration: "H:MM:SS"
timer_time: "H:MM:SS"
pace: "M:SS"                  # effective pace; omit for non-running
hf_avg: XXX
hf_max: XXX
hoehenmeter_auf: XX           # omit if absent
hoehenmeter_ab: XX            # omit if absent
kadenz_avg: XXX               # already ×2; omit if absent
schrittlaenge_avg: XXXX       # mm; omit if absent
vertikale_oszillation: XX.X   # mm; omit if absent
stance_time: XXX              # ms; omit if absent
vertical_ratio: X.XX          # %; omit if absent
kalorien: XXXX
training_effect: X.X          # omit if absent
temp_avg_c: XX.X              # omit if weather block absent
temp_max_c: XX.X              # omit if weather block absent
apparent_temp_avg_c: XX.X     # omit if weather block absent
apparent_temp_max_c: XX.X     # omit if weather block absent
humidity_avg_pct: XX          # omit if weather block absent
plan: "<current_plan>"        # omit for free runs
planwoche: "W<N>"             # omit for free runs
plan_day: "<Mo|Di|Mi|Do|Fr|Sa|So>"  # omit for free runs
soll_distance_km: X.X         # omit for free runs
soll_pace: "M:SS–M:SS"        # omit for free runs
soll_duration_min: XX         # omit for free runs
laps:                         # omit for strength/yoga
  - n: 1
    distance_km: X.XX
    pace: "M:SS"
    hf_avg: XXX
reflexion:
  gut: "<free text>"
  aufgefallen: "<free text or empty string>"
```

---

## Step 6 — Update the Lauftagebuch index

### Markdown index

The markdown index lives at `<output_dir>/Lauftagebuch/Lauftagebuch.md`. Append a row in chronological order:

```markdown
| [[YYYY-MM/<filename without .md>]] | <Type> | <sport> | XX,XX km | M:SS | XXX | X.X |
```

For non-running activities where pace is not applicable, use `—` for the pace cell. If the index table header does not yet have a `Sport` column, add it.

The index table header should be:

```markdown
| Eintrag | Typ | Sport | Distanz | Pace | HF Ø | TE |
| --- | --- | --- | --- | --- | --- | --- |
```

### YAML index

Also update `<output_dir>/Lauftagebuch/lauftagebuch.yaml`. If it does not exist, create it with empty `entries: []` and `health: []` lists. Prepend a compact entry to the `entries` list (newest first):

```yaml
entries:
  - date: "YYYY-MM-DD"
    type: <type_lowercase>
    sport: <running|cycling|strength|other>
    distance_km: X.XX
    pace: "M:SS"              # omit for non-running
    hf_avg: XXX
    training_effect: X.X      # omit if absent
    hr_drift: X.X             # running only: (avg HR second half − avg HR first half) in bpm;
                              # positive = drift (fatigue signal), 0 = flat, negative = negative split
    plan: "<current_plan>"    # omit for free runs
    planwoche: "W<N>"         # omit for free runs
    soll_pace: "M:SS–M:SS"    # omit for free runs
    reflexion_aufgefallen: "<text or empty string>"
    file: "YYYY-MM/YYYY-MM-DD <Type>"  # path relative to Lauftagebuch/, without extension
health: []  # unchanged
```

To compute `hr_drift`: split the valid laps into two halves, take the average `hf_avg` of each half, subtract first from second. Omit if fewer than 4 laps or lap HR data is absent.

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

Locate the current and next week YAML files:

1. Read `<output_dir>/<Race-Type-Folder>/<current_plan>/` and list all week YAMLs (`W<N> – *.yaml`, excluding `.bak.` files).
2. Identify the **current week YAML**: the one whose `dates.start`–`dates.end` range contains today.
3. Identify the **next week YAML**: the immediately following one, if it exists.
4. Identify the **2 prior week YAMLs** for load trajectory.
5. Also locate the sibling `.md` files for the current and next week (same base name) — these are passed read-only so the agent can faithfully reproduce unchanged rows.
6. Read the last 14 entries from `<output_dir>/Lauftagebuch/lauftagebuch.yaml` (both `entries` and `health` lists).

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
> **CURRENT_WEEK_YAML** (`<W<N> – DD.MM–DD.MM.yaml>`)**:**
> ```yaml
> <full YAML content>
> ```
>
> **NEXT_WEEK_YAML** (`<W<N+1> – DD.MM–DD.MM.yaml | none>`)**:**
> ```yaml
> <full YAML content, or "none">
> ```
>
> **PRIOR_WEEK_YAMLS:**
> ```yaml
> <full YAML content of 2 prior week files, separated by "---">
> ```
>
> **ACTIVITY_HISTORY** (last 14 entries, newest first)**:**
> ```yaml
> <entries list from lauftagebuch.yaml, last 14>
> ```
>
> **HEALTH_HISTORY** (last 14 days, newest first)**:**
> ```yaml
> <health list from lauftagebuch.yaml, last 14>
> ```
>
> **CURRENT_WEEK_FILE** (read-only — for faithful markdown reproduction only)**:**
> <raw markdown of the current week .md file>
>
> **NEXT_WEEK_FILE** (read-only)**:**
> <raw markdown of the next week .md file, or "none">

After the agent responds, parse its output for `REWRITE_YAML:` and `REWRITE_FILE:` blocks. Write the YAML block **before** the markdown block for each week, so the `.yaml` exists on disk when `push-workouts-garmin.py` looks for it.

Each block uses `<<<` / `>>>` as content delimiters:

```text
REWRITE_YAML: <full path to .yaml>
BACKUP_AS: <path with final .yaml replaced by .bak.YYYY-MM-DD.yaml>
<<<
<complete new YAML content>
>>>

REWRITE_FILE: <full path to .md>
BACKUP_AS: <path with final .md replaced by .bak.YYYY-MM-DD.md>
<<<
<complete new markdown content>
>>>
```

For each block:
1. Copy the current file to the `BACKUP_AS` path.
2. Overwrite the original file with the new content.

Then parse `CHANGED_DATES: <comma-separated dates or "none">` from the agent response.

If `garmin_email` is set in `$CONFIG` and `CHANGED_DATES` is not "none", delete and re-upload Garmin workouts for each changed date that is **strictly after today and within the next 7 days** (same horizon as `--week`). Dates beyond 7 days are skipped — they will be picked up by the next `/sync-garmin` run:

```bash
# For each changed date that is > today AND ≤ today + 7 days:
uv run --script <skill-dir>/push-workouts-garmin.py $USER --delete-date <YYYY-MM-DD>
uv run --script <skill-dir>/push-workouts-garmin.py $USER --week <rewritten-week-yaml-path>
```

Pass the `.yaml` path (not `.md`) to `--week` — both files are rewritten by the agent, and the script reads the YAML.

Run push-workouts silently — if it fails, log the error but do not block the user.

Then display the agent's `COACHING_NOTE:` to the user.

---

## Key rules and reminders

- **Cadence ×2**: fit-analyzer reports per-leg cadence for running. Always multiply by 2.
- **Effective pace**: exclude "Uhr nicht gestoppt" and pause laps from pace calculation.
- **German locale**: comma as decimal separator.
- **Soll distance**: calculate as `Soll_minutes / Soll_pace_per_km`, or read directly from the week YAML's `distance_km` field.
- **current_plan**: always read from `$CONFIG` — never hardcode a plan name.
- **adapt-week**: skip if `current_plan` is empty; never block the user waiting for agent output if the agent fails — log the error and continue.
