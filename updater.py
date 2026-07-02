"""Update wiki pages from parsed homepage data.

Key design: auto-generated sections are wrapped in:
    <!-- HOMEPAGE_AUTO: section_name -->
    ... generated content ...
    <!-- /HOMEPAGE_AUTO: section_name -->

Everything OUTSIDE these markers is preserved verbatim.
Homepage is the authority — if a service is removed from homepage,
it's removed from the auto sections. Added services appear. Changed
services update. Content never in homepage stays exactly as-is.
"""

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path


def _hash_block(text: str) -> str:
    """Short hash of a text block for change detection."""
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def _build_service_groups(data: dict) -> str:
    """Generate the Service Groups section from homepage data."""
    services = data.get("services", [])
    if not services:
        return "*(No services found in homepage config)*\n"

    # Group by group_name, preserving order from homepage
    groups: dict[str, list[dict]] = {}
    group_order: list[str] = []
    for svc in services:
        g = svc.get("group", "Other")
        if g not in groups:
            groups[g] = []
            group_order.append(g)
        groups[g].append(svc)

    lines = []
    for group_name in group_order:
        svcs = groups[group_name]
        lines.append(f"### {group_name}\n")

        for svc in svcs:
            name = svc["name"]
            desc = svc.get("description") or ""
            href = svc.get("href") or ""
            container = svc.get("container") or ""
            server = svc.get("server") or ""
            widget = svc.get("widget_type") or ""
            monitor = svc.get("site_monitor") or ""

            parts = [f"- **{name}**"]
            if desc:
                parts.append(f" — {desc}")
            parts.append(f"  [{href}]({href})" if href else "")
            extras = []
            if monitor:
                extras.append(f"monitor: `{monitor}`")
            if container:
                extras.append(f"container: `{container}`")
            if server:
                extras.append(f"server: `{server}`")
            if widget:
                extras.append(f"widget: `{widget}`")
            if extras:
                parts.append("  (" + ", ".join(extras) + ")")

            lines.append("".join(parts))

        lines.append("")

    return "\n".join(lines)


def _build_subdomain_table(data: dict) -> str:
    """Generate the Domain/Subdomain table from homepage data."""
    subdomains = data.get("subdomains", [])
    if not subdomains:
        return "*(No subdomains found)*\n"

    lines = [
        "| Subdomain | Service | URL | Monitor | Group |",
        "|-----------|---------|-----|---------|-------|",
    ]

    # Sort by subdomain name
    for entry in sorted(subdomains, key=lambda s: s["subdomain"]):
        sub = entry["subdomain"]
        name = entry["name"]
        href = entry.get("href", "")
        monitor = entry.get("site_monitor", "")
        group = entry.get("group", "")

        monitor_cell = f"`{monitor}`" if monitor else "—"
        href_cell = f"<{href}>" if href else "—"

        lines.append(f"| {sub} | {name} | {href_cell} | {monitor_cell} | {group} |")

    return "\n".join(lines) + "\n"


def _build_cross_reference_section(data: dict) -> str:
    """Generate the Cross-Reference Findings section."""
    cross_ref = data.get("cross_ref")

    if cross_ref is None:
        return "*(Cross-reference skipped — no data available)*\n"

    if not cross_ref.get("entities_dir_found", False):
        return (
            "*(Cross-reference skipped — entity pages directory not found.*\n"
            "*Create `entities/<name>.md` files with YAML frontmatter to enable checks.)*\n"
        )

    missing = cross_ref.get("missing_entity_pages", [])
    ip_drift = cross_ref.get("ip_drift", [])
    orphaned = cross_ref.get("orphaned_entity_pages", [])
    unmapped_servers = cross_ref.get("unmapped_servers", [])

    if not missing and not ip_drift and not orphaned and not unmapped_servers:
        return "✅ All clear — no issues found\n"

    lines: list[str] = []

    if missing:
        lines.append("⚠️ New services without entity pages:")
        for svc in missing:
            href = svc.get("href", "")
            href_part = f" (href: {href})" if href else ""
            lines.append(f"  - {svc['name']}{href_part} — no entity page found")
            lines.append(
                f'    → Run `/update-infrastructure create "{svc["name"]}"` to scaffold one'
            )
        lines.append("")

    if ip_drift:
        lines.append("⚠️ IP drift detected:")
        for item in ip_drift:
            lines.append(
                f"  - {item['name']}: siteMonitor=`{item['site_monitor']}`,"
                f" entity ip=`{item['entity_ip']}` (MISMATCH)"
            )
            lines.append(
                f'    → Run `/update-infrastructure fix-ip "{item["name"]}"` to repair'
            )
        if len(ip_drift) > 1:
            lines.append("    → Run `/update-infrastructure fix all` to repair all at once")
        lines.append("")

    if orphaned:
        lines.append("ℹ️ Orphaned entity pages (not in homepage):")
        for page in orphaned:
            lines.append(f"  - {page}")
        lines.append("  → Run `/update-infrastructure cleanup` for details")
        lines.append("")

    if unmapped_servers:
        lines.append("⚠️ Unmapped server fields:")
        for item in unmapped_servers:
            suggestions = item.get("suggestions", [])
            suggestion_str = ""
            if suggestions:
                quoted = ", ".join(f"`{s}`" for s in suggestions)
                suggestion_str = f" — did you mean {quoted}?"
            lines.append(
                f"  - {item['name']}: server=`{item['server']}`{suggestion_str}"
            )
        lines.append("")

    return "\n".join(lines)


AUTO_SECTION_TEMPLATES = {
    "service_groups": _build_service_groups,
    "subdomains": _build_subdomain_table,
    "cross_reference": _build_cross_reference_section,
}

MARKER_START = "<!-- HOMEPAGE_AUTO:"
MARKER_END = "<!-- /HOMEPAGE_AUTO:"


def update_wiki_page(page_path: str, data: dict, dry_run: bool = False) -> dict:
    """Update the homelab wiki page with fresh homepage data.

    Args:
        page_path: Path to the markdown wiki page
        data: Parsed homepage data from parser.parse_all()
        dry_run: If True, return diff without writing

    Returns:
        {
            "changed": bool,
            "sections_updated": ["service_groups", ...],
            "sections_unchanged": ["subdomains", ...],
            "sections_added": ["new_section", ...],
            "hash_before": "...",
            "hash_after": "...",
        }
    """
    path = Path(page_path)
    if not path.exists():
        # Page doesn't exist yet — generate the full page
        new_content = _generate_full_page(data)
        if not dry_run:
            path.write_text(new_content)
        return {
            "changed": True,
            "sections_updated": list(AUTO_SECTION_TEMPLATES.keys()),
            "sections_unchanged": [],
            "sections_added": list(AUTO_SECTION_TEMPLATES.keys()),
            "hash_before": "none",
            "hash_after": _hash_block(new_content),
        }

    original = path.read_text()
    hash_before = _hash_block(original)

    # Parse existing auto sections
    updated = original
    sections_updated = []
    sections_unchanged = []
    sections_added = []

    for section_name, builder in AUTO_SECTION_TEMPLATES.items():
        new_block = builder(data)
        start_tag = f"{MARKER_START} {section_name} -->"
        end_tag = f"{MARKER_END} {section_name} -->"

        if start_tag in updated and end_tag in updated:
            # Replace existing auto section
            old_block = _extract_block(updated, start_tag, end_tag)
            if old_block is not None and old_block.strip() == new_block.strip():
                sections_unchanged.append(section_name)
            else:
                updated = _replace_block(updated, start_tag, end_tag, new_block)
                sections_updated.append(section_name)
        else:
            # Section doesn't exist yet — append it
            updated += f"\n\n{start_tag}\n{new_block}\n{end_tag}\n"
            sections_added.append(section_name)

    hash_after = _hash_block(updated)

    if updated != original:
        if not dry_run:
            path.write_text(updated)
        return {
            "changed": True,
            "sections_updated": sections_updated,
            "sections_unchanged": sections_unchanged,
            "sections_added": sections_added,
            "hash_before": hash_before,
            "hash_after": hash_after,
        }

    return {
        "changed": False,
        "sections_updated": [],
        "sections_unchanged": list(AUTO_SECTION_TEMPLATES.keys()),
        "sections_added": [],
        "hash_before": hash_before,
        "hash_after": hash_after,
    }


def _extract_block(text: str, start_tag: str, end_tag: str) -> str | None:
    """Extract the content between start_tag and end_tag markers."""
    start_idx = text.find(start_tag)
    if start_idx == -1:
        return None
    start_idx += len(start_tag)

    end_idx = text.find(end_tag, start_idx)
    if end_idx == -1:
        return None

    return text[start_idx:end_idx]


def _replace_block(text: str, start_tag: str, end_tag: str, new_block: str) -> str:
    """Replace the content between start_tag and end_tag with new_block."""
    start_idx = text.find(start_tag)
    end_idx = text.find(end_tag, start_idx)

    if start_idx == -1 or end_idx == -1:
        return text

    before = text[:start_idx + len(start_tag)]
    after = text[end_idx:]
    return f"{before}\n{new_block}\n{after}"


def _generate_full_page(data: dict) -> str:
    """Generate a complete new homelab-infrastructure page from scratch."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    lines = [
        "---",
        "title: Homelab Infrastructure",
        f"created: {today}",
        f"updated: {today}",
        "type: guide",
        "tags: [self-hosted, server, linux, reference]",
        "confidence: high",
        "---",
        "",
        "# Homelab Infrastructure",
        "",
        "> Auto-generated from [homepage dashboard config](https://github.com/Skulldorom/homepage).",
        "> Use `/update-infrastructure` to refresh.",
        "",
        "---",
        "",
        "## Service Groups",
        "",
        f"{MARKER_START} service_groups -->",
        _build_service_groups(data),
        f"{MARKER_END} service_groups -->",
        "",
        "---",
        "",
        "## Domain / Subdomain Map",
        "",
        f"{MARKER_START} subdomains -->",
        _build_subdomain_table(data),
        f"{MARKER_END} subdomains -->",
        "",
        "---",
        "",
        "## Cross-Reference Findings",
        "",
        f"{MARKER_START} cross_reference -->",
        _build_cross_reference_section(data),
        f"{MARKER_END} cross_reference -->",
        "",
        "---",
        "",
        "## Manual Sections",
        "",
        "> Sections below are maintained manually and are not affected by `/update-infrastructure`.",
        "> Add network topology, machine details, DHCP config, Tailscale info, etc. here.",
        "",
    ]

    return "\n".join(lines)


def update_wiki_log(log_path: str, result: dict) -> None:
    """Append a log entry to the wiki log."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    parts = []
    if result["sections_updated"]:
        parts.append(f"updated: {', '.join(result['sections_updated'])}")
    if result["sections_added"]:
        parts.append(f"added: {', '.join(result['sections_added'])}")
    if not parts:
        return

    entry = (
        f"\n## [{today}] update | homelab-infrastructure (homepage sync)\n"
        f"- {'; '.join(parts)}\n"
        f"- homepage source: https://github.com/Skulldorom/homepage\n"
    )

    log_file = Path(log_path)
    if log_file.exists():
        with open(log_file, "a") as f:
            f.write(entry)
