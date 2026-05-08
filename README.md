# k15rhub — Claude Code Plugin Marketplace

A personal Claude Code plugin marketplace by k15r.

## Installation

### 1. Add the marketplace

```bash
/plugin marketplace add k15r/k15rhub
```

### 2. Install a plugin

```bash
/plugin install running@k15rhub
```

### 3. Verify installation

```bash
/plugin
```

The installed skills will be available as `/running:marathon-coach` and `/running:analyze-run`.

## Available Plugins

| Plugin | Description | Version |
| --- | --- | --- |
| **running** | Analyze runs from Runalyze and manage marathon/half-marathon training plans in Obsidian. | 0.1.0 |

## Updating

Refresh the marketplace catalog and upgrade plugins:

```bash
/plugin marketplace update k15rhub
```

## Uninstalling

```bash
/plugin uninstall running@k15rhub
```

To remove the marketplace entirely:

```bash
/plugin marketplace remove k15rhub
```

## For Plugin Authors

Plugins live in `plugins/<name>/` and must contain:

```text
├── .claude-plugin/
│   └── plugin.json        # Plugin manifest
└── skills/
    └── my-skill/
        └── SKILL.md       # Skill definition with YAML frontmatter
```

After adding or modifying a plugin, register it in `.claude-plugin/marketplace.json` and bump the version in both `marketplace.json` and `plugin.json`. The pre-commit lint hook enforces version consistency.

## License

MIT
