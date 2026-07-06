# hermes-update-infrastructure

Hermes Agent plugin — `/update-infrastructure` slash command. Syncs your homelab wiki from your [Homepage](https://gethomepage.dev/) dashboard config.

![Version](https://img.shields.io/badge/version-2.2.2-blue)

## Install

```bash
git clone https://github.com/Skulldorom/hermes-update-infrastructure.git \
  ~/.hermes/plugins/update-infrastructure/

hermes plugins enable update-infrastructure
# /reset or restart your session
```

> Using a named profile? Clone into `~/.hermes/profiles/<name>/plugins/` and run `hermes -p <name> plugins enable update-infrastructure`.

## Usage

Type `/update-infrastructure` (or the short aliases `/update-infra` / `/sync-infra`) in any Hermes session — optionally followed by an inline command or extra context:

```
/update-infrastructure
/update-infra create "Bento PDF"
/sync-infra fix all
/update-infrastructure status
```

The plugin will:

1. Pull the latest from your homepage dashboard repo
2. Parse `services.yaml` and `bookmarks.yaml` for all services and bookmarks
3. Update the wiki page `guides/homelab-infrastructure.md` with:
   - **Service Groups** — all services grouped by layout section, with URLs, monitors, containers, widgets
   - **Subdomain Table** — complete subdomain → service → URL → monitor mapping
   - **Cross-Reference Findings** — entity page gaps, IP drift, orphaned pages, unmapped server fields
   - **Network Topology** — auto-generated Mermaid diagram from entity pages (renders natively in Obsidian)
4. Log the sync to `log.md`
5. Return a sync report with topology summary, cross-reference findings, elapsed time, and the result of any inline command

### Inline commands

Text typed after the command is parsed for known intents and acted on. Unrecognised text is echoed as before.

| Command | Action |
|---------|--------|
| `create "Service Name"` | Scaffold a new `entities/<slug>.md` with sensible defaults |
| `create-entity "Service Name"` | Same as above |
| `fix all` | Apply all IP drift fixes found in this sync |
| `fix-ip "Service Name"` | Fix the `ip` field for one specific service |
| `fix-ip "Service Name" --dry-run` | Show what would change, without writing |
| `check "Service Name"` | Deep-check one service across homepage, entity page, and cross-ref |
| `cleanup` | List orphaned entity pages with removal instructions |
| `status` | Return just the cross-reference summary (skips wiki update) |
| *(anything else)* | Echoed as extra context — current behaviour |

Commands are **case-insensitive** and match on prefix/keyword, not exact string.

### Examples

```
# Scaffold a missing entity page
/update-infrastructure create "Bento PDF"

# Fix an IP mismatch (dry-run first)
/update-infrastructure fix-ip "AdGuard Primary" --dry-run
/update-infrastructure fix-ip "AdGuard Primary"

# Fix every IP-drift at once
/update-infrastructure fix all

# Deep-check one service
/update-infrastructure check "Bento PDF"

# Status only (no wiki update)
/update-infrastructure status
```

### Sync Rules

- **Added in homepage** → added to wiki
- **Removed from homepage** → removed from wiki
- **Changed in homepage** → updated in wiki
- **Only in wiki, never in homepage** → preserved untouched (manual content)

Auto-generated sections are wrapped in `<!-- HOMEPAGE_AUTO: -->` markers — the plugin only touches content inside those markers. Everything else is your manual territory.

## Network Topology

On every sync the plugin generates a Mermaid topology diagram from your `entities/*.md` entity pages:

### Mermaid Diagram (in-wiki)

A `graph TD` diagram embedded directly in `guides/homelab-infrastructure.md`. Renders natively in Obsidian. Machines are grouped into color-coded zones:

| Zone | Color | Category |
|------|-------|----------|
| Infrastructure | Purple | Servers, NAS, hypervisors, storage |
| Networking | Orange | Routers, switches, access points, firewalls |
| Containers / VMs | Blue | Docker containers, LXCs, VMs |
| Devices | Green | Physical devices, phones, laptops |

Hosts with child containers show as parent boxes with child nodes nested inside. Edges link hosts → children.

### Entity Page Format

The diagram reads from the same entity pages used by cross-reference checks. For topology rendering, these frontmatter fields are read:

```yaml
---
title: Proxmox-1
ip: 192.168.0.10
os: Debian 12
host: beast          # parent host slug (makes this a child node)
type: container       # maps to category: infrastructure | networking | container | device
services: [dns, adblock]
tailscale_ip: 100.x.y.z
---
```

Category mapping rules:
- `infra`, `server`, `proxmox`, `nas`, `storage`, `hypervisor` → **Infrastructure**
- `network`, `router`, `switch`, `ap`, `wireless`, `firewall` → **Networking**
- `container`, `vm`, `lxc`, `virtual` → **Containers / VMs**
- Everything else (e.g. `device`, `phone`, `laptop`) → preserved as-is

## Project Structure

| File | Role |
|------|------|
| `__init__.py` | Plugin registration, intent parsing, main command handler |
| `parser.py` | Homepage YAML parsing (`services.yaml`, `bookmarks.yaml`), cross-reference checks, entity page scaffolding, IP repair |
| `updater.py` | Wiki page generation with `<!-- HOMEPAGE_AUTO: -->` markers, log writing |
| `topology.py` | Mermaid `graph TD` topology diagram from entity pages |
| `parser_wiki.py` | Entity page frontmatter parsing for topology data |
| `plugin.yaml` | Plugin manifest (name, version, description) |

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
| **Missing entity pages** | Services in homepage that have no `entities/<slug>.md` (generic infra names filtered) |
| **IP drift** | Services where `siteMonitor` URL contains an IP that differs from the entity `ip` field |
| **Orphaned entity pages** | Entity pages whose slug matches no service in homepage (personal devices excluded) |
| **Unmapped server fields** | Services with a `server:` value that does not map to any entity, with fuzzy suggestions |

### Noise filtering

The cross-reference output is filtered to surface only actionable findings:

- **Generic infra names** (e.g. "Proxmox", "Nginx", "Synology", "Authentik") are not reported as missing entity pages — they are platform/stack labels, not specific hosts.
- **Personal device / person entity pages** (those with `type: phone`, `type: tablet`, `type: person`, etc.) are excluded from orphaned-page reports.
- **Unmapped server aliases** include fuzzy suggestions — e.g. `server: nas` → "did you mean `thehoard-nas`?".

Extend the generic-name list via the `NOISE_FILTER_NAMES` env var (comma-separated).

### Scaffolding entity pages

When a genuinely new service is detected the sync report includes an actionable hint:

```
⚠️ Bento PDF — no entity page found
    → Run `/update-infrastructure create "Bento PDF"` to scaffold one
```

The created page includes:

```yaml
---
title: Bento PDF
created: 2025-01-01
updated: 2025-01-01
type: container
status: active
ip: 192.168.0.12   # extracted from siteMonitor or href
---
```

### IP drift repair

When IP drift is detected the sync report includes a repair hint:

```
⚠️ AdGuard Primary: siteMonitor=`http://192.168.0.2`, entity ip=`192.168.0.8` (MISMATCH)
    → Run `/update-infrastructure fix-ip "AdGuard Primary"` to repair
```

Use `--dry-run` to preview the change before writing:

```
/update-infrastructure fix-ip "AdGuard Primary" --dry-run
```

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
| `NOISE_FILTER_NAMES` | *(see below)* | Comma-separated extra generic names to filter from missing-entity reports |

### `SUBDOMAIN_PATTERNS`

The default pattern extracts subdomains from `*.skulldorom.xyz` and `*.skulldorom.com`. To support additional domains, set this variable to a comma-separated list of Python regex strings. **Each pattern must capture the subdomain label in group 1.**

```bash
export SUBDOMAIN_PATTERNS='https?://([a-z0-9-]+)\.skulldorom\.(xyz|com),https?://([a-z0-9-]+)\.example\.net'
```

### `NOISE_FILTER_NAMES`

Comma-separated list of additional service names to treat as generic infrastructure. Names are slugified before comparison.

```bash
export NOISE_FILTER_NAMES='my-custom-platform,another-stack-name'
```

## How It Works

```
/update-infrastructure [inline command]
      │
      ▼
parse inline command (intent)
      │
      ├─ status? → cross-ref only, return immediately
      │
      ▼
git pull homepage repo
      │
      ▼
parse services.yaml + bookmarks.yaml ──→  services + subdomains
      │
      ▼
load entities/*.md ──→ noise-filtered cross-reference checks
      │
      ▼
diff wiki page ──→ add/update/skip auto sections
      │              (service groups, subdomains, cross-ref, topology)
      │
      ▼
write page + log.md
      │
      ▼
execute intent (create / fix-ip / fix all / check / cleanup)
      │
      ▼
report back (topology summary + cross-ref findings + elapsed time)
```

## License

MIT
