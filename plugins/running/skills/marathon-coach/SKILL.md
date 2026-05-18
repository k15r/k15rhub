---
name: marathon-coach
description: >-
  Marathon and half-marathon training coach. Creates and updates training plans based on
  the user's fitness level, goals, and recent run history (Runalyze or manually provided).
  Adapts to any experience level. Supports onboarding for new users.
argument-hint: "[user=<name>] [new | update | status | hm | <coaching question>]"
---

# Marathon Coach

**User arguments:** `$ARGUMENTS`

- `user=<name>` *(optional)* — which user's config to use
- `new` — create a new training plan (marathon or half-marathon)
- `update` — adjust an existing plan (schedule change, race result, injury)
- `status` — assess current training state
- `hm` — half-marathon plan (default: marathon)
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

## Step 1 — Onboarding (first-time setup)

Check whether `$CONFIG` exists.

**If it does not exist**, run interactive onboarding — ask the user in one message for:

1. **Name** — how to address them (pre-fill with `USER` if derived from directory name)
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

Create `$CONFIG_DIR` if it does not exist, then write `$CONFIG`:

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
current_plan: ""
```

Confirm the file was written, then continue to Step 2.

---

## Step 2 — Gather run history

Read `$CONFIG` and parse `output_dir`, `runalyze_token`, and `current_plan`.

### 2a — Lauftagebuch (primary source)

Check whether `<output_dir>/Lauftagebuch/Lauftagebuch.md` exists (written by the `analyze-run` skill).

If it exists: read the index, then read the **5 most recent entry files** in full. Collect their raw markdown — it will be passed verbatim to the agent.

### 2b — FIT files fallback

If no Lauftagebuch exists, run:

```bash
bash <skill-dir>/fetch-recent-runs.sh $USER [count]   # default: 5
```

- `NO_CONFIG` → jump back to Step 1
- `NO_TOKEN` → ask the user to paste a summary of their 3–5 most recent runs (any format); collect their reply
- Otherwise: collect the full script output (all `---ACTIVITY--- / ---FIT-ANALYZER---` blocks)

---

## Step 3 — Gather plan context

Read `current_plan` from `$CONFIG`.

If `current_plan` is set, look for plan files under:

```text
<output_dir>/Marathon/<current_plan>/       (race_type = marathon)
<output_dir>/Halbmarathon/<current_plan>/   (race_type = half-marathon)
```

If `current_plan` is empty or the directory does not exist: scan `<output_dir>/Marathon/` (or `Halbmarathon/`) for existing plan directories. If multiple are found, ask the user which one to use. If none are found, treat plan context as absent.

If a plan is found: read the plan index file, the current week file, and the 2 prior week files. Collect their raw markdown.

---

## Step 4 — Delegate to the marathon-coach agent

Invoke the `marathon-coach` agent with the following prompt, substituting all collected data:

> You are the marathon-coach agent.
>
> **ACTION:** `$ACTION`
>
> **CONFIG:**
> ```yaml
> <full contents of $CONFIG>
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
> Proceed according to your instructions. When you create or activate a plan, return the plan slug on its own line as: `PLAN_SLUG: <slug>`
>
> Report back when done.

After the agent responds: if the response contains a `PLAN_SLUG: <slug>` line, update `current_plan` in `$CONFIG` to that value by rewriting the `current_plan:` field.
