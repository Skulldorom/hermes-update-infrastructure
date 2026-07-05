"""Parse wiki entity pages into structured machine/topology data.

Reads ``<wiki_root>/entities/*.md`` frontmatter and returns a normalised
machine list suitable for topology diagram generation.
"""

from pathlib import Path

import yaml


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


def _normalise_category(fm: dict) -> str:
    """Derive a normalised category string from entity frontmatter.

    Checks ``category`` first, then ``type``, then falls back to ``"device"``.
    """
    raw = str(fm.get("category") or fm.get("type") or "device").lower()

    # Map common type aliases to canonical categories
    if any(k in raw for k in ("infra", "server", "proxmox", "nas", "storage", "hypervisor")):
        return "infrastructure"
    if any(k in raw for k in ("network", "router", "switch", "ap", "wireless", "firewall")):
        return "networking"
    if raw in ("container", "vm", "lxc", "virtual"):
        return "container"
    return raw  # preserve as-is (e.g. "device", "person", …)


def _normalise_services(raw) -> list[str]:
    """Return a flat list of service name strings from various YAML shapes."""
    if not raw:
        return []
    if isinstance(raw, str):
        return [s.strip() for s in raw.split(",") if s.strip()]
    if isinstance(raw, list):
        return [str(s).strip() for s in raw if str(s).strip()]
    return []


def parse_entity_pages(entities_dir: str) -> dict:
    """Read entity ``.md`` files and return a normalised machine list.

    Args:
        entities_dir: Absolute path to the ``entities/`` directory.

    Returns:
        A dict with a single key ``"machines"`` whose value is a list of
        machine dicts::

            {
              "name": str,          # display name (frontmatter ``title`` or stem)
              "slug": str,          # filename stem (lowercased)
              "ip": str,            # IPv4 from frontmatter ``ip``, or ""
              "host": str,          # parent host slug from ``host`` field, or ""
              "category": str,      # normalised: infrastructure | networking | container | device | …
              "os": str,            # ``os`` field, or ""
              "services": list[str],# ``services`` list
              "tailscale_ip": str,  # ``tailscale_ip`` field, or ""
            }

        Returns ``{"machines": []}`` when the directory does not exist or
        contains no readable entity pages.
    """
    entities_path = Path(entities_dir)
    if not entities_path.exists():
        return {"machines": []}

    machines: list[dict] = []
    for page in sorted(entities_path.glob("*.md")):
        fm = _parse_frontmatter(page)

        # Skip pages that carry no meaningful data
        if not fm:
            continue

        name = str(fm.get("title") or page.stem)
        machine: dict = {
            "name": name,
            "slug": page.stem,
            "ip": str(fm.get("ip") or ""),
            "host": str(fm.get("host") or ""),
            "category": _normalise_category(fm),
            "os": str(fm.get("os") or ""),
            "services": _normalise_services(fm.get("services")),
            "tailscale_ip": str(fm.get("tailscale_ip") or ""),
        }
        machines.append(machine)

    return {"machines": machines}
