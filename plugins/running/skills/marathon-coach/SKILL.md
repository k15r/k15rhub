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

**If it does not exist**, run interactive onboarding — ask the user in one message for:

1. **Name** — how to address them
2. **Language** — `de` (German) or `en` (English)
3. **Race type** — marathon or half-marathon
4. **Race date** — target race date (YYYY-MM-DD)
5. **Goal time** — HH:MM, or a descriptor like "finish", "sub-4h", "BQ"
6. **Weekly hours** — max training hours per week
7. **Experience level** — `beginner` / `intermediate` / `advanced`
   - beginner: first marathon / running < 2 years / < 30 km/week
   - intermediate: 1–3 marathons / 30–60 km/week
   - advanced: multiple marathons / 60+ km/week
8. **Notes / constraints** — injuries, constraints, preferences (may be empty)
9. **Output directory** — base path where plan files will be written
10. **Runalyze token** *(optional)* — leave blank to enter run data manually

Write `~/.marathon-coach/config.yaml`:

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

Confirm the file was written, then continue to Step 2.

---

## Step 2 — Gather run history

Read `~/.marathon-coach/config.yaml` and parse `output_dir` and `runalyze_token`.

### 2a — Lauftagebuch (primary source)

Check whether `<output_dir>/Lauftagebuch/Lauftagebuch.md` exists (written by the `analyze-run` skill).

If it exists: read the index, then read the **5 most recent entry files** in full. Collect their raw markdown — it will be passed verbatim to the agent.

### 2b — FIT files fallback

If no Lauftagebuch exists, run:

```bash
bash <skill-dir>/fetch-recent-runs.sh [count]   # default: 5
```

- `NO_CONFIG` → jump back to Step 1
- `NO_TOKEN` → ask the user to paste a summary of their 3–5 most recent runs (any format); collect their reply
- Otherwise: collect the full script output (all `---ACTIVITY--- / ---FIT-ANALYZER---` blocks)

---

## Step 3 — Gather plan context

Check for existing plan files under:

```text
<output_dir>/Marathon/       (race_type = marathon)
<output_dir>/Halbmarathon/   (race_type = half-marathon)
```

If a plan exists: read the plan index file, the current week file, and the 2 prior week files. Collect their raw markdown.

---

## Step 4 — Delegate to the marathon-coach agent

Invoke the `marathon-coach` agent with the following prompt, substituting all collected data:

> You are the marathon-coach agent.
>
> **ACTION:** `$ARGUMENTS`
>
> **CONFIG:**
> ```yaml
> <full contents of ~/.marathon-coach/config.yaml>
> ```
>
> **RUN HISTORY** (`<source: lauftagebuch | fit-analyzer | manual>`)**:**
> <raw markdown of the 5 most recent Lauftagebuch entries,
>  OR the full fetch-recent-runs.sh output,
>  OR the manually pasted run summary>
>
> **PLAN CONTEXT** (`<found | none>`)**:**
> <raw markdown of the plan index + current week + 2 prior week files,
>  OR "none" if no plan exists>
>
> Proceed according to your instructions. Report back when done.
