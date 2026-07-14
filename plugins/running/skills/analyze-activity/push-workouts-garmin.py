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
from datetime import date as date_cls, timedelta
from pathlib import Path


# ---------------------------------------------------------------------------
# Garmin exercise catalogue (loaded from sibling garmin_exercises.json)
# ---------------------------------------------------------------------------

def _load_exercise_catalogue() -> dict[str, set[str]]:
    """Return {CATEGORY: {exercise_name, ...}} from the bundled FIT SDK catalogue."""
    catalogue_path = Path(__file__).parent / "garmin_exercises.json"
    if not catalogue_path.exists():
        return {}
    with open(catalogue_path) as f:
        raw = json.load(f)
    return {cat.upper(): {ex.upper() for ex in names} for cat, names in raw.items()}

_CATALOGUE = _load_exercise_catalogue()


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
    # Garmin displays targetValueOne first — fast end so zone reads fast→slow.
    return {
        "targetType": {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone", "displayOrder": 6},
        "targetValueOne": fast_mps,
        "targetValueTwo": slow_mps,
    }


def hr_zone_target(hr_range: str) -> dict:
    """Build a heart rate zone target from 'NNN–NNN' bpm range string."""
    parts = [p.strip() for p in hr_range.replace("—", "–").split("–") if p.strip()]
    low = int(parts[0])
    high = int(parts[1]) if len(parts) > 1 else low + 10
    # Garmin displays low bpm first for HR zones
    return {
        "targetType": {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone", "displayOrder": 4},
        "targetValueOne": low,
        "targetValueTwo": high,
    }


def resolve_target(s: dict) -> dict:
    """Pick the best target from a session dict.
    Priority: pace_range > hr_range > no target.
    """
    if s.get("pace_range"):
        return pace_zone_target(s["pace_range"])
    if s.get("hr_range"):
        return hr_zone_target(s["hr_range"])
    return no_target()


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


def resolve_end_condition(s: dict, prefer_key: str = "distance") -> tuple[int, str, float | None]:
    """Return (cond_id, cond_key, cond_value) for the best end condition.
    prefer_key: 'distance' or 'time' — which to prefer when both are present.
    For session-level keys use distance_km / duration_min / effort_min.
    """
    dist = s.get("distance_km") or s.get("effort_km")
    dur = s.get("duration_min") or s.get("effort_min")

    if prefer_key == "distance" and dist is not None:
        return 3, "distance", float(dist) * 1000
    if dur is not None:
        return 2, "time", float(dur) * 60
    if dist is not None:
        return 3, "distance", float(dist) * 1000
    return 1, "lap.button", None  # fallback


def step_name_suffix(s: dict) -> str:
    """Build the '@pace' or '@HR' suffix for a workout name."""
    if s.get("pace_range"):
        return f"@{pace_midpoint_str(s['pace_range'])}"
    if s.get("hr_range"):
        return f"@{s['hr_range']}bpm"
    return ""


# ---------------------------------------------------------------------------
# Garmin step builders
# ---------------------------------------------------------------------------

def _running_sport() -> dict:
    return {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1}


def _strength_sport() -> dict:
    return {"sportTypeId": 5, "sportTypeKey": "strength_training", "displayOrder": 5}


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


def recovery_lap(order: int) -> dict:
    """Recovery ending on lap button — runner decides when recovered enough."""
    step = {
        "type": "ExecutableStepDTO",
        "stepOrder": order,
        "stepType": {"stepTypeId": 4, "stepTypeKey": "recovery", "displayOrder": 4},
        "endCondition": {
            "conditionTypeId": 1, "conditionTypeKey": "lap.button",
            "displayOrder": 1, "displayable": True,
        },
        "targetType": no_target()["targetType"],
    }
    return step


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


def build_workout_payload(name: str, steps: list, estimated_secs: int,
                          sport: dict | None = None) -> dict:
    sport = sport or _running_sport()
    return {
        "workoutName": name,
        "sportType": sport,
        "estimatedDurationInSecs": estimated_secs,
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": sport,
            "workoutSteps": steps,
        }],
    }


def append_strides(steps: list, strides: dict, base_order: int, estimated_secs: int) -> tuple[list, int]:
    """Append a strides block to an existing step list.

    strides fields (all optional with sensible defaults):
      reps        int   — number of strides (default 4)
      distance_m  int   — distance per stride in metres (default 100)
      pace_note   str   — hint shown in workout name, e.g. "~3:30" (not enforced as zone)

    Structure appended:
      <base_order>   main/lap-button  — "run to stride start, press lap when ready"
      <base_order+1> repeat N× [interval_distance, recovery_lap]

    Returns (updated_steps, updated_estimated_secs).
    """
    reps = int(strides.get("reps", 4))
    dist_m = float(strides.get("distance_m", 100))
    pace_note = strides.get("pace_note", "")

    # Transition step: press lap when you reach the start of your stride section
    transition = {
        "type": "ExecutableStepDTO",
        "stepOrder": base_order,
        "stepType": {"stepTypeId": 8, "stepTypeKey": "main", "displayOrder": 8},
        "endCondition": {
            "conditionTypeId": 1, "conditionTypeKey": "lap.button",
            "displayOrder": 1, "displayable": True,
        },
        "targetType": no_target()["targetType"],
        "description": "Laufe zur Startzone der Steigerungen, dann Runde drücken",
    }

    stride_step = interval_distance(1, dist_m, no_target())
    rec_step = recovery_lap(2)
    rg = repeat_group(base_order + 1, reps, [stride_step, rec_step])

    label = f"{reps}×{int(dist_m)}m"
    if pace_note:
        label += f" {pace_note}"

    # Estimated time: ~15 s per stride + ~45 s recovery each, plus ~30 s transition
    stride_est = int(reps * (15 + 45) + 30)

    return steps + [transition, rg], estimated_secs + stride_est


# ---------------------------------------------------------------------------
# Session → Garmin workout (from YAML session dict)
# ---------------------------------------------------------------------------

# Legacy name→Garmin mapping kept for backward compatibility with existing YAMLs
# that don't yet have garmin_category/garmin_exercise fields.
# New YAMLs should set those fields directly (see marathon-coach schema).
_EXERCISE_MAP: dict[str, tuple[str, str]] = {
    "clamshells":                     ("BANDED_EXERCISES", "CLAM_SHELLS"),
    "seitliches beinheben":           ("HIP_STABILITY", "SIDE_LYING_LEG_RAISE"),
    "hüftabduktion":                  ("HIP_STABILITY", "STANDING_HIP_ABDUCTION"),
    "hüftkreisen":                    ("HIP_STABILITY", "HIP_CIRCLES"),
    "monster walks":                  ("HIP_STABILITY", "LATERAL_WALKS_WITH_BAND_AT_ANKLES"),
    "bulgarian split squat":          ("SQUAT", "PISTOL_SQUAT"),
    "einbeinige kniebeuge":           ("SQUAT", "PISTOL_SQUAT"),
    "kniebeuge":                      ("SQUAT", "BACK_SQUATS"),
    "einbeiniges kreuzheben":         ("DEADLIFT", "SINGLE_LEG_BARBELL_DEADLIFT"),
    "einbeinige rdl":                 ("DEADLIFT", "SINGLE_LEG_BARBELL_DEADLIFT"),
    "einbeiniges rdl":                ("DEADLIFT", "SINGLE_LEG_BARBELL_DEADLIFT"),
    "rdl":                            ("DEADLIFT", "ROMANIAN_DEADLIFT"),
    "kreuzheben":                     ("DEADLIFT", "BARBELL_DEADLIFT"),
    "wadenheben":                     ("CALF_RAISE", "STANDING_CALF_RAISE"),
    "wadenheben exzentrisch":         ("CALF_RAISE", "SINGLE_LEG_STANDING_CALF_RAISE"),
    "wadenheben exz.":                ("CALF_RAISE", "SINGLE_LEG_STANDING_CALF_RAISE"),
    "einbeiniges wadenheben":         ("CALF_RAISE", "SINGLE_LEG_STANDING_CALF_RAISE"),
    "tibialis anterior heben":        ("WARM_UP", "WALKOUT"),
    "tibialis heben":                 ("WARM_UP", "WALKOUT"),
    "plank":                          ("PLANK", "PLANK"),
    "seitstütz":                      ("PLANK", "SIDE_PLANK"),
    "seitstütz links":                ("PLANK", "SIDE_PLANK"),
    "seitstütz rechts":               ("PLANK", "SIDE_PLANK"),
    "side plank":                     ("PLANK", "SIDE_PLANK"),
    "dead bug":                       ("HIP_STABILITY", "DEAD_BUG"),
    "deadbugs":                       ("HIP_STABILITY", "DEAD_BUG"),
    "hollow holds":                   ("HYPEREXTENSION", "HOLLOW_HOLD_AND_ROLL"),
    "hollow hold":                    ("HYPEREXTENSION", "HOLLOW_HOLD_AND_ROLL"),
    "bird dog":                       ("HIP_STABILITY", "QUADRUPED"),
    "bird-dogs":                      ("HIP_STABILITY", "QUADRUPED"),
    "bird-dog":                       ("HIP_STABILITY", "QUADRUPED"),
    "brücke":                         ("HIP_RAISE", "HIP_RAISE"),
    "einbeinige brücke":              ("HIP_RAISE", "SINGLE_LEG_HIP_RAISE"),
    "crunch":                         ("CRUNCH", "CRUNCH"),
    "ausfallschritt":                 ("LUNGE", "LUNGE"),
    "curtsy lunge":                   ("LUNGE", "CURTSY_LUNGE"),
    "liegestütz":                     ("PUSH_UP", "PUSH_UP"),
    "row":                            ("ROW", "BENT_OVER_ROW_WITH_BARBELL"),
    "pogos":                          ("PLYO", "BOX_JUMP"),
    "einbeinige hüpfer":              ("PLYO", "BOX_JUMP"),
    "sprünge":                        ("PLYO", "BOX_JUMP"),
    "sprunggelenk-cars":              ("WARM_UP", "ANKLE_CIRCLES"),
    "sprunggelenk cars":              ("WARM_UP", "ANKLE_CIRCLES"),
    "fersengehen":                    ("RUN", "WALK"),
    "gehen auf fußkanten":            ("RUN", "WALK"),
}

if _CATALOGUE:
    for _german, (_cat, _ex) in _EXERCISE_MAP.items():
        if _cat not in _CATALOGUE:
            print(f"WARN exercise map: unknown category {_cat!r} for {_german!r}", file=sys.stderr)
        elif _ex not in _CATALOGUE[_cat]:
            print(f"WARN exercise map: {_ex!r} not in category {_cat!r} for {_german!r}", file=sys.stderr)

_WEIGHT_UNIT = {"unitId": 8, "unitKey": "kilogram", "factor": 1000.0}
_STROKE_TYPE = {"strokeTypeId": 0, "strokeTypeKey": None, "displayOrder": 0}
_EQUIP_TYPE  = {"equipmentTypeId": 0, "equipmentTypeKey": None, "displayOrder": 0}


def _strength_rest_step(order: int, child_step_id: int) -> dict:
    return {
        "type": "ExecutableStepDTO",
        "stepOrder": order,
        "stepType": {"stepTypeId": 5, "stepTypeKey": "rest", "displayOrder": 5},
        "childStepId": child_step_id,
        "endCondition": {
            "conditionTypeId": 1, "conditionTypeKey": "lap.button",
            "displayOrder": 1, "displayable": True,
        },
        "endConditionValue": 0.0,
        "targetType": no_target()["targetType"],
        "targetValueOne": None,
        "targetValueTwo": None,
        "strokeType": _STROKE_TYPE,
        "equipmentType": _EQUIP_TYPE,
        "category": None,
        "exerciseName": None,
        "weightValue": -1.0,
        "weightUnit": _WEIGHT_UNIT,
    }


def exercise_step(order: int, ex: dict, child_step_id: int = 1) -> dict | None:
    """Build a single strength interval step from an exercise dict.

    New YAMLs supply garmin_category + garmin_exercise directly.
    Legacy YAMLs supply only name — looked up via _EXERCISE_MAP (deprecated).
    Returns None if the exercise cannot be resolved.
    """
    name = ex.get("name", "")
    sets = int(ex.get("sets", 3))
    reps = str(ex.get("reps", "10"))
    notes = str(ex.get("notes", ""))

    # Prefer explicit Garmin fields
    category = ex.get("garmin_category", "").upper().strip()
    exercise_name = ex.get("garmin_exercise", "").upper().strip()

    if not category or not exercise_name:
        # Legacy fallback: look up by name
        mapping = _EXERCISE_MAP.get(name.lower().strip())
        if mapping is None:
            print(f"  WARN: unmapped exercise {name!r} — skipped (add garmin_category/garmin_exercise to YAML)",
                  file=sys.stderr)
            return None
        category, exercise_name = mapping
        print(f"  DEPRECATION: {name!r} resolved via legacy map — add garmin_category/garmin_exercise to YAML",
              file=sys.stderr)

    # Validate against catalogue
    if _CATALOGUE:
        if category not in _CATALOGUE:
            print(f"  WARN: unknown garmin_category {category!r} for {name!r} — skipped", file=sys.stderr)
            return None
        if exercise_name not in _CATALOGUE[category]:
            print(f"  WARN: {exercise_name!r} not in category {category!r} for {name!r} — skipped", file=sys.stderr)
            return None

    reps_str = reps.strip()
    is_numeric = reps_str.isdigit()
    parts = [name, reps_str] if name else [exercise_name.lower(), reps_str]
    if notes:
        parts.append(notes)
    description = " | ".join(parts)

    return {
        "type": "ExecutableStepDTO",
        "stepOrder": order,
        "stepType": {"stepTypeId": 3, "stepTypeKey": "interval", "displayOrder": 3},
        "childStepId": child_step_id,
        "endCondition": {
            "conditionTypeId": 10 if is_numeric else 1,
            "conditionTypeKey": "reps" if is_numeric else "lap.button",
            "displayOrder": 10 if is_numeric else 1,
            "displayable": True,
        },
        "endConditionValue": float(reps_str) if is_numeric else 0.0,
        "targetType": no_target()["targetType"],
        "targetValueOne": None,
        "targetValueTwo": None,
        "strokeType": _STROKE_TYPE,
        "equipmentType": _EQUIP_TYPE,
        "category": category,
        "exerciseName": exercise_name,
        "description": description,
        "weightValue": -1.0,
        "weightUnit": _WEIGHT_UNIT,
    }


def _strength_workout(s: dict) -> dict | None:
    """Build a strength_training Garmin workout from a strength block dict.

    s is either a full session (type=strength) or the value of session['strength'].
    Expected keys: focus, duration_min, exercises (list of {name, sets, reps}).
    """
    block = s.get("strength", s) if s.get("type") == "strength" else s
    focus = block.get("focus", "Kraft/Stabi")
    duration_min = block.get("duration_min", 20)
    exercises = block.get("exercises", [])

    if not exercises:
        print("  SKIP strength: no exercises defined", file=sys.stderr)
        return None

    name = f"Kraft {focus} {int(duration_min)}'"

    # Warmup: lap-button, no exercise
    warmup = {
        "type": "ExecutableStepDTO",
        "stepOrder": 1,
        "stepType": {"stepTypeId": 1, "stepTypeKey": "warmup", "displayOrder": 1},
        "childStepId": None,
        "endCondition": {
            "conditionTypeId": 1, "conditionTypeKey": "lap.button",
            "displayOrder": 1, "displayable": True,
        },
        "endConditionValue": 0.0,
        "targetType": no_target()["targetType"],
        "targetValueOne": None,
        "targetValueTwo": 0.0,
        "strokeType": _STROKE_TYPE,
        "equipmentType": _EQUIP_TYPE,
        "category": "CARDIO",
        "exerciseName": "",
        "weightValue": -1.0,
        "weightUnit": _WEIGHT_UNIT,
    }

    steps = [warmup]
    step_order = 2
    for ex in exercises:
        sets = int(ex.get("sets", 3))

        ex_step = exercise_step(step_order + 1, ex, child_step_id=1)
        if ex_step is None:
            continue  # unresolvable — already warned

        rest_step = _strength_rest_step(step_order + 2, child_step_id=1)

        rg = {
            "type": "RepeatGroupDTO",
            "stepOrder": step_order,
            "stepType": {"stepTypeId": 6, "stepTypeKey": "repeat", "displayOrder": 6},
            "childStepId": 1,
            "numberOfIterations": sets,
            "workoutSteps": [ex_step, rest_step],
            "endCondition": {
                "conditionTypeId": 7, "conditionTypeKey": "iterations",
                "displayOrder": 7, "displayable": False,
            },
            "endConditionValue": float(sets),
            "skipLastRestStep": False,
            "smartRepeat": False,
        }
        steps.append(rg)
        step_order += 1

    if len(steps) == 1:
        # only the warmup — all exercises were unmapped
        print("  SKIP strength: all exercises unmapped", file=sys.stderr)
        return None

    return {
        "name": name,
        "steps": steps,
        "estimated_secs": int(float(duration_min) * 60),
        "sport": _strength_sport(),
    }



def session_to_workout(session: dict) -> list[dict]:
    """Convert a YAML session dict to a list of Garmin workout payload dicts.

    Returns [] for rest (with no strength block), optional sessions, or unhandled types.
    May return two specs when a running session also has a strength sub-block.
    """
    stype = session.get("type", "rest")
    if session.get("optional"):
        return []

    specs = []

    if stype == "rest":
        pass  # no running workout; strength sub-block handled below
    elif stype == "strength":
        spec = _strength_workout(session)
        if spec:
            specs.append(spec)
        return specs
    elif stype == "easy":
        spec = _easy_workout(session)
        if spec:
            specs.append(spec)
    elif stype == "tempo":
        spec = _tempo_workout(session)
        if spec:
            specs.append(spec)
    elif stype == "long_run":
        spec = _long_run_workout(session)
        if spec:
            specs.append(spec)
    elif stype == "intervals":
        spec = _intervals_workout(session)
        if spec:
            specs.append(spec)
    elif stype == "race":
        return []  # races are not pre-programmed
    else:
        print(f"  SKIP unknown type {stype!r}", file=sys.stderr)
        return []

    # Append strides block to the running spec if present
    if specs and session.get("strides"):
        strides = session["strides"]
        reps = int(strides.get("reps", 4))
        dist_m = int(strides.get("distance_m", 100))
        pace_note = strides.get("pace_note", "")
        label = f" + {reps}× Steig.{(' ' + pace_note) if pace_note else ''}"
        specs[0]["name"] = specs[0]["name"] + label
        specs[0]["steps"], specs[0]["estimated_secs"] = append_strides(
            specs[0]["steps"], strides, len(specs[0]["steps"]) + 1, specs[0]["estimated_secs"]
        )

    # Strength sub-block on any session type (including rest)
    if session.get("strength"):
        strength_spec = _strength_workout(session["strength"])
        if strength_spec:
            specs.append(strength_spec)

    return specs


def _easy_workout(s: dict) -> dict:
    subtype = s.get("subtype", "jogging").capitalize()
    duration_min = int(s["duration_min"])
    target = resolve_target(s)
    suffix = step_name_suffix(s)
    name = f"{subtype} {duration_min}'{suffix}"
    steps = [main_time(1, duration_min * 60, target)]
    return {"name": name, "steps": steps, "estimated_secs": duration_min * 60}


def _tempo_workout(s: dict) -> dict:
    dist_km = s.get("distance_km")
    effort_min = s.get("effort_min")
    warmup_min = s.get("warmup_min")
    cooldown_min = s.get("cooldown_min")
    target = resolve_target(s)
    easy = easy_pace_for(s["pace_range"]) if s.get("pace_range") else no_target()
    suffix = step_name_suffix(s)

    wu_secs = float(warmup_min) * 60 if warmup_min else 600
    cd_secs = float(cooldown_min) * 60 if cooldown_min else 600
    wu = warmup_time(1, wu_secs) if warmup_min else warmup_lap(1, easy)
    cd = cooldown_time(3, cd_secs) if cooldown_min else cooldown_lap(3, easy)

    if dist_km is not None:
        dist_km = float(dist_km)
        name = f"Flotter DL {dist_km:.0f}km{suffix}"
        mps = parse_pace_mps(s["pace_range"].split("–")[0]) if s.get("pace_range") else 0.05
        est = int(wu_secs + dist_km * 1000 / mps + cd_secs)
        steps = [wu, main_distance(2, dist_km * 1000, target), cd]
    elif effort_min is not None:
        name = f"Tempo {int(effort_min)}'{suffix}"
        est = int(wu_secs + float(effort_min) * 60 + cd_secs)
        steps = [wu, main_time(2, float(effort_min) * 60, target), cd]
    else:
        print("  SKIP: tempo requires distance_km or effort_min", file=sys.stderr)
        return None

    return {"name": name, "steps": steps, "estimated_secs": est}


def _long_run_workout(s: dict) -> dict:
    dist_km = s.get("distance_km")
    duration_min = s.get("duration_min")
    with_efforts = s.get("with_efforts", False)
    target = resolve_target(s)
    suffix = step_name_suffix(s)

    if with_efforts:
        if dist_km is None:
            print("  SKIP: long_run with_efforts requires distance_km", file=sys.stderr)
            return None
        dist_km = float(dist_km)
        easy_p = s.get("easy_pace", "5:30")
        effort_p = s.get("effort_pace", easy_p)
        reps = int(s.get("effort_reps", 3))
        effort_km = float(s.get("effort_km", 3.0))
        recovery_km = float(s.get("recovery_km", 1.0))
        warmup_km = float(s.get("warmup_km", max(dist_km * 0.25, 3.0)))
        cooldown_km = float(s.get("cooldown_km", max(dist_km * 0.1, 1.0)))
        # Support pace or HR targets for each phase
        easy_target = pace_zone_target(easy_p) if ":" in str(easy_p) else (hr_zone_target(easy_p) if easy_p else no_target())
        effort_session = {"pace_range": effort_p} if ":" in str(effort_p) else {"hr_range": effort_p}
        effort_target = resolve_target(effort_session)
        name = f"Langer DL {dist_km:.0f}km mit Einschüben"
        steps = [
            main_distance(1, warmup_km * 1000, easy_target),
            repeat_group(2, reps, [
                interval_distance(1, effort_km * 1000, effort_target),
                recovery_distance(2, recovery_km * 1000),
            ]),
            main_distance(3, cooldown_km * 1000, easy_target),
        ]
        mps = parse_pace_mps(easy_p.split("–")[0]) if "–" in str(easy_p) else (parse_pace_mps(easy_p) if ":" in str(easy_p) else 0.05)
        est = int(dist_km * 1000 / mps)
    elif dist_km is not None:
        dist_km = float(dist_km)
        name = f"Langer DL {dist_km:.0f}km{suffix}"
        steps = [main_distance(1, dist_km * 1000, target)]
        mps = parse_pace_mps(s["pace_range"].split("–")[0]) if s.get("pace_range") else 0.05
        est = int(dist_km * 1000 / mps)
    elif duration_min is not None:
        name = f"Langer DL {int(duration_min)}'{suffix}"
        steps = [main_time(1, float(duration_min) * 60, target)]
        est = int(float(duration_min) * 60)
    else:
        print("  SKIP: long_run requires distance_km or duration_min", file=sys.stderr)
        return None

    return {"name": name, "steps": steps, "estimated_secs": est}


def _intervals_workout(s: dict) -> dict:
    reps = int(s["reps"])
    dist_m = s.get("distance_m")
    effort_min = s.get("effort_min")
    recovery_type = s.get("recovery_type", "time")
    recovery_m = s.get("recovery_m", 400)
    recovery_min = s.get("recovery_min", 1.5)
    recovery_sec = s.get("recovery_sec")
    warmup_min = s.get("warmup_min")
    cooldown_min = s.get("cooldown_min")
    label = s.get("label", "")

    target = resolve_target(s)
    easy = easy_pace_for(s["pace_range"]) if s.get("pace_range") else no_target()
    suffix = step_name_suffix(s)

    # Interval step
    if dist_m is not None:
        dist_m = float(dist_m)
        dist_label = f"{int(dist_m)}m" if dist_m < 1000 else f"{dist_m/1000:.1f}km"
        interval_step = interval_distance(1, dist_m, target)
        mps = parse_pace_mps(s["pace_range"].split("–")[0]) if s.get("pace_range") else 0.05
        interval_sec = dist_m / mps
    elif effort_min is not None:
        effort_sec = float(effort_min) * 60
        dist_label = f"{int(effort_min)}'"
        interval_step = make_step(1, 3, "interval", 2, "time", effort_sec, target)
        interval_sec = effort_sec
    else:
        print("  SKIP: intervals requires distance_m or effort_min", file=sys.stderr)
        return None

    name_parts = [f"Intervall {reps}×{dist_label}"]
    if label:
        name_parts.append(label)
    name_parts[-1] += suffix
    name = " ".join(name_parts)

    # Recovery step
    if recovery_sec is not None:
        rec = recovery_time(2, float(recovery_sec))
        rec_sec = float(recovery_sec)
    elif recovery_type == "distance":
        rec = recovery_distance(2, float(recovery_m))
        rec_sec = float(recovery_m) / 2.5
    else:
        rec = recovery_time(2, float(recovery_min) * 60)
        rec_sec = float(recovery_min) * 60

    rg = repeat_group(2, reps, [interval_step, rec])

    wu_secs = float(warmup_min) * 60 if warmup_min else 600
    cd_secs = float(cooldown_min) * 60 if cooldown_min else 600
    wu = warmup_time(1, wu_secs) if warmup_min else warmup_lap(1, easy)
    cd = cooldown_time(3, cd_secs) if cooldown_min else cooldown_lap(3, easy)

    est = int(wu_secs + reps * (interval_sec + rec_sec) + cd_secs)
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
    sport = spec.get("sport")
    payload = build_workout_payload(name, spec["steps"], spec["estimated_secs"], sport)

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


def dry_run_week(yaml_path: Path, fmt: str = "table") -> None:
    """Print what would be uploaded for a week without touching Garmin."""
    data = load_week_yaml(yaml_path)
    rows = []
    payloads = []
    for session in data.get("sessions", []):
        date_str = session.get("date", "")
        if session.get("optional"):
            continue
        specs = session_to_workout(session)
        for spec in specs:
            sport = spec.get("sport")
            payload = build_workout_payload(spec["name"], spec["steps"], spec["estimated_secs"], sport)
            rows.append({"date": date_str, "name": spec["name"],
                         "estimated_min": spec["estimated_secs"] // 60,
                         "steps": len(spec["steps"])})
            payloads.append({"date": date_str, "workout": payload})

    if not rows:
        print("No uploadable sessions found.")
        return

    if fmt == "json":
        print(json.dumps(payloads, indent=2))
    elif fmt == "yaml":
        print(yaml.dump(payloads, allow_unicode=True, sort_keys=False, default_flow_style=False))
    else:
        # tabular — human-readable summary
        col_w = [10, 35, 10, 7]
        header = ["DATE", "NAME", "EST_MIN", "STEPS"]
        sep = "| " + " | ".join("-" * w for w in col_w) + " |"

        def fmt_row(cells):
            return "| " + " | ".join(str(c).ljust(w) for c, w in zip(cells, col_w)) + " |"

        print(fmt_row(header))
        print(sep)
        for r in rows:
            print(fmt_row([r["date"], r["name"], r["estimated_min"], r["steps"]]))

        # Also print the full payload(s) as JSON so step details are visible
        print()
        print(json.dumps(payloads, indent=2))


def process_week(garmin, tokenstore: str, yaml_path: Path,
                 future_only: bool = False, horizon: str | None = None) -> None:
    """Upload sessions from a week YAML.
    future_only: skip today and earlier.
    horizon: skip sessions after this date (default: today + 7 days).
    Deletes any previously tracked workout for each date before uploading.
    """
    data = load_week_yaml(yaml_path)
    today = date_cls.today().isoformat()
    if horizon is None:
        horizon = (date_cls.today() + timedelta(days=7)).isoformat()
    for session in data.get("sessions", []):
        date_str = session.get("date", "")
        if future_only and date_str <= today:
            continue
        if date_str > horizon:
            continue
        specs = session_to_workout(session)
        if not specs:
            continue
        # Delete any existing tracked workout for this date before uploading
        delete_date_workouts(garmin, tokenstore, date_str)
        for spec in specs:
            upload_and_schedule(garmin, tokenstore, date_str, spec)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 3:
        die(
            "Usage:\n"
            "  push-workouts-garmin.py <user> --week <week-file> [--dry-run [--format table|json|yaml]]\n"
            "  push-workouts-garmin.py <user> --date <YYYY-MM-DD> <week-file>\n"
            "  push-workouts-garmin.py <user> --plan <plan-dir>  [--dry-run [--format table|json|yaml]]\n"
            "  push-workouts-garmin.py <user> --delete-date <YYYY-MM-DD>"
        )

    user = sys.argv[1]
    mode = sys.argv[2]
    tokenstore = str(Path(os.getenv("GARMINTOKENS", f"~/.garminconnect/{user}")).expanduser())

    args = sys.argv[3:]
    dry_run = "--dry-run" in args
    fmt = "table"
    positional = []
    i = 0
    while i < len(args):
        if args[i] == "--dry-run":
            i += 1
        elif args[i] == "--format" and i + 1 < len(args):
            fmt = args[i + 1]
            i += 2
        else:
            positional.append(args[i])
            i += 1

    if mode == "--delete-date":
        date_str = positional[0] if positional else die("Missing date")
        garmin = init_garmin(tokenstore)
        delete_date_workouts(garmin, tokenstore, date_str)
        return

    if mode == "--date":
        # Upload a single specific date from a week YAML (bypasses future_only filter).
        if len(positional) < 2:
            die("Usage: --date <YYYY-MM-DD> <week-file>")
        date_str = positional[0]
        path = Path(positional[1])
        if not path.exists() and not path.with_suffix(".yaml").exists():
            die(f"File not found: {path}")
        data = load_week_yaml(path)
        session = next((s for s in data.get("sessions", []) if s.get("date") == date_str), None)
        if session is None:
            die(f"No session found for {date_str} in {path}")
        specs = session_to_workout(session)
        if not specs:
            die(f"No uploadable workout for {date_str} (rest or optional)")
        garmin = init_garmin(tokenstore)
        delete_date_workouts(garmin, tokenstore, date_str)
        for spec in specs:
            upload_and_schedule(garmin, tokenstore, date_str, spec)
        return

    if mode == "--week":
        if not positional:
            die("Missing week file path")
        path = Path(positional[0])
        if not path.exists() and not path.with_suffix(".yaml").exists():
            die(f"File not found: {path}")
        if dry_run:
            dry_run_week(path, fmt)
            return
        garmin = init_garmin(tokenstore)
        process_week(garmin, tokenstore, path, future_only=True)
        return

    if mode == "--plan":
        if not positional:
            die("Missing plan directory path")
        plan_dir = Path(positional[0])
        if not plan_dir.is_dir():
            die(f"Plan directory not found: {plan_dir}")
        yaml_files = sorted(plan_dir.glob("W[0-9]* – *.yaml"))
        if not yaml_files:
            die(f"No week YAML files found in {plan_dir}")
        today = date_cls.today().isoformat()
        if dry_run:
            for yf in yaml_files:
                data = load_week_yaml(yf)
                if data.get("dates", {}).get("end", "9999") < today:
                    continue
                print(f"=== {yf.name} ===", file=sys.stderr)
                dry_run_week(yf, fmt)
            return
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
