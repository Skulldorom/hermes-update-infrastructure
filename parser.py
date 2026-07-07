"""Parse Pulse API data and wiki entity pages into structured homelab data.

Pulse (rcourtman/Pulse) is the single source of truth for infrastructure data:
IPs, OS names, node assignments, CT/VM IDs, and real-time status.
"""

import os
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

try:
    from . import config, pulse_client
except ImportError:  # pragma: no cover - supports direct module loading in tests
    import config  # type: ignore
    import pulse_client  # type: ignore


def _slugify(name: str) -> str:
    """Convert a service/entity name to a lowercase hyphenated slug.

    e.g. "AdGuard Primary" → "adguard-primary", "Bento PDF" → "bento-pdf"
    """
    slug = name.lower().replace(" ", "-")
    return re.sub(r"[^a-z0-9-]", "", slug)


def _get_generic_infra_names() -> frozenset[str]:
    """Return the set of generic infra names to filter from missing-entity reports.

    Delegates to :func:`config.get_generic_infra_names` which resolves
    ``infrastructure-plugin.generic_infra_names`` in Hermes config.yaml,
    then the ``NOISE_FILTER_NAMES`` env var, then the built-in default set.
    """
    return config.get_generic_infra_names()


def _parse_server_aliases() -> dict[str, list[str]]:
    """Return the server alias mapping (short name → list of entity slugs).

    Delegates to :func:`config.get_server_aliases` which resolves
    ``infrastructure-plugin.server_aliases`` in Hermes config.yaml,
    then the ``SERVER_ALIASES`` env var, then an empty dict.
    """
    return config.get_server_aliases()


def _get_suppressed_entity_names() -> frozenset[str]:
    """Return service names to skip in the missing-entity-page report.

    Delegates to :func:`config.get_suppressed_entity_names` which resolves
    ``infrastructure-plugin.suppress_entity_check`` in Hermes config.yaml,
    then the ``SUPPRESS_ENTITY_CHECK`` env var, then an empty frozenset.
    """
    return config.get_suppressed_entity_names()


def _is_personal_device_entity(entity: dict) -> bool:
    """Return True when the entity page represents a personal device or person.

    Checks the ``type`` field and ``tags`` list in the frontmatter.
    """
    personal_types = config.get_personal_device_types()
    if str(entity.get("type", "")).lower() in personal_types:
        return True
    tags = entity.get("tags", [])
    if isinstance(tags, list):
        return any(str(t).lower() in personal_types for t in tags)
    return False


def _suggest_server_match(
    server: str,
    entity_pages: dict,
    alias_targets: set[str] | None = None,
) -> list[str]:
    """Return entity slugs that contain *server* as a substring.

    When *alias_targets* is provided, also includes matching alias targets
    in the suggestion list (useful when the user's short server name matches
    an alias but not the entity slug itself).

    Used to generate "Did you mean…?" suggestions for unmapped server fields.
    """
    server_slug = _slugify(str(server))
    if not server_slug:
        return []

    results = sorted(slug for slug in entity_pages if server_slug in slug)

    # Include alias targets that contain the server slug
    if alias_targets:
        alias_matches = sorted(a for a in alias_targets if server_slug in a)
        # Merge, deduplicate, preserve order
        seen: set[str] = set(results)
        for a in alias_matches:
            if a not in seen:
                results.append(a)
                seen.add(a)

    return results


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


# Well-known container name suffixes that indicate stack components.
# Stripped greedily from the end to group containers into stacks.
# e.g. "authentik-worker-1" → strip "-1" → "authentik-worker" → strip "-worker"
_STACK_SUFFIX_RE = re.compile(
    r"[-_](?:\d+|db|postgres|postgresql|redis|worker|proxy|server|cache|"
    r"broker|mq|queue|web|frontend|backend|api|scheduler|cron|beat|"
    r"migrate|init|seed|backup|restore|sidecar|exporter|collector)$",
    re.IGNORECASE,
)


def _strip_component_suffixes(slug: str) -> str:
    """Strip well-known stack component suffixes from a container slug.

    Strips greedily from the end: ``\"authentik-worker-1\"`` →
    ``\"authentik\"`` (via ``-1`` then ``-worker``).

    Returns the original slug unchanged when no suffixes match.
    """
    while True:
        stripped = _STACK_SUFFIX_RE.sub("", slug)
        if stripped == slug:
            break
        slug = stripped
    return slug


def _extract_pulse_containers(entity: dict) -> list[str]:
    """Extract the pulse_containers list from an entity frontmatter dict.

    Handles both YAML list and comma-separated string formats.
    Returns slugified container names.
    """
    raw = entity.get("pulse_containers")
    if not raw:
        return []
    if isinstance(raw, list):
        items = [str(c).strip() for c in raw if str(c).strip()]
    else:
        items = [c.strip() for c in str(raw).split(",") if c.strip()]
    return [_slugify(item) for item in items if _slugify(item)]


def _append_to_pulse_containers(wiki_root: Path, parent_slug: str, container_name: str) -> bool:
    """Append a container name to a parent entity's pulse_containers frontmatter.

    Reads the entity file, parses frontmatter, adds *container_name* to
    the ``pulse_containers`` list if not already present, and writes back.

    Returns True if the file was modified, False if the container was
    already listed or the parent page was not found.
    """
    page_path = wiki_root / "entities" / f"{parent_slug}.md"
    if not page_path.exists():
        return False

    original = page_path.read_text()
    fm = _parse_frontmatter(page_path)
    if not fm:
        return False

    existing = _extract_pulse_containers(fm)
    if _slugify(container_name) in existing:
        return False  # already there

    # Build the updated frontmatter
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    existing_display = [str(c).strip() for c in (
        fm.get("pulse_containers") if isinstance(fm.get("pulse_containers"), list)
        else str(fm.get("pulse_containers", "")).split(",")
    ) if str(c).strip()]
    existing_display.append(container_name)

    # Find the pulse_containers line and replace, or insert
    pc_pattern = re.compile(r"^(pulse_containers:\\s*).*$", re.MULTILINE)
    updated_pattern = re.compile(r"^(updated:\\s*).*$", re.MULTILINE)

    if pc_pattern.search(original):
        # YAML list format: pulse_containers: [a, b, c]
        yaml_list = "[" + ", ".join(existing_display) + "]"
        updated = pc_pattern.sub(rf"\\g<1>{yaml_list}", original)
    else:
        # Insert before the closing --- of the frontmatter
        end_fm = original.find("---", 3)
        if end_fm == -1:
            return False
        yaml_list = "[" + ", ".join(existing_display) + "]"
        new_line = f"pulse_containers: {yaml_list}\n"
        updated = original[:end_fm] + new_line + original[end_fm:]

    if updated_pattern.search(updated):
        updated = updated_pattern.sub(rf"\\g<1>{today}", updated)

    page_path.write_text(updated)
    return True


def _detect_stack_groups(
    services: list[dict],
    entity_pages: dict,
) -> set[str]:
    """Group sibling Docker containers into stacks and return covered slugs.

    Strips well-known suffixes and trailing numbers from container names,
    groups by the remaining prefix as the "stack name", and marks all
    containers in a group as covered when the stack name matches an entity
    slug or any ``pulse_containers`` entry.

    Returns a set of container slugs that should be excluded from the
    "missing entity pages" report.
    """
    entity_slugs = set(entity_pages.keys())

    # Collect all pulse_containers entries as a flat set of slugs
    pulse_container_slugs: set[str] = set()
    for entity in entity_pages.values():
        raw = entity.get("pulse_containers")
        if not raw:
            continue
        items = raw if isinstance(raw, list) else [
            s.strip() for s in str(raw).split(",")
        ]
        for item in items:
            slug = _slugify(str(item).strip())
            if slug:
                pulse_container_slugs.add(slug)

    covered: set[str] = set()
    stack_groups: dict[str, list[str]] = {}

    for svc in services:
        slug = _slugify(svc["name"])
        # Already covered by entity page or pulse_containers — skip
        if slug in entity_slugs or slug in pulse_container_slugs:
            continue
        # Strip well-known suffixes greedily from the end
        stack_name = _strip_component_suffixes(slug)
        # Only group when suffixes were actually stripped
        if stack_name and stack_name != slug:
            stack_groups.setdefault(stack_name, []).append(slug)

    # Mark containers as covered when their stack name resolves to an entity
    for stack_name, container_slugs in stack_groups.items():
        if stack_name in entity_slugs or stack_name in pulse_container_slugs:
            covered.update(container_slugs)
        # Also check slugified pulse_host values
        for entity in entity_pages.values():
            pulse_host = entity.get("pulse_host")
            if pulse_host and _slugify(str(pulse_host)) == stack_name:
                covered.update(container_slugs)
                break

    return covered


def _categorize_orphan(slug: str, entity: dict) -> str:
    """Categorize an orphaned entity page for actionable cleanup output.

    Categories (in descending order of actionability):
    * ``truly_orphaned`` — no connection to Pulse at all; candidate for removal
    * ``has_pulse_host_match`` — entity has ``pulse_host`` set; may resolve with tweaks
    * ``has_pulse_container_match`` — entity has ``pulse_containers`` set; parent of stacks
    * ``networking_device`` — entity category is networking; expected, keep
    * ``infrastructure_node`` — entity category is infrastructure; expected, keep
    """
    fm_category = str(entity.get("category", "")).lower()

    if "infrastructure" in fm_category or "infra" in fm_category:
        return "infrastructure_node"
    if "networking" in fm_category or "network" in fm_category:
        return "networking_device"
    if entity.get("pulse_host"):
        return "has_pulse_host_match"
    if entity.get("pulse_containers"):
        return "has_pulse_container_match"
    return "truly_orphaned"


def cross_reference(data: dict, entity_pages: dict | None) -> dict:
    """Cross-reference Pulse services against wiki entity pages.

    Args:
        data: Structured data from :func:`parse_all` (Pulse API).
        entity_pages: Dict returned by :func:`load_entity_pages`, or ``None``
            when the entities directory was not found.

    Returns:
        A dict with the following keys:

        ``entities_dir_found`` (bool)
            False when *entity_pages* is ``None``.
        ``missing_entity_pages`` (list[dict])
            Services with no matching entity page (generic infra names filtered out).
        ``ip_drift`` (list[dict])
            Services where the Pulse live IP differs from the entity ``ip`` field.
            Each item has keys ``name``, ``pulse_ip``, ``entity_ip``.
        ``orphaned_entity_pages`` (list[str])
            Entity page slugs whose name does not match any Pulse resource
            (personal-device / person pages filtered out).
        ``unmapped_hosts`` (list[dict])
            Services whose ``host:`` field does not map to any entity, with
            fuzzy ``suggestions`` for likely matches.
    """
    if entity_pages is None:
        return {"entities_dir_found": False}

    services = data.get("services", [])
    generic_names = _get_generic_infra_names()
    suppressed_names = _get_suppressed_entity_names()

    # Build a set of service slugs for fast lookup
    service_slugs = {_slugify(svc["name"]) for svc in services}

    # Parse host→entity aliases and build a reverse lookup
    server_aliases = _parse_server_aliases()
    # alias_targets: set of all entity slugs referenced by any alias
    alias_targets: set[str] = set()
    for targets in server_aliases.values():
        alias_targets.update(targets)

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

    # Expand known_hosts with alias keys and their resolved slugs
    for alias_key, targets in server_aliases.items():
        known_hosts.add(_slugify(alias_key))
        known_hosts.update(targets)

    # Expand known_hosts and service_slugs with pulse_host / pulse_containers
    # frontmatter — entities can declare their Pulse name mapping directly,
    # eliminating the need for opaque server_aliases config entries.
    _pulse_container_slugs: set[str] = set()
    for _slug, entity in entity_pages.items():
        pulse_host = entity.get("pulse_host")
        if pulse_host:
            known_hosts.add(_slugify(str(pulse_host)))
        pulse_containers_raw = entity.get("pulse_containers")
        if pulse_containers_raw:
            if isinstance(pulse_containers_raw, list):
                containers = [str(c).strip() for c in pulse_containers_raw if str(c).strip()]
            elif isinstance(pulse_containers_raw, str):
                containers = [c.strip() for c in pulse_containers_raw.split(",") if c.strip()]
            else:
                containers = []
            for container_name in containers:
                container_slug = _slugify(container_name)
                _pulse_container_slugs.add(container_slug)
                service_slugs.add(container_slug)
                known_hosts.add(container_slug)

    # Run stack detection heuristic — strip component suffixes from Docker
    # container names and mark containers as covered when their parent stack
    # has an entity page or pulse_containers entry.
    stack_covered = _detect_stack_groups(services, entity_pages)
    service_slugs.update(stack_covered)

    missing_entity_pages: list[dict] = []
    ip_drift: list[dict] = []
    unmapped_hosts: list[dict] = []

    for svc in services:
        slug = _slugify(svc["name"])
        entity = entity_pages.get(slug)

        if entity is None:
            # Skip well-known generic infrastructure names — they are stack/type
            # labels, not specific hosts, so a missing entity page is expected.
            if slug not in generic_names:
                # Skip services already covered by pulse_containers or stack
                # detection — they belong to a parent entity page.
                if slug in _pulse_container_slugs or slug in stack_covered:
                    continue
                # Skip services explicitly suppressed via SUPPRESS_ENTITY_CHECK
                if svc["name"].lower() not in suppressed_names:
                    # Skip services whose host field maps to a known entity
                    # (the service is documented via its parent's entity page)
                    host_val = svc.get("host")
                    host_is_known = False
                    if host_val:
                        host_slug = _slugify(str(host_val))
                        # Check direct match + alias resolution
                        if host_slug in known_hosts:
                            host_is_known = True
                        else:
                            aliases = server_aliases.get(host_slug, [])
                            if any(a in known_hosts for a in aliases):
                                host_is_known = True
                    if not host_is_known:
                        missing_entity_pages.append(
                            {"name": svc["name"], "ip": svc.get("ip", "")}
                        )
        else:
            # IP drift: compare Pulse live IP against entity page ip field
            entity_ip = entity.get("ip")
            pulse_ip = svc.get("ip") or ""
            if entity_ip and pulse_ip:
                if str(pulse_ip) != str(entity_ip):
                    ip_drift.append({
                        "name": svc["name"],
                        "pulse_ip": pulse_ip,
                        "entity_ip": str(entity_ip),
                    })

        # Unmapped host field — resolve aliases before flagging
        host = svc.get("host")
        if host:
            host_slug = _slugify(str(host))
            if host_slug not in known_hosts:
                # Check if host resolves via alias
                aliases = server_aliases.get(host_slug, [])
                if not any(a in known_hosts for a in aliases):
                    suggestions = _suggest_server_match(host, entity_pages, alias_targets)
                    unmapped_hosts.append({
                        "name": svc["name"],
                        "host": host,
                        "suggestions": suggestions,
                    })

    # Orphaned entity pages: slugs not matched by any service slug.
    # Personal device / people pages are silently excluded.
    # Categorized for actionable cleanup output.
    orphaned: list[dict] = []
    for slug, entity in entity_pages.items():
        if slug in service_slugs or _is_personal_device_entity(entity):
            continue
        category = _categorize_orphan(slug, entity)
        orphaned.append({"path": f"entities/{slug}.md", "category": category})

    # Sort: truly_orphaned first, then infrastructure/nodes last
    _CATEGORY_ORDER = {
        "truly_orphaned": 0,
        "has_pulse_host_match": 1,
        "has_pulse_container_match": 2,
        "networking_device": 3,
        "infrastructure_node": 4,
    }
    orphaned.sort(key=lambda o: (_CATEGORY_ORDER.get(o["category"], 99), o["path"]))

    return {
        "entities_dir_found": True,
        "missing_entity_pages": missing_entity_pages,
        "ip_drift": ip_drift,
        "orphaned_entity_pages": orphaned,
        "unmapped_hosts": unmapped_hosts,
    }


def scaffold_entity_page(
    wiki_root: Path,
    name: str,
    ip: str = "",
    host: str = "",
    os: str = "",
    vmid: str | int = "",
    entity_pages: dict | None = None,
) -> dict:
    """Scaffold a new entity page for a service that has no existing page.

    Args:
        wiki_root: Root of the wiki directory.
        name: Human-readable service name (e.g. "Bento PDF").
        ip: IP address from Pulse live data.
        host: Proxmox node name or Docker host name from Pulse.
        os: OS name from Pulse (e.g. "Debian GNU/Linux 12").
        vmid: CT/VM ID from Pulse.
        entity_pages: Optional dict from :func:`load_entity_pages`.  When
            provided, checks for stack membership before creating —
            containers belonging to an existing parent stack are appended
            to the parent's ``pulse_containers`` frontmatter instead of
            getting a standalone page.

    Returns:
        A dict with keys:

        ``created`` (bool)
            True if a new file was written.
        ``path`` (str)
            Absolute path to the entity page.
        ``slug`` (str, only when created)
            The slugified filename stem.
        ``reason`` (str, only when not created)
            Why the file was not created.  Possible reasons:

            * ``"already exists"`` — file already present
            * ``"belongs to stack: <parent>"`` — container is listed in
              parent's ``pulse_containers``
            * ``"appended to <parent>/pulse_containers"`` — container was
              added to the parent entity as a stack member
    """
    slug = _slugify(name)
    entities_dir = wiki_root / "entities"
    entities_dir.mkdir(parents=True, exist_ok=True)

    page_path = entities_dir / f"{slug}.md"
    if page_path.exists():
        return {"created": False, "path": str(page_path), "reason": "already exists"}

    # ── Stack-aware check: avoid entity page sprawl for stack components ──
    if entity_pages is not None:
        # Check 1: container already listed in another entity's pulse_containers
        for parent_slug, entity in entity_pages.items():
            if slug in _extract_pulse_containers(entity):
                return {
                    "created": False,
                    "path": str(page_path),
                    "reason": f"belongs to stack: {parent_slug}",
                }

        # Check 2: container can be grouped via suffix stripping heuristic
        stack_name = _strip_component_suffixes(slug)
        if stack_name and stack_name != slug and stack_name in entity_pages:
            appended = _append_to_pulse_containers(wiki_root, stack_name, name)
            return {
                "created": False,
                "path": str(wiki_root / "entities" / f"{stack_name}.md"),
                "reason": (
                    f"appended to {stack_name}/pulse_containers (stack member)"
                    if appended else
                    f"stack {stack_name} exists but append failed"
                ),
            }

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

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
    if host:
        fm_lines.append(f"host: {_slugify(str(host))}")
    if os:
        fm_lines.append(f"os: {os}")
    if vmid:
        fm_lines.append(f"vmid: {vmid}")
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


def parse_all() -> dict:
    """Fetch data from the Pulse API and return structured homelab data.

    Uses ``PULSE_RESOURCE_TYPES`` (env var or config default) to filter
    resource types returned by Pulse.

    Returns:
        A dict with keys:

        ``services`` (list[dict])
            Flat list of service dicts built from Pulse resources.
            Each dict has: ``name``, ``group``, ``ip``, ``host``, ``vmid``,
            ``os``, ``status``, ``type``, ``parent_id``, ``tags``.
        ``subdomains`` (list[dict])
            Empty list — subdomains are no longer auto-extracted from URLs.
            Store ``urls:`` in entity page frontmatter for URL tracking.
    """
    resource_types = os.environ.get("PULSE_RESOURCE_TYPES", config.PULSE_RESOURCE_TYPES)
    resources = pulse_client.fetch_resources(type_filter=resource_types)
    services = pulse_client.build_service_list(resources)
    return {
        "services": services,
        "subdomains": [],
    }
