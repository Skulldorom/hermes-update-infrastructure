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

Type `/update-infrastructure` in any Hermes session. It will:

1. Pull the latest from your homepage dashboard repo
2. Parse `services.yaml` and `bookmarks.yaml`
3. Update the wiki page `guides/homelab-infrastructure.md` with:
   - **Service Groups** — all services grouped by layout section, with URLs, monitors, containers, widgets
   - **Subdomain Table** — complete subdomain → service → URL → monitor mapping
4. Log the sync to `log.md`

### Sync Rules

- **Added in homepage** → added to wiki
- **Removed from homepage** → removed from wiki
- **Changed in homepage** → updated in wiki
- **Only in wiki, never in homepage** → preserved untouched (manual content)

Auto-generated sections are wrapped in `<!-- HOMEPAGE_AUTO: -->` markers — the plugin only touches content inside those markers. Everything else is your manual territory.

## Requirements

- Hermes Agent (any recent version with plugin support)
- `WIKI_PATH` environment variable set (defaults to `~/wiki`)
- Your homepage repo at `https://github.com/Skulldorom/homepage` (or change `HOMEPAGE_REPO` in `parser.py`)

## How It Works

```
/update-infrastructure
      │
      ▼
git pull homepage repo
      │
      ▼
parse services.yaml ──→  services + bookmarks
      │
      ▼
diff wiki page ──→ add/update/skip auto sections
      │
      ▼
write page + log.md ──→ report back
```

## License

MIT
