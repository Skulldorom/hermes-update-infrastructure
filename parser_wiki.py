"""Parse wiki entity pages into structured machine/topology data.

Reads ``<wiki_root>/entities/*.md`` frontmatter and returns a normalised
machine list suitable for topology diagram generation.

Also parses inline container / VM tables from entity page bodies
(e.g. ``## Containers`` headings followed by pipe tables) to auto-generate
child machine nodes.
"""

import re
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


def _slugify(name: str) -> str:
    """Convert a display name to a lowercase hyphenated slug."""
    slug = name.lower().replace(" ", "-")
    return re.sub(r"[^a-z0-9-]", "", slug)


# ---------------------------------------------------------------------------
# Inline table parser
# ---------------------------------------------------------------------------

# Table headings we recognise and auto-parse
_TABLE_HEADINGS = re.compile(
    r"^##\s+(Containers|Docker|VMs?\s*/\s*LXCs?)\s*\n"
    r"((?:\|.*\|\n)+)",
    re.MULTILINE,
)

# Column patterns for VMs / LXCs tables — col 1 is often an IPv4
_IP_PATTERN = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")

# Wiki-link stripper: [[name]] or [[name|alias]] → name
_WIKI_LINK = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


def _extract_vm_row(row: list[str], _header: list[str], child: dict) -> None:
    """Enrich a child dict from a VM / LXC table row.

    If column 1 looks like an IP, use it; column 2 (Service) becomes
    the child's services list.
    """
    if len(row) >= 2 and _IP_PATTERN.match(row[1].strip()):
        child["ip"] = row[1].strip()
        # Column 2 is the service / linked page
        if len(row) >= 3 and row[2].strip():
            service = _WIKI_LINK.sub(r"\1", row[2].strip())
            child["services"] = [service]
    elif len(row) >= 2:
        # No IP in col 1 — treat col 1 as a service name
        service = _WIKI_LINK.sub(r"\1", row[1].strip())
        if service:
            child["services"] = [service]


def _split_table_row(line: str) -> list[str]:
    """Split a pipe row without treating pipes inside wiki links as delimiters."""
    protected: list[str] = []

    def protect(match: re.Match) -> str:
        protected.append(match.group(0))
        return f"__WIKI_LINK_{len(protected) - 1}__"

    safe_line = _WIKI_LINK.sub(protect, line)
    cells = [cell.strip() for cell in safe_line.split("|")[1:-1]]
    for index, cell in enumerate(cells):
        for link_index, link in enumerate(protected):
            cell = cell.replace(f"__WIKI_LINK_{link_index}__", link)
        cells[index] = cell
    return cells


def _parse_body_tables(
    body: str, parent_slug: str, parent_ip: str, existing_slugs: set[str]
) -> list[dict]:
    """Scan an entity page body for inline container/VM tables.

    Recognises ``## Containers``, ``## Docker``, and ``## VMs / LXCs``
    headings followed by a pipe table.  Each data row becomes a child
    machine dict with ``host`` set to *parent_slug*.

    Rows whose slugified name already exists in *existing_slugs*
    (i.e. they have their own entity page) are silently skipped to
    avoid duplicates.

    Args:
        body: The markdown body text (everything after frontmatter).
        parent_slug: Slug of the enclosing entity page.
        parent_ip: IP address to inherit for children without their own IP.
        existing_slugs: Set of all known entity-page slugs to dedupe against.

    Returns:
        A list of child machine dicts (same shape as
        :func:`parse_entity_pages`).
    """
    children: list[dict] = []

    for match in _TABLE_HEADINGS.finditer(body):
        heading = match.group(1).strip()
        table_text = match.group(2)

        # Split into rows, skip the separator line
        rows: list[list[str]] = []
        for line in table_text.strip().split("\n"):
            line = line.strip()
            if not line.startswith("|"):
                continue
            cells = _split_table_row(line)
            # Skip separator rows (all dashes/spaces/colons, e.g. |---|:---:|---|)
            if all(re.fullmatch(r"[- :]*", c) for c in cells):
                continue
            rows.append(cells)

        if len(rows) < 2:  # need header + ≥1 data row
            continue

        header = [h.lower().replace(" ", "") for h in rows[0]]
        data_rows = rows[1:]

        is_vm_table = "vm" in heading.lower() or "lxc" in heading.lower()

        for row in data_rows:
            if not row or not row[0].strip():
                continue

            name = _WIKI_LINK.sub(r"\1", row[0].strip())
            slug = _slugify(name)

            if slug in existing_slugs:
                continue  # already have a dedicated entity page

            child: dict = {
                "name": name,
                "slug": slug,
                "ip": parent_ip,
                "host": parent_slug,
                "category": "container",
                "os": "",
                "services": [],
                "tailscale_ip": "",
            }

            if is_vm_table:
                # VMs / LXCs: col 1 may be an IP, col 2 may be the
                # service name (with an optional IP column header).
                _extract_vm_row(row, header, child)
            else:
                # Containers / Docker: last column is Purpose (services)
                if len(row) > 1 and row[-1].strip():
                    purpose = _WIKI_LINK.sub(r"\1", row[-1].strip())
                    child["services"] = [purpose]

            children.append(child)

    return children


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

    # ── Second pass: parse inline container / VM tables ──────────
    # Only dedupe against actual entity-page slugs — a container
    # named "log-viewer" on docker-host is distinct from "log-viewer" on
    # docker-crucial, so we don't cross-dedupe table children.
    entity_slugs = {m["slug"] for m in machines}
    all_slugs = set(entity_slugs)
    table_children: list[dict] = []

    for page in sorted(entities_path.glob("*.md")):
        try:
            text = page.read_text()
        except OSError:
            continue
        if not text.startswith("---"):
            continue
        fm_end = text.find("---", 3)
        if fm_end == -1:
            continue
        body = text[fm_end + 3 :]

        parent_slug = page.stem
        parent_ip = ""
        for m in machines:
            if m["slug"] == parent_slug:
                parent_ip = m["ip"]
                break

        children = _parse_body_tables(body, parent_slug, parent_ip, entity_slugs)

        # Handle slug collisions: if a table child's slug already
        # exists in *all_slugs* (from a previous table's child with
        # a different parent), suffix it with the parent slug.
        for ch in children:
            slug = ch["slug"]
            if slug in all_slugs:
                new_slug = f"{slug}-{parent_slug}"
                ch["slug"] = new_slug
                ch["name"] = f"{ch['name']} ({parent_slug})"
                slug = new_slug
            all_slugs.add(slug)

        table_children.extend(children)

    machines.extend(table_children)

    return {"machines": machines}
