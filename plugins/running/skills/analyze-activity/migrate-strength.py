#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "pyyaml",
# ]
# ///
"""
migrate-strength.py — backfill strength sub-blocks into week YAML files.

Reads each W*.yaml in a plan directory, finds sessions whose date has a
non-empty Kraft/Stabi cell in the sibling .md file, and writes a minimal
`strength` sub-block into the YAML.

The parser is best-effort: it extracts duration and a focus label.
Individual exercises are NOT parsed from the markdown — the block will
have an empty `exercises` list with a TODO comment. Fill them in manually
or let the coach rewrite the week.

Usage:
    uv run --script migrate-strength.py <plan-dir>
    uv run --script migrate-strength.py <plan-dir> --dry-run
"""

import re
import sys
import yaml
from pathlib import Path


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def find_week_files(plan_dir: Path) -> list[Path]:
    files = sorted(plan_dir.glob("W[0-9]* – *.yaml"))
    if not files:
        # also try ASCII hyphen variant
        files = sorted(plan_dir.glob("W[0-9]* - *.yaml"))
    return files


def parse_kraft_table(md_path: Path) -> dict[str, str]:
    """Return {date_string: kraft_cell_text} for all rows with a non-empty Kraft/Stabi cell.

    The markdown table looks like:
      | Tag | Datum  | Session            | Kraft/Stabi | Log |
    We identify the Kraft/Stabi column by header position, then match each
    data row. The Datum cell is matched against session dates in the YAML.
    """
    if not md_path.exists():
        return {}

    text = md_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    # Find the header row that contains "Kraft" or "Kraft/Stabi"
    header_idx = None
    kraft_col = None
    datum_col = None
    for i, line in enumerate(lines):
        if re.search(r"\|\s*Kraft", line, re.IGNORECASE):
            header_idx = i
            cells = [c.strip() for c in line.split("|")]
            for j, cell in enumerate(cells):
                if re.search(r"Kraft", cell, re.IGNORECASE):
                    kraft_col = j
                if re.search(r"Datum", cell, re.IGNORECASE):
                    datum_col = j
            break

    if header_idx is None or kraft_col is None:
        return {}

    result: dict[str, str] = {}
    # rows start after header + separator
    for line in lines[header_idx + 2:]:
        if not line.strip().startswith("|"):
            break
        cells = [c.strip() for c in line.split("|")]
        if len(cells) <= max(kraft_col, datum_col or 0):
            continue
        kraft_text = cells[kraft_col] if kraft_col < len(cells) else ""
        if not kraft_text or kraft_text == "–" or kraft_text == "-":
            continue

        # The Datum cell is typically DD.MM. — we can't directly map it to a date
        # without knowing the year. We'll use it as a key and match against YAML sessions.
        datum_text = cells[datum_col].strip() if datum_col and datum_col < len(cells) else ""
        if datum_text:
            result[datum_text] = kraft_text

    return result


def parse_duration(text: str) -> int:
    """Extract the first integer or float followed by ' (min) from text, default 20."""
    m = re.search(r"(\d+)\s*[`']", text)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*min", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return 20


def parse_focus(text: str) -> str:
    """Extract a short focus label — everything up to the first colon or digit."""
    text = text.strip()
    m = re.match(r"([A-ZÄÖÜa-zäöüß /+&-]+?)(?:\s*\d|\s*:|\s*$)", text)
    if m:
        return m.group(1).strip(" +-")
    return text[:30].strip()


def datum_matches_date(datum: str, date: str) -> bool:
    """Check if a DD.MM. string from markdown matches a YYYY-MM-DD date string."""
    # date = "2026-07-14", datum = "14.07."
    try:
        parts = date.split("-")
        expected = f"{parts[2]}.{parts[1]}."
        return datum.strip(".") == expected.strip(".") or datum == expected
    except Exception:
        return False


def migrate_yaml(yaml_path: Path, dry_run: bool) -> int:
    """Add strength sub-blocks to sessions that are missing them.
    Returns number of sessions updated.
    """
    md_path = yaml_path.with_suffix(".md")
    kraft_by_datum = parse_kraft_table(md_path)

    if not kraft_by_datum:
        return 0

    with open(yaml_path) as f:
        data = yaml.safe_load(f)

    if not data or "sessions" not in data:
        return 0

    updated = 0
    for session in data["sessions"]:
        if session.get("strength"):
            continue  # already has a strength block
        date_str = session.get("date", "")
        # match datum cell to date
        matched_kraft = None
        for datum, kraft_text in kraft_by_datum.items():
            if datum_matches_date(datum, date_str):
                matched_kraft = kraft_text
                break
        if not matched_kraft:
            continue

        duration = parse_duration(matched_kraft)
        focus = parse_focus(matched_kraft)

        session["strength"] = {
            "duration_min": duration,
            "focus": focus,
            "exercises": [],  # TODO: fill in exercises manually
        }
        print(f"  {date_str}: added strength block (focus={focus!r}, duration={duration}')"
              f"{' [dry-run]' if dry_run else ''}")
        updated += 1

    if updated and not dry_run:
        # Write back preserving as much YAML style as possible
        with open(yaml_path, "w") as f:
            yaml.dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)

    return updated


def main() -> None:
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    positional = [a for a in args if not a.startswith("--")]

    if not positional:
        die(
            "Usage:\n"
            "  migrate-strength.py <plan-dir> [--dry-run]\n\n"
            "Backfills strength sub-blocks from markdown Kraft/Stabi cells into YAML sessions.\n"
            "Run --dry-run first to preview changes without writing files."
        )

    plan_dir = Path(positional[0])
    if not plan_dir.is_dir():
        die(f"Not a directory: {plan_dir}")

    yaml_files = find_week_files(plan_dir)
    if not yaml_files:
        die(f"No week YAML files found in {plan_dir}")

    total = 0
    for yf in yaml_files:
        n = migrate_yaml(yf, dry_run)
        if n:
            print(f"{yf.name}: {n} session(s) updated")
        total += n

    if total == 0:
        print("No sessions needed migration (all already have strength blocks or no Kraft/Stabi cells).")
    else:
        if dry_run:
            print(f"\nDry run: {total} session(s) would be updated. Run without --dry-run to apply.")
        else:
            print(f"\nMigrated {total} session(s). Re-run /sync-garmin to upload strength workouts.")
            print("Note: exercises lists are empty — fill them in manually or let /marathon-coach rewrite the weeks.")


if __name__ == "__main__":
    main()
