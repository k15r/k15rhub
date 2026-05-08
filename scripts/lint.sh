#!/usr/bin/env bash
# Lint all markdown files and validate plugin manifests.
# Run from the repo root: ./scripts/lint.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

errors=0

# Collect markdown files (exclude .git)
files=$(find . -name '*.md' -not -path './.git/*')

check_file() {
  local file="$1"
  local in_fence=0
  local prev_line=""
  local line_num=0

  while IFS= read -r line || [[ -n "$line" ]]; do
    line_num=$((line_num + 1))

    if [[ "$line" =~ ^\`\`\` ]]; then
      if [[ $in_fence -eq 0 ]]; then
        in_fence=1

        if [[ "$line" == '```' ]]; then
          echo "$file:$line_num: MD040 fenced code block without language specifier"
          errors=$((errors + 1))
        fi

        if [[ $line_num -gt 1 && -n "$prev_line" ]]; then
          echo "$file:$line_num: MD031 missing blank line before opening code fence"
          errors=$((errors + 1))
        fi
      else
        in_fence=0
      fi

      prev_line="$line"
      continue
    fi

    if [[ $in_fence -eq 1 ]]; then
      prev_line="$line"
      continue
    fi

    if [[ "$line" =~ ^\|[-]+\| ]]; then
      echo "$file:$line_num: MD060 table separator not in compact style (use | --- |)"
      errors=$((errors + 1))
    fi

    if [[ "$line" =~ ^#{1,6}\  && $line_num -gt 1 && -n "$prev_line" ]]; then
      echo "$file:$line_num: MD022 missing blank line before heading"
      errors=$((errors + 1))
    fi

    if [[ "$line" =~ [[:space:]]$ && -n "$line" ]]; then
      echo "$file:$line_num: trailing whitespace"
      errors=$((errors + 1))
    fi

    prev_line="$line"
  done < "$file"
}

echo "=== Markdown lint ==="
for f in $files; do
  check_file "$f"
done

# --- Version bump check ---
echo ""
echo "=== Version bump check ==="

if git rev-parse HEAD >/dev/null 2>&1; then
  for plugin_dir in "$ROOT"/plugins/*/; do
    plugin_name=$(basename "$plugin_dir")

    changed=$(git diff HEAD --name-only -- "plugins/$plugin_name/" | grep -v '.claude-plugin/plugin.json' || true)
    if [[ -n "$changed" ]]; then
      version_bumped=$(git diff HEAD --name-only -- "plugins/$plugin_name/.claude-plugin/plugin.json" || true)
      if [[ -z "$version_bumped" ]]; then
        echo "UNBUMPED: $plugin_name has modified files but plugin.json version was not changed"
        errors=$((errors + 1))
      else
        echo "OK: $plugin_name version bumped"
      fi
    fi
  done
fi

# --- Version consistency ---
echo ""
echo "=== Version consistency ==="

marketplace="$ROOT/.claude-plugin/marketplace.json"
if [[ -f "$marketplace" ]]; then
  for plugin_dir in "$ROOT"/plugins/*/; do
    plugin_name=$(basename "$plugin_dir")
    plugin_json="$plugin_dir/.claude-plugin/plugin.json"

    if [[ ! -f "$plugin_json" ]]; then
      echo "WARN: $plugin_name has no plugin.json"
      continue
    fi

    pv=$(grep '"version"' "$plugin_json" | head -1 | sed 's/.*: *"\(.*\)".*/\1/')
    mv=$(grep -A5 "\"name\": \"$plugin_name\"" "$marketplace" | grep '"version"' | head -1 | sed 's/.*: *"\(.*\)".*/\1/')

    if [[ "$pv" != "$mv" ]]; then
      echo "MISMATCH: $plugin_name — plugin.json=$pv, marketplace.json=$mv"
      errors=$((errors + 1))
    else
      echo "OK: $plugin_name v$pv"
    fi
  done
fi

echo ""
if [[ $errors -eq 0 ]]; then
  echo "All checks passed."
else
  echo "$errors issue(s) found."
  exit 1
fi
