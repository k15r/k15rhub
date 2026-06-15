#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "garminconnect==0.3.6",
#   "curl_cffi",
# ]
# ///
"""
fetch-fit-garmin.py — download FIT files and daily health summaries from Garmin Connect.

Single-activity mode (used by fetch-fit.sh):
    fetch-fit-garmin.py <user> [activity-id | YYYY-MM-DD]

Batch mode (used by fetch-recent-activities.sh):
    fetch-fit-garmin.py <user> --batch [<count>]

    Without a prior sync: fetches <count> most recent activities + health summaries (default 5).
    With a prior sync:    fetches all activities + health summaries since last sync (up to <count>).
    After each batch the date of the newest activity is saved to
    ~/.garminconnect/<user>/last_sync for the next incremental run.

Health summary mode:
    fetch-fit-garmin.py <user> --health [YYYY-MM-DD]

    Fetches daily health summary (RHR, HRV, sleep, body battery, stress, steps, SpO2)
    for the given date (default: yesterday). Writes a markdown entry to the Lauftagebuch.

Single-activity output (stdout):
    ACTIVITY_ID=<id>\tDATE=<YYYY-MM-DD>\tTITLE=<title>\tDIST_KM=<km>\tDUR_SEC=<s>\tDEST=<path>
    ---FIT-ANALYZER---
    <fit-analyzer output>

Batch output: ---ACTIVITY--- blocks followed by ---HEALTH--- blocks per date.

Reads ~/.marathon-coach/<user>/config.yaml for output_dir.
Reads/writes Garmin tokens to ~/.garminconnect/<user>/garmin_tokens.json.
Set GARMINTOKENS env var to override the token directory.
Exits non-zero on any error.
"""

import os
import sys
import re
import zipfile
import tempfile
import shutil
import subprocess
from datetime import date as date_cls, timedelta
from pathlib import Path


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def read_yaml_field(path: str, field: str) -> str:
    with open(path) as f:
        for line in f:
            if line.startswith(f"{field}:"):
                return line.split(":", 1)[1].strip().strip('"').strip("'")
    return ""


def type_hint_from_title(title: str) -> str:
    t = title.lower()
    if re.search(r"jogging|regeneration|regen", t):             return "Jogging"
    if re.search(r"dauerlauf\b|(?<!\w)dl(?!\w)", t):            return "Dauerlauf"
    if re.search(r"crescendo", t):                               return "Crescendo"
    if re.search(r"intervall\b|(?<!\w)it(?!\w)", t):            return "Intervall"
    if re.search(r"tempo\b|(?<!\w)tdl(?!\w)", t):               return "Tempo"
    if re.search(r"trail", t):                                   return "Trail"
    if re.search(r"wettkampf|rennen|(?<!\w)wk(?!\w)|race", t):  return "Rennen"
    if re.search(r"laufen|lauf|running|run", t):                 return "Laufen"
    if re.search(r"kraft|strength|gym|training", t):             return "Kraft"
    if re.search(r"rad|cycling|bike|fahren|velo", t):            return "Radfahren"
    if re.search(r"schwimm|swim", t):                            return "Schwimmen"
    if re.search(r"wandern|hike|hiking", t):                     return "Wandern"
    if re.search(r"yoga", t):                                    return "Yoga"
    slug = re.sub(r"[^a-z0-9_-]", "", t.replace(" ", "_"))
    return slug or "Aktivitaet"


def token_artifact(tokenstore: str) -> str:
    p = Path(tokenstore)
    if p.suffix == ".json":
        return str(p)
    return str(p / "garmin_tokens.json")


def tokens_present(tokenstore: str) -> bool:
    return os.path.exists(token_artifact(tokenstore))


def last_sync_path(tokenstore: str) -> str:
    return os.path.join(tokenstore, "last_sync")


def read_last_sync(tokenstore: str) -> str | None:
    path = last_sync_path(tokenstore)
    if os.path.exists(path):
        return Path(path).read_text().strip() or None
    return None


def write_last_sync(tokenstore: str, date_str: str) -> None:
    Path(last_sync_path(tokenstore)).write_text(date_str)


def init_garmin(tokenstore: str):
    from garminconnect import (
        Garmin,
        GarminConnectAuthenticationError,
        GarminConnectConnectionError,
        GarminConnectTooManyRequestsError,
    )

    have_tokens = tokens_present(tokenstore)

    def prompt_mfa() -> str:
        if not sys.stdin.isatty():
            die(
                "Garmin requires an MFA code but no interactive terminal is available.\n"
                "Run once interactively to save tokens, then subsequent runs will be silent."
            )
        return input("Garmin MFA code: ").strip()

    os.makedirs(tokenstore, exist_ok=True)

    email = None
    password = None
    if not have_tokens:
        if not sys.stdin.isatty():
            die(
                "No Garmin token cache found and no interactive terminal available.\n"
                "Run once interactively to create the token cache:\n"
                "  uv run --script fetch-fit-garmin.py <user>\n"
                f"Tokens will be saved to: {token_artifact(tokenstore)}"
            )
        from getpass import getpass
        print("Garmin Connect — first-time login (credentials are not stored)", file=sys.stderr)
        email = input("Email: ").strip()
        password = getpass("Password: ")

    try:
        garmin = Garmin(email=email, password=password, prompt_mfa=prompt_mfa)
        garmin.login(tokenstore)
    except (GarminConnectAuthenticationError, GarminConnectConnectionError) as e:
        die(
            f"Garmin login failed: {e}\n"
            "Ensure your credentials are correct and you have a working internet connection."
        )
    except GarminConnectTooManyRequestsError as e:
        die(f"Garmin rate limit: {e}")

    artifact = token_artifact(tokenstore)
    if not os.path.exists(artifact):
        print(
            f"WARNING: Garmin tokens were not saved to {artifact}.\n"
            "Check directory permissions. Future runs will require re-authentication.",
            file=sys.stderr,
        )

    return garmin


def resolve_activity(garmin, arg: str) -> dict:
    if not arg:
        activity = garmin.get_last_activity()
        if not activity:
            die("No recent Garmin activity found")
        return activity

    if re.match(r"^\d+$", arg):
        activities = garmin.get_activities(0, 50)
        for a in (activities or []):
            if str(a.get("activityId")) == arg:
                return a
        die(f"Activity ID {arg} not found in last 50 activities")

    if re.match(r"^\d{4}-\d{2}-\d{2}$", arg):
        results = garmin.get_activities_fordate(arg)
        if isinstance(results, dict):
            results = results.get("activityList", [])
        if not results:
            die(f"No Garmin activity found for date {arg}")
        return results[0]

    die(f"Unrecognised argument: {arg!r} — expected blank, numeric ID, or YYYY-MM-DD")


def download_fit(garmin, activity_id: str, dest: str) -> None:
    from garminconnect import Garmin as _Garmin
    data = garmin.download_activity(activity_id, dl_fmt=_Garmin.ActivityDownloadFormat.ORIGINAL)
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = os.path.join(tmp, "activity.zip")
        with open(zip_path, "wb") as f:
            f.write(data)
        with zipfile.ZipFile(zip_path) as zf:
            fit_names = [n for n in zf.namelist() if n.lower().endswith(".fit")]
            if not fit_names:
                die(f"No FIT file found in Garmin zip for activity {activity_id}")
            zf.extract(fit_names[0], tmp)
            extracted = os.path.join(tmp, fit_names[0])
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(extracted, dest)


def emit_activity(activity: dict, garmin, fit_dir: Path) -> str | None:
    """Download FIT, run fit-analyzer, emit structured output. Returns date on success."""
    activity_id = str(activity.get("activityId", ""))
    title = activity.get("activityName") or activity.get("activityDescription") or ""
    raw_date = (activity.get("startTimeLocal") or activity.get("beginTimestamp") or "")[:10]
    distance_km = round((activity.get("distance") or 0) / 1000, 3)
    duration_sec = int(activity.get("duration") or activity.get("elapsedDuration") or 0)

    if not activity_id:
        die("Could not determine activity ID from Garmin response")
    if not raw_date:
        die("Could not determine activity date from Garmin response")

    type_hint = type_hint_from_title(title)
    dest = str(fit_dir / f"{raw_date} {type_hint}.fit")

    print(f">>> Downloading activity {activity_id} ({raw_date}, ~{distance_km} km) …", file=sys.stderr)
    download_fit(garmin, activity_id, dest)

    size = os.path.getsize(dest)
    if size <= 1000:
        os.remove(dest)
        print(f"WARN: activity {activity_id} too small ({size} bytes), skipping", file=sys.stderr)
        return None

    print(f">>> Saved to: {dest}", file=sys.stderr)
    print(f">>> Running fit-analyzer …", file=sys.stderr)

    print(f"ACTIVITY_ID={activity_id}\tDATE={raw_date}\tTITLE={title}\tDIST_KM={distance_km}\tDUR_SEC={duration_sec}\tDEST={dest}")
    print("---FIT-ANALYZER---")
    sys.stdout.flush()
    subprocess.run(["fit-analyzer", dest], check=False)
    return raw_date


def _safe(fn, *args, **kwargs):
    """Call a Garmin API method, return None on any error (metric not available on device)."""
    try:
        return fn(*args, **kwargs)
    except Exception:
        return None


def fetch_health_summary(garmin, cdate: str, output_dir: Path) -> None:
    """Fetch daily health metrics and write a markdown entry to the Lauftagebuch."""
    print(f">>> Fetching health summary for {cdate} …", file=sys.stderr)

    stats       = _safe(garmin.get_stats, cdate) or {}
    hrv         = _safe(garmin.get_hrv_data, cdate) or {}
    sleep       = _safe(garmin.get_sleep_data, cdate) or {}
    body_bat    = _safe(garmin.get_body_battery, cdate, cdate) or []
    rhr         = _safe(garmin.get_rhr_day, cdate) or {}
    spo2        = _safe(garmin.get_spo2_data, cdate) or {}

    # --- Extract values (all optional — not every device supports all metrics) ---

    # RHR
    rhr_val = (rhr.get("allMetrics", {}) or {}).get("metricsMap", {}).get("WELLNESS_RESTING_HEART_RATE", [{}])
    rhr_bpm = rhr_val[0].get("value") if rhr_val else None

    # HRV (last night's average)
    hrv_summary = hrv.get("hrvSummary") or {}
    hrv_avg = hrv_summary.get("lastNight")
    hrv_status = hrv_summary.get("status")  # e.g. "BALANCED", "UNBALANCED"

    # Sleep
    sleep_data = sleep.get("dailySleepDTO") or {}
    sleep_sec = sleep_data.get("sleepTimeSeconds")
    sleep_score = sleep_data.get("sleepScores", {}).get("overall", {}).get("value") if sleep_data.get("sleepScores") else None
    sleep_h = f"{sleep_sec // 3600}h {(sleep_sec % 3600) // 60}min" if sleep_sec else None

    # Body battery
    bb_entry = body_bat[0] if body_bat else {}
    bb_max = bb_entry.get("charged")
    bb_min = bb_entry.get("drained")

    # Stress
    stress_avg = stats.get("averageStressLevel")
    stress_max = stats.get("maxStressLevel")

    # Steps
    steps = stats.get("totalSteps")

    # SpO2
    spo2_avg = (spo2.get("averageSpO2") if isinstance(spo2, dict) else None)

    # Calories / active time from stats
    calories_active = stats.get("activeKilocalories")
    active_min = stats.get("highlyActiveSeconds")
    active_min_fmt = f"{active_min // 60} min" if active_min else None

    # --- Build markdown entry ---
    ym = cdate[:7]  # YYYY-MM
    entry_dir = output_dir / "Lauftagebuch" / ym
    entry_dir.mkdir(parents=True, exist_ok=True)
    entry_path = entry_dir / f"{cdate} Gesundheit.md"

    def row(label: str, value, unit: str = "") -> str:
        if value is None:
            return ""
        return f"| {label} | {value}{(' ' + unit) if unit else ''} |\n"

    lines = [
        "---\n",
        f"tags: [gesundheit, garmin, lauftagebuch]\n",
        f"date: {cdate}\n",
        f"sport: health\n",
        f"typ: gesundheit\n",
        f"isowoche: KW{date_cls.fromisoformat(cdate).isocalendar()[1]:02d}\n",
        "---\n",
        "\n",
        f"# Gesundheit – {cdate[8:10]}.{cdate[5:7]}.{cdate[:4]}\n",
        "\n",
        "## Erholung\n",
        "\n",
        "| | |\n",
        "| --- | --- |\n",
    ]

    for lbl, val, unit in [
        ("HF Ruhe",       rhr_bpm,    "bpm"),
        ("HRV letzte Nacht", hrv_avg, "ms"),
        ("HRV Status",    hrv_status, ""),
        ("Schlaf",        sleep_h,    ""),
        ("Schlaf Score",  sleep_score, ""),
        ("Body Battery",  f"{bb_min}–{bb_max}" if bb_min is not None and bb_max is not None else None, ""),
    ]:
        r = row(lbl, val, unit)
        if r:
            lines.append(r)

    lines += [
        "\n",
        "## Aktivität & Belastung\n",
        "\n",
        "| | |\n",
        "| --- | --- |\n",
    ]

    for lbl, val, unit in [
        ("Schritte",      steps,       ""),
        ("Aktive Zeit",   active_min_fmt, ""),
        ("Aktive kcal",   calories_active, "kcal"),
        ("Stress Ø",      stress_avg,  ""),
        ("Stress max",    stress_max,  ""),
        ("SpO2 Ø",        spo2_avg,    "%"),
    ]:
        r = row(lbl, val, unit)
        if r:
            lines.append(r)

    lines.append("\n")

    entry_path.write_text("".join(lines))
    print(f">>> Health summary written to {entry_path}", file=sys.stderr)

    # Emit structured marker for the skill to parse
    print(f"---HEALTH---")
    print(f"DATE={cdate}\tDEST={entry_path}")
    sys.stdout.flush()


def load_config(user: str) -> tuple[Path, Path]:
    """Returns (output_dir, fit_dir)."""
    config = Path.home() / ".marathon-coach" / user / "config.yaml"
    if not config.exists():
        die(f"Config not found: {config}")
    output_dir_str = read_yaml_field(str(config), "output_dir")
    if not output_dir_str:
        die(f"output_dir not set in {config}")
    output_dir = Path(output_dir_str)
    return output_dir, output_dir / "Lauftagebuch" / "fit"


def main() -> None:
    if len(sys.argv) < 2:
        die("Usage: fetch-fit-garmin.py <user> [activity-id|YYYY-MM-DD]\n"
            "       fetch-fit-garmin.py <user> --batch [<count>]\n"
            "       fetch-fit-garmin.py <user> --health [YYYY-MM-DD]")

    user = sys.argv[1]
    tokenstore = str(Path(os.getenv("GARMINTOKENS", f"~/.garminconnect/{user}")).expanduser())
    output_dir, fit_dir = load_config(user)

    # --health mode: fetch daily health summary for one date
    if len(sys.argv) >= 3 and sys.argv[2] == "--health":
        cdate = sys.argv[3] if len(sys.argv) >= 4 else (date_cls.today() - timedelta(days=1)).isoformat()
        garmin = init_garmin(tokenstore)
        fetch_health_summary(garmin, cdate, output_dir)
        return

    # --batch mode: incremental activity + health sync
    if len(sys.argv) >= 3 and sys.argv[2] == "--batch":
        count = int(sys.argv[3]) if len(sys.argv) >= 4 else 5
        garmin = init_garmin(tokenstore)

        last_sync = read_last_sync(tokenstore)
        if last_sync:
            print(f">>> Incremental sync since {last_sync} …", file=sys.stderr)
            today = date_cls.today().isoformat()
            activities = garmin.get_activities_by_date(last_sync, today)
            if isinstance(activities, dict):
                activities = activities.get("activityList", [])
            # get_activities_by_date returns oldest-first; reverse for newest-first
            activities = list(reversed(activities or []))[:count]
        else:
            print(f">>> First sync — fetching {count} most recent activities …", file=sys.stderr)
            activities = garmin.get_activities(0, count)
            if isinstance(activities, dict):
                activities = activities.get("activityList", [])
            activities = (activities or [])[:count]

        newest_date = None
        activity_dates = set()
        for activity in activities:
            print("---ACTIVITY---")
            sys.stdout.flush()
            date_str = emit_activity(activity, garmin, fit_dir)
            if date_str:
                activity_dates.add(date_str)
                if newest_date is None or date_str > newest_date:
                    newest_date = date_str

        # Fetch health summaries for each unique activity date
        for cdate in sorted(activity_dates):
            fetch_health_summary(garmin, cdate, output_dir)

        if newest_date:
            write_last_sync(tokenstore, newest_date)
            print(f">>> Sync complete. Last sync updated to {newest_date}.", file=sys.stderr)
        return

    # Single-activity mode
    arg = sys.argv[2] if len(sys.argv) > 2 else ""
    garmin = init_garmin(tokenstore)
    activity = resolve_activity(garmin, arg)
    emit_activity(activity, garmin, fit_dir)


if __name__ == "__main__":
    main()
