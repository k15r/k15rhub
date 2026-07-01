#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""
fetch-weather.py — fetch ambient weather data from Open-Meteo for GPS track points.

Usage:
    fetch-weather.py <start_time_utc> <lat1,lon1,elapsed1> [<lat2,lon2,elapsed2> ...]

    start_time_utc: ISO 8601 UTC timestamp, e.g. 2026-07-01T15:23:38Z
    Each point: lat,lon,elapsed_sec  (as output by fit-analyzer gps_track)

Output (stdout):
    ---WEATHER---
    <yaml block>

The YAML block contains:
    weather:
      source: open-meteo
      samples:
        - elapsed_sec: 0
          time: "15:23"
          temp_c: 28.4
          apparent_temp_c: 31.2
          humidity_pct: 45
          windspeed_kmh: 8.3
      avg_temp_c: 28.8
      max_temp_c: 29.5
      avg_apparent_temp_c: 31.8
      max_apparent_temp_c: 33.1
      avg_humidity_pct: 44
"""

import sys
import json
import math
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen
from urllib.parse import urlencode
from urllib.error import URLError


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def parse_utc(s: str) -> datetime:
    s = s.rstrip("Z")
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def fetch_open_meteo(lat: float, lon: float, hours: list[str], date: str) -> dict:
    """Fetch hourly weather for a location on a given date."""
    params = {
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "hourly": "temperature_2m,apparent_temperature,relative_humidity_2m,windspeed_10m",
        "start_date": date,
        "end_date": date,
        "timezone": "UTC",
        "timeformat": "iso8601",
    }
    url = "https://api.open-meteo.com/v1/forecast?" + urlencode(params)
    try:
        with urlopen(url, timeout=10) as resp:
            return json.loads(resp.read())
    except URLError as e:
        die(f"Open-Meteo request failed: {e}")


def interpolate(hourly: dict, ts: datetime) -> dict | None:
    """Linear interpolation between the two surrounding hourly samples."""
    times = hourly.get("time", [])
    if not times:
        return None

    parsed = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in times]

    # find surrounding indices
    idx = None
    for i in range(len(parsed) - 1):
        if parsed[i] <= ts <= parsed[i + 1]:
            idx = i
            break
    if idx is None:
        # clamp to nearest
        if ts <= parsed[0]:
            idx_use = 0
        else:
            idx_use = len(parsed) - 1
        return {
            "temp_c": hourly["temperature_2m"][idx_use],
            "apparent_temp_c": hourly["apparent_temperature"][idx_use],
            "humidity_pct": hourly["relative_humidity_2m"][idx_use],
            "windspeed_kmh": hourly["windspeed_10m"][idx_use],
        }

    t0, t1 = parsed[idx], parsed[idx + 1]
    frac = (ts - t0).total_seconds() / (t1 - t0).total_seconds()

    def lerp(key):
        v0 = hourly[key][idx]
        v1 = hourly[key][idx + 1]
        if v0 is None or v1 is None:
            return v0 if v1 is None else v1
        return round(v0 + frac * (v1 - v0), 1)

    return {
        "temp_c": lerp("temperature_2m"),
        "apparent_temp_c": lerp("apparent_temperature"),
        "humidity_pct": round(lerp("relative_humidity_2m")),
        "windspeed_kmh": lerp("windspeed_10m"),
    }


def main() -> None:
    if len(sys.argv) < 3:
        die("Usage: fetch-weather.py <start_time_utc> <lat,lon,elapsed> [...]")

    start_utc = parse_utc(sys.argv[1])
    date_str = start_utc.date().isoformat()

    points = []
    for arg in sys.argv[2:]:
        parts = arg.split(",")
        if len(parts) != 3:
            die(f"Invalid point format (expected lat,lon,elapsed_sec): {arg!r}")
        lat, lon, elapsed = float(parts[0]), float(parts[1]), int(parts[2])
        ts = start_utc + timedelta(seconds=elapsed)
        points.append({"lat": lat, "lon": lon, "elapsed": elapsed, "ts": ts})

    if not points:
        die("No GPS points provided")

    # Use centroid for the single Open-Meteo request (activity radius is small)
    avg_lat = sum(p["lat"] for p in points) / len(points)
    avg_lon = sum(p["lon"] for p in points) / len(points)

    print(f">>> Fetching Open-Meteo weather ({date_str}, {avg_lat:.4f},{avg_lon:.4f}) …", file=sys.stderr)
    data = fetch_open_meteo(avg_lat, avg_lon, [], date_str)
    hourly = data.get("hourly", {})

    samples = []
    for p in points:
        vals = interpolate(hourly, p["ts"])
        if vals is None:
            continue
        sample = {
            "elapsed_sec": p["elapsed"],
            "time": p["ts"].strftime("%H:%M"),
            **vals,
        }
        samples.append(sample)

    if not samples:
        die("No weather samples could be computed")

    # Aggregates
    temps = [s["temp_c"] for s in samples if s["temp_c"] is not None]
    app_temps = [s["apparent_temp_c"] for s in samples if s["apparent_temp_c"] is not None]
    humids = [s["humidity_pct"] for s in samples if s["humidity_pct"] is not None]

    avg_temp = round(sum(temps) / len(temps), 1) if temps else None
    max_temp = round(max(temps), 1) if temps else None
    avg_app = round(sum(app_temps) / len(app_temps), 1) if app_temps else None
    max_app = round(max(app_temps), 1) if app_temps else None
    avg_hum = round(sum(humids) / len(humids)) if humids else None

    # Emit YAML manually (no dependency)
    print("---WEATHER---")
    print("weather:")
    print("  source: open-meteo")
    print("  samples:")
    for s in samples:
        print(f"    - elapsed_sec: {s['elapsed_sec']}")
        print(f"      time: \"{s['time']}\"")
        if s.get("temp_c") is not None:
            print(f"      temp_c: {s['temp_c']}")
        if s.get("apparent_temp_c") is not None:
            print(f"      apparent_temp_c: {s['apparent_temp_c']}")
        if s.get("humidity_pct") is not None:
            print(f"      humidity_pct: {s['humidity_pct']}")
        if s.get("windspeed_kmh") is not None:
            print(f"      windspeed_kmh: {s['windspeed_kmh']}")
    if avg_temp is not None:
        print(f"  avg_temp_c: {avg_temp}")
    if max_temp is not None:
        print(f"  max_temp_c: {max_temp}")
    if avg_app is not None:
        print(f"  avg_apparent_temp_c: {avg_app}")
    if max_app is not None:
        print(f"  max_apparent_temp_c: {max_app}")
    if avg_hum is not None:
        print(f"  avg_humidity_pct: {avg_hum}")


if __name__ == "__main__":
    main()
