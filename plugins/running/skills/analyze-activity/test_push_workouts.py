#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "garminconnect==0.3.6",
#   "curl_cffi",
#   "pydantic",
#   "pyyaml",
#   "pytest",
# ]
# ///
"""
Tests for push-workouts-garmin.py — pure logic only, no Garmin connection needed.

Run with:
    uv run --script test_push_workouts.py
    uv run --script test_push_workouts.py -v
    uv run --script test_push_workouts.py -k intervals
"""

import importlib.util
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Load the script as a module (hyphenated name, not importable normally)
# ---------------------------------------------------------------------------

_script = Path(__file__).parent / "push-workouts-garmin.py"
_spec = importlib.util.spec_from_file_location("push_workouts", _script)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

# Pull everything we test into local names
parse_pace_mps      = _mod.parse_pace_mps
pace_range_targets  = _mod.pace_range_targets
pace_midpoint_str   = _mod.pace_midpoint_str
pace_zone_target    = _mod.pace_zone_target
hr_zone_target      = _mod.hr_zone_target
easy_pace_for       = _mod.easy_pace_for
no_target           = _mod.no_target
resolve_target      = _mod.resolve_target
step_name_suffix    = _mod.step_name_suffix
append_strides      = _mod.append_strides
session_to_workout  = _mod.session_to_workout
build_workout_payload = _mod.build_workout_payload
exercise_step       = _mod.exercise_step


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def steps_of(spec):
    return spec["steps"]


def step_types(spec):
    return [s.get("stepType", {}).get("stepTypeKey") or s.get("type") for s in steps_of(spec)]


def end_condition(step):
    return step.get("endCondition", {}).get("conditionTypeKey")


def target_type(step):
    return step.get("targetType", {}).get("workoutTargetTypeKey")


def first(session: dict):
    """Return the first spec from session_to_workout (the running spec)."""
    specs = session_to_workout(session)
    assert specs, f"Expected at least one spec for {session}"
    return specs[0]


# ---------------------------------------------------------------------------
# Pace utilities
# ---------------------------------------------------------------------------

class TestParsePaceMps:
    def test_five_forty(self):
        mps = parse_pace_mps("5:40")
        assert abs(mps - 1000 / 340) < 0.001

    def test_four_zero_zero(self):
        mps = parse_pace_mps("4:00")
        assert abs(mps - 1000 / 240) < 0.001

    def test_roundtrip(self):
        # parse → convert back to sec/km → matches original
        for pace in ("4:00", "5:30", "6:15", "3:45"):
            m, s = pace.split(":")
            expected_sec = int(m) * 60 + int(s)
            assert abs(1000 / parse_pace_mps(pace) - expected_sec) < 0.01


class TestPaceRangeTargets:
    def test_fast_slower_than_slow(self):
        fast, slow = pace_range_targets("5:40–5:50")
        assert fast > slow  # faster pace = higher m/s

    def test_symmetric_single_pace(self):
        fast, slow = pace_range_targets("5:00")
        assert fast > slow
        mid = 1000 / parse_pace_mps("5:00")
        assert abs(1000 / fast - (mid - 5)) < 0.5
        assert abs(1000 / slow - (mid + 5)) < 0.5

    def test_values_match_individual_parses(self):
        fast, slow = pace_range_targets("4:10–4:20")
        assert abs(fast - parse_pace_mps("4:10")) < 0.001
        assert abs(slow - parse_pace_mps("4:20")) < 0.001


class TestPaceMidpointStr:
    def test_midpoint_between_bounds(self):
        mid = pace_midpoint_str("5:40–5:50")
        m, s = mid.split(":")
        mid_sec = int(m) * 60 + int(s)
        assert 340 <= mid_sec <= 350

    def test_symmetric_around_5_45(self):
        assert pace_midpoint_str("5:40–5:50") == "5:45"

    def test_format(self):
        result = pace_midpoint_str("4:00–4:10")
        assert ":" in result
        m, s = result.split(":")
        assert len(s) == 2


# ---------------------------------------------------------------------------
# Target builders
# ---------------------------------------------------------------------------

class TestPaceZoneTarget:
    def test_type_key(self):
        t = pace_zone_target("5:00–5:10")
        assert t["targetType"]["workoutTargetTypeKey"] == "pace.zone"

    def test_fast_end_is_value_one(self):
        t = pace_zone_target("5:00–5:10")
        assert t["targetValueOne"] > t["targetValueTwo"]  # fast m/s > slow m/s


class TestHrZoneTarget:
    def test_type_key(self):
        t = hr_zone_target("140–155")
        assert t["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone"

    def test_bounds(self):
        t = hr_zone_target("140–155")
        assert t["targetValueOne"] == 140
        assert t["targetValueTwo"] == 155


class TestResolveTarget:
    def test_prefers_pace_over_hr(self):
        t = resolve_target({"pace_range": "5:00–5:10", "hr_range": "140–155"})
        assert t["targetType"]["workoutTargetTypeKey"] == "pace.zone"

    def test_falls_back_to_hr(self):
        t = resolve_target({"hr_range": "140–155"})
        assert t["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone"

    def test_no_target_when_neither(self):
        t = resolve_target({})
        assert t["targetType"]["workoutTargetTypeKey"] == "no.target"


class TestEasyPaceFor:
    def test_slower_than_slow_end(self):
        # easy = slow end + offset, so easy_mps < slow_mps
        _, slow_mps = pace_range_targets("5:00–5:10")
        t = easy_pace_for("5:00–5:10", offset_sec=60)
        assert t["targetValueOne"] < slow_mps  # both values are slower

    def test_type_key(self):
        t = easy_pace_for("5:00–5:10")
        assert t["targetType"]["workoutTargetTypeKey"] == "pace.zone"


# ---------------------------------------------------------------------------
# session_to_workout — easy
# ---------------------------------------------------------------------------

class TestEasyWorkout:
    def _session(self, **kwargs):
        base = {"type": "easy", "subtype": "jogging", "duration_min": 30, "pace_range": "5:40–5:50"}
        base.update(kwargs)
        return base

    def test_name_contains_subtype_and_duration(self):
        s = first(self._session())
        assert "Jogging" in s["name"]
        assert "30'" in s["name"]

    def test_name_contains_pace_midpoint(self):
        s = first(self._session())
        assert "5:45" in s["name"]

    def test_single_timed_main_step(self):
        s = first(self._session())
        assert len(s["steps"]) == 1
        assert s["steps"][0]["stepType"]["stepTypeKey"] == "main"
        assert end_condition(s["steps"][0]) == "time"

    def test_estimated_secs(self):
        s = first(self._session(duration_min=45))
        assert s["estimated_secs"] == 2700

    def test_hr_target(self):
        s = first(self._session(pace_range=None, hr_range="130–140"))
        assert target_type(s["steps"][0]) == "heart.rate.zone"

    def test_rest_returns_empty(self):
        assert session_to_workout({"type": "rest"}) == []

    def test_optional_returns_empty(self):
        assert session_to_workout(self._session(optional=True)) == []


# ---------------------------------------------------------------------------
# session_to_workout — tempo
# ---------------------------------------------------------------------------

class TestTempoWorkout:
    def _session(self, **kwargs):
        base = {"type": "tempo", "effort_min": 20, "pace_range": "4:10–4:20"}
        base.update(kwargs)
        return base

    def test_three_steps_wu_main_cd(self):
        s = first(self._session())
        keys = step_types(s)
        assert "warmup" in keys
        assert "main" in keys
        assert "cooldown" in keys

    def test_distance_based(self):
        s = first({"type": "tempo", "distance_km": 8, "pace_range": "4:10–4:20"})
        main = next(st for st in s["steps"] if st.get("stepType", {}).get("stepTypeKey") == "main")
        assert end_condition(main) == "distance"
        assert main["endConditionValue"] == 8000.0

    def test_lap_button_warmup_when_no_warmup_min(self):
        s = first(self._session())
        wu = s["steps"][0]
        assert end_condition(wu) == "lap.button"

    def test_timed_warmup_when_warmup_min_set(self):
        s = first(self._session(warmup_min=10))
        wu = s["steps"][0]
        assert end_condition(wu) == "time"
        assert wu["endConditionValue"] == 600.0

    def test_missing_effort_and_distance_returns_empty(self):
        assert session_to_workout({"type": "tempo", "pace_range": "4:10–4:20"}) == []


# ---------------------------------------------------------------------------
# session_to_workout — long_run
# ---------------------------------------------------------------------------

class TestLongRunWorkout:
    def test_distance_based_name(self):
        s = first({"type": "long_run", "distance_km": 22, "pace_range": "5:10–5:20"})
        assert "22" in s["name"]

    def test_time_based(self):
        s = first({"type": "long_run", "duration_min": 90, "pace_range": "5:10–5:20"})
        assert end_condition(s["steps"][0]) == "time"
        assert s["estimated_secs"] == 5400

    def test_with_efforts_structure(self):
        s = first({
            "type": "long_run",
            "distance_km": 20,
            "with_efforts": True,
            "easy_pace": "5:20",
            "effort_pace": "4:30",
            "effort_reps": 3,
            "effort_km": 2.0,
            "recovery_km": 1.0,
        })
        assert any(st.get("type") == "RepeatGroupDTO" for st in s["steps"])

    def test_missing_both_returns_empty(self):
        assert session_to_workout({"type": "long_run", "pace_range": "5:00–5:10"}) == []


# ---------------------------------------------------------------------------
# session_to_workout — intervals
# ---------------------------------------------------------------------------

class TestIntervalsWorkout:
    def _session(self, **kwargs):
        base = {
            "type": "intervals",
            "reps": 6,
            "distance_m": 1000,
            "pace_range": "3:50–4:00",
            "recovery_type": "time",
            "recovery_min": 2,
        }
        base.update(kwargs)
        return base

    def test_has_repeat_group(self):
        s = first(self._session())
        assert any(st.get("type") == "RepeatGroupDTO" for st in s["steps"])

    def test_repeat_group_iterations(self):
        s = first(self._session(reps=5))
        rg = next(st for st in s["steps"] if st.get("type") == "RepeatGroupDTO")
        assert rg["numberOfIterations"] == 5

    def test_interval_step_distance(self):
        s = first(self._session(distance_m=400))
        rg = next(st for st in s["steps"] if st.get("type") == "RepeatGroupDTO")
        interval = rg["workoutSteps"][0]
        assert interval["endConditionValue"] == 400.0

    def test_recovery_lap_button_when_distance(self):
        s = first(self._session(recovery_type="distance", recovery_m=200))
        rg = next(st for st in s["steps"] if st.get("type") == "RepeatGroupDTO")
        rec = rg["workoutSteps"][1]
        assert end_condition(rec) == "distance"
        assert rec["endConditionValue"] == 200.0

    def test_recovery_time(self):
        s = first(self._session(recovery_type="time", recovery_min=90 / 60))
        rg = next(st for st in s["steps"] if st.get("type") == "RepeatGroupDTO")
        rec = rg["workoutSteps"][1]
        assert end_condition(rec) == "time"

    def test_name_contains_reps_and_distance(self):
        s = first(self._session(reps=8, distance_m=400))
        assert "8" in s["name"]
        assert "400" in s["name"]

    def test_missing_distance_and_effort_returns_empty(self):
        assert session_to_workout({"type": "intervals", "reps": 5, "pace_range": "4:00–4:10"}) == []


# ---------------------------------------------------------------------------
# append_strides
# ---------------------------------------------------------------------------

class TestAppendStrides:
    def _base_spec(self):
        s = first({
            "type": "easy", "subtype": "jogging",
            "duration_min": 30, "pace_range": "5:40–5:50",
        })
        return s["steps"], s["estimated_secs"]

    def test_adds_two_steps(self):
        steps, est = self._base_spec()
        new_steps, _ = append_strides(steps, {"reps": 3, "distance_m": 100}, len(steps) + 1, est)
        assert len(new_steps) == len(steps) + 2  # transition + repeat group

    def test_transition_is_lap_button(self):
        steps, est = self._base_spec()
        new_steps, _ = append_strides(steps, {"reps": 3}, len(steps) + 1, est)
        transition = new_steps[-2]
        assert end_condition(transition) == "lap.button"

    def test_repeat_group_iterations(self):
        steps, est = self._base_spec()
        new_steps, _ = append_strides(steps, {"reps": 5, "distance_m": 80}, len(steps) + 1, est)
        rg = new_steps[-1]
        assert rg["type"] == "RepeatGroupDTO"
        assert rg["numberOfIterations"] == 5

    def test_stride_distance(self):
        steps, est = self._base_spec()
        new_steps, _ = append_strides(steps, {"reps": 4, "distance_m": 150}, len(steps) + 1, est)
        rg = new_steps[-1]
        stride = rg["workoutSteps"][0]
        assert stride["endConditionValue"] == 150.0

    def test_recovery_is_lap_button(self):
        steps, est = self._base_spec()
        new_steps, _ = append_strides(steps, {"reps": 4}, len(steps) + 1, est)
        rg = new_steps[-1]
        rec = rg["workoutSteps"][1]
        assert end_condition(rec) == "lap.button"

    def test_estimated_secs_increases(self):
        steps, est = self._base_spec()
        _, new_est = append_strides(steps, {"reps": 4}, len(steps) + 1, est)
        assert new_est > est

    def test_defaults(self):
        steps, est = self._base_spec()
        new_steps, _ = append_strides(steps, {}, len(steps) + 1, est)
        rg = new_steps[-1]
        assert rg["numberOfIterations"] == 4           # default reps
        assert rg["workoutSteps"][0]["endConditionValue"] == 100.0  # default 100m


# ---------------------------------------------------------------------------
# strides via session_to_workout (end-to-end)
# ---------------------------------------------------------------------------

class TestSessionWithStrides:
    def test_easy_with_strides_name(self):
        s = first({
            "type": "easy", "subtype": "jogging", "duration_min": 35,
            "pace_range": "5:40–5:50",
            "strides": {"reps": 3, "distance_m": 100, "pace_note": "~3:30"},
        })
        assert "Steig" in s["name"]
        assert "3" in s["name"]
        assert "~3:30" in s["name"]

    def test_intervals_with_strides(self):
        s = first({
            "type": "intervals", "reps": 5, "distance_m": 1000,
            "pace_range": "3:50–4:00", "recovery_min": 2,
            "strides": {"reps": 4, "distance_m": 100},
        })
        # Should have: warmup, repeat-group(intervals), cooldown, transition, repeat-group(strides)
        assert len(s["steps"]) == 5
        assert s["steps"][-1]["type"] == "RepeatGroupDTO"
        assert s["steps"][-1]["numberOfIterations"] == 4

    def test_long_run_with_strides(self):
        s = first({
            "type": "long_run", "distance_km": 20, "pace_range": "5:10–5:20",
            "strides": {"reps": 4},
        })
        assert "Steig" in s["name"]
        rg = s["steps"][-1]
        assert rg["type"] == "RepeatGroupDTO"
        assert rg["numberOfIterations"] == 4


# ---------------------------------------------------------------------------
# build_workout_payload structure
# ---------------------------------------------------------------------------

class TestBuildWorkoutPayload:
    def test_sport_type(self):
        p = build_workout_payload("Test", [], 600)
        assert p["sportType"]["sportTypeKey"] == "running"

    def test_strength_sport_type(self):
        from importlib import import_module
        sport = _mod._strength_sport()
        p = build_workout_payload("Test", [], 600, sport)
        assert p["sportType"]["sportTypeKey"] == "strength_training"
        assert p["workoutSegments"][0]["sportType"]["sportTypeKey"] == "strength_training"

    def test_name(self):
        p = build_workout_payload("My Workout", [], 600)
        assert p["workoutName"] == "My Workout"

    def test_estimated_duration(self):
        p = build_workout_payload("Test", [], 1800)
        assert p["estimatedDurationInSecs"] == 1800

    def test_single_segment(self):
        p = build_workout_payload("Test", [], 600)
        assert len(p["workoutSegments"]) == 1
        assert p["workoutSegments"][0]["segmentOrder"] == 1


# ---------------------------------------------------------------------------
# Strength workouts
# ---------------------------------------------------------------------------

_STRENGTH_EXERCISES = [
    {"name": "Clamshells", "sets": 3, "reps": "15"},
    {"name": "Wadenheben", "sets": 3, "reps": "12"},
]


class TestStrengthWorkout:
    def _session(self, **kwargs):
        base = {
            "type": "strength",
            "focus": "Hüftstabi",
            "duration_min": 20,
            "exercises": _STRENGTH_EXERCISES,
        }
        base.update(kwargs)
        return base

    def test_returns_one_spec(self):
        specs = session_to_workout(self._session())
        assert len(specs) == 1

    def test_sport_is_strength_training(self):
        s = first(self._session())
        assert s["sport"]["sportTypeKey"] == "strength_training"

    def test_name_contains_focus_and_duration(self):
        s = first(self._session())
        assert "Hüftstabi" in s["name"]
        assert "20'" in s["name"]

    def test_one_repeat_group_per_exercise(self):
        s = first(self._session())
        rgs = [st for st in s["steps"] if st.get("type") == "RepeatGroupDTO"]
        assert len(rgs) == len(_STRENGTH_EXERCISES)

    def test_repeat_group_iterations_match_sets(self):
        s = first(self._session())
        rgs = [st for st in s["steps"] if st.get("type") == "RepeatGroupDTO"]
        assert rgs[0]["numberOfIterations"] == 3

    def test_estimated_secs(self):
        s = first(self._session(duration_min=25))
        assert s["estimated_secs"] == 1500

    def test_no_exercises_returns_empty(self):
        assert session_to_workout(self._session(exercises=[])) == []

    def test_optional_returns_empty(self):
        assert session_to_workout(self._session(optional=True)) == []


class TestRestWithStrength:
    def _session(self, **kwargs):
        base = {
            "type": "rest",
            "strength": {
                "focus": "Rumpf",
                "duration_min": 20,
                "exercises": _STRENGTH_EXERCISES,
            },
        }
        base.update(kwargs)
        return base

    def test_returns_one_strength_spec(self):
        specs = session_to_workout(self._session())
        assert len(specs) == 1

    def test_spec_is_strength_training(self):
        s = session_to_workout(self._session())[0]
        assert s["sport"]["sportTypeKey"] == "strength_training"

    def test_pure_rest_returns_empty(self):
        assert session_to_workout({"type": "rest"}) == []


class TestEasyWithStrength:
    def _session(self, **kwargs):
        base = {
            "type": "easy",
            "subtype": "jogging",
            "duration_min": 40,
            "pace_range": "6:30–7:00",
            "strength": {
                "focus": "Hüftstabi",
                "duration_min": 20,
                "exercises": _STRENGTH_EXERCISES,
            },
        }
        base.update(kwargs)
        return base

    def test_returns_two_specs(self):
        specs = session_to_workout(self._session())
        assert len(specs) == 2

    def test_first_spec_is_running(self):
        specs = session_to_workout(self._session())
        assert "sport" not in specs[0] or specs[0].get("sport", {}).get("sportTypeKey") == "running"

    def test_second_spec_is_strength(self):
        specs = session_to_workout(self._session())
        assert specs[1]["sport"]["sportTypeKey"] == "strength_training"


class TestExerciseStep:
    def test_known_exercise_maps_category(self):
        step = exercise_step(1, "Clamshells", 3, "15")
        assert step["exerciseCategory"]["categoryKey"] == "HIP_STABILITY"

    def test_unknown_exercise_falls_back_to_other(self):
        step = exercise_step(1, "Unbekannte Übung", 3, "10")
        assert step["exerciseCategory"]["categoryKey"] == "OTHER"

    def test_numeric_reps_sets_end_condition_reps(self):
        step = exercise_step(1, "Plank", 3, "30")
        assert step["endCondition"]["conditionTypeKey"] == "reps"
        assert step["endConditionValue"] == 30.0

    def test_non_numeric_reps_sets_lap_button(self):
        step = exercise_step(1, "Plank", 3, "30 s")
        assert step["endCondition"]["conditionTypeKey"] == "lap.button"

    def test_sets_stored(self):
        step = exercise_step(1, "Wadenheben", 4, "12")
        assert step["numberOfSets"] == 4


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", *sys.argv[1:]]))
