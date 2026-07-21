"""Human-readable and agent-oriented rendering for update-infrastructure."""

from pathlib import Path
from typing import Any

try:
    from . import config
except ImportError:  # pragma: no cover - supports direct module loading
    import config  # type: ignore


def _plugin_version() -> str:
    """Read the plugin manifest version, keeping help output release-safe."""
    manifest = Path(__file__).with_name("plugin.yaml")
    try:
        for line in manifest.read_text().splitlines():
            if line.startswith("version:"):
                return line.partition(":")[2].strip()
    except OSError:
        pass
    return "unknown"


def render_update_output(data: dict) -> str:
    """Render a human-readable slash-command response from structured sync data."""
    lines: list[str] = []
    elapsed = data.get("elapsed_seconds", 0)
    changed = data.get("changed", False)

    # ── Intent-specific messages ──
    intent = data.get("intent", {})
    intent_type = intent.get("type", "none")

    if intent_type == "status":
        lines.append(_render_status(data))
    elif intent_type == "echo":
        lines.append(_render_echo(intent))
    else:
        renderer = _INTENT_RENDERERS.get(intent_type)
        if renderer is not None:
            lines.append(renderer(intent.get("result", {})))
        else:
            # ── Full sync summary ──
            source = data.get("source", {})
            svc_count = source.get("services", 0)
            sub_count = source.get("subdomains", 0)

            icon = "✅" if not changed else "↕"
            lines.append(f"{icon} Homelab infrastructure synced ({elapsed}s)")
            lines.append(f"   Source: {svc_count} services, {sub_count} subdomains")
            lines.append(f"   Generated: {data.get('generated_file', '?')}")

            if changed:
                lines.append("\n📝 Generated data file updated")

            # Cross-reference summary
            cross_ref = data.get("cross_reference") or {}
            _render_cross_ref_summary(lines, cross_ref)

            # Topology
            topology = data.get("topology", {})
            mc = topology.get("machine_count", 0)
            if mc:
                cats = topology.get("categories", {})
                cat_str = ", ".join(f"{k}: {v}" for k, v in sorted(cats.items()))
                lines.append(f"\n🖥️ Topology: {mc} machines ({cat_str})")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Human-readable renderers for each intent type
# ---------------------------------------------------------------------------


def _cross_ref_data(cross_ref: dict) -> dict:
    """Normalize cross-reference fields shared by human and agent renderers."""
    orphaned = cross_ref.get("orphaned_entity_pages", [])
    category_counts: dict[str, int] = {}
    for item in orphaned:
        category = (
            item.get("category", "truly_orphaned")
            if isinstance(item, dict)
            else "truly_orphaned"
        )
        category_counts[category] = category_counts.get(category, 0) + 1
    return {
        "entities_found": cross_ref.get("entities_dir_found", False),
        "missing": cross_ref.get("missing_entity_pages", []),
        "ip_drift": cross_ref.get("ip_drift", []),
        "orphaned": orphaned,
        "unmapped": cross_ref.get("unmapped_hosts", []),
        "orphan_summary": ", ".join(
            f"{category}:{count}" for category, count in sorted(category_counts.items())
        ),
    }


def _render_cross_ref_summary(lines: list[str], cross_ref: dict) -> None:
    """Append cross-reference findings as human-readable lines."""
    report = _cross_ref_data(cross_ref)
    if not report["entities_found"]:
        lines.append("\n\u274c Cross-reference skipped -- entities/ directory not found")
        return

    missing = report["missing"]
    ip_drift = report["ip_drift"]
    orphaned = report["orphaned"]
    unmapped = report["unmapped"]

    issues = bool(missing or ip_drift or unmapped)
    if not issues and not orphaned:
        lines.append("\n\u2705 Cross-reference: all clear")
        return

    lines.append("")
    if missing:
        lines.append(f"\n\u26a0\ufe0f {len(missing)} service(s) without entity pages:")
        for svc in missing:
            ip = svc.get("ip", "")
            h = f" ({ip})" if ip else ""
            lines.append(f"   \u2022 {svc['name']}{h}")
            lines.append(f'     \u2192 /update-infrastructure create "{svc["name"]}"')

    if ip_drift:
        lines.append(f"\n\u26a0\ufe0f {len(ip_drift)} IP drift(s) detected:")
        for item in ip_drift:
            lines.append(
                f"   \u2022 {item['name']}:"
                f" pulse_ip={item['pulse_ip']},"
                f" entity ip={item['entity_ip']} (MISMATCH)"
            )
            lines.append(f'     \u2192 /update-infrastructure fix-ip "{item["name"]}"')
        if len(ip_drift) > 1:
            lines.append("     \u2192 /update-infrastructure fix all")

    if unmapped:
        lines.append("\n\u26a0\ufe0f Unmapped host fields:")
        for item in unmapped:
            sug = item.get("suggestions", [])
            sug_str = f" -- did you mean {', '.join(sug)}?" if sug else ""
            lines.append(f"   \u2022 {item['name']}: host={item['host']}{sug_str}")

    if orphaned:
        cat_summary = report["orphan_summary"]
        lines.append(f"\nℹ️  {len(orphaned)} orphaned entity page(s) ({cat_summary})")
        lines.append("   → /update-infrastructure cleanup")


def _render_status(data: dict) -> str:
    """Render a /update-infrastructure status check."""
    cross_ref = data.get("cross_reference", {})
    source = data.get("source", {})
    lines = [
        "\U0001f50d Infrastructure status check",
        f"   Services in Pulse: {source.get('services', 0)}",
        f"   Subdomains: {source.get('subdomains', 0)}",
    ]
    _render_cross_ref_summary(lines, cross_ref)
    return "\n".join(lines)


def _render_create(result: dict) -> str:
    """Render entity page creation result."""
    if result.get("created"):
        return f"\u2705 Entity page created: {result['path']}"
    reason = result.get("reason", "unknown")
    return f"\u274c Entity page not created: {reason}"


def _render_fix_all(result: dict) -> str:
    """Render batch IP fix result."""
    fixed = result.get("fixed", [])
    errors = result.get("errors", [])
    lines: list[str] = []
    if fixed:
        for f in fixed:
            lines.append(f"\u2705 Fixed: {f['name']} \u2192 {f['ip']}")
    if errors:
        for e in errors:
            lines.append(f"\u26a0\ufe0f {e['name']}: {e['reason']}")
    if not fixed and not errors:
        lines.append("\u2139\ufe0f No IP drift found to fix")
    return "\n".join(lines)


def _render_fix_ip(result: dict) -> str:
    """Render single IP fix result."""
    if result.get("fixed"):
        if result.get("dry_run"):
            return f"\U0001f50d [DRY-RUN] Would fix {result.get('name', '?')} to {result.get('ip', '?')}"
        return f"\u2705 IP fixed for {result.get('name', '?')} \u2192 {result.get('ip', '?')}"
    return f"\u274c Fix failed: {result.get('reason', 'unknown')}"


def _render_check(result: dict) -> str:
    """Render a deep service check."""
    name = result.get("name", "?")
    lines: list[str] = [
        f"\U0001f50d Service check: {name}",
        f"   In Pulse: {result.get('in_pulse', False)}",
    ]
    if result.get("pulse_group"):
        lines.append(f"   Group: {result['pulse_group']}")
    if result.get("pulse_ip"):
        lines.append(f"   Pulse IP: {result['pulse_ip']}")
    if result.get("pulse_status"):
        lines.append(f"   Status: {result['pulse_status']}")
    if result.get("pulse_host"):
        lines.append(f"   Host: {result['pulse_host']}")
    lines.append(f"   Entity page: {result.get('has_entity_page', False)}")
    if result.get("entity_page"):
        lines.append(f"   Entity file: {result['entity_page']}")
    if result.get("entity_ip"):
        lines.append(f"   Entity IP: {result['entity_ip']}")
    drift = result.get("ip_drift")
    if drift:
        lines.append(
            f"\n\u26a0\ufe0f IP drift:"
            f" pulse_ip={drift['pulse_ip']},"
            f" entity={drift['entity_ip']}"
        )
        lines.append(f'   \u2192 /update-infrastructure fix-ip "{name}"')
    elif result.get("entity_ip"):
        lines.append("\n\u2705 No IP drift detected")
    return "\n".join(lines)


def _render_cleanup(result: dict) -> str:
    """Render orphaned entity page list with categories."""
    orphaned = result.get("orphaned_pages", [])
    if not orphaned:
        return "✅ No orphaned entity pages found"

    # Group by category
    by_category: dict[str, list[str]] = {}
    for item in orphaned:
        cat = item.get("category", "truly_orphaned")
        path = item.get("path", item) if isinstance(item, dict) else item
        by_category.setdefault(cat, []).append(path)

    _CAT_LABELS: dict[str, str] = {
        "truly_orphaned": "❓ Orphaned — no Pulse connection",
        "has_pulse_host_match": "🔗 Resolvable via pulse_host",
        "has_pulse_container_match": "📦 Parent of containers (pulse_containers set)",
        "networking_device": "🌐 Networking devices (expected)",
        "infrastructure_node": "🖥️ Infrastructure nodes (expected)",
    }

    lines = [f"ℹ️  {len(orphaned)} orphaned entity page(s):"]
    for category, paths in by_category.items():
        label = _CAT_LABELS.get(category, f"  {category}")
        lines.append(f"\n  {label}:")
        for p in sorted(paths):
            lines.append(f"     ➕ {p}")
    return "\n".join(lines)


def _render_audit(result: dict) -> str:
    """Render the systems audit report produced by audit_health."""
    return result.get("report", "⚠️ Systems audit produced no report")


def _render_echo(intent: dict) -> str:
    """Render an echo / unrecognised-command intent."""
    text = intent.get("result", {}).get("echoed_text", "")
    return (
        f"> {text}\n\n"
        f'ℹ️ Unrecognised command. Try `/update-infrastructure help`'
        f" for available sub-commands."
    )


def _render_help(result: dict) -> str:
    """Render a comprehensive help / usage message."""
    wiki_path = config.get_wiki_root()

    lines = [
        "",
        "┌─────────────────────────────────────────────────────────────┐",
        f"│               /update-infrastructure  v{_plugin_version():<20}│",
        "│  Sync homelab wiki from Pulse monitoring API                 │",
        "└─────────────────────────────────────────────────────────────┘",
        "",
        "🔄 COMMANDS (aliases: /update-infra, /sync-infra)",
        "",
        "   /update-infrastructure",
        "      Full sync: fetch Pulse API → cross-ref → update wiki",
        "      → hands off review prompt for agent follow-up",
        "",
        "   /update-infrastructure status",
        "      Cross-reference check only (skips wiki update)",
        "",
        "   /systems-audit    (aliases: /audit, /health)",
        "      Read-only Pulse health report: alerts, disks, host/container pressure",
        "",
        "   /update-infrastructure audit",
        "      Same systems audit report through the update-infrastructure command",
        "",
        "   /update-infrastructure create \"Service Name\"",
        "      Scaffold an entities/<slug>.md page with sensible defaults",
        "",
        "   /update-infrastructure check \"Service Name\"",
        "      Deep-check one service: Pulse data + entity page + IP drift",
        "",
        "   /update-infrastructure fix-ip \"Service Name\" [--dry-run]",
        "      Repair IP drift for one service (--dry-run to preview)",
        "",
        "   /update-infrastructure fix all",
        "      Repair all IP drift in one go",
        "",
        "   /update-infrastructure cleanup",
        "      List orphaned entity pages (not in Pulse)",
        "",
        "   /update-infrastructure json    (or 'tool')",
        "      Raw JSON output — escape hatch for scripting",
        "",
        "   /update-infrastructure help    (or --help, -h)",
        "      Show this help message",
        "",
        "⚙️  CONFIGURATION  (infrastructure-plugin: section in config.yaml)",
        "",
        f"   wiki_root             Wiki root (current: {wiki_path})",
        "   pulse_url             Pulse API base URL",
        "   PULSE_API_TOKEN       Pulse API token (env var, monitoring:read scope required)",
        "   pulse_resource_types  Resource types to fetch (default: vm,system-container,app-container,agent)",
        "   generic_infra_names   Extra generic names to suppress in cross-ref",
        "   server_aliases        Short-name→entity mappings",
        "   suppress_entity_check Service names to skip in entity checks",
        "   npm_url               NGINX Proxy Manager base URL (optional)",
        "   NPM_EMAIL             NPM login email (env var, optional)",
        "   NPM_PASSWORD          NPM login password (env var, optional)",
        "",
        "📂 SYNC RULES",
        "",
        "   • Added in Pulse    → added to generated data file",
        "   • Removed from Pulse → removed from generated data file",
        "   • Changed in Pulse  → updated in generated data file",
        "",
        "   Auto-generated data is written to raw/sources/pulse-infrastructure-data.md.",
        "   The Hermes agent reads that file and updates guides/homelab-infrastructure.md.",
        "   Everything in the wiki page is your manual territory.",
        "",
    ]
    return "\n".join(lines)


# Dispatch table: intent_type -> renderer(result_dict) -> human-readable string
_INTENT_RENDERERS: dict[str, Any] = {
    "audit": _render_audit,
    "create": _render_create,
    "fix_all": _render_fix_all,
    "fix_ip": _render_fix_ip,
    "check": _render_check,
    "cleanup": _render_cleanup,
    "help": _render_help,
}


# ---------------------------------------------------------------------------
# Review prompt builder (for agent handoff)
# ---------------------------------------------------------------------------

def build_review_prompt(data: dict) -> str:
    """Build a structured review prompt from sync result data.

    Returned by the default (no-intent) path so the LLM agent can
    summarise changes, review cross-reference issues, topology drift,
    and follow up with the user via clarify().
    """
    elapsed = data.get("elapsed_seconds", 0)
    changed = data.get("changed", False)
    source = data.get("source", {})
    cross_ref = data.get("cross_reference", {})
    topology = data.get("topology", {})

    svc_count = source.get("services", 0)
    sub_count = source.get("subdomains", 0)
    generated_file = data.get("generated_file", "?")
    wiki_file = data.get("wiki_file", "?")

    lines = [
        "# Infrastructure Sync Review",
        "",
        "## Summary",
    ]

    if changed:
        lines.append(
            f"↕ Synced in {elapsed}s — {svc_count} services,"
            f" {sub_count} subdomains"
        )
        lines.append(f"Generated data file updated: `{generated_file}`")
    else:
        lines.append(f"✅ Synced in {elapsed}s — generated data file is up to date (no changes)")
        lines.append(
            f"   Source: {svc_count} services, {sub_count} subdomains"
        )
        lines.append(f"   Generated file: `{generated_file}`")

    # ── Generated file / wiki update instructions ──
    lines.append("")
    lines.append("## Wiki Update Instructions")
    lines.append("")
    lines.append(
        f"Read `{generated_file}` to get the latest infrastructure data,"
        f" then update `{wiki_file}` surgically:"
    )
    lines.append("")
    lines.append("1. Add/remove CT/VM entries from the Machine Details section")
    lines.append("2. Update IP, OS, and host info for any drifted entries")
    lines.append("3. Update the DHCP / Free IPs section based on IP changes")
    lines.append(
        "4. Append a concise **Status** paragraph at the end"
        ' (e.g. "✅ 0 alerts, 2 nodes online, 14 containers running")'
    )
    lines.append("")
    lines.append(
        "New syntheses or comparisons discovered during review can be filed"
        " under `concepts/`, `comparisons/`, or `queries/`."
    )
    lines.append("")
    lines.append(
        "After updating any wiki files, reindex the search index:"
        " `python3 ~/.hermes/scripts/wiki-search.py reindex`"
    )

    # ── Cross-reference report ──
    lines.append("")
    lines.append("## Cross-Reference Findings")

    report = _cross_ref_data(cross_ref)
    entities_found = report["entities_found"]
    if not entities_found:
        lines.append(
            "❌ Cross-reference skipped — entities/ directory not found"
        )
    else:
        missing = report["missing"]
        ip_drift = report["ip_drift"]
        orphaned = report["orphaned"]
        unmapped = report["unmapped"]

        if not missing and not ip_drift and not unmapped and not orphaned:
            lines.append("✅ All clear — no issues found")
        else:
            if missing:
                lines.append("")
                lines.append(
                    f"⚠️ {len(missing)} service(s) without entity pages:"
                )
                for svc in missing:
                    ip = svc.get("ip", "")
                    h = f" ({ip})" if ip else ""
                    lines.append(f"  • {svc['name']}{h}")
                    lines.append(
                        f'    → /update-infrastructure create "{svc["name"]}"'
                    )

            if ip_drift:
                lines.append("")
                lines.append(f"⚠️ {len(ip_drift)} IP drift(s) detected:")
                for item in ip_drift:
                    lines.append(
                        f"  • {item['name']}:"
                        f" pulse_ip={item['pulse_ip']},"
                        f" entity ip={item['entity_ip']} (MISMATCH)"
                    )
                    lines.append(
                        f'    → /update-infrastructure fix-ip "{item["name"]}"'
                    )
                if len(ip_drift) > 1:
                    lines.append(
                        "    → /update-infrastructure fix all (repair all at once)"
                    )

            if unmapped:
                lines.append("")
                lines.append("⚠️ Unmapped host fields:")
                for item in unmapped:
                    sug = item.get("suggestions", [])
                    sug_str = (
                        f" — did you mean {', '.join(sug)}?" if sug else ""
                    )
                    lines.append(
                        f"  • {item['name']}:"
                        f" host={item['host']}{sug_str}"
                    )

            if orphaned:
                lines.append("")
                cat_summary = report["orphan_summary"]
                lines.append(
                    f"ℹ️ {len(orphaned)} orphaned entity page(s) ({cat_summary})"
                )
                lines.append(
                    "  → /update-infrastructure cleanup for details"
                )

    # ── NPM Proxy Host Cross-Reference ──
    npm_sync_data = data.get("npm_sync")
    if npm_sync_data is not None:
        lines.append("")
        lines.append("## NPM Proxy Host Cross-Reference")
        npm_error = npm_sync_data.get("error")
        if npm_error:
            lines.append(f"⚠️ NPM sync error: {npm_error}")
        else:
            npm_host_count = npm_sync_data.get("npm_host_count", 0)
            wiki_sub_count = npm_sync_data.get("wiki_subdomain_count", 0)
            domains_found = npm_sync_data.get("domains_page_found", False)
            unmatched = npm_sync_data.get("unmatched_npm", [])
            missing = npm_sync_data.get("missing_npm", [])
            cfg_drift = npm_sync_data.get("config_drift", [])
            ssl_drift = npm_sync_data.get("ssl_drift", [])

            if not domains_found:
                lines.append("⚠️ Wiki domains page not found — subdomain comparison skipped")
            else:
                lines.append(
                    f"NPM: {npm_host_count} proxy hosts,"
                    f" Wiki: {wiki_sub_count} subdomains"
                )

            if not unmatched and not missing and not cfg_drift and not ssl_drift:
                lines.append("✅ All clear — NPM hosts match wiki subdomains")
            else:
                if unmatched:
                    lines.append("")
                    lines.append(
                        f"⚠️ {len(unmatched)} NPM proxy host(s) not in wiki:"
                    )
                    for item in unmatched:
                        domains = ", ".join(item.get("domain_names", []))
                        fwd = f"{item.get('forward_host', '')}:{item.get('forward_port', '')}"
                        lines.append(f"  • {domains} → {fwd}")

                if missing:
                    lines.append("")
                    lines.append(
                        f"⚠️ {len(missing)} wiki subdomain(s) with no NPM proxy host:"
                    )
                    for label in missing:
                        lines.append(f"  • {label}")

                if cfg_drift:
                    lines.append("")
                    lines.append(f"⚠️ {len(cfg_drift)} config drift(s) detected:")
                    for item in cfg_drift:
                        domains = ", ".join(item.get("domain_names", []))
                        lines.append(f"  • {domains}: {item.get('issue', '')}")

                if ssl_drift:
                    lines.append("")
                    lines.append(f"⚠️ {len(ssl_drift)} SSL drift(s) detected:")
                    for item in ssl_drift:
                        domains = ", ".join(item.get("domain_names", []))
                        lines.append(f"  • {domains}: {item.get('issue', '')}")

    # ── Topology ──
    lines.append("")
    lines.append("## Topology")
    mc = topology.get("machine_count", 0)
    if mc:
        cats = topology.get("categories", {})
        cat_str = ", ".join(
            f"{k}: {v}" for k, v in sorted(cats.items())
        )
        lines.append(f"🖥️ {mc} machines ({cat_str})")

        connections = topology.get("connections", [])
        if connections:
            lines.append("")
            lines.append("Parent → child relationships:")
            for conn in connections:
                lines.append(
                    f"  • {conn['parent']} → {conn['child']}"
                )
    else:
        lines.append(
            "*(No entity pages found — topology unavailable)*"
        )

    # ── Agent follow-up instructions ──
    lines.append("")
    lines.append("## Follow-Up Instructions")
    lines.append("")
    lines.append(
        "The sync is complete. Review the findings above and follow up"
        " with the user:"
    )
    lines.append("")

    has_issues = False
    if entities_found:
        if cross_ref.get("missing_entity_pages"):
            has_issues = True
            lines.append(
                '- Ask: "Found services without entity pages —'
                ' want me to create them?"'
            )
        if cross_ref.get("ip_drift"):
            has_issues = True
            lines.append(
                '- Ask: "IP drift detected — want me to fix it?"'
            )
        if cross_ref.get("orphaned_entity_pages"):
            has_issues = True
            lines.append(
                '- Ask: "Orphaned entity pages found —'
                ' want me to clean them up?"'
            )
        if cross_ref.get("unmapped_hosts"):
            has_issues = True
            lines.append(
                '- Ask: "Some host fields don\'t match known machines'
                ' — need help mapping them?"'
            )

    if npm_sync_data is not None and not npm_sync_data.get("error"):
        if npm_sync_data.get("unmatched_npm") or npm_sync_data.get("missing_npm"):
            has_issues = True
            lines.append(
                '- Ask: "NPM proxy host drift detected —'
                ' want me to review the discrepancies?"'
            )
        if npm_sync_data.get("ssl_drift"):
            has_issues = True
            lines.append(
                '- Ask: "Some NPM proxy hosts have SSL drift —'
                ' want me to flag them for review?"'
            )

    if not has_issues:
        lines.append(
            '- Report: "Wiki is up to date, no issues found.'
            ' Topology looks clean."'
        )

    return "\n".join(lines)
