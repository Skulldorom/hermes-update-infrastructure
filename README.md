# hermes-update-infrastructure

Hermes Agent plugin — `/update-infrastructure` slash command. Syncs your
homelab wiki from [Pulse](https://pulse.zingerlabs.com/) monitoring API data,
with optional [NGINX Proxy Manager](https://nginxproxymanager.com/) (NPM)
integration for domain/proxy-host cross-referencing.

![Version](https://img.shields.io/badge/version-3.0.17-blue)

## Install

```bash
git clone https://github.com/your-org/hermes-update-infrastructure.git \
  ~/.hermes/plugins/update-infrastructure/

hermes plugins enable update-infrastructure
# /reset or restart your session
```

> Using a named profile? Clone into `~/.hermes/profiles/<name>/plugins/` and
> run `hermes -p <name> plugins enable update-infrastructure`.

## Quickstart

1. Install the plugin (see above).
2. Add the `infrastructure-plugin:` section to your Hermes `config.yaml`
   (see [Configuration](#configuration) below).
3. Add your Pulse API token to `.env`:
   ```
   PULSE_API_TOKEN=your-token-here
   ```
4. Run `/update-infrastructure` — the plugin fetches live data from Pulse,
   cross-references it against your wiki entity pages, and updates the
   generated data file.

## Usage

Type `/update-infrastructure` (or the short aliases `/update-infra` /
`/sync-infra`) in any Hermes session — optionally followed by an inline
command or extra context:

```
/update-infrastructure
/update-infra create "My Service"
/sync-infra fix all
/update-infrastructure status
/update-infrastructure help
```

The plugin will:

1. Fetch service and host data from the Pulse monitoring API
2. Cross-reference the data against your wiki entity pages
3. Update `raw/sources/pulse-infrastructure-data.md` with the latest data
4. Hand off a structured review prompt to the agent loop (agent summarizes
   and follows up); inline sub-commands return direct output

### Inline commands

Text typed after the command is parsed for known intents and acted on.
Unrecognised text is echoed back with a hint to try `help`.

| Command | Action |
|---------|--------|
| `create "Service Name"` | Scaffold a new `entities/<slug>.md` with sensible defaults |
| `create-entity "Service Name"` | Same as above |
| `fix all` | Apply all IP drift fixes found in this sync |
| `fix-ip "Service Name"` | Fix the `ip` field for one specific service |
| `fix-ip "Service Name" --dry-run` | Show what would change, without writing |
| `check "Service Name"` | Deep-check one service across Pulse, entity page, and cross-ref |
| `cleanup` | List orphaned entity pages with removal instructions |
| `status` | Return just the cross-reference summary (skips wiki update) |
| `help` / `--help` / `-h` | Show usage, sub-commands, and configuration keys |
| *(anything else)* | Echoed as extra context, with hint to try `help` |

Commands are **case-insensitive** and match on prefix/keyword, not exact string.

### Examples

```
# Scaffold a missing entity page
/update-infrastructure create "My Service"

# Fix an IP mismatch (dry-run first)
/update-infrastructure fix-ip "AdGuard Primary" --dry-run
/update-infrastructure fix-ip "AdGuard Primary"

# Fix every IP-drift at once
/update-infrastructure fix all

# Deep-check one service
/update-infrastructure check "My Service"

# Status only (no wiki update)
/update-infrastructure status
```

### Sync Rules

- **Added in Pulse** → added to generated data file
- **Removed from Pulse** → removed from generated data file
- **Changed in Pulse** → updated in generated data file
- **Only in wiki, never in Pulse** → preserved untouched (manual content)

Auto-generated data is written to `raw/sources/pulse-infrastructure-data.md`. The
Hermes agent reads that file and updates `guides/homelab-infrastructure.md`.
Everything else in the wiki page is your manual territory.

## Network Topology

On every sync the plugin generates a Mermaid topology diagram from your
`entities/*.md` entity pages:

### Mermaid Diagram (in-wiki)

A `graph TD` diagram embedded directly in `guides/homelab-infrastructure.md`.
Renders natively in Obsidian. Machines are grouped into colour-coded zones:

| Zone | Color | Category |
|------|-------|----------|
| Infrastructure | Purple | Servers, NAS, hypervisors, storage |
| Networking | Orange | Routers, switches, access points, firewalls |
| Containers / VMs | Blue | Docker containers, LXCs, VMs |
| Devices | Green | Physical devices, phones, laptops |

Hosts with child containers show as parent boxes with child nodes nested
inside. Edges link hosts → children.

### Entity Page Format

The diagram reads from the same entity pages used by cross-reference checks.
For topology rendering, these frontmatter fields are read:

```yaml
---
title: My-Container
ip: 10.0.0.10
os: Debian 12
host: my-host        # parent host slug (makes this a child node)
type: container      # maps to category: infrastructure | networking | container | device
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
| `config.py` | Configuration getters (reads from Hermes `config.yaml`) |
| `pulse_client.py` | Pulse monitoring API client |
| `npm_client.py` | NGINX Proxy Manager API client + wiki subdomain sync |
| `parser.py` | Cross-reference checks, entity page scaffolding, IP repair |
| `updater.py` | Wiki page generation, log writing |
| `topology.py` | Mermaid `graph TD` topology diagram from entity pages |
| `parser_wiki.py` | Entity page frontmatter parsing for topology data |
| `plugin.yaml` | Plugin manifest (name, version, description) |

## Cross-Reference Checks

On every sync the plugin reads entity pages from `<wiki_root>/entities/*.md`.
Each page should have YAML frontmatter such as:

```yaml
---
host: my-service
ip: 10.0.0.2
os: Alpine
services: [dns, adblock]
status: active
type: container
---
```

The plugin then flags:

| Check | Description |
|-------|-------------|
| **Missing entity pages** | Services in Pulse that have no `entities/<slug>.md` (generic infra names filtered) |
| **IP drift** | Services where the monitored URL contains an IP that differs from the entity `ip` field |
| **Orphaned entity pages** | Entity pages whose slug matches no service in Pulse (personal devices excluded) |
| **Unmapped server fields** | Services with a `server:` value that does not map to any entity, with fuzzy suggestions |

### Noise filtering

The cross-reference output is filtered to surface only actionable findings:

- **Generic infra names** (e.g. "Proxmox", "Nginx", "Synology", "Authentik")
  are not reported as missing entity pages — they are platform/stack labels,
  not specific hosts.
- **Personal device / person entity pages** (those with `type: phone`,
  `type: tablet`, `type: person`, etc.) are excluded from orphaned-page
  reports.
- **Orphan suppression whitelist** — entity slugs listed in
  `suppress_orphan_check` are excluded from orphaned-page reports. Use
  this for real hardware (IR blasters, routers), external services (VPN),
  and other legitimate wiki pages that will never appear in Pulse.
- **Unmapped server aliases** include fuzzy suggestions — e.g.
  `server: nas` → "did you mean `my-nas`?".

Extend the generic-name list via `infrastructure-plugin.generic_infra_names`
in `config.yaml`.  Use `suppress_orphan_check` to whitelist entity slugs
that should never appear in orphan reports.

### Scaffolding entity pages

When a genuinely new service is detected the sync report includes an
actionable hint:

```
⚠️ My Service — no entity page found
    → Run `/update-infrastructure create "My Service"` to scaffold one
```

## Configuration

All plugin settings live in the `infrastructure-plugin:` section of your
Hermes `config.yaml`.  No environment variables are needed for configuration
(credentials are the exception — see below).

```yaml
infrastructure-plugin:
  pulse_url: http://your-pulse-server:7655
  npm_url: http://your-npm-server:81          # optional
  wiki_root: ~/wiki                            # default: ~/wiki
  wiki_page: guides/homelab-infrastructure.md  # default
  subdomain_patterns:
    - 'https?://([a-z0-9-]+)\.example\.com'
  generic_infra_names:
    - my-custom-platform
    - another-stack-name
  server_aliases:
    beast: beast-proxmox
    nas: my-nas-1
  suppress_entity_check:
    - "Some Generic Service"
  suppress_orphan_check:
    - broadlink-ir
    - the-beast
```

### Configuration keys

| Key | Default | Description |
|-----|---------|-------------|
| `pulse_url` | *(empty)* | Base URL of the Pulse monitoring API |
| `npm_url` | *(empty)* | Base URL of the NGINX Proxy Manager instance (optional) |
| `wiki_root` | `~/wiki` | Root of your wiki directory |
| `wiki_page` | `guides/homelab-infrastructure.md` | Path to the main infrastructure page (relative to wiki root) |
| `subdomain_patterns` | *(empty list)* | List of Python regex patterns for subdomain extraction; each must capture the subdomain label in group 1 |
| `generic_infra_names` | *(built-in list)* | Replaces the built-in list of platform names filtered from missing-entity reports |
| `server_aliases` | *(empty)* | Dict mapping short server names to entity slugs |
| `suppress_entity_check` | *(empty)* | List of service display names to skip in missing-entity reports |
| `suppress_orphan_check` | *(empty)* | List of entity slugs (filename without `.md`) to skip in orphaned-entity reports |

### Credentials (environment variables)

Credentials are **not** stored in `config.yaml`. Set them in `.env` or your
shell environment:

| Variable | Description |
|----------|-------------|
| `PULSE_API_TOKEN` | Pulse API token (`monitoring:read`, `agent:read`, `settings:read` scopes) |
| `NPM_EMAIL` | Email used to authenticate with NPM (optional) |
| `NPM_PASSWORD` | Password used to authenticate with NPM (optional) |

## Pulse Setup

1. Install [Pulse](https://pulse.zingerlabs.com/) in your homelab.
2. In the Pulse web UI, create an API token. Select **Custom scopes** and
   tick exactly these three checkboxes (all read-only):

   | Pulse scope (code) | UI checkbox |
   |---|---|
   | `monitoring:read` | **Monitoring & alerts (read)** |
   | `agent:read` | **Agent reporting** |
   | `settings:read` | **Settings (read)** |

   > The plugin only calls `GET` endpoints — no `write` or lifecycle
   > management scopes are needed.
3. Add the token to your `.env` file:
   ```
   PULSE_API_TOKEN=your-token-here
   ```
4. Add the Pulse URL to `config.yaml`:
   ```yaml
   infrastructure-plugin:
     pulse_url: http://your-pulse-server:7655
   ```
5. Verify: run `/update-infrastructure status` — the output should show your
   services and hosts.

## NPM Setup (optional)

NGINX Proxy Manager integration cross-references your wiki domain table
against live NPM proxy hosts and reports mismatches, disabled hosts, and
missing SSL enforcement.

1. In the NPM web UI, create a read-only API user (or use an existing account
   with limited permissions).
2. Add the credentials to `.env`:
   ```
   NPM_EMAIL=readonly@example.com
   NPM_PASSWORD=your-password
   ```
3. Add the NPM URL to `config.yaml`:
   ```yaml
   infrastructure-plugin:
     npm_url: http://your-npm-server:81
   ```
4. Verify: the next full sync will include NPM data alongside Pulse results.

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
fetch data from Pulse API
      │
      ▼
load entities/*.md ──→ noise-filtered cross-reference checks
      │
      ▼
diff generated data file ──→ add/update/skip auto sections
      │
      ▼
write raw/sources/pulse-infrastructure-data.md + log.md
      │
      ▼
execute intent (create / fix-ip / fix all / check / cleanup)
      │
      ▼
report back (topology summary + cross-ref findings + elapsed time)
```

## License

MIT
