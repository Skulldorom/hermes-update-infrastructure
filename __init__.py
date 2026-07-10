"""update-infrastructure plugin -- update_infrastructure / update_infrastructure_review tools + /update-infrastructure command.

Syncs the homelab wiki from the Pulse monitoring API.
Pulse is the single source of truth for infrastructure data.
Wiki content not managed by this plugin is preserved untouched.

Provides three interfaces:
- update_infrastructure tool (infra toolset) -- returns review prompt by default, JSON with "json"/"tool"
- update_infrastructure_review tool (infra toolset) -- always returns a review prompt
- /update-infrastructure slash command -- injects review prompt into the agent loop by default

Default (no args): returns a structured review prompt for agent follow-up.
Escape hatch: pass "json" or "tool" for raw JSON output.

Sub-commands (via intent parsing):
  /update-infrastructure                  -> full sync with review prompt
  /update-infrastructure json             -> raw JSON output (escape hatch)
  /update-infrastructure tool             -> raw JSON output (backward compat)
  /update-infrastructure status           -> cross-reference check only
  /update-infrastructure create NAME      -> scaffold an entity page
  /update-infrastructure fix-ip NAME      -> repair IP drift for one service
  /update-infrastructure fix all          -> repair all IP drift
  /update-infrastructure check NAME       -> deep-check one service
  /update-infrastructure cleanup          -> list orphaned entity pages
"""


import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from . import config, parser, updater
    from . import parser_wiki
    from . import npm_client as _npm_client
except ImportError:  # pragma: no cover - supports direct module loading in tests
    import config  # type: ignore
    import parser  # type: ignore
    import updater  # type: ignore
    import parser_wiki  # type: ignore
    import npm_client as _npm_client  # type: ignore

logger = logging.getLogger(__name__)

WIKI_PAGE = config.WIKI_PAGE

# ---------------------------------------------------------------------------
# Intent parsing
# ---------------------------------------------------------------------------

# Each entry is (compiled_pattern, intent_name, name_group_index_or_None).
# The patterns are tried in order; first match wins.
_INTENT_PATTERNS: list[tuple[re.Pattern, str, int | None]] = [
    # tool -- escape hatch: raw JSON output, no agent summarisation
    (re.compile(r'^tool\b', re.IGNORECASE), "tool", None),
    # create / create-entity "Name"  or  create Name
    (re.compile(r'^create(?:-entity)?\s+"([^"]+)"', re.IGNORECASE), "create", 1),
    (re.compile(r'^create(?:-entity)?\s+(.+)', re.IGNORECASE), "create", 1),
    # fix-ip "Name" [--dry-run]  or  fix-ip Name [--dry-run]
    (re.compile(r'^fix[-\s]?ip\s+"([^"]+)"', re.IGNORECASE), "fix_ip", 1),
    (re.compile(r'^fix[-\s]?ip\s+(.*?)(?:\s+--dry-run)?$', re.IGNORECASE), "fix_ip", 1),
    # fix all
    (re.compile(r'^fix\s+all\b', re.IGNORECASE), "fix_all", None),
    # check "Name"  or  check Name
    (re.compile(r'^check\s+"([^"]+)"', re.IGNORECASE), "check", 1),
    (re.compile(r'^check\s+(.+)', re.IGNORECASE), "check", 1),
    # cleanup
    (re.compile(r'^cleanup\b', re.IGNORECASE), "cleanup", None),
    # status
    (re.compile(r'^status\b', re.IGNORECASE), "status", None),
    # json -- escape hatch: raw JSON output, no review prompt
    (re.compile(r'^json\b', re.IGNORECASE), "json", None),
    # help / --help / -h
    (re.compile(r'^(?:--)?help$', re.IGNORECASE), "help", None),
    (re.compile(r'^-h$', re.IGNORECASE), "help", None),
]


def _parse_intent(extra: str) -> tuple[str, dict]:
    """Parse *extra* text for a known intent.

    Returns:
        A ``(intent_name, params)`` tuple.
        *intent_name* is one of ``"create"``, ``"fix_ip"``, ``"fix_all"``,
        ``"check"``, ``"cleanup"``, ``"status"``, ``"tool"``, ``"echo"``,
        or ``"none"``.
        *params* contains intent-specific keys (``name``, ``dry_run``, ``text``).
    """
    if not extra:
        return "none", {}

    text = extra.strip()
    for pattern, intent, group in _INTENT_PATTERNS:
        m = pattern.match(text)
        if m:
            params: dict = {}
            if group is not None:
                params["name"] = m.group(group).strip()
            if intent == "fix_ip":
                params["dry_run"] = "--dry-run" in text.lower()
            return intent, params

    return "echo", {"text": extra}


# ---------------------------------------------------------------------------
# Tool handler (returns JSON for LLM agent loop / agent summarisation)
# ---------------------------------------------------------------------------

def _build_topology_dict(machines_data: dict) -> dict:
    """Return structured topology data as a dict (for tool JSON output)."""
    machines = machines_data.get("machines", [])
    if not machines:
        return {"machine_count": 0, "categories": {}, "connections": []}

    counts: dict[str, int] = {}
    for m in machines:
        cat = m.get("category", "other")
        counts[cat] = counts.get(cat, 0) + 1

    slug_map = {m["slug"]: m for m in machines}
    connections: list[dict] = []
    for m in machines:
        host_slug = m.get("host", "")
        if host_slug and host_slug in slug_map:
            connections.append({
                "parent": slug_map[host_slug]["name"],
                "child": m["name"],
            })

    return {
        "machine_count": len(machines),
        "categories": counts,
        "connections": connections,
    }


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

def _get_wiki_path() -> Path:
    """Resolve the wiki root path."""
    return Path(config.get_wiki_root())


def _render_update_output(data: dict) -> str:
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


def _handle_update(raw_args: str) -> str:
    """Handler for /update-infrastructure slash command.

    Renders a human-readable summary from the JSON output so the user
    sees a concise report without raw JSON in the terminal.

    Pass ``"tool"`` or ``"json"`` as *raw_args* to get raw JSON output
    (escape hatch for scripting / integration).
    """
    raw = raw_args or ""
    raw_l = raw.strip().lower()
    if raw_l.startswith("tool") or raw_l.startswith("json"):
        return _handle_update_tool(raw_args)

    json_str = _handle_update_tool(raw_args)
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        return json_str  # fallback to raw

    if "error" in data:
        return f"\u26a0\ufe0f Sync failed: {data['error']}"

    return _render_update_output(data)


# ---------------------------------------------------------------------------
# Human-readable renderers for each intent type
# ---------------------------------------------------------------------------


def _render_cross_ref_summary(lines: list[str], cross_ref: dict) -> None:
    """Append cross-reference findings as human-readable lines."""
    if not cross_ref.get("entities_dir_found", False):
        lines.append("\n\u274c Cross-reference skipped -- entities/ directory not found")
        return

    missing = cross_ref.get("missing_entity_pages", [])
    ip_drift = cross_ref.get("ip_drift", [])
    orphaned = cross_ref.get("orphaned_entity_pages", [])
    unmapped = cross_ref.get("unmapped_hosts", [])

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
        lines.append(f"\n\u26a0\ufe0f Unmapped host fields:")
        for item in unmapped:
            sug = item.get("suggestions", [])
            sug_str = f" -- did you mean {', '.join(sug)}?" if sug else ""
            lines.append(f"   \u2022 {item['name']}: host={item['host']}{sug_str}")

    if orphaned:
        # Count by category for a concise summary
        cat_counts: dict[str, int] = {}
        for item in orphaned:
            if isinstance(item, dict):
                cat = item.get("category", "truly_orphaned")
                cat_counts[cat] = cat_counts.get(cat, 0) + 1
            else:
                cat_counts["truly_orphaned"] = cat_counts.get("truly_orphaned", 0) + 1
        cat_summary = ", ".join(f"{c}:{n}" for c, n in sorted(cat_counts.items()))
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
        "│               /update-infrastructure  v3.0.0                │",
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

def _build_review_prompt(data: dict) -> str:
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

    entities_found = cross_ref.get("entities_dir_found", False)
    if not entities_found:
        lines.append(
            "❌ Cross-reference skipped — entities/ directory not found"
        )
    else:
        missing = cross_ref.get("missing_entity_pages", [])
        ip_drift = cross_ref.get("ip_drift", [])
        orphaned = cross_ref.get("orphaned_entity_pages", [])
        unmapped = cross_ref.get("unmapped_hosts", [])

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
                cat_counts: dict[str, int] = {}
                for item in orphaned:
                    cat = item.get("category", "truly_orphaned") if isinstance(item, dict) else "truly_orphaned"
                    cat_counts[cat] = cat_counts.get(cat, 0) + 1
                cat_summary = ", ".join(f"{c}:{n}" for c, n in sorted(cat_counts.items()))
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


# ---------------------------------------------------------------------------
# Tool handler (returns JSON for LLM agent loop)
# ---------------------------------------------------------------------------

def _handle_update_tool(raw_args: str = "", **kwargs) -> str:
    """Tool handler for update_infrastructure -- returns structured JSON.

    Runs the same sync pipeline as the slash command but returns a JSON
    dict so the LLM can process, summarise, and follow up naturally.

    Accepts **kwargs to absorb any extra parameters (e.g. task_id) that
    the tool wrapper may pass.
    """
    t_start = time.monotonic()
    started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    extra = raw_args.strip() if raw_args else ""
    intent, intent_params = _parse_intent(extra)

    try:
        wiki_root = _get_wiki_path()

        # --- status intent: cross-ref only, no wiki update ---
        if intent == "status":
            entity_pages = parser.load_entity_pages(wiki_root)
            data = parser.parse_all()
            cross_ref = parser.cross_reference(data, entity_pages)
            npm_url = config.get_npm_url()
            npm_email = os.environ.get("NPM_EMAIL", config.NPM_EMAIL)
            npm_password = os.environ.get("NPM_PASSWORD", config.NPM_PASSWORD)
            npm_status_result: dict | None = None
            if npm_url and npm_email and npm_password:
                try:
                    npm_c = _npm_client.NPMClient(npm_url, npm_email, npm_password)
                    npm_status_result = _npm_client.npm_sync(npm_c, wiki_root)
                except Exception as npm_exc:  # noqa: BLE001
                    logger.warning("NPM sync failed: %s", npm_exc)
                    npm_status_result = {"error": str(npm_exc)}
            elapsed = round(time.monotonic() - t_start, 2)
            return json.dumps({
                "sync_completed_at": started_at,
                "elapsed_seconds": elapsed,
                "wiki_file": str(wiki_root / WIKI_PAGE),
                "changed": False,
                "source": {"services": len(data.get("services", [])),
                           "subdomains": len(data.get("subdomains", []))},
                "cross_reference": cross_ref,
                "npm_sync": npm_status_result,
                "intent": {"type": "status"},
            })

        # --- help intent: no sync needed, return immediately ---
        if intent == "help":
            return json.dumps({
                "sync_completed_at": started_at,
                "elapsed_seconds": 0,
                "wiki_file": str(wiki_root / WIKI_PAGE),
                "changed": False,
                "intent": {"type": "help", "result": {}},
            })

        # --- Full sync pipeline ---
        data = parser.parse_all()

        svc_count = len(data.get("services", []))
        sub_count = len(data.get("subdomains", []))

        entity_pages = parser.load_entity_pages(wiki_root)
        cross_ref = parser.cross_reference(data, entity_pages)
        data["cross_ref"] = cross_ref
        data["entities_dir"] = str(wiki_root / "entities")

        # --- NPM sync (best-effort; skipped when credentials are not configured) ---
        npm_result: dict | None = None
        npm_url = config.get_npm_url()
        npm_email = os.environ.get("NPM_EMAIL", config.NPM_EMAIL)
        npm_password = os.environ.get("NPM_PASSWORD", config.NPM_PASSWORD)
        if npm_url and npm_email and npm_password:
            try:
                npm_c = _npm_client.NPMClient(npm_url, npm_email, npm_password)
                npm_result = _npm_client.npm_sync(npm_c, wiki_root)
            except Exception as npm_exc:  # noqa: BLE001
                logger.warning("NPM sync failed: %s", npm_exc)
                npm_result = {"error": str(npm_exc)}

        generated_path = wiki_root / config.GENERATED_OUTPUT_DIR / config.GENERATED_DATA_FILE
        log_path = wiki_root / "log.md"

        result = updater.write_generated_file(str(generated_path), data,
                                               npm_data=npm_result)
        updater.update_wiki_log(str(log_path), result)

        machines_data = parser_wiki.parse_entity_pages(str(wiki_root / "entities"))
        topology = _build_topology_dict(machines_data)

        # Execute intent and capture structured result
        intent_result = None
        if intent == "create":
            name = intent_params.get("name", "")
            services = data.get("services", [])
            svc = next((s for s in services if s["name"].lower() == name.lower()), None)
            ip = (svc or {}).get("ip", "")
            host = (svc or {}).get("host", "")
            os_name = (svc or {}).get("os", "")
            vmid = (svc or {}).get("vmid", "")
            r = parser.scaffold_entity_page(wiki_root, name, ip, host, os_name, vmid,
                                             entity_pages=entity_pages)
            intent_result = {"created": r["created"], "path": r["path"]}
            if not r["created"]:
                intent_result["reason"] = r.get("reason", "")

        elif intent == "fix_all":
            ip_drift = cross_ref.get("ip_drift", [])
            fixed = []
            errors = []
            for item in ip_drift:
                new_ip = item.get("pulse_ip", "")
                if not new_ip:
                    errors.append({"name": item["name"], "reason": "no Pulse IP available"})
                    continue
                r = parser.fix_entity_ip(wiki_root, item["name"], new_ip)
                if r["fixed"]:
                    fixed.append({"name": item["name"], "ip": new_ip})
                else:
                    errors.append({"name": item["name"], "reason": r.get("reason", "unknown")})
            intent_result = {"fixed": fixed, "errors": errors}

        elif intent == "fix_ip":
            name = intent_params.get("name", "")
            dry_run = intent_params.get("dry_run", False)
            ip_drift = cross_ref.get("ip_drift", [])
            item = next((i for i in ip_drift if i["name"].lower() == name.lower()), None)
            if item is None:
                intent_result = {"name": name, "fixed": False, "reason": f"no IP drift detected for {name}"}
            else:
                new_ip = item.get("pulse_ip", "")
                if not new_ip:
                    intent_result = {
                        "name": name,
                        "fixed": False,
                        "reason": "no Pulse IP available for this service",
                    }
                else:
                    r = parser.fix_entity_ip(wiki_root, name, new_ip, dry_run=dry_run)
                    intent_result = {"name": name, "fixed": r["fixed"], "dry_run": dry_run}
                    if r["fixed"] and not dry_run:
                        intent_result["path"] = r.get("path", "")
                        intent_result["ip"] = new_ip

        elif intent == "check":
            name = intent_params.get("name", "")
            services = data.get("services", [])
            svc = next((s for s in services if s["name"].lower() == name.lower()), None)
            entity_pages_chk = parser.load_entity_pages(wiki_root)
            slug = parser._slugify(name)
            entity = (entity_pages_chk or {}).get(slug)
            drift_item = next(
                (i for i in cross_ref.get("ip_drift", []) if i["name"].lower() == name.lower()),
                None,
            )
            intent_result = {
                "name": name,
                "in_pulse": svc is not None,
                "pulse_group": svc.get("group") if svc else None,
                "pulse_ip": svc.get("ip") if svc else None,
                "pulse_status": svc.get("status") if svc else None,
                "pulse_host": svc.get("host") if svc else None,
                "has_entity_page": entity is not None,
                "entity_page": f"entities/{slug}.md" if entity else None,
                "entity_ip": entity.get("ip") if entity else None,
                "ip_drift": {
                    "pulse_ip": drift_item["pulse_ip"],
                    "entity_ip": drift_item["entity_ip"],
                } if drift_item else None,
            }

        elif intent == "cleanup":
            orphaned = cross_ref.get("orphaned_entity_pages", [])
            intent_result = {"orphaned_pages": orphaned}

        elif intent == "echo":
            intent_result = {"echoed_text": intent_params.get("text", "")}

        elapsed = round(time.monotonic() - t_start, 2)

        result_dict = {
            "sync_completed_at": started_at,
            "elapsed_seconds": elapsed,
            "generated_file": result["path"],
            "wiki_file": str(wiki_root / WIKI_PAGE),
            "changed": result["changed"],
            "source": {"services": svc_count, "subdomains": sub_count},
            "cross_reference": cross_ref,
            "topology": topology,
            "npm_sync": npm_result,
            "intent": {
                "type": intent,
                "result": intent_result,
            },
        }

        if intent in ("none",):
            # Default: embed the review prompt in the JSON so callers can
            # extract it without hitting a JSONDecodeError fallthrough.
            result_dict["review_prompt"] = _build_review_prompt(result_dict)

        return json.dumps(result_dict)

    except Exception as e:
        logger.exception("update_infrastructure tool failed")
        elapsed = round(time.monotonic() - t_start, 2)
        return json.dumps({
            "sync_completed_at": started_at,
            "elapsed_seconds": elapsed,
            "error": str(e),
        })


# ---------------------------------------------------------------------------
# Review tool handler (always returns a review prompt)
# ---------------------------------------------------------------------------

def _handle_update_and_review(raw_args: str = "") -> str:
    """Handler for update_infrastructure_review — always returns a review prompt.

    Runs the full sync pipeline and returns a structured markdown prompt
    that tells the LLM agent to summarise changes, review cross-reference
    and topology findings, and follow up with the user via clarify().

    Unlike ``_handle_update_tool()``, this always produces the review
    prompt — even when sub-commands are passed.  For raw JSON, use the
    ``update_infrastructure`` tool with ``"json"`` or ``"tool"`` intent.
    """
    raw_result = _handle_update_tool(raw_args)
    try:
        data = json.loads(raw_result)
    except json.JSONDecodeError:
        return raw_result  # shouldn't happen now, but safe fallback

    review_prompt = data.get("review_prompt")
    if review_prompt:
        return review_prompt
    # For subcommands, re-run via the default (no-args) path to get a review prompt
    default_result = _handle_update_tool("")
    try:
        default_data = json.loads(default_result)
    except json.JSONDecodeError:
        return raw_result
    return default_data.get("review_prompt", raw_result)


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

def register(ctx):
    """Register the /update-infrastructure slash command and update_infrastructure tool."""
    description = "Sync homelab wiki from Pulse monitoring API"

    def _handle_update_with_inject(raw_args: str) -> str:
        """Slash command wrapper that hands default sync output to the agent loop."""
        raw = raw_args or ""
        raw_l = raw.strip().lower()
        if raw_l.startswith("tool") or raw_l.startswith("json"):
            return _handle_update_tool(raw_args)

        result = _handle_update_tool(raw_args)
        try:
            data = json.loads(result)
        except json.JSONDecodeError:
            inject = getattr(ctx, "inject_message", None)
            if callable(inject):
                ok = inject(result)
                if ok:
                    return "🔄 Sync complete. Handing off results to agent for review..."
                # Gateway mode: inject_message is callable but returns False
                # because there is no CLI ref. Return the result directly.
            return result

        if "error" in data:
            return f"⚠️ Sync failed: {data['error']}"

        intent = (data.get("intent") or {}).get("type")
        if intent == "none":
            # Use the prebuilt review prompt embedded in the JSON by _handle_update_tool
            review_prompt = data.get("review_prompt") or _build_review_prompt(data)
            inject = getattr(ctx, "inject_message", None)
            if callable(inject):
                ok = inject(review_prompt)
                if ok:
                    return "🔄 Sync complete. Handing off results to agent for review..."
                # Gateway/WebUI mode: inject_message is callable but returns False
                # because there is no CLI ref. Render a concise human-readable
                # summary instead of dumping the raw markdown review prompt.
                return _render_update_output(data)

        return _render_update_output(data)

    # ── Tool registration (primary interface for LLM agent loop) ──
    ctx.register_tool(
        name="update_infrastructure",
        toolset="infra",
        schema={
            "name": "update_infrastructure",
            "description": (
                "Sync homelab wiki from Pulse monitoring API and return a "
                "structured review prompt by default. Fetches live infrastructure "
                "data from Pulse (IPs, OS, node assignments, status), parses "
                "entity pages, cross-references, detects IP drift, and updates "
                "the wiki. Pass 'json' or 'tool' as raw_args for raw JSON output "
                "(escape hatch). Sub-commands: "
                '"status", "check <name>", "create <name>", '
                '"fix-ip <name>", "fix all", "cleanup".'
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "raw_args": {
                        "type": "string",
                        "description": (
                            'Optional sub-command or "json"/"tool" for raw output. '
                            'Default (no args): returns a review prompt. '
                            'Sub-commands: "status", "check <name>", '
                            '"create <name>", "fix-ip <name>", '
                            '"fix all", "cleanup"'
                        ),
                    },
                },
            },
        },
        handler=_handle_update_tool,
        description="Sync homelab wiki from Pulse API — returns review prompt by default",
    )

    # ── Review tool (always returns a review prompt) ──
    ctx.register_tool(
        name="update_infrastructure_review",
        toolset="infra",
        schema={
            "name": "update_infrastructure_review",
            "description": (
                "Run the infrastructure sync from Pulse API and return a structured "
                "review prompt. The prompt summarises changes, cross-reference issues, "
                "IP drift, orphaned pages, topology, and instructs the agent "
                "to follow up with the user via clarify(). Always returns a "
                "readable prompt — never raw JSON."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
        handler=_handle_update_and_review,
        description="Sync + review: returns a structured agent follow-up prompt",
    )

    # ── Slash command (backward-compatible convenience) ──
    ctx.register_command(
        "update-infrastructure",
        handler=_handle_update_with_inject,
        description=description,
    )

    # Register short aliases where the registrar permits; ignore silently if not.
    for alias in ("update-infra", "sync-infra"):
        try:
            ctx.register_command(alias, handler=_handle_update_with_inject, description=description)
        except Exception:
            pass

    logger.info(
        "update-infrastructure plugin registered "
        "(tools: update_infrastructure, update_infrastructure_review;"
        " command: /update-infrastructure)"
    )
