"""Write auto-generated infrastructure data to a dedicated file.

Key design: auto-generated data is written to a separate file
(``<wiki_root>/raw/sources/pulse-infrastructure-data.md``).  The human-readable
wiki page (``guides/homelab-infrastructure.md``) is never touched by this
module — the Hermes agent reads the generated file and updates the wiki
page surgically.
"""

import hashlib
from datetime import datetime, timezone
from pathlib import Path

try:
    from . import config
except ImportError:  # pragma: no cover - supports direct module loading in tests
    import config  # type: ignore

def _hash_block(text: str) -> str:
    """Short hash of a text block for change detection."""
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def _build_service_groups(data: dict) -> str:
    """Generate the Service Groups section from Pulse data."""
    services = data.get("services", [])
    if not services:
        return "*(No services found in Pulse)*\n"

    # Group by platformType, preserving order from Pulse
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
            ip = svc.get("ip") or ""
            host = svc.get("host") or ""
            vmid = svc.get("vmid") or ""
            os_name = svc.get("os") or ""
            status = svc.get("status") or ""
            svc_type = svc.get("type") or ""

            status_icon = "🟢" if status == "running" else ("🔴" if status in ("stopped", "offline") else "⚪")
            parts = [f"- {status_icon} **{name}**"]

            extras = []
            if ip:
                extras.append(f"ip: `{ip}`")
            if host:
                extras.append(f"host: `{host}`")
            if vmid:
                extras.append(f"vmid: `{vmid}`")
            if os_name:
                extras.append(f"os: {os_name}")
            if svc_type:
                extras.append(f"type: {svc_type}")
            if extras:
                parts.append("  (" + ", ".join(extras) + ")")

            lines.append("".join(parts))

        lines.append("")

    return "\n".join(lines)


def _build_subdomain_table(data: dict, npm_suggestions: list[dict] | None = None) -> str:
    """Generate the Domain/Subdomain table.

    Subdomains are no longer auto-extracted from Pulse data (Pulse does not
    know about reverse-proxy URLs).  When *npm_suggestions* is provided from
    the NPM sync, unmatched NPM proxy hosts are included as ``[NPM]`` rows
    so the agent can add ``urls:`` frontmatter or update the domains page.
    """
    subdomains = data.get("subdomains", [])
    suggestions = npm_suggestions or []

    if not subdomains and not suggestions:
        return (
            "*(No subdomain data available — Pulse does not expose reverse-proxy URLs.*\\n"
            "*Add `urls:` frontmatter to entity pages to track service URLs.)*\\n"
        )

    lines = [
        "| Subdomain | Service | URL | Group |",
        "|-----------|---------|-----|-------|",
    ]

    for entry in sorted(subdomains, key=lambda s: s["subdomain"]):
        sub = entry["subdomain"]
        name = entry["name"]
        href = entry.get("href", "")
        group = entry.get("group", "")

        href_cell = f"<{href}>" if href else "—"
        lines.append(f"| {sub} | {name} | {href_cell} | {group} |")

    # NPM-sourced suggestions: proxy hosts found in NPM but not yet in the wiki
    if suggestions:
        lines.append("| *(NPM suggestions)* | | | |")
        for item in suggestions:
            sub = item.get("subdomain", "")
            domain = item.get("domain", "")
            fwd = f"{item.get('forward_host', '')}:{item.get('forward_port', '')}"
            lines.append(f"| {sub} | [NPM] {domain} | — | NPM → {fwd} |")

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
    unmapped_hosts = cross_ref.get("unmapped_hosts", [])

    if not missing and not ip_drift and not orphaned and not unmapped_hosts:
        return "✅ All clear — no issues found\n"

    lines: list[str] = []

    if missing:
        lines.append("⚠️ New services without entity pages:")
        for svc in missing:
            ip = svc.get("ip", "")
            ip_part = f" (ip: {ip})" if ip else ""
            lines.append(f"  - {svc['name']}{ip_part} — no entity page found")
            lines.append(
                f'    → Run `/update-infrastructure create "{svc["name"]}"` to scaffold one'
            )
        lines.append("")

    if ip_drift:
        lines.append("⚠️ IP drift detected:")
        for item in ip_drift:
            lines.append(
                f"  - {item['name']}: pulse_ip=`{item['pulse_ip']}`,"
                f" entity ip=`{item['entity_ip']}` (MISMATCH)"
            )
            lines.append(
                f'    → Run `/update-infrastructure fix-ip "{item["name"]}"` to repair'
            )
        if len(ip_drift) > 1:
            lines.append("    → Run `/update-infrastructure fix all` to repair all at once")
        lines.append("")

    if orphaned:
        lines.append("ℹ️ Orphaned entity pages (not in Pulse):")
        for item in orphaned:
            if isinstance(item, dict):
                page = item["path"]
                cat_label = item.get("category", "truly_orphaned").replace("_", " ")
                lines.append(f"  - {page} ({cat_label})")
            else:
                lines.append(f"  - {item}")
        lines.append("  → Run `/update-infrastructure cleanup` for details")
        lines.append("")

    if unmapped_hosts:
        lines.append("⚠️ Unmapped host fields:")
        for item in unmapped_hosts:
            suggestions = item.get("suggestions", [])
            suggestion_str = ""
            if suggestions:
                quoted = ", ".join(f"`{s}`" for s in suggestions)
                suggestion_str = f" — did you mean {quoted}?"
            lines.append(
                f"  - {item['name']}: host=`{item['host']}`{suggestion_str}"
            )
        lines.append("")

    return "\n".join(lines)


def write_generated_file(output_path: str, data: dict, dry_run: bool = False,
                        npm_data: dict | None = None) -> dict:
    """Write auto-generated infrastructure data to a dedicated file.

    The content is always replaced wholesale — there are no markers or
    partial updates.  The Hermes agent reads this file after sync and
    updates ``guides/homelab-infrastructure.md`` surgically.

    Args:
        output_path: Full path where the generated file should be written.
        data: Parsed data from ``parser.parse_all()`` (may include
              ``cross_ref`` and ``entities_dir`` keys added by the caller).
        dry_run: If True, return the result without writing.
        npm_data: Optional NPM sync result from ``npm_sync()``.  When
            provided, unmatched NPM proxy hosts are rendered as
            ``[NPM]`` suggestion rows in the subdomain table.

    Returns:
        {
            "changed": bool,
            "path": str,
            "hash_before": str,
            "hash_after": str,
        }
    """
    path = Path(output_path)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    services = data.get("services", [])
    subdomains = data.get("subdomains", [])
    npm_suggestions = (npm_data or {}).get("suggested_additions", [])

    lines = [
        "---",
        f"generated_at: {now}",
        f"source: {config.get_pulse_url()}",
        "type: generated_data",
        f"services: {len(services)}",
        f"subdomains: {len(subdomains)}",
        "---",
        "",
        "# Infrastructure Data (Auto-Generated)",
        "",
        "> ⚠️ Do not edit this file manually — it is overwritten on every sync.",
        "> The Hermes agent reads this file after `/update-infrastructure` runs",
        "> and uses it to update `guides/homelab-infrastructure.md` surgically.",
        "",
        "---",
        "",
        "## Service Groups",
        "",
        _build_service_groups(data),
        "",
        "---",
        "",
        "## Domain / Subdomain Map",
        "",
        _build_subdomain_table(data, npm_suggestions=npm_suggestions),
        "",
        "---",
        "",
        "## Cross-Reference Findings",
        "",
        _build_cross_reference_section(data),
    ]

    new_content = "\n".join(lines)
    hash_after = _hash_block(new_content)

    if path.exists():
        original = path.read_text()
        hash_before = _hash_block(original)
        changed = new_content != original
    else:
        hash_before = "none"
        changed = True

    if changed and not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_content)

    return {
        "changed": changed,
        "path": str(path),
        "hash_before": hash_before,
        "hash_after": hash_after,
    }


def update_wiki_log(log_path: str, result: dict) -> None:
    """Append a log entry to the wiki log."""
    if not result.get("changed"):
        return

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    generated_path = result.get("path", "unknown")

    entry = (
        f"\n## [{today}] update | infrastructure-data (pulse sync)\n"
        f"- generated file updated: {generated_path}\n"
        f"- pulse source: {config.get_pulse_url()}\n"
    )

    log_file = Path(log_path)
    if log_file.exists():
        with open(log_file, "a") as f:
            f.write(entry)
