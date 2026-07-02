"""Parse homepage YAML configs into structured homelab data."""

import os
import re
import subprocess
import tempfile
from pathlib import Path

import yaml


HOMEPAGE_REPO = "https://github.com/Skulldorom/homepage"
REPO_CACHE = Path(tempfile.gettempdir()) / "hermes-homepage-sync"

# Default subdomain patterns — each pattern must use group(1) to capture the subdomain label.
# Override by setting SUBDOMAIN_PATTERNS to a comma-separated list of regex strings.
_DEFAULT_SUBDOMAIN_PATTERNS = [r"https?://([a-z0-9-]+)\.skulldorom\.(xyz|com)"]


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
            Services with no matching entity page.
        ``ip_drift`` (list[dict])
            Services where the siteMonitor IP differs from the entity ``ip``.
        ``orphaned_entity_pages`` (list[str])
            Entity page slugs whose name does not match any homepage service.
        ``unmapped_servers`` (list[dict])
            Services whose ``server:`` field does not map to any entity.
    """
    if entity_pages is None:
        return {"entities_dir_found": False}

    services = data.get("services", [])

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

        # Unmapped server field
        server = svc.get("server")
        if server and _slugify(str(server)) not in known_hosts:
            unmapped_servers.append({"name": svc["name"], "server": server})

    # Orphaned entity pages: slugs not matched by any service slug
    orphaned = [
        f"entities/{slug}.md"
        for slug in entity_pages
        if slug not in service_slugs
    ]

    return {
        "entities_dir_found": True,
        "missing_entity_pages": missing_entity_pages,
        "ip_drift": ip_drift,
        "orphaned_entity_pages": sorted(orphaned),
        "unmapped_servers": unmapped_servers,
    }


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
