#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "garminconnect==0.3.6",
#   "curl_cffi",
# ]
# ///
"""
clean-garmin-calendar.py — remove all scheduled workouts from today onward.

Usage:
    clean-garmin-calendar.py <user>
    clean-garmin-calendar.py <user> --library   # also delete workout definitions

Reads Garmin tokens from ~/.garminconnect/<user>/garmin_tokens.json.
"""

import os
import sys
from datetime import date as date_cls
from pathlib import Path


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def token_artifact(tokenstore: str) -> str:
    p = Path(tokenstore)
    return str(p) if p.suffix == ".json" else str(p / "garmin_tokens.json")


def init_garmin(tokenstore: str):
    from garminconnect import (
        Garmin,
        GarminConnectAuthenticationError,
        GarminConnectConnectionError,
        GarminConnectTooManyRequestsError,
    )
    if not os.path.exists(token_artifact(tokenstore)):
        die("No Garmin token cache found. Run analyze-activity once interactively first.")

    def prompt_mfa() -> str:
        if not sys.stdin.isatty():
            die("Garmin MFA required but no TTY.")
        return input("Garmin MFA code: ").strip()

    try:
        garmin = Garmin(prompt_mfa=prompt_mfa)
        garmin.login(tokenstore)
        return garmin
    except (GarminConnectAuthenticationError, GarminConnectConnectionError) as e:
        die(f"Garmin login failed: {e}")
    except GarminConnectTooManyRequestsError as e:
        die(f"Garmin rate limit: {e}")


def get_future_scheduled(garmin) -> list[dict]:
    """Fetch scheduled workouts for the current month + next 3 months."""
    today = date_cls.today()
    entries = []
    seen_ids = set()

    for month_offset in range(4):
        year = today.year + (today.month - 1 + month_offset) // 12
        month = (today.month - 1 + month_offset) % 12 + 1
        result = garmin.get_scheduled_workouts(year, month)
        # API returns a dict or list depending on version
        if isinstance(result, dict):
            items = result.get("calendarItems", result.get("scheduledWorkouts", []))
        else:
            items = result or []

        for item in items:
            # Scheduled workout entries have a date and a scheduledWorkoutId
            sched_id = item.get("scheduledWorkoutId") or item.get("id")
            if not sched_id or sched_id in seen_ids:
                continue
            item_date = (item.get("date") or item.get("startDate") or "")[:10]
            if item_date >= today.isoformat():
                entries.append({
                    "scheduledId": sched_id,
                    "workoutId": item.get("workoutId") or (item.get("workout") or {}).get("workoutId"),
                    "date": item_date,
                    "name": item.get("workoutName") or (item.get("workout") or {}).get("workoutName") or "",
                })
                seen_ids.add(sched_id)

    return entries


def main() -> None:
    if len(sys.argv) < 2:
        die("Usage: clean-garmin-calendar.py <user> [--library]")

    user = sys.argv[1]
    delete_library = "--library" in sys.argv[2:]
    tokenstore = str(Path(os.getenv("GARMINTOKENS", f"~/.garminconnect/{user}")).expanduser())

    garmin = init_garmin(tokenstore)

    print(">>> Fetching scheduled workouts …", file=sys.stderr)
    entries = get_future_scheduled(garmin)

    if not entries:
        print("No future scheduled workouts found.")
        return

    print(f"Found {len(entries)} scheduled workout(s) from today onward.", file=sys.stderr)

    unscheduled = 0
    deleted_ids = set()

    for entry in entries:
        sched_id = entry["scheduledId"]
        workout_id = entry["workoutId"]
        date_str = entry["date"]
        name = entry["name"]

        try:
            garmin.unschedule_workout(sched_id)
            print(f"UNSCHEDULED\t{date_str}\t{name}\tscheduledId={sched_id}")
            unscheduled += 1
        except Exception as e:
            print(f"WARN: failed to unschedule {sched_id} ({date_str}): {e}", file=sys.stderr)

        if delete_library and workout_id and workout_id not in deleted_ids:
            try:
                garmin.delete_workout(workout_id)
                print(f"DELETED_WORKOUT\t{workout_id}\t{name}")
                deleted_ids.add(workout_id)
            except Exception as e:
                print(f"WARN: failed to delete workout {workout_id}: {e}", file=sys.stderr)

    print(f">>> Done. Unscheduled: {unscheduled}. "
          f"Deleted from library: {len(deleted_ids)}.", file=sys.stderr)

    # Clear our own tracking file so push-workouts starts fresh
    tracking = Path(tokenstore) / "scheduled_workouts.json"
    if tracking.exists():
        tracking.write_text("{}")
        print(">>> Cleared scheduled_workouts.json.", file=sys.stderr)


if __name__ == "__main__":
    main()
