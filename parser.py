"""Parse homepage YAML configs into structured homelab data."""

import re
import subprocess
import tempfile
from pathlib import Path

import yaml


HOMEPAGE_REPO = "https://github.com/Skulldorom/homepage"
REPO_CACHE = Path(tempfile.gettempdir()) / "hermes-homepage-sync"


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


def extract_subdomain(service: dict) -> str | None:
    """Extract subdomain from href. Returns e.g. 'prox' or None."""
    href = service.get("href", "")
    if not href:
        return None

    # Match subdomain from *.skulldorom.xyz or *.skulldorom.com
    match = re.search(r"https?://([a-z0-9-]+)\.skulldorom\.(xyz|com)", href)
    if match:
        return match.group(1)

    return None


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
