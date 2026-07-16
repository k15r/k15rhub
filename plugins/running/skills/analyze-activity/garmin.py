#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "garminconnect==0.3.6",
#   "curl_cffi",
#   "pydantic",
#   "pyyaml",
# ]
# ///
"""
garmin.py — unified CLI for the running plugin's Garmin Connect integration.

All previously separate scripts are exposed here as subcommands. The user is
resolved once (flag → env → config) and passed to the underlying logic.

Usage:
    garmin.py [--user <user>] <group> <command> [options]

User resolution order (highest priority first):
    1. --user <user>
    2. GARMIN_USER environment variable
    3. default_user: in ~/.marathon-coach/config.yaml
    4. the sole subdirectory of ~/.marathon-coach/ containing a config.yaml

Groups & commands:
    activity  list [--count N]
    activity  fetch [--id ID | --date YYYY-MM-DD]
    activity  sync [--count N]
    health    fetch [--date YYYY-MM-DD]
    health    sync
    training  list [<week-file>] [--from D] [--to D] [--format table|json|yaml]
    training  fetch <workout-id> [--format yaml|json]
    training  push  <week-file> [--date D] [--dry-run] [--format table|json|yaml]
    training  delete <YYYY-MM-DD>
    plan      list  <plan-dir> [--from D] [--to D] [--format table|json|yaml]
    plan      push  <plan-dir> [--dry-run] [--format table|json|yaml]
    calendar  clean [--date YYYY-MM-DD] [--library]
    migrate   health [--dry-run]
    migrate   strength <plan-dir> [--dry-run]
    exercise  list [--category CATEGORY]
    exercise  search <query>

The exercise, training, plan, calendar, activity, health and migrate groups load
their implementation from the sibling scripts (fetch-fit-garmin.py,
push-workouts-garmin.py, clean-garmin-calendar.py, migrate-health.py,
migrate-strength.py) so there is a single source of truth for the logic.
"""

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).parent


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Load sibling scripts as modules (hyphenated names are not importable directly)
# ---------------------------------------------------------------------------

def _load(script_name: str, module_name: str):
    path = _HERE / script_name
    if not path.exists():
        die(f"Required script not found: {path}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Lazily loaded — importing garminconnect is only needed for network commands.
_MODS: dict[str, object] = {}


def mod(script_name: str, module_name: str):
    if module_name not in _MODS:
        _MODS[module_name] = _load(script_name, module_name)
    return _MODS[module_name]


def fetch_mod():
    return mod("fetch-fit-garmin.py", "fetch_fit_garmin")


def push_mod():
    return mod("push-workouts-garmin.py", "push_workouts")


def clean_mod():
    return mod("clean-garmin-calendar.py", "clean_garmin_calendar")


def migrate_health_mod():
    return mod("migrate-health.py", "migrate_health")


def migrate_strength_mod():
    return mod("migrate-strength.py", "migrate_strength")


# ---------------------------------------------------------------------------
# User resolution
# ---------------------------------------------------------------------------

def _read_default_user() -> str:
    """Read default_user from ~/.marathon-coach/config.yaml (flat top-level config)."""
    cfg = Path.home() / ".marathon-coach" / "config.yaml"
    if not cfg.exists():
        return ""
    try:
        with open(cfg) as f:
            for line in f:
                if line.startswith("default_user:"):
                    return line.split(":", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _sole_user_subdir() -> str:
    """If exactly one ~/.marathon-coach/<user>/config.yaml exists, return <user>."""
    base = Path.home() / ".marathon-coach"
    if not base.is_dir():
        return ""
    users = [
        p.name for p in base.iterdir()
        if p.is_dir() and (p / "config.yaml").exists()
    ]
    return users[0] if len(users) == 1 else ""


def resolve_user(explicit: str | None) -> str:
    """Resolve the user: --user flag → GARMIN_USER → default_user → sole subdir."""
    if explicit:
        return explicit
    env = os.getenv("GARMIN_USER")
    if env:
        return env
    default = _read_default_user()
    if default:
        return default
    sole = _sole_user_subdir()
    if sole:
        return sole
    die(
        "No user specified. Set one of:\n"
        "  --user <user>\n"
        "  GARMIN_USER=<user> environment variable\n"
        "  default_user: <user> in ~/.marathon-coach/config.yaml\n"
        "  (or keep a single ~/.marathon-coach/<user>/ directory)"
    )


def tokenstore_for(user: str) -> str:
    return str(Path(os.getenv("GARMINTOKENS", f"~/.garminconnect/{user}")).expanduser())


# ---------------------------------------------------------------------------
# activity group
# ---------------------------------------------------------------------------

def cmd_activity_list(args, user: str) -> None:
    m = fetch_mod()
    tokenstore = tokenstore_for(user)
    garmin = m.init_garmin(tokenstore)
    m.list_activities(garmin, args.count)


def cmd_activity_fetch(args, user: str) -> None:
    m = fetch_mod()
    tokenstore = tokenstore_for(user)
    output_dir, fit_dir = m.load_config(user)
    garmin = m.init_garmin(tokenstore)
    arg = args.id or args.date or ""
    activity = m.resolve_activity(garmin, arg)
    m.emit_activity(activity, garmin, fit_dir)
    m.run_health_sync(garmin, tokenstore, output_dir)


def cmd_activity_sync(args, user: str) -> None:
    m = fetch_mod()
    tokenstore = tokenstore_for(user)
    output_dir, fit_dir = m.load_config(user)
    garmin = m.init_garmin(tokenstore)

    last_sync = m.read_last_sync(tokenstore)
    from datetime import date as _date
    if last_sync:
        print(f">>> Incremental sync since {last_sync} …", file=sys.stderr)
        today = _date.today().isoformat()
        activities = garmin.get_activities_by_date(last_sync, today)
        if isinstance(activities, dict):
            activities = activities.get("activityList", [])
        activities = list(reversed(activities or []))[: args.count]
    else:
        print(f">>> First sync — fetching {args.count} most recent activities …", file=sys.stderr)
        activities = garmin.get_activities(0, args.count)
        if isinstance(activities, dict):
            activities = activities.get("activityList", [])
        activities = (activities or [])[: args.count]

    newest_date = None
    for activity in activities:
        print("---ACTIVITY---")
        sys.stdout.flush()
        date_str = m.emit_activity(activity, garmin, fit_dir)
        if date_str and (newest_date is None or date_str > newest_date):
            newest_date = date_str

    if newest_date:
        m.write_last_sync(tokenstore, newest_date)
        print(f">>> Activity sync complete through {newest_date}.", file=sys.stderr)

    m.run_health_sync(garmin, tokenstore, output_dir)


# ---------------------------------------------------------------------------
# health group
# ---------------------------------------------------------------------------

def cmd_health_fetch(args, user: str) -> None:
    m = fetch_mod()
    from datetime import date as _date, timedelta as _td
    tokenstore = tokenstore_for(user)
    output_dir, _ = m.load_config(user)
    cdate = args.date or (_date.today() - _td(days=1)).isoformat()
    garmin = m.init_garmin(tokenstore)
    m.fetch_health_summary(garmin, cdate, output_dir)


def cmd_health_sync(args, user: str) -> None:
    m = fetch_mod()
    tokenstore = tokenstore_for(user)
    output_dir, _ = m.load_config(user)
    garmin = m.init_garmin(tokenstore)
    m.run_health_sync(garmin, tokenstore, output_dir)


# ---------------------------------------------------------------------------
# training group
# ---------------------------------------------------------------------------

def cmd_training_list(args, user: str) -> None:
    m = push_mod()
    tokenstore = tokenstore_for(user)
    garmin = m.init_garmin(tokenstore)
    # push-workouts' cmd_training_list reads args.week_file/from_date/to_date/fmt
    ns = argparse.Namespace(
        week_file=args.week_file,
        from_date=args.from_date,
        to_date=args.to_date,
        fmt=args.fmt,
    )
    m.cmd_training_list(ns, garmin, tokenstore)


def cmd_training_push(args, user: str) -> None:
    m = push_mod()
    tokenstore = tokenstore_for(user)
    path = Path(args.week_file)
    if not path.exists() and not path.with_suffix(".yaml").exists():
        die(f"File not found: {path}")
    if args.dry_run:
        m.dry_run_week(path, args.fmt)
    elif args.date:
        data = m.load_week_yaml(path)
        session = next(
            (s for s in data.get("sessions", []) if s.get("date") == args.date), None
        )
        if session is None:
            die(f"No session found for {args.date} in {path}")
        specs = m.session_to_workout(session)
        if not specs:
            die(f"No uploadable workout for {args.date} (rest or optional)")
        garmin = m.init_garmin(tokenstore)
        m.delete_date_workouts(garmin, tokenstore, args.date)
        for spec in specs:
            m.upload_and_schedule(garmin, tokenstore, args.date, spec)
    else:
        garmin = m.init_garmin(tokenstore)
        m.process_week(garmin, tokenstore, path, future_only=True)


def cmd_training_delete(args, user: str) -> None:
    m = push_mod()
    tokenstore = tokenstore_for(user)
    garmin = m.init_garmin(tokenstore)
    m.delete_date_workouts(garmin, tokenstore, args.date)


def cmd_training_fetch(args, user: str) -> None:
    """Download a Garmin workout definition and emit it as our YAML session schema."""
    m = push_mod()
    tokenstore = tokenstore_for(user)
    garmin = m.init_garmin(tokenstore)

    try:
        wk = garmin.connectapi(f"/workout-service/workout/{args.workout_id}")
    except Exception as e:
        die(f"Failed to fetch workout {args.workout_id}: {e}")
    if not wk:
        die(f"Workout {args.workout_id} not found")

    session = workout_to_session(wk)

    import yaml as _yaml
    if args.fmt == "json":
        print(json.dumps(session, indent=2, ensure_ascii=False))
    else:
        print(_yaml.dump(session, allow_unicode=True, sort_keys=False, default_flow_style=False))


# ---------------------------------------------------------------------------
# Garmin workout → our YAML session (best-effort reverse mapping)
# ---------------------------------------------------------------------------

def _mps_to_pace(mps) -> str | None:
    """Convert metres/second to 'M:SS' per km."""
    if not mps:
        return None
    sec = round(1000.0 / float(mps))
    return f"{sec // 60}:{sec % 60:02d}"


def _pace_range_from_target(step: dict) -> str | None:
    tt = (step.get("targetType") or {}).get("workoutTargetTypeKey")
    if tt == "pace.zone":
        fast = _mps_to_pace(step.get("targetValueOne"))
        slow = _mps_to_pace(step.get("targetValueTwo"))
        if fast and slow:
            return f"{fast}–{slow}"
        return fast or slow
    return None


def _hr_range_from_target(step: dict) -> str | None:
    tt = (step.get("targetType") or {}).get("workoutTargetTypeKey")
    if tt == "heart.rate.zone":
        low = step.get("targetValueOne")
        high = step.get("targetValueTwo")
        if low is not None and high is not None:
            return f"{int(low)}–{int(high)}"
    return None


def _flatten_steps(steps: list) -> list:
    """Flatten a Garmin step tree into (step, repeat_iterations) pairs, top level only.

    Returns a list of dicts, each either an executable step or a repeat group marker.
    """
    out = []
    for s in steps:
        if s.get("type") == "RepeatGroupDTO":
            out.append({"_repeat": int(s.get("numberOfIterations") or 1),
                        "steps": s.get("workoutSteps", [])})
        else:
            out.append(s)
    return out


def workout_to_session(wk: dict) -> dict:
    """Convert a fetched Garmin workout into our YAML session dict (best-effort).

    Running workouts are mapped to easy/tempo/intervals/long_run by structure.
    Strength workouts are mapped to a `type: strength` block with `steps`.
    """
    name = wk.get("workoutName", "")
    sport_key = (wk.get("sportType") or {}).get("sportTypeKey", "")
    segments = wk.get("workoutSegments") or []
    steps = segments[0].get("workoutSteps", []) if segments else []

    session: dict = {"name": name}

    if sport_key == "strength_training":
        session["type"] = "strength"
        session["focus"] = name
        session["steps"] = _strength_steps_to_yaml(steps)
        return session

    # Running: classify by presence of repeat groups / interval steps
    flat = _flatten_steps(steps)
    has_repeat = any(isinstance(x, dict) and "_repeat" in x for x in flat)
    main_steps = [
        s for s in flat
        if isinstance(s, dict) and "_repeat" not in s
        and (s.get("stepType") or {}).get("stepTypeKey") in ("main", "interval", "warmup", "cooldown")
    ]

    if has_repeat:
        session["type"] = "intervals"
        _fill_interval_session(session, flat)
    else:
        # Single main step → easy or tempo depending on warmup/cooldown presence
        keys = [(s.get("stepType") or {}).get("stepTypeKey") for s in main_steps]
        session["type"] = "tempo" if ("warmup" in keys or "cooldown" in keys) else "easy"
        _fill_simple_session(session, main_steps)

    return session


def _end_value(step: dict):
    cond = (step.get("endCondition") or {}).get("conditionTypeKey")
    val = step.get("endConditionValue")
    return cond, val


def _fill_simple_session(session: dict, main_steps: list) -> None:
    body = next(
        (s for s in main_steps if (s.get("stepType") or {}).get("stepTypeKey") == "main"),
        main_steps[0] if main_steps else None,
    )
    if body is None:
        return
    cond, val = _end_value(body)
    if cond == "time" and val:
        session["duration_min"] = round(float(val) / 60)
    elif cond == "distance" and val:
        session["distance_km"] = round(float(val) / 1000, 2)
    pace = _pace_range_from_target(body)
    hr = _hr_range_from_target(body)
    if pace:
        session["pace_range"] = pace
    elif hr:
        session["hr_range"] = hr


def _fill_interval_session(session: dict, flat: list) -> None:
    repeat = next((x for x in flat if isinstance(x, dict) and "_repeat" in x), None)
    if repeat is None:
        return
    session["reps"] = repeat["_repeat"]
    inner = repeat["steps"]
    interval = next(
        (s for s in inner if (s.get("stepType") or {}).get("stepTypeKey") == "interval"),
        None,
    )
    recovery = next(
        (s for s in inner if (s.get("stepType") or {}).get("stepTypeKey") == "recovery"),
        None,
    )
    if interval:
        cond, val = _end_value(interval)
        if cond == "distance" and val:
            session["distance_m"] = round(float(val))
        elif cond == "time" and val:
            session["effort_min"] = round(float(val) / 60, 1)
        pace = _pace_range_from_target(interval)
        hr = _hr_range_from_target(interval)
        if pace:
            session["pace_range"] = pace
        elif hr:
            session["hr_range"] = hr
    if recovery:
        cond, val = _end_value(recovery)
        if cond == "time" and val:
            session["recovery_type"] = "time"
            session["recovery_sec"] = round(float(val))
        elif cond == "distance" and val:
            session["recovery_type"] = "distance"
            session["recovery_m"] = round(float(val))


def _strength_steps_to_yaml(steps: list) -> list:
    """Convert Garmin strength steps into our `steps` schema (exercise/pause/group)."""
    out = []
    for s in steps:
        stype = (s.get("stepType") or {}).get("stepTypeKey")
        if s.get("type") == "RepeatGroupDTO":
            out.append({
                "group": {
                    "rounds": int(s.get("numberOfIterations") or 1),
                    "rest": "lap",
                    "steps": _strength_steps_to_yaml(s.get("workoutSteps", [])),
                }
            })
        elif stype == "rest":
            cond, val = _end_value(s)
            out.append({"pause": f"{round(float(val))}s" if cond == "time" and val else "lap"})
        elif stype in ("interval", "main"):
            item: dict = {"exercise": True}
            cat = s.get("category")
            ex = s.get("exerciseName")
            if cat:
                item["garmin_category"] = cat
            if ex:
                item["garmin_exercise"] = ex
            desc = s.get("description")
            if desc:
                # description is "name | reps | notes" — split back out
                parts = [p.strip() for p in desc.split("|")]
                if parts:
                    item["name"] = parts[0]
                if len(parts) > 1:
                    item["reps"] = parts[1]
                if len(parts) > 2:
                    item["notes"] = " | ".join(parts[2:])
            out.append(item)
        # warmup/cooldown on strength are synthesised on push — skip on fetch
    return out


# ---------------------------------------------------------------------------
# plan group
# ---------------------------------------------------------------------------

def cmd_plan_list(args, user: str) -> None:
    m = push_mod()
    tokenstore = tokenstore_for(user)
    garmin = m.init_garmin(tokenstore)
    ns = argparse.Namespace(
        plan_dir=args.plan_dir,
        from_date=args.from_date,
        to_date=args.to_date,
        fmt=args.fmt,
    )
    m.cmd_plan_list(ns, garmin, tokenstore)


def cmd_plan_push(args, user: str) -> None:
    m = push_mod()
    from datetime import date as _date
    tokenstore = tokenstore_for(user)
    plan_dir = Path(args.plan_dir)
    if not plan_dir.is_dir():
        die(f"Plan directory not found: {plan_dir}")
    yaml_files = sorted(plan_dir.glob("W[0-9]* – *.yaml"))
    if not yaml_files:
        die(f"No week YAML files found in {plan_dir}")
    today = _date.today().isoformat()
    if args.dry_run:
        for yf in yaml_files:
            data = m.load_week_yaml(yf)
            if data.get("dates", {}).get("end", "9999") < today:
                continue
            print(f"=== {yf.name} ===", file=sys.stderr)
            m.dry_run_week(yf, args.fmt)
    else:
        garmin = m.init_garmin(tokenstore)
        pushed = 0
        for yf in yaml_files:
            data = m.load_week_yaml(yf)
            if data.get("dates", {}).get("end", "9999") < today:
                print(f"  Skipping past week: {yf.name}", file=sys.stderr)
                continue
            print(f">>> Processing {yf.name} …", file=sys.stderr)
            m.process_week(garmin, tokenstore, yf, future_only=True)
            pushed += 1
        print(f">>> Done — pushed {pushed} week(s).", file=sys.stderr)


# ---------------------------------------------------------------------------
# calendar group
# ---------------------------------------------------------------------------

def cmd_calendar_clean(args, user: str) -> None:
    m = clean_mod()
    from datetime import date as _date
    tokenstore = tokenstore_for(user)
    garmin = m.init_garmin(tokenstore)

    if args.date:
        # Single-day clean: fetch only that day's scheduled workouts.
        d = _date.fromisoformat(args.date)
        result = garmin.get_scheduled_workouts(d.year, d.month)
        if isinstance(result, dict):
            items = result.get("calendarItems", result.get("scheduledWorkouts", []))
        else:
            items = result or []
        entries = []
        seen = set()
        for item in items:
            sched_id = item.get("scheduledWorkoutId") or item.get("id")
            if not sched_id or sched_id in seen:
                continue
            item_date = (item.get("date") or item.get("startDate") or "")[:10]
            if item_date == args.date:
                entries.append({
                    "scheduledId": sched_id,
                    "workoutId": item.get("workoutId") or (item.get("workout") or {}).get("workoutId"),
                    "date": item_date,
                    "name": item.get("workoutName") or (item.get("workout") or {}).get("workoutName") or "",
                })
                seen.add(sched_id)
        print(f">>> Cleaning scheduled workouts on {args.date} …", file=sys.stderr)
    else:
        print(">>> Fetching scheduled workouts …", file=sys.stderr)
        entries = m.get_future_scheduled(garmin, user)

    if not entries:
        print("No scheduled workouts found." if args.date
              else "No future scheduled workouts found.")
        return

    print(f"Found {len(entries)} scheduled workout(s).", file=sys.stderr)

    unscheduled = 0
    deleted_ids = set()
    for entry in entries:
        sched_id = entry["scheduledId"]
        workout_id = entry["workoutId"]
        date_str = entry["date"]
        wname = entry["name"]
        try:
            garmin.unschedule_workout(sched_id)
            print(f"UNSCHEDULED\t{date_str}\t{wname}\tscheduledId={sched_id}")
            unscheduled += 1
        except Exception as e:
            print(f"WARN: failed to unschedule {sched_id} ({date_str}): {e}", file=sys.stderr)
        if args.library and workout_id and workout_id not in deleted_ids:
            try:
                garmin.delete_workout(workout_id)
                print(f"DELETED_WORKOUT\t{workout_id}\t{wname}")
                deleted_ids.add(workout_id)
            except Exception as e:
                print(f"WARN: failed to delete workout {workout_id}: {e}", file=sys.stderr)

    print(f">>> Done. Unscheduled: {unscheduled}. "
          f"Deleted from library: {len(deleted_ids)}.", file=sys.stderr)

    if not args.date:
        # Clear our own tracking file so push-workouts starts fresh.
        tracking = Path(tokenstore) / "scheduled_workouts.json"
        if tracking.exists():
            tracking.write_text("{}")
            print(">>> Cleared scheduled_workouts.json.", file=sys.stderr)
    else:
        # Remove just this date from the tracking file.
        tracking = Path(tokenstore) / "scheduled_workouts.json"
        if tracking.exists():
            data = json.loads(tracking.read_text() or "{}")
            if args.date in data:
                del data[args.date]
                tracking.write_text(json.dumps(data, indent=2))
                print(f">>> Removed {args.date} from scheduled_workouts.json.", file=sys.stderr)


# ---------------------------------------------------------------------------
# migrate group
# ---------------------------------------------------------------------------

def cmd_migrate_health(args, user: str) -> None:
    m = migrate_health_mod()
    import yaml  # noqa: F401 — migrate_health.main imports it lazily
    from pathlib import Path as _P
    dry_run = args.dry_run

    if dry_run:
        print("DRY RUN — no files will be moved or modified")

    config = _P.home() / ".marathon-coach" / user / "config.yaml"
    if not config.exists():
        die(f"Config not found: {config}")
    output_dir_str = m.read_yaml_field(str(config), "output_dir")
    if not output_dir_str:
        die(f"output_dir not set in {config}")

    # Reuse migrate-health's logic by invoking its main with argv shaped for it.
    argv_backup = sys.argv
    try:
        sys.argv = ["migrate-health.py", user] + (["--dry-run"] if dry_run else [])
        m.main()
    finally:
        sys.argv = argv_backup


def cmd_migrate_strength(args, user: str) -> None:
    m = migrate_strength_mod()
    argv_backup = sys.argv
    try:
        sys.argv = ["migrate-strength.py", args.plan_dir] + (["--dry-run"] if args.dry_run else [])
        m.main()
    finally:
        sys.argv = argv_backup


# ---------------------------------------------------------------------------
# exercise group (reads the local garmin_exercises.json catalogue)
# ---------------------------------------------------------------------------

def _load_catalogue() -> dict:
    path = _HERE / "garmin_exercises.json"
    if not path.exists():
        die(f"Exercise catalogue not found: {path}")
    with open(path) as f:
        return json.load(f)


def cmd_exercise_list(args, user: str) -> None:
    cat = _load_catalogue()
    if args.category:
        key = args.category.upper()
        if key not in cat:
            die(f"Unknown category: {args.category!r}. Run `exercise list` to see all.")
        for ex in sorted(cat[key]):
            print(f"{key}\t{ex}")
    else:
        for name in sorted(cat):
            print(f"{name}\t({len(cat[name])} exercises)")


def cmd_exercise_search(args, user: str) -> None:
    cat = _load_catalogue()
    q = args.query.lower().replace(" ", "_")
    hits = []
    for category, names in cat.items():
        for ex in names:
            if q in ex.lower() or q in category.lower():
                hits.append((category, ex))
    if not hits:
        print(f"No exercises match {args.query!r}.")
        return
    for category, ex in sorted(hits):
        print(f"{category}\t{ex}")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="garmin", description="Unified Garmin Connect CLI for the running plugin.")
    p.add_argument("--user", help="user config to use (overrides GARMIN_USER and default_user)")
    groups = p.add_subparsers(dest="group", required=True)

    # activity
    activity = groups.add_parser("activity", help="fetch and analyze activities")
    a_sub = activity.add_subparsers(dest="cmd", required=True)
    al = a_sub.add_parser("list", help="list recent activities")
    al.add_argument("--count", type=int, default=20)
    al.set_defaults(func=cmd_activity_list)
    af = a_sub.add_parser("fetch", help="download & analyze one activity")
    af.add_argument("--id", help="numeric activity ID")
    af.add_argument("--date", help="first activity on YYYY-MM-DD")
    af.set_defaults(func=cmd_activity_fetch)
    asy = a_sub.add_parser("sync", help="incremental batch sync")
    asy.add_argument("--count", type=int, default=5)
    asy.set_defaults(func=cmd_activity_sync)

    # health
    health = groups.add_parser("health", help="daily health summaries")
    h_sub = health.add_subparsers(dest="cmd", required=True)
    hf = h_sub.add_parser("fetch", help="fetch health summary for one date")
    hf.add_argument("--date", help="YYYY-MM-DD (default: yesterday)")
    hf.set_defaults(func=cmd_health_fetch)
    hs = h_sub.add_parser("sync", help="fetch all outstanding health summaries")
    hs.set_defaults(func=cmd_health_sync)

    # training
    training = groups.add_parser("training", help="week-level workout management")
    t_sub = training.add_subparsers(dest="cmd", required=True)
    tl = t_sub.add_parser("list", help="compare week YAML vs Garmin calendar")
    tl.add_argument("week_file", nargs="?")
    tl.add_argument("--from", dest="from_date")
    tl.add_argument("--to", dest="to_date")
    tl.add_argument("--format", dest="fmt", default="table", choices=["table", "json", "yaml"])
    tl.set_defaults(func=cmd_training_list)
    tfe = t_sub.add_parser("fetch", help="download a Garmin workout as a YAML session")
    tfe.add_argument("workout_id")
    tfe.add_argument("--format", dest="fmt", default="yaml", choices=["yaml", "json"])
    tfe.set_defaults(func=cmd_training_fetch)
    tp = t_sub.add_parser("push", help="upload & schedule a week")
    tp.add_argument("week_file")
    tp.add_argument("--date", dest="date")
    tp.add_argument("--dry-run", action="store_true")
    tp.add_argument("--format", dest="fmt", default="table", choices=["table", "json", "yaml"])
    tp.set_defaults(func=cmd_training_push)
    tde = t_sub.add_parser("delete", help="remove scheduled workouts for a date")
    tde.add_argument("date")
    tde.set_defaults(func=cmd_training_delete)

    # plan
    plan = groups.add_parser("plan", help="full plan directory management")
    p_sub = plan.add_subparsers(dest="cmd", required=True)
    pl = p_sub.add_parser("list", help="compare plan YAML vs Garmin calendar")
    pl.add_argument("plan_dir")
    pl.add_argument("--from", dest="from_date")
    pl.add_argument("--to", dest="to_date")
    pl.add_argument("--format", dest="fmt", default="table", choices=["table", "json", "yaml"])
    pl.set_defaults(func=cmd_plan_list)
    pp = p_sub.add_parser("push", help="upload & schedule all future weeks")
    pp.add_argument("plan_dir")
    pp.add_argument("--dry-run", action="store_true")
    pp.add_argument("--format", dest="fmt", default="table", choices=["table", "json", "yaml"])
    pp.set_defaults(func=cmd_plan_push)

    # calendar
    calendar = groups.add_parser("calendar", help="Garmin calendar cleanup")
    c_sub = calendar.add_subparsers(dest="cmd", required=True)
    cc = c_sub.add_parser("clean", help="remove scheduled workouts")
    cc.add_argument("--date", help="only clean this YYYY-MM-DD (default: today → race_date)")
    cc.add_argument("--library", action="store_true", help="also delete workout definitions")
    cc.set_defaults(func=cmd_calendar_clean)

    # migrate
    migrate = groups.add_parser("migrate", help="one-time data migrations")
    mg_sub = migrate.add_subparsers(dest="cmd", required=True)
    mh = mg_sub.add_parser("health", help="move health entries to Gesundheitstagebuch")
    mh.add_argument("--dry-run", action="store_true")
    mh.set_defaults(func=cmd_migrate_health)
    ms = mg_sub.add_parser("strength", help="backfill strength blocks from markdown")
    ms.add_argument("plan_dir")
    ms.add_argument("--dry-run", action="store_true")
    ms.set_defaults(func=cmd_migrate_strength)

    # exercise
    exercise = groups.add_parser("exercise", help="browse the Garmin exercise catalogue")
    e_sub = exercise.add_subparsers(dest="cmd", required=True)
    el = e_sub.add_parser("list", help="list categories, or exercises in a category")
    el.add_argument("--category", help="show all exercises in this category")
    el.set_defaults(func=cmd_exercise_list)
    es = e_sub.add_parser("search", help="substring search across all exercises")
    es.add_argument("query")
    es.set_defaults(func=cmd_exercise_search)

    return p


# Commands that do not need a Garmin login / user resolution against config.
_NO_USER_REQUIRED = {cmd_exercise_list, cmd_exercise_search}


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    user = None
    if args.func not in _NO_USER_REQUIRED:
        user = resolve_user(args.user)

    args.func(args, user)


if __name__ == "__main__":
    main()
