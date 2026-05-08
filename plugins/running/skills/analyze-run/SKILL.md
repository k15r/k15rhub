---
name: analyze-run
description: >-
  Fetches the latest (or a specified) activity from Runalyze, downloads the original FIT file,
  analyzes it with fit-analyzer, compares it against the marathon training plan, and writes a
  standardized Lauftagebuch entry to the Zettelkasten. Use this skill after a run to document
  it automatically.
argument-hint: "[optional: Runalyze activity ID or date YYYY-MM-DD — default: latest activity]"
---

# Analyze Run

**User arguments:** `$ARGUMENTS` — optional Runalyze activity ID or date (YYYY-MM-DD). Default: fetch the latest activity.

**Zettelkasten base:** `/Users/D064028/Library/Mobile Documents/iCloud~md~obsidian/Documents/Zettelkasten`

---

## Workflow

### Step 1 — Fetch activity and FIT file

Run the fetch script (located in the same directory as this skill file):

```bash
bash <skill-dir>/fetch-fit.sh $ARGUMENTS
```

The script checks that `fit-analyzer` is installed and exits with an error if not — install it from https://github.com/k15r/fit-analyzer

- No argument → latest running activity
- Numeric ID → that specific activity
- `YYYY-MM-DD` → first running activity on that date

The script handles: resolving the activity via the Runalyze API, downloading the FIT file via `/fit-original` to `Sport/Lauftagebuch/fit/`, running `fit-analyzer`, and printing everything to stdout.

**Output format — first line:**

```text
ACTIVITY_ID=<id>	DATE=<YYYY-MM-DD>	TITLE=<title>	DIST_KM=<km>	DUR_SEC=<s>	DEST=<path>
```
Followed by `---FIT-ANALYZER---` and the full fit-analyzer YAML output.

The filename uses a type hint from the Runalyze title (e.g. `2026-04-23 Jogging.fit`). If the hint is wrong, rename the file after determining the correct type in Step 4.

### Step 2 — Parse fit-analyzer output

From the output above, extract:

**From `session` record:**
- `total_distance` → km (÷1000, 2 decimal places)
- `total_elapsed_time` → total time incl. pauses (format as H:MM:SS)
- `total_timer_time` → active time
- `avg_heart_rate`, `max_heart_rate`
- `total_calories`
- `avg_cadence` → **multiply ×2** (fit-analyzer reports per-leg, Garmin shows both legs)
- `avg_step_length` → mm (already in mm)
- `avg_vertical_oscillation` → mm
- `avg_stance_time` → ms
- `avg_vertical_ratio` → %
- `total_ascent`, `total_descent` → m
- `training_effect` (aerobic TE, 0.0–5.0)
- `height_profile.sparkline` → ASCII bar chart (copy verbatim)
- `height_profile.min_elevation`, `height_profile.max_elevation` → m

**From `lap` records:**
- List all laps with: lap number, `total_distance` (km), `avg_pace` (min:sec/km), `avg_heart_rate`
- Detect "Uhr nicht gestoppt": laps at the END of the file with `total_distance < 0.05 km` AND `avg_pace > 10:00 min/km` → mark these and exclude from effective pace calculation
- Calculate effective pace: sum distance and timer_time of valid laps only
- Detect short pause laps: `total_distance < 0.1 km` AND pace significantly slower than surrounding laps (likely Ampel/pause) → note in Kennzahlen

### Step 3 — Determine workout type and context

**Check the current training week** by reading the marathon plan index:

```text
Sport/Marathon/Marathonplan 3-06/Marathonplan 3-06.md
```
Then read the current week's plan file (e.g., `W8 – 20.04–26.04.md`).

Determine the run type based on:
- Duration and pace vs plan targets
- Day of week (Mo=recovery, Mi=Dauerlauf, Sa=long run, So=Crescendo, etc.)
- Workout name from FIT if available

**Run types and their naming:**
- `Jogging` — easy recovery run, pace ≥ 5:40
- `Dauerlauf` — aerobic base run, pace 5:00–5:35
- `Crescendo` — progressive long run, pace starts slow and builds
- `Rennen` — race
- `Trail` — off-road/trail run
- `Intervall` — interval training with fast segments
- `Tempo` — tempo run / threshold run
- `Laufen` — unspecified / free run

**File naming convention:**
- Plan run: `YYYY-MM-DD <Type> W<N> <Day>.md` (e.g., `2026-04-22 Dauerlauf W8 Mi.md`)
- Free run: `YYYY-MM-DD <Type>.md` (e.g., `2026-04-18 Trail Run.md`)

**Week abbreviation:** Mo, Di, Mi, Do, Fr, Sa, So

### Step 4 — Read the plan for Soll values

From the weekly plan file, find the matching workout entry (by day/type) and note:
- Target duration (Soll minutes)
- Target pace (Soll min/km) → derive target distance as Soll_min ÷ Soll_pace_min_per_km
- Any special workout structure (Steigerungen, Fahrtspiel, etc.)

### Step 5 — Write the Lauftagebuch entry

Create the note at:

```text
Sport/Lauftagebuch/<filename>.md
```

Use this exact template:

~~~markdown
---
tags: [sport, lauf, <type_lowercase>, lauftagebuch]
date: YYYY-MM-DD
sport: running
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

> <Erklärung von Pausen/Anomalien in den Laps, falls vorhanden. Laps mit "Uhr nicht gestoppt" nennen.>

## Höhenprofil

```text
<sparkline aus height_profile.sparkline>
XXXm                                                                                        XXXm
```

- Km 0–X: <Beschreibung>
- Km X–X: <markante Abschnitte, Anstiege, Abstiege>

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

- [[Marathonplan 3-06/W<N> – DD.MM–DD.MM|Woche <N>]] — <Wochentag> <Workout-Name>
- FIT-File: [[fit/<filename>.fit|<filename>.fit]]
~~~

**Notes on filling the template:**
- Omit the "Soll" column for free runs (no plan context)
- Omit the `> Erklärung...` blockquote if no anomalies exist
- For "Uhr nicht gestoppt": note which laps (e.g., "Laps 17–18 = Uhr nicht gestoppt")
- For Ampel/pause laps: note them (e.g., "Lap 2 (6:13) = kurze Pause/Ampel")
- Lap table: group consecutive laps with same pace tier as a range (e.g., "3–16")
- Höhenprofil section: describe the elevation in natural language based on sparkline shape and min/max
- If running dynamics are missing (no cadence/oscillation data), omit the Laufqualität section

### Step 6 — Update the Lauftagebuch index

Append a row to the table in `Sport/Lauftagebuch/Lauftagebuch.md`:

```markdown
| [[<filename without .md>]] | <Type> | XX,XX km | M:SS | XXX | X.X |
```

Insert it in chronological order (by date).

### Step 7 — Compare with training plan and add assessment

After writing the note, provide a brief coaching assessment in your response (not in the note):
- Was the pace/HR appropriate for the workout type?
- How does it fit the training week context?
- Any patterns worth noting (trend in cadence, HR drift, etc.)?
- Concrete suggestion for the next workout if relevant

---

## Key rules and reminders

- **Cadence ×2**: fit-analyzer reports per-leg cadence. Always multiply by 2 for the note.
- **Effective pace**: exclude "Uhr nicht gestoppt" laps and pause laps from pace calculation. Use `total_timer_time` of valid laps ÷ their distance.
- **FIT original**: always download via `/fit-original` endpoint (not `/fit`) to get running dynamics.
- **German locale**: use comma as decimal separator (5,27 not 5.27), period for thousands if needed.
- **Soll distance**: calculate as `Soll_minutes / Soll_pace_per_km` (e.g., 85' at 5:25 = 85/5.417 ≈ 15,7 km).
- **Training Effect**: from `training_effect` in session record (scale 0.0–5.0).
- **Tags**: use lowercase type in frontmatter (jogging, dauerlauf, trail, rennen, intervall, tempo).
