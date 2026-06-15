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
push-workouts-garmin.py — upload structured workouts to Garmin Connect and schedule them.

Reads week YAML files (W<N> – DD.MM–DD.MM.yaml) as the source of truth.
No markdown parsing — YAML is the canonical plan data.

Modes:
    push-workouts-garmin.py <user> --week <week-file-path>
        Upload and schedule all non-rest, non-optional sessions from one week YAML.
        Also accepts a .md path — will resolve to the sibling .yaml.

    push-workouts-garmin.py <user> --plan <plan-dir-path>
        Upload and schedule all sessions from every week YAML in a plan directory.

    push-workouts-garmin.py <user> --delete-date <YYYY-MM-DD>
        Delete all scheduled Garmin workouts tracked for a given date.

Tracks uploaded workout IDs in ~/.garminconnect/<user>/scheduled_workouts.json.
Reads Garmin tokens from ~/.garminconnect/<user>/garmin_tokens.json.
"""

import json
import os
import sys
import yaml
from datetime import date as date_cls
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def read_yaml_field(path: str, field: str) -> str:
    with open(path) as f:
        for line in f:
            if line.startswith(f"{field}:"):
                return line.split(":", 1)[1].strip().strip('"').strip("'")
    return ""


def token_artifact(tokenstore: str) -> str:
    p = Path(tokenstore)
    return str(p) if p.suffix == ".json" else str(p / "garmin_tokens.json")


def tokens_present(tokenstore: str) -> bool:
    return os.path.exists(token_artifact(tokenstore))


def init_garmin(tokenstore: str):
    from garminconnect import (
        Garmin,
        GarminConnectAuthenticationError,
        GarminConnectConnectionError,
        GarminConnectTooManyRequestsError,
    )

    if not tokens_present(tokenstore):
        die(
            "No Garmin token cache found.\n"
            "Run fetch-fit-garmin.py <user> once interactively to create it."
        )

    def prompt_mfa() -> str:
        if not sys.stdin.isatty():
            die("Garmin MFA required but no TTY. Run fetch-fit-garmin.py interactively first.")
        return input("Garmin MFA code: ").strip()

    try:
        garmin = Garmin(prompt_mfa=prompt_mfa)
        garmin.login(tokenstore)
        return garmin
    except (GarminConnectAuthenticationError, GarminConnectConnectionError) as e:
        die(f"Garmin login failed: {e}")
    except GarminConnectTooManyRequestsError as e:
        die(f"Garmin rate limit: {e}")


# ---------------------------------------------------------------------------
# Scheduled workouts tracking
# ---------------------------------------------------------------------------

def scheduled_workouts_path(tokenstore: str) -> str:
    return os.path.join(tokenstore, "scheduled_workouts.json")


def load_scheduled(tokenstore: str) -> dict:
    path = scheduled_workouts_path(tokenstore)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def save_scheduled(tokenstore: str, data: dict) -> None:
    with open(scheduled_workouts_path(tokenstore), "w") as f:
        json.dump(data, f, indent=2)


def track_workout(tokenstore: str, date_str: str, workout_id: int, scheduled_id: int) -> None:
    data = load_scheduled(tokenstore)
    data.setdefault(date_str, []).append({"workoutId": workout_id, "scheduledId": scheduled_id})
    save_scheduled(tokenstore, data)


def untrack_date(tokenstore: str, date_str: str) -> None:
    data = load_scheduled(tokenstore)
    data.pop(date_str, None)
    save_scheduled(tokenstore, data)


# ---------------------------------------------------------------------------
# Pace utilities
# ---------------------------------------------------------------------------

def parse_pace_mps(pace_str: str) -> float:
    """Convert 'M:SS' to metres per second."""
    pace_str = pace_str.strip()
    m, s = pace_str.split(":")
    sec_per_km = int(m) * 60 + int(s)
    return 1000.0 / sec_per_km


def pace_range_targets(pace_range: str) -> tuple[float, float]:
    """Return (fast_mps, slow_mps) from 'M:SS–M:SS' range string."""
    parts = [p.strip() for p in pace_range.replace("—", "–").split("–") if p.strip()]
    if len(parts) == 2:
        return parse_pace_mps(parts[0]), parse_pace_mps(parts[1])
    mps = parse_pace_mps(parts[0])
    sec = 1000.0 / mps
    return 1000.0 / (sec - 5), 1000.0 / (sec + 5)


def pace_midpoint_str(pace_range: str) -> str:
    fast, slow = pace_range_targets(pace_range)
    mid_mps = (fast + slow) / 2
    sec = round(1000.0 / mid_mps)
    return f"{sec // 60}:{sec % 60:02d}"


def pace_zone_target(pace_range: str) -> dict:
    fast_mps, slow_mps = pace_range_targets(pace_range)
    return {
        "targetType": {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone", "displayOrder": 6},
        "targetValueOne": slow_mps,
        "targetValueTwo": fast_mps,
    }


def easy_pace_for(pace_range: str, offset_sec: int = 60) -> dict:
    """Easy pace target = slow end of pace_range + offset_sec per km."""
    _, slow_mps = pace_range_targets(pace_range)
    easy_sec = 1000.0 / slow_mps + offset_sec
    band_sec = easy_sec + 15
    return {
        "targetType": {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone", "displayOrder": 6},
        "targetValueOne": 1000.0 / band_sec,
        "targetValueTwo": 1000.0 / easy_sec,
    }


def no_target() -> dict:
    return {
        "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target", "displayOrder": 1},
        "targetValueOne": None,
        "targetValueTwo": None,
    }


# ---------------------------------------------------------------------------
# Garmin step builders
# ---------------------------------------------------------------------------

def _running_sport() -> dict:
    return {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1}


def make_step(order: int, type_id: int, type_key: str,
              cond_id: int, cond_key: int, cond_value: float,
              target: dict) -> dict:
    step = {
        "type": "ExecutableStepDTO",
        "stepOrder": order,
        "stepType": {"stepTypeId": type_id, "stepTypeKey": type_key, "displayOrder": type_id},
        "endCondition": {
            "conditionTypeId": cond_id, "conditionTypeKey": cond_key,
            "displayOrder": cond_id, "displayable": True,
        },
        "endConditionValue": cond_value,
        "targetType": target["targetType"],
        "targetValueOne": target.get("targetValueOne"),
        "targetValueTwo": target.get("targetValueTwo"),
    }
    return {k: v for k, v in step.items() if v is not None}


def warmup_time(order: int, secs: float) -> dict:
    return make_step(order, 1, "warmup", 2, "time", secs, no_target())


def warmup_lap(order: int, target: dict | None = None) -> dict:
    """Warmup ending on lap button press — runner decides when they've arrived."""
    step = {
        "type": "ExecutableStepDTO",
        "stepOrder": order,
        "stepType": {"stepTypeId": 1, "stepTypeKey": "warmup", "displayOrder": 1},
        "endCondition": {
            "conditionTypeId": 1, "conditionTypeKey": "lap.button",
            "displayOrder": 1, "displayable": True,
        },
        "targetType": (target or no_target())["targetType"],
        "targetValueOne": (target or no_target()).get("targetValueOne"),
        "targetValueTwo": (target or no_target()).get("targetValueTwo"),
    }
    return {k: v for k, v in step.items() if v is not None}


def cooldown_lap(order: int, target: dict | None = None) -> dict:
    """Cooldown ending on lap button press."""
    step = {
        "type": "ExecutableStepDTO",
        "stepOrder": order,
        "stepType": {"stepTypeId": 2, "stepTypeKey": "cooldown", "displayOrder": 2},
        "endCondition": {
            "conditionTypeId": 1, "conditionTypeKey": "lap.button",
            "displayOrder": 1, "displayable": True,
        },
        "targetType": (target or no_target())["targetType"],
        "targetValueOne": (target or no_target()).get("targetValueOne"),
        "targetValueTwo": (target or no_target()).get("targetValueTwo"),
    }
    return {k: v for k, v in step.items() if v is not None}


def cooldown_time(order: int, secs: float) -> dict:
    return make_step(order, 2, "cooldown", 2, "time", secs, no_target())


def main_time(order: int, secs: float, target: dict) -> dict:
    return make_step(order, 8, "main", 2, "time", secs, target)


def main_distance(order: int, metres: float, target: dict) -> dict:
    return make_step(order, 8, "main", 3, "distance", metres, target)


def interval_distance(order: int, metres: float, target: dict) -> dict:
    return make_step(order, 3, "interval", 3, "distance", metres, target)


def recovery_time(order: int, secs: float) -> dict:
    return make_step(order, 4, "recovery", 2, "time", secs, no_target())


def recovery_distance(order: int, metres: float) -> dict:
    return make_step(order, 4, "recovery", 3, "distance", metres, no_target())


def repeat_group(order: int, iterations: int, steps: list) -> dict:
    return {
        "type": "RepeatGroupDTO",
        "stepOrder": order,
        "stepType": {"stepTypeId": 6, "stepTypeKey": "repeat", "displayOrder": 6},
        "numberOfIterations": iterations,
        "workoutSteps": steps,
        "endCondition": {
            "conditionTypeId": 7, "conditionTypeKey": "iterations",
            "displayOrder": 7, "displayable": False,
        },
        "endConditionValue": float(iterations),
        "smartRepeat": False,
    }


def build_workout_payload(name: str, steps: list, estimated_secs: int) -> dict:
    return {
        "workoutName": name,
        "sportType": _running_sport(),
        "estimatedDurationInSecs": estimated_secs,
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": _running_sport(),
            "workoutSteps": steps,
        }],
    }


# ---------------------------------------------------------------------------
# Session → Garmin workout (from YAML session dict)
# ---------------------------------------------------------------------------

def session_to_workout(session: dict) -> dict | None:
    """Convert a YAML session dict to a Garmin workout payload dict.
    Returns None for rest, optional, or unhandled types."""
    stype = session.get("type", "rest")
    if stype == "rest":
        return None
    if session.get("optional"):
        return None

    if stype == "easy":
        return _easy_workout(session)
    if stype == "tempo":
        return _tempo_workout(session)
    if stype == "long_run":
        return _long_run_workout(session)
    if stype == "intervals":
        return _intervals_workout(session)
    if stype == "race":
        return None  # races are not pre-programmed

    print(f"  SKIP unknown type {stype!r}", file=sys.stderr)
    return None


def _easy_workout(s: dict) -> dict:
    subtype = s.get("subtype", "jogging").capitalize()
    duration_min = int(s["duration_min"])
    pace_range = s.get("pace_range", "")
    total_sec = duration_min * 60
    target = pace_zone_target(pace_range) if pace_range else no_target()
    mid = pace_midpoint_str(pace_range) if pace_range else f"{duration_min}'"
    name = f"{subtype} {duration_min}'@{mid}" if pace_range else f"{subtype} {duration_min}'"
    # Easy runs are single continuous steps — no warmup/cooldown needed
    steps = [main_time(1, total_sec, target)]
    return {"name": name, "steps": steps, "estimated_secs": total_sec}


def _tempo_workout(s: dict) -> dict:
    dist_km = float(s["distance_km"])
    pace_range = s.get("pace_range", "")
    target = pace_zone_target(pace_range) if pace_range else no_target()
    easy = easy_pace_for(pace_range) if pace_range else no_target()
    mid = pace_midpoint_str(pace_range) if pace_range else ""
    name = f"Flotter DL {dist_km:.0f}km@{mid}" if mid else f"Flotter DL {dist_km:.0f}km"
    mps = parse_pace_mps(pace_range.split("–")[0]) if pace_range else 0.05
    est = int(1200 + dist_km * 1000 / mps)
    steps = [warmup_time(1, 600), main_distance(2, dist_km * 1000, target), cooldown_time(3, 600)]
    return {"name": name, "steps": steps, "estimated_secs": est}


def _long_run_workout(s: dict) -> dict:
    dist_km = float(s["distance_km"])
    with_efforts = s.get("with_efforts", False)

    if with_efforts:
        easy_p = s.get("easy_pace", "5:30")
        effort_p = s.get("effort_pace", easy_p)
        reps = int(s.get("effort_reps", 3))
        effort_km = float(s.get("effort_km", 3.0))
        recovery_km = float(s.get("recovery_km", 1.0))
        # warmup_km / cooldown_km can be specified explicitly; fall back to 25%/10% of total
        warmup_km = float(s.get("warmup_km", max(dist_km * 0.25, 3.0)))
        cooldown_km = float(s.get("cooldown_km", max(dist_km * 0.1, 1.0)))
        easy_target = pace_zone_target(easy_p) if ":" in easy_p else no_target()
        effort_target = pace_zone_target(effort_p) if ":" in effort_p else no_target()
        name = f"Langer DL {dist_km:.0f}km mit Einschüben"
        steps = [
            main_distance(1, warmup_km * 1000, easy_target),
            repeat_group(2, reps, [
                interval_distance(1, effort_km * 1000, effort_target),
                recovery_distance(2, recovery_km * 1000),
            ]),
            main_distance(3, cooldown_km * 1000, easy_target),
        ]
        mps = parse_pace_mps(easy_p.split("–")[0]) if "–" in easy_p else parse_pace_mps(easy_p)
        est = int(dist_km * 1000 / mps)
    else:
        pace_range = s.get("pace_range", "")
        target = pace_zone_target(pace_range) if pace_range else no_target()
        mid = pace_midpoint_str(pace_range) if pace_range else ""
        name = f"Langer DL {dist_km:.0f}km@{mid}" if mid else f"Langer DL {dist_km:.0f}km"
        steps = [main_distance(1, dist_km * 1000, target)]
        mps = parse_pace_mps(pace_range.split("–")[0]) if pace_range else 0.05
        est = int(dist_km * 1000 / mps)

    return {"name": name, "steps": steps, "estimated_secs": est}


def _intervals_workout(s: dict) -> dict:
    reps = int(s["reps"])
    dist_m = float(s["distance_m"])
    pace_range = s.get("pace_range", "")
    recovery_type = s.get("recovery_type", "time")
    recovery_m = float(s.get("recovery_m", 400))
    recovery_min = float(s.get("recovery_min", 1.5))
    label = s.get("label", "")

    target = pace_zone_target(pace_range) if pace_range else no_target()
    easy = easy_pace_for(pace_range) if pace_range else no_target()
    mid = pace_midpoint_str(pace_range) if pace_range else ""
    dist_label = f"{int(dist_m)}m" if dist_m < 1000 else f"{dist_m/1000:.1f}km"
    name_parts = [f"Intervall {reps}×{dist_label}"]
    if label:
        name_parts.append(label)
    if mid:
        name_parts[-1] += f"@{mid}"
    name = " ".join(name_parts)

    if recovery_type == "distance":
        rec = recovery_distance(2, recovery_m)
    else:
        rec = recovery_time(2, recovery_min * 60)

    rg = repeat_group(2, reps, [interval_distance(1, dist_m, target), rec])

    # Warmup and cooldown on lap button — runner jogs to their session spot
    wu = warmup_lap(1, easy)
    cd = cooldown_lap(3, easy)

    mps = parse_pace_mps(pace_range.split("–")[0]) if pace_range else 0.05
    rec_sec = recovery_m / 2.5 if recovery_type == "distance" else recovery_min * 60
    est = int(1200 + reps * (dist_m / mps + rec_sec))
    return {"name": name, "steps": [wu, rg, cd], "estimated_secs": est}


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------

def resolve_yaml_path(path: Path) -> Path:
    """Given a .md or .yaml path, return the sibling .yaml path."""
    if path.suffix == ".yaml":
        return path
    return path.with_suffix(".yaml")


def load_week_yaml(path: Path) -> dict:
    yaml_path = resolve_yaml_path(path)
    if not yaml_path.exists():
        die(f"Week YAML not found: {yaml_path}\nCreate the plan with /marathon-coach new first.")
    with open(yaml_path) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Upload and schedule
# ---------------------------------------------------------------------------

def upload_and_schedule(garmin, tokenstore: str, date_str: str, spec: dict) -> None:
    name = spec["name"]
    payload = build_workout_payload(name, spec["steps"], spec["estimated_secs"])

    print(f"  Uploading '{name}' for {date_str} …", file=sys.stderr)
    result = garmin.upload_workout(payload)
    workout_id = result.get("workoutId") or (result.get("workout") or {}).get("workoutId")
    if not workout_id:
        print(f"  WARN: no workoutId in response for '{name}': {result}", file=sys.stderr)
        return

    sched = garmin.schedule_workout(workout_id, date_str)
    scheduled_id = sched.get("scheduledWorkoutId") or sched.get("workoutScheduleId") or 0

    track_workout(tokenstore, date_str, workout_id, scheduled_id)
    print(f"GARMIN_WORKOUT_ID={workout_id}\tDATE={date_str}\tSESSION={name}")


def delete_date_workouts(garmin, tokenstore: str, date_str: str) -> None:
    data = load_scheduled(tokenstore)
    entries = data.get(date_str, [])
    if not entries:
        print(f"  No tracked workouts for {date_str}", file=sys.stderr)
        return
    for entry in entries:
        if sid := entry.get("scheduledId"):
            try:
                garmin.unschedule_workout(sid)
            except Exception as e:
                print(f"  WARN: unschedule {sid}: {e}", file=sys.stderr)
        if wid := entry.get("workoutId"):
            try:
                garmin.delete_workout(wid)
                print(f"  Deleted workout {wid} (was on {date_str})", file=sys.stderr)
            except Exception as e:
                print(f"  WARN: delete {wid}: {e}", file=sys.stderr)
    untrack_date(tokenstore, date_str)


def process_week(garmin, tokenstore: str, yaml_path: Path, future_only: bool = False) -> None:
    data = load_week_yaml(yaml_path)
    today = date_cls.today().isoformat()
    for session in data.get("sessions", []):
        date_str = session.get("date", "")
        if future_only and date_str <= today:
            continue
        spec = session_to_workout(session)
        if spec is None:
            continue
        upload_and_schedule(garmin, tokenstore, date_str, spec)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 3:
        die(
            "Usage:\n"
            "  push-workouts-garmin.py <user> --week <week-file>\n"
            "  push-workouts-garmin.py <user> --plan <plan-dir>\n"
            "  push-workouts-garmin.py <user> --delete-date <YYYY-MM-DD>"
        )

    user = sys.argv[1]
    mode = sys.argv[2]
    tokenstore = str(Path(os.getenv("GARMINTOKENS", f"~/.garminconnect/{user}")).expanduser())

    if mode == "--delete-date":
        date_str = sys.argv[3] if len(sys.argv) > 3 else die("Missing date")
        garmin = init_garmin(tokenstore)
        delete_date_workouts(garmin, tokenstore, date_str)
        return

    if mode == "--week":
        if len(sys.argv) < 4:
            die("Missing week file path")
        path = Path(sys.argv[3])
        if not path.exists() and not path.with_suffix(".yaml").exists():
            die(f"File not found: {path}")
        garmin = init_garmin(tokenstore)
        process_week(garmin, tokenstore, path, future_only=False)
        return

    if mode == "--plan":
        if len(sys.argv) < 4:
            die("Missing plan directory path")
        plan_dir = Path(sys.argv[3])
        if not plan_dir.is_dir():
            die(f"Plan directory not found: {plan_dir}")
        yaml_files = sorted(plan_dir.glob("W[0-9]* – *.yaml"))
        if not yaml_files:
            die(f"No week YAML files found in {plan_dir}")
        today = date_cls.today().isoformat()
        garmin = init_garmin(tokenstore)
        pushed = 0
        for yf in yaml_files:
            # Skip weeks that have already ended
            data = load_week_yaml(yf)
            if data.get("dates", {}).get("end", "9999") < today:
                print(f"  Skipping past week: {yf.name}", file=sys.stderr)
                continue
            print(f">>> Processing {yf.name} …", file=sys.stderr)
            process_week(garmin, tokenstore, yf, future_only=True)
            pushed += 1
        print(f">>> Done — pushed {pushed} week(s).", file=sys.stderr)
        return

    die(f"Unknown mode: {mode!r}")


if __name__ == "__main__":
    main()
