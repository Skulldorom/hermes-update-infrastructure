# hermes-update-infrastructure

Hermes Agent plugin — `/update-infrastructure` slash command. Syncs your homelab wiki from your [Homepage](https://gethomepage.dev/) dashboard config.

## Install

```bash
git clone https://github.com/Skulldorom/hermes-update-infrastructure.git \
  ~/.hermes/plugins/update-infrastructure/

hermes plugins enable update-infrastructure
# /reset or restart your session
```

> Using a named profile? Clone into `~/.hermes/profiles/<name>/plugins/` and run `hermes -p <name> plugins enable update-infrastructure`.

## Usage

Type `/update-infrastructure` in any Hermes session — optionally followed by extra context:

```
/update-infrastructure also check if Bento PDF has an entity page
```

The plugin will:

1. Pull the latest from your homepage dashboard repo
2. Parse `services.yaml` (bookmarks also live here, inside a "Bookmarks" group — `bookmarks.yaml` is not used)
3. Update the wiki page `guides/homelab-infrastructure.md` with:
   - **Service Groups** — all services grouped by layout section, with URLs, monitors, containers, widgets
   - **Subdomain Table** — complete subdomain → service → URL → monitor mapping
   - **Cross-Reference Findings** — entity page gaps, IP drift, orphaned pages, unmapped server fields
4. Log the sync to `log.md`
5. Return a sync report that includes cross-reference findings and any extra instructions you supplied

### Extra instructions

Text typed after the command is captured and appended to the sync report so you (or downstream processing) can see it:

```
/update-infrastructure investigate why AdGuard is showing the wrong IP
```

### Sync Rules

- **Added in homepage** → added to wiki
- **Removed from homepage** → removed from wiki
- **Changed in homepage** → updated in wiki
- **Only in wiki, never in homepage** → preserved untouched (manual content)

Auto-generated sections are wrapped in `<!-- HOMEPAGE_AUTO: -->` markers — the plugin only touches content inside those markers. Everything else is your manual territory.

## Cross-Reference Checks

On every sync the plugin reads entity pages from `<WIKI_PATH>/entities/*.md`. Each page should have YAML frontmatter such as:

```yaml
---
host: adguard
ip: 192.168.0.2
os: Alpine
services: [dns, adblock]
status: active
type: container
---
```

The plugin then flags:

| Check | Description |
|-------|-------------|
| **Missing entity pages** | Services in homepage that have no `entities/<slug>.md` |
| **IP drift** | Services where `siteMonitor` URL contains an IP that differs from the entity `ip` field |
| **Orphaned entity pages** | Entity pages whose slug matches no service in homepage (listed for review, never deleted) |
| **Unmapped server fields** | Services with a `server:` value that does not map to any known entity host or type |

If the `entities/` directory does not exist the cross-reference step is skipped silently.

## Requirements

- Hermes Agent (any recent version with plugin support)
- `WIKI_PATH` environment variable set (defaults to `~/wiki`)
- Your homepage repo at `https://github.com/Skulldorom/homepage` (or change `HOMEPAGE_REPO` in `parser.py`)

## Configuration

| Environment Variable | Default | Description |
|----------------------|---------|-------------|
| `WIKI_PATH` | `~/wiki` | Root of your wiki directory |
| `SUBDOMAIN_PATTERNS` | *(see below)* | Comma-separated list of subdomain extraction regexes |

### `SUBDOMAIN_PATTERNS`

The default pattern extracts subdomains from `*.skulldorom.xyz` and `*.skulldorom.com`. To support additional domains, set this variable to a comma-separated list of Python regex strings. **Each pattern must capture the subdomain label in group 1.**

```bash
export SUBDOMAIN_PATTERNS='https?://([a-z0-9-]+)\.skulldorom\.(xyz|com),https?://([a-z0-9-]+)\.example\.net'
```

## How It Works

```
/update-infrastructure [extra instructions]
      │
      ▼
git pull homepage repo
      │
      ▼
parse services.yaml ──→  services + subdomains
      │
      ▼
load entities/*.md ──→ cross-reference checks
      │
      ▼
diff wiki page ──→ add/update/skip auto sections
      │
      ▼
write page + log.md ──→ report back (includes cross-ref + extra instructions)
```

## License

MIT
