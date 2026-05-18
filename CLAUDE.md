# k15rhub — Claude Code Instructions

## Content

This repository is a Claude Code plugin marketplace. It hosts plugins (skills, agents, hooks) that can be installed via `/plugin marketplace update k15rhub`.

## Style

- Always use compact-style markdown tables with spaces around pipes: `| --- |` not `|---|` or `|--------|`. When verifying, search for any `|` immediately followed by `-` (regex `\|-`) to catch all variants.
- Always specify a language on fenced code blocks (e.g. ` ```bash `, ` ```json `, ` ```text `). Use `text` for plain output or ASCII art.
- Always surround fenced code blocks with blank lines — both above the opening fence and below the closing fence.
- Always surround headings with blank lines — both above and below.
- Always surround lists with blank lines — both above the first item and below the last item.

## Testing plugin changes locally

Claude Code re-syncs the marketplace directory from the remote on restart, so copying files there is unreliable. Instead, overwrite the **cache** for the currently installed version — Claude Code loads plugin code from there.

```bash
# Look up the version Claude Code currently knows about
version=$(jq -r '.plugins[] | select(.name=="<name>") .version' \
  ~/.claude/plugins/marketplaces/k15rhub/.claude-plugin/marketplace.json)

# Replace that version's cache with your working copy
rm -rf ~/.claude/plugins/cache/k15rhub/<name>/$version/
cp -R plugins/<name>/ ~/.claude/plugins/cache/k15rhub/<name>/$version/
```

Then restart Claude Code and invoke the skill to verify. Repeat the two commands after each edit — no version bump needed during development.

When you are done testing, bump the version, commit, push, and run `/plugin marketplace update k15rhub` to publish.

## Before committing plugin changes

1. **Run the lint script** — `./scripts/lint.sh` checks markdown style, version bumps, and version consistency.
2. **Review the whole repo** — check that all cross-references between skills, agents, and marketplace.json are consistent.
3. **Bump the version** in `plugins/<name>/.claude-plugin/plugin.json` (semver).
4. **Update the version** in `.claude-plugin/marketplace.json` for the same plugin.
5. Commit both the plugin files and the version bump together.

The plugin cache is keyed by version — without a bump, consumers won't pick up new files after running `/plugin marketplace update k15rhub`.
