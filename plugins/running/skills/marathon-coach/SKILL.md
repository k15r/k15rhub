---
name: marathon-coach
description: >-
  Marathon and half-marathon training coach. Creates and updates training plans based on
  the user's fitness level, goals, and recent run history (Runalyze or manually provided).
  Adapts to any experience level. Supports onboarding for new users.
argument-hint: "[new | update | status | hm | <coaching question>]"
---

# Marathon Coach

**User arguments:** `$ARGUMENTS`

- `new` — create a new training plan (marathon or half-marathon)
- `update` — adjust an existing plan (schedule change, race result, injury)
- `status` — assess current training state
- `hm` — half-marathon plan (default: marathon)
- Free text → coaching question, adjustment, or analysis

---

## Step 1 — Onboarding (first-time setup)

Check whether `~/.marathon-coach/config.yaml` exists.

**If it does not exist**, run interactive onboarding:

Ask the user (in one message) for:

1. **Name** — how to address them
2. **Language** — `de` (German) or `en` (English); all subsequent output will use this language
3. **Race type** — marathon or half-marathon
4. **Race date** — target race date (YYYY-MM-DD)
5. **Goal time** — HH:MM, or a descriptor like "finish", "sub-4h", "BQ"
6. **Weekly hours** — max training hours per week (respecting life constraints)
7. **Experience level** — `beginner` / `intermediate` / `advanced`
   - beginner: first marathon / running < 2 years / < 30 km/week
   - intermediate: 1–3 marathons / 30–60 km/week
   - advanced: multiple marathons / 60+ km/week
8. **Notes / constraints** — injuries, constraints, preferences (free text; may be empty)
9. **Output directory** — base path where plan files will be written (e.g. `/path/to/Zettelkasten/Sport`)
10. **Runalyze token** *(optional)* — API token for automatic run history; leave blank to enter run data manually

Once collected, write `~/.marathon-coach/config.yaml`:

```yaml
name: <name>
language: <de|en>
output_dir: <output_dir>
runalyze_token: "<token or empty>"
race_type: <marathon|half-marathon>
race_date: <YYYY-MM-DD>
goal_time: "<HH:MM or descriptor>"
weekly_hours: <number>
experience: <beginner|intermediate|advanced>
notes: "<free text>"
```

Confirm the file was written and continue to the requested action.

---

## Step 2 — Load configuration and run history

Read `~/.marathon-coach/config.yaml` and parse all fields.

### 2a — Lauftagebuch (analyzed run entries)

Check whether `<output_dir>/Lauftagebuch/Lauftagebuch.md` exists. This file is written by the
`analyze-run` skill and is the richest source of training data — prefer it over raw API data.

**If the index exists:**

1. Read `<output_dir>/Lauftagebuch/Lauftagebuch.md` to get the list of recent entries.
   Index row format: `| [[<filename>]] | <Type> | <distance> | <pace> | <avg_hr> | <TE> |`

2. Read the **5 most recent entry files** in full. Each entry contains:
   - **Kennzahlen**: actual distance, time, effective pace, HR avg/max, elevation, calories, Training Effect (TE)
   - **Soll column** (if present): target distance, time, and pace from the plan — use this for compliance comparison
   - **Laufqualität**: cadence, step length, vertical oscillation, stance time (when available)
   - **Verlauf**: lap table showing pace and HR progression within the run
   - **Reflexion**: subjective notes ("Was gut lief", "Was aufgefallen ist") — treat as qualitative signal
   - **Kontext**: wiki-link back to the plan week file (e.g. `[[MarathonplanX/W8 – 20.04–26.04|Woche 8]]`)

3. From these entries, extract the following signals for use in coaching logic:

   | Signal | How to derive | Coaching use |
   | --- | --- | --- |
   | **Pace compliance** | actual pace vs. Soll pace per entry | Identify systematic over/under-effort |
   | **TE trend** | Training Effect across last 5 runs | Rising → fitness building; plateau/drop → fatigue or staleness |
   | **HR drift** | HR in first vs. second half of Verlauf laps | Cardiac drift signals fatigue or heat; flat = good aerobic shape |
   | **Easy run HR** | avg HR on Jogging/Dauerlauf entries | Rising week-on-week at same pace = accumulated fatigue |
   | **Volume last 7 / 14 days** | sum of distances from dates | Check 10% rule; inform regen week decision |
   | **Cadence trend** | avg cadence across last 3–5 runs | Declining cadence = fatigue-related form breakdown |
   | **Reflexion flags** | "Was aufgefallen ist" notes | Subjective fatigue, pain, or unusual effort — treat as injury signal |

**If the index does not exist**, fall back to the API-based approach in Step 2b.

### 2b — Runalyze FIT files fallback

Run the fetch script:

```bash
bash <skill-dir>/fetch-recent-runs.sh [count]   # count defaults to 5
```

**Interpret the output:**

- First line `NO_CONFIG` → config missing; jump back to Step 1.
- First line `NO_TOKEN` → no Runalyze token configured.
  Ask the user to paste a summary of their **3–5 most recent runs** in any format
  (e.g. "2026-04-27, 14 km, 5:10/km, easy"). Parse whatever they provide.
- Otherwise: the script downloads each FIT file and runs `fit-analyzer` on it.
  Output is one block per activity, separated by `---ACTIVITY---`:

  ```text
  ---ACTIVITY---
  ACTIVITY_ID=<id>	DATE=<YYYY-MM-DD>	TITLE=<title>	DIST_KM=<km>	DUR_SEC=<s>	DEST=<path>
  ---FIT-ANALYZER---
  <fit-analyzer YAML output>
  ---ACTIVITY---
  ...
  ```

  Parse each block the same way as `analyze-run` parses a single activity: extract session
  record fields (distance, time, avg/max HR, cadence ×2, TE, vertical oscillation) and lap
  records (pace per lap, HR per lap). Use these to build the same coaching signals as in 2a.

Note: FIT-based data is just as rich as Lauftagebuch entries, but lacks the Soll comparison
and Reflexion notes that `analyze-run` adds. If both sources are available, prefer Lauftagebuch
entries for runs that have already been analyzed, and use FIT data for the most recent runs
not yet in the Lauftagebuch.

---

## Step 3 — Load plan context

Check for existing plan files under:

```text
<output_dir>/Marathon/       (race_type = marathon)
<output_dir>/Halbmarathon/   (race_type = half-marathon)
```

**If a plan exists:**

1. Read the plan index file to get the macrocycle table (all weeks, phases, km targets).
2. Determine the current week by matching today's date to the week date ranges.
3. Read the current week file to see planned sessions and targets.
4. For `update` or `status`: also read the **2 previous week files** to understand the recent load trajectory.

**Cross-reference plan with Lauftagebuch** (when both exist):

- Follow each entry's Kontext wiki-link back to its plan week file to match actual vs. planned sessions.
- Identify missed sessions, substitutions, or extra runs not in the plan.
- Flag: sessions done significantly off-pace (>15 s/km from Soll), TE below target for key workouts,
  or consecutive entries showing HR drift — these indicate the plan may need adjustment.

**If no plan exists** (first `new` invocation): skip this step.

---

## Step 4 — Coaching logic

### Experience-level calibration

Adapt all volume, intensity, and pace targets to the user's experience level:

| | beginner | intermediate | advanced |
| --- | --- | --- | --- |
| Weekly km peak | 30–50 km | 50–75 km | 75–110 km |
| Long run max | 28 km | 32 km | 36 km |
| Intense sessions / week | 1 | 1–2 | 2 |
| Regen week cycle | every 3 weeks | every 3–4 weeks | every 4 weeks |
| Taper length | 3 weeks | 2–3 weeks | 2 weeks |
| Max single session | 2:30 h | 3:00 h | 3:30 h |

Also cap all sessions at `weekly_hours / 5` per day (rough daily budget).

### Pace zones (derived from goal time)

For marathon (distance = 42.195 km) or half-marathon (21.098 km):

| Session type | Formula | Example 4:00 marathon |
| --- | --- | --- |
| Race pace (RP) | goal_time_sec / distance_km | 5:41 /km |
| Long run | RP + 60–90 s | 6:41–7:11 |
| Easy run | RP + 75–90 s | 6:56–7:11 |
| Moderate run | RP + 30–50 s | 6:11–6:31 |
| Threshold | 10km pace + 5–10 s | RP − 30 s approx |
| 1000m intervals | 10km pace − 10 s | RP − 60 s approx |

For a descriptor goal ("finish", "sub-4h", "BQ"), convert to an estimated target time before calculating paces.

### Plan structure (macrocycle)

Create a tabular overview from today to race day:

```text
| Week | Dates | Phase | Focus | ~km | Intense |
```

Standard phases (adapt length to available weeks):

- **Build** (first third): volume accumulation, aerobic base
- **Development** (middle third): specific tempo work, race-pace runs, long runs near race distance
- **Peak** (1–2 weeks): highest load, race simulation
- **Taper** (2–3 weeks for marathon, 1–2 for half): volume reduction, maintain intensity

### Weekly structure principles

Typical 7-day pattern (adjust for the user's available days):

```text
Mon: Rest or strength/stability
Tue: Intense session (intervals or tempo)
Wed: Easy run
Thu: Moderate run
Fri: Easy or rest
Sat: Short or moderate run
Sun: Long run
```

Rules:
- Never two intense sessions back-to-back (mandatory easy or rest day between them)
- 10% rule: do not increase weekly volume > 10% from the prior week (except after a regen week)
- Mandatory regen week: reduce volume to ~65–70% of prior week
- Every session has an explicit goal (1 sentence stating why it is in the plan)
- Respect weekly_hours and session duration caps from config

---

## Step 5 — Write plan files

Write all files under `output_dir` from config (not a hardcoded path).

### Directory layout

```text
<output_dir>/Marathon/<plan-slug>/           (marathon)
<output_dir>/Halbmarathon/<plan-slug>/       (half-marathon)
```

Create directories as needed.

### Weekly files

```text
W<N> – DD.MM–DD.MM.md
```

Template:

~~~markdown
---
tags: [sport, <race_type>, plan, <plan-slug>]
---

# WEEK <N> (<Phase>) | DD.MM – DD.MM.YYYY

[[<plan-index-filename>|← Back to plan]]

| Day | Date   | Session            | Strength |
| --- | ------ | ------------------ | -------- |
| Mon | DD.MM  | –                  |          |
| Tue | DD.MM  | <session> (<pace>) |          |
...

---

## Weekly goal

<1–2 sentences on the week's focus>

## Session goals

**Tue – <session>:** <why this session>
**Sun – <session>:** <why this session>
~~~

### Plan index file

`<plan-slug>/<plan-slug>.md` — contains pace overview, macrocycle table, and links to all week files.

If the output directory appears to be an Obsidian vault (contains `[[` links in existing files), use wiki-link format `[[filename]]` for internal references. Otherwise use regular markdown links.

---

## Step 6 — Coaching summary in the response

After writing files, provide a brief summary:

- Training block at a glance (weeks, phases, peak km/week)
- Current week highlights
- Key pace and injury-prevention points
- Any flags from the user's notes (injury history, time constraints)

---

## Core coaching rules (always apply regardless of experience level)

- Never two intense sessions back-to-back
- 10% weekly volume rule (skip after regen weeks)
- Mandatory regen weeks per cycle
- Taper: reduce volume in final weeks, maintain intensity
- Every run has an explicit stated goal
- Respect session duration caps from config
- Do not ask before writing plan files — write directly, then summarize
