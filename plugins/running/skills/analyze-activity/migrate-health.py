#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "pyyaml",
# ]
# ///
"""
migrate-health.py — migrate health entries from Lauftagebuch/ to Gesundheitstagebuch/.

Moves all YYYY-MM-DD Gesundheit.md and YYYY-MM-DD Gesundheit.yaml files from
  <output_dir>/Lauftagebuch/YYYY-MM/
to
  <output_dir>/Gesundheitstagebuch/YYYY-MM/

Also migrates the `health` list from lauftagebuch.yaml into a new
gesundheitstagebuch.yaml (as `entries`), then removes the `health` key
from lauftagebuch.yaml.

Usage:
    migrate-health.py <user> [--dry-run]

Reads ~/.marathon-coach/<user>/config.yaml for output_dir.
Safe to re-run: skips files that already exist in the destination.
"""

import sys
import shutil
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


def main() -> None:
    import yaml

    if len(sys.argv) < 2:
        die("Usage: migrate-health.py <user> [--dry-run]")

    user = sys.argv[1]
    dry_run = "--dry-run" in sys.argv

    if dry_run:
        print("DRY RUN — no files will be moved or modified")

    config = Path.home() / ".marathon-coach" / user / "config.yaml"
    if not config.exists():
        die(f"Config not found: {config}")

    output_dir_str = read_yaml_field(str(config), "output_dir")
    if not output_dir_str:
        die(f"output_dir not set in {config}")

    output_dir = Path(output_dir_str)
    lauf_dir = output_dir / "Lauftagebuch"
    gesund_dir = output_dir / "Gesundheitstagebuch"

    if not lauf_dir.exists():
        die(f"Lauftagebuch directory not found: {lauf_dir}")

    # --- 1. Move Gesundheit files ---
    moved = 0
    skipped = 0
    for src in sorted(lauf_dir.glob("????-??/*Gesundheit*")):
        ym = src.parent.name
        dest_dir = gesund_dir / ym
        dest = dest_dir / src.name

        if dest.exists():
            print(f"  SKIP (exists): {dest.relative_to(output_dir)}")
            skipped += 1
            continue

        print(f"  MOVE: {src.relative_to(output_dir)}  →  {dest.relative_to(output_dir)}")
        if not dry_run:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
        moved += 1

    print(f"\nFiles: {moved} moved, {skipped} skipped")

    # --- 2. Migrate lauftagebuch.yaml health entries ---
    ltb_yaml = lauf_dir / "lauftagebuch.yaml"
    if not ltb_yaml.exists():
        print("\nlauftagebuch.yaml not found — skipping YAML migration")
        return

    with open(ltb_yaml) as f:
        ltb_data = yaml.safe_load(f) or {}

    health_entries = ltb_data.get("health", [])
    if not health_entries:
        print("\nNo health entries in lauftagebuch.yaml — nothing to migrate")
    else:
        # Update file paths: strip "YYYY-MM/" prefix since gesundheitstagebuch.yaml
        # paths are relative to Gesundheitstagebuch/, same as before
        gtb_yaml = gesund_dir / "gesundheitstagebuch.yaml"

        if gtb_yaml.exists():
            with open(gtb_yaml) as f:
                gtb_data = yaml.safe_load(f) or {}
        else:
            gtb_data = {}

        gtb_data.setdefault("entries", [])

        existing_dates = {e.get("date") for e in gtb_data["entries"]}
        new_entries = [e for e in health_entries if e.get("date") not in existing_dates]

        print(f"\nHealth entries: {len(new_entries)} to migrate, "
              f"{len(health_entries) - len(new_entries)} already present")

        if new_entries:
            # Merge: existing entries first (newer), append migrated ones; then sort desc
            gtb_data["entries"] = sorted(
                gtb_data["entries"] + new_entries,
                key=lambda e: e.get("date", ""),
                reverse=True,
            )
            print(f"  → {gtb_yaml.relative_to(output_dir)}")
            if not dry_run:
                gesund_dir.mkdir(parents=True, exist_ok=True)
                with open(gtb_yaml, "w") as f:
                    yaml.dump(gtb_data, f, allow_unicode=True, sort_keys=False,
                              default_flow_style=False)

        # Remove health key from lauftagebuch.yaml
        if "health" in ltb_data:
            print(f"  → removing health list from {ltb_yaml.relative_to(output_dir)}")
            if not dry_run:
                del ltb_data["health"]
                with open(ltb_yaml, "w") as f:
                    yaml.dump(ltb_data, f, allow_unicode=True, sort_keys=False,
                              default_flow_style=False)

    if dry_run:
        print("\nDRY RUN complete — rerun without --dry-run to apply")
    else:
        print("\nMigration complete.")


if __name__ == "__main__":
    main()
