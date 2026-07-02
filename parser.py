"""Parse homepage YAML configs into structured homelab data."""

import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml


HOMEPAGE_REPO = "https://github.com/Skulldorom/homepage"
REPO_CACHE = Path(tempfile.gettempdir()) / "hermes-homepage-sync"

# Default subdomain patterns — each pattern must use group(1) to capture the subdomain label.
# Override by setting SUBDOMAIN_PATTERNS to a comma-separated list of regex strings.
_DEFAULT_SUBDOMAIN_PATTERNS = [r"https?://([a-z0-9-]+)\.skulldorom\.(xyz|com)"]

# Default set of generic infrastructure/platform names that are NOT specific hosts.
# Services whose slugified name matches one of these are filtered from "missing entity pages"
# unless a matching entity page already exists for that name.
# Extend via the NOISE_FILTER_NAMES env var (comma-separated list).
_DEFAULT_GENERIC_INFRA_NAMES: frozenset[str] = frozenset({
    "synology", "glances", "dozzle", "proxmox", "nginx", "authentik",
    "traefik", "caddy", "haproxy", "portainer", "watchtower", "homepage",
    "grafana", "prometheus", "loki", "influxdb", "telegraf", "netdata",
    "unifi", "pihole", "adguard-home", "adguardhome", "wireguard", "tailscale",
    "docker", "kubernetes", "k8s", "vaultwarden", "bitwarden",
    "nextcloud", "jellyfin", "plex", "emby", "sonarr", "radarr",
    "lidarr", "readarr", "prowlarr", "sabnzbd", "qbittorrent", "transmission",
    "overseerr", "tautulli", "uptime-kuma", "changedetection",
    "gitea", "forgejo", "gitlab", "immich", "photoprism", "paperless-ngx", "paperless",
})

# Frontmatter ``type`` values that indicate personal devices / people (not services).
# Entity pages with these types are silently excluded from orphaned-page reports.
_PERSONAL_DEVICE_TYPES: frozenset[str] = frozenset({
    "person", "people", "phone", "tablet", "device", "mobile", "laptop",
})


def _parse_yaml(path: Path) -> dict:
    """Parse a Homepage YAML config file, stripping template comments."""
    text = path.read_text()
    cleaned = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        cleaned.append(line)
    raw = yaml.safe_load("\n".join(cleaned))
    return raw if raw else {}


def fetch_repo() -> Path:
    """Clone or pull the homepage repo. Returns path to the config dir."""
    if REPO_CACHE.exists() and (REPO_CACHE / ".git").exists():
        subprocess.run(
            ["git", "fetch", "--depth", "1", "origin", "main"],
            cwd=REPO_CACHE,
            capture_output=True,
            timeout=30,
        )
        subprocess.run(
            ["git", "reset", "--hard", "origin/main"],
            cwd=REPO_CACHE,
            capture_output=True,
            timeout=10,
        )
    else:
        REPO_CACHE.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--depth", "1", HOMEPAGE_REPO, str(REPO_CACHE)],
            capture_output=True,
            timeout=60,
        )
    return REPO_CACHE / "config"


def _walk_services(raw, group_name: str = "", subgroup_name: str = "") -> list[dict]:
    """Recursively walk the homepage YAML tree to find leaf service entries."""
    result = []

    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                for key, val in item.items():
                    if isinstance(val, (dict, list)):
                        # Could be a group, subgroup, or service
                        if isinstance(val, dict) and "href" in val:
                            # Leaf service: {name: {href: ..., icon: ...}}
                            widget = val.get("widget", {})
                            widget_type = widget.get("type") if isinstance(widget, dict) else None
                            result.append({
                                "name": key,
                                "group": group_name or "Ungrouped",
                                "subgroup": subgroup_name,
                                "href": val.get("href"),
                                "site_monitor": val.get("siteMonitor"),
                                "icon": val.get("icon"),
                                "description": val.get("description"),
                                "container": val.get("container"),
                                "server": val.get("server"),
                                "widget_type": widget_type,
                            })
                        else:
                            # Intermediate level — recurse
                            if group_name:
                                result.extend(_walk_services(val, group_name, key))
                            else:
                                result.extend(_walk_services(val, key, subgroup_name))

    return result


def extract_services(config_dir: Path) -> list[dict]:
    """Parse services.yaml into a flat list of service dicts."""
    services_raw = _parse_yaml(config_dir / "services.yaml")
    if not services_raw:
        return []
    return _walk_services(services_raw)


def extract_bookmarks(config_dir: Path) -> list[dict]:
    """Parse bookmarks.yaml for additional URL entries."""
    bookmarks_raw = _parse_yaml(config_dir / "bookmarks.yaml")
    if not bookmarks_raw:
        return []
    return _walk_services(bookmarks_raw)


def _get_subdomain_patterns() -> list[str]:
    """Return subdomain regex patterns from SUBDOMAIN_PATTERNS env var or defaults.

    Each pattern must capture the subdomain label in group 1.
    The env var accepts a comma-separated list of raw regex strings, e.g.:
        SUBDOMAIN_PATTERNS=https?://([a-z0-9-]+)\\.example\\.com,https?://([a-z0-9-]+)\\.other\\.net
    """
    env_val = os.environ.get("SUBDOMAIN_PATTERNS", "").strip()
    if env_val:
        return [p.strip() for p in env_val.split(",") if p.strip()]
    return _DEFAULT_SUBDOMAIN_PATTERNS


def extract_subdomain(service: dict) -> str | None:
    """Extract subdomain from href using configurable patterns.

    Returns the subdomain label (group 1 of the first matching pattern), or None.
    """
    href = service.get("href", "")
    if not href:
        return None

    for pattern in _get_subdomain_patterns():
        match = re.search(pattern, href)
        if match:
            return match.group(1)

    return None


def _slugify(name: str) -> str:
    """Convert a service/entity name to a lowercase hyphenated slug.

    e.g. "AdGuard Primary" → "adguard-primary", "Bento PDF" → "bento-pdf"
    """
    slug = name.lower().replace(" ", "-")
    return re.sub(r"[^a-z0-9-]", "", slug)


def _get_generic_infra_names() -> frozenset[str]:
    """Return the set of generic infra names to filter from missing-entity reports.

    Reads the ``NOISE_FILTER_NAMES`` env var for additional names (comma-separated).
    """
    env_val = os.environ.get("NOISE_FILTER_NAMES", "").strip()
    if env_val:
        extra = frozenset(_slugify(n.strip()) for n in env_val.split(",") if n.strip())
        return _DEFAULT_GENERIC_INFRA_NAMES | extra
    return _DEFAULT_GENERIC_INFRA_NAMES


def _is_personal_device_entity(entity: dict) -> bool:
    """Return True when the entity page represents a personal device or person.

    Checks the ``type`` field and ``tags`` list in the frontmatter.
    """
    if str(entity.get("type", "")).lower() in _PERSONAL_DEVICE_TYPES:
        return True
    tags = entity.get("tags", [])
    if isinstance(tags, list):
        return any(str(t).lower() in _PERSONAL_DEVICE_TYPES for t in tags)
    return False


def _suggest_server_match(server: str, entity_pages: dict) -> list[str]:
    """Return entity slugs that contain *server* as a substring.

    Used to generate "Did you mean…?" suggestions for unmapped server fields.
    """
    server_slug = _slugify(str(server))
    if not server_slug:
        return []
    return sorted(slug for slug in entity_pages if server_slug in slug)


def _extract_ip_from_url(url: str) -> str | None:
    """Extract the first IPv4 address found in a URL string, or None."""
    if not url:
        return None
    m = re.search(r"https?://(\d{1,3}(?:\.\d{1,3}){3})", url)
    return m.group(1) if m else None


def _simple_diff(original: str, updated: str) -> str:
    """Return a minimal line-level diff between two strings."""
    orig_lines = original.splitlines()
    new_lines = updated.splitlines()
    diff_lines: list[str] = []
    for old_line, new_line in zip(orig_lines, new_lines):
        if old_line != new_line:
            diff_lines.append(f"- {old_line}")
            diff_lines.append(f"+ {new_line}")
    return "\n".join(diff_lines)


def _parse_frontmatter(path: Path) -> dict:
    """Parse YAML frontmatter (--- delimited) from a markdown file."""
    try:
        text = path.read_text()
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end == -1:
        return {}
    fm_text = text[3:end].strip()
    try:
        data = yaml.safe_load(fm_text)
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError:
        return {}


def load_entity_pages(wiki_root: Path) -> dict | None:
    """Load all entity pages from ``<wiki_root>/entities/*.md``.

    Returns:
        A dict keyed by slug (filename stem), values are frontmatter dicts
        merged with ``_slug`` and ``_path`` keys.
        Returns ``None`` when the entities directory does not exist so callers
        can distinguish "no directory" from "directory exists but is empty".
    """
    entities_dir = wiki_root / "entities"
    if not entities_dir.exists():
        return None

    result: dict[str, dict] = {}
    for page in sorted(entities_dir.glob("*.md")):
        slug = page.stem
        fm = _parse_frontmatter(page)
        result[slug] = {**fm, "_slug": slug, "_path": str(page)}
    return result


def cross_reference(data: dict, entity_pages: dict | None) -> dict:
    """Cross-reference homepage services against wiki entity pages.

    Args:
        data: Structured homepage data from :func:`parse_all`.
        entity_pages: Dict returned by :func:`load_entity_pages`, or ``None``
            when the entities directory was not found.

    Returns:
        A dict with the following keys:

        ``entities_dir_found`` (bool)
            False when *entity_pages* is ``None``.
        ``missing_entity_pages`` (list[dict])
            Services with no matching entity page (generic infra names filtered out).
        ``ip_drift`` (list[dict])
            Services where the siteMonitor IP differs from the entity ``ip``.
        ``orphaned_entity_pages`` (list[str])
            Entity page slugs whose name does not match any homepage service
            (personal-device / person pages filtered out).
        ``unmapped_servers`` (list[dict])
            Services whose ``server:`` field does not map to any entity, with
            fuzzy ``suggestions`` for likely matches.
    """
    if entity_pages is None:
        return {"entities_dir_found": False}

    services = data.get("services", [])
    generic_names = _get_generic_infra_names()

    # Build a set of service slugs for fast lookup
    service_slugs = {_slugify(svc["name"]) for svc in services}

    # Build a set of known host identifiers from entity pages
    known_hosts: set[str] = set()
    for slug, entity in entity_pages.items():
        known_hosts.add(slug)
        host = entity.get("host")
        if host:
            known_hosts.add(_slugify(str(host)))
        entity_type = entity.get("type")
        if entity_type:
            known_hosts.add(_slugify(str(entity_type)))

    missing_entity_pages: list[dict] = []
    ip_drift: list[dict] = []
    unmapped_servers: list[dict] = []

    for svc in services:
        slug = _slugify(svc["name"])
        entity = entity_pages.get(slug)

        if entity is None:
            # Skip well-known generic infrastructure names — they are stack/type
            # labels, not specific hosts, so a missing entity page is expected.
            if slug not in generic_names:
                missing_entity_pages.append({"name": svc["name"], "href": svc.get("href", "")})
        else:
            # IP drift: compare siteMonitor URL IP against entity page ip field
            entity_ip = entity.get("ip")
            site_monitor = svc.get("site_monitor") or ""
            if entity_ip and site_monitor:
                ip_match = re.search(r"https?://(\d{1,3}(?:\.\d{1,3}){3})", site_monitor)
                if ip_match and ip_match.group(1) != str(entity_ip):
                    ip_drift.append({
                        "name": svc["name"],
                        "site_monitor": site_monitor,
                        "entity_ip": str(entity_ip),
                    })

        # Unmapped server field — add fuzzy suggestions
        server = svc.get("server")
        if server and _slugify(str(server)) not in known_hosts:
            suggestions = _suggest_server_match(server, entity_pages)
            unmapped_servers.append({
                "name": svc["name"],
                "server": server,
                "suggestions": suggestions,
            })

    # Orphaned entity pages: slugs not matched by any service slug.
    # Personal device / people pages are silently excluded.
    orphaned = [
        f"entities/{slug}.md"
        for slug, entity in entity_pages.items()
        if slug not in service_slugs and not _is_personal_device_entity(entity)
    ]

    return {
        "entities_dir_found": True,
        "missing_entity_pages": missing_entity_pages,
        "ip_drift": ip_drift,
        "orphaned_entity_pages": sorted(orphaned),
        "unmapped_servers": unmapped_servers,
    }


def scaffold_entity_page(
    wiki_root: Path,
    name: str,
    href: str = "",
    site_monitor: str = "",
    server: str = "",
) -> dict:
    """Scaffold a new entity page for a service that has no existing page.

    Args:
        wiki_root: Root of the wiki directory.
        name: Human-readable service name (e.g. "Bento PDF").
        href: Service URL (used to extract IP if *site_monitor* has none).
        site_monitor: siteMonitor URL (used to extract IP).
        server: server field value from homepage (used to populate ``host``).

    Returns:
        A dict with keys:

        ``created`` (bool)
            True if a new file was written.
        ``path`` (str)
            Absolute path to the entity page.
        ``slug`` (str, only when created)
            The slugified filename stem.
        ``reason`` (str, only when not created)
            Why the file was not created.
    """
    slug = _slugify(name)
    entities_dir = wiki_root / "entities"
    entities_dir.mkdir(parents=True, exist_ok=True)

    page_path = entities_dir / f"{slug}.md"
    if page_path.exists():
        return {"created": False, "path": str(page_path), "reason": "already exists"}

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ip = _extract_ip_from_url(site_monitor) or _extract_ip_from_url(href)

    fm_lines = [
        "---",
        f"title: {name}",
        f"created: {today}",
        f"updated: {today}",
        "type: container",
        "status: active",
    ]
    if ip:
        fm_lines.append(f"ip: {ip}")
    if server:
        fm_lines.append(f"host: {_slugify(str(server))}")
    fm_lines += [
        "---",
        "",
        f"# {name}",
        "",
        "> Auto-scaffolded by `/update-infrastructure`.",
        "",
    ]

    page_path.write_text("\n".join(fm_lines))
    return {"created": True, "path": str(page_path), "slug": slug}


def fix_entity_ip(
    wiki_root: Path,
    name: str,
    new_ip: str,
    dry_run: bool = False,
) -> dict:
    """Patch the ``ip`` field in an entity page to *new_ip*.

    Args:
        wiki_root: Root of the wiki directory.
        name: Human-readable service name matching the entity slug.
        new_ip: The corrected IP address to write.
        dry_run: When True, compute the diff but do not write to disk.

    Returns:
        A dict with keys:

        ``fixed`` (bool)
            True if the repair succeeded (or would succeed in dry-run mode).
        ``dry_run`` (bool)
            Mirrors the *dry_run* argument.
        ``diff`` (str, only when dry_run=True)
            Line-level diff showing what would change.
        ``path`` (str, only when fixed=True and dry_run=False)
            Path to the entity page that was written.
        ``reason`` (str, only when fixed=False)
            Why the repair failed.
    """
    slug = _slugify(name)
    page_path = wiki_root / "entities" / f"{slug}.md"

    if not page_path.exists():
        return {"fixed": False, "dry_run": dry_run, "reason": f"entity page not found: entities/{slug}.md"}

    original = page_path.read_text()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    ip_pattern = re.compile(r"^(ip:\s*).*$", re.MULTILINE)
    updated_pattern = re.compile(r"^(updated:\s*).*$", re.MULTILINE)

    if ip_pattern.search(original):
        updated = ip_pattern.sub(rf"\g<1>{new_ip}", original)
    else:
        # Insert ip field just before the closing --- of the frontmatter
        end_fm = original.find("---", 3)
        if end_fm == -1:
            return {"fixed": False, "dry_run": dry_run, "reason": "could not locate frontmatter closing ---"}
        updated = original[:end_fm] + f"ip: {new_ip}\n" + original[end_fm:]

    if updated_pattern.search(updated):
        updated = updated_pattern.sub(rf"\g<1>{today}", updated)

    if dry_run:
        return {
            "fixed": True,
            "dry_run": True,
            "diff": _simple_diff(original, updated),
        }

    page_path.write_text(updated)
    return {"fixed": True, "dry_run": False, "path": str(page_path)}


def parse_all(config_dir: Path) -> dict:
    """Parse all homepage configs and return structured homelab data."""
    services = extract_services(config_dir)
    bookmarks = extract_bookmarks(config_dir)

    # Merge all entries for subdomain table
    all_entries = services + bookmarks

    # Build subdomain table
    subdomains = []
    for entry in all_entries:
        subdomain = extract_subdomain(entry)
        if subdomain:
            subdomains.append({
                "subdomain": subdomain,
                "href": entry["href"],
                "name": entry["name"],
                "site_monitor": entry.get("site_monitor"),
                "description": entry.get("description"),
                "group": entry["group"],
            })

    return {
        "services": services,
        "bookmarks": bookmarks,
        "subdomains": subdomains,
    }
