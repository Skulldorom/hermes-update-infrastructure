"""update-infrastructure plugin -- update_infrastructure tool + /update-infrastructure command.

Syncs the homelab wiki from the homepage dashboard config.
Homepage is the authority for services it knows about.
Wiki content never in homepage is preserved untouched.

Provides two interfaces:
- update_infrastructure tool (infra toolset) -- returns structured JSON for LLM processing
- /update-infrastructure slash command -- renders human-readable summary directly

Sub-commands (via intent parsing):
  /update-infrastructure                  -> full sync with human-readable report
  /update-infrastructure tool             -> raw JSON output (for scripting)
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

from . import config, parser, updater
from . import parser_wiki

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
    wiki_root = os.environ.get("WIKI_PATH", os.path.expanduser(config.DEFAULT_WIKI_ROOT))
    return Path(wiki_root)


def _handle_update(raw_args: str) -> str:
    """Handler for /update-infrastructure slash command.

    Renders a human-readable summary from the JSON output so the user
    sees a concise report without raw JSON in the terminal.

    Pass ``"tool"`` as *raw_args* to get raw JSON output with no human
    rendering (escape hatch for scripting / integration).
    """
    raw = raw_args or ""
    if raw.strip().lower().startswith("tool"):
        return _handle_update_tool(raw_args)

    json_str = _handle_update_tool(raw_args)
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        return json_str  # fallback to raw

    if "error" in data:
        return f"\u26a0\ufe0f Sync failed: {data['error']}"

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
            lines.append(f"   Wiki: {data.get('wiki_file', '?')}")

            sections = data.get("sections", {})
            updated = sections.get("updated", [])
            added = sections.get("added", [])
            if updated or added:
                lines.append("")
                lines.append("📝 Changes:")
                if updated:
                    lines.append(f"   Updated: {', '.join(updated)}")
                if added:
                    lines.append(f"   Added: {', '.join(added)}")
            elif changed:
                lines.append("\n📝 Sections changed (detail unavailable)")

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


def _render_cross_ref_summary(lines: list[str], cross_ref: dict) -> None:
    """Append cross-reference findings as human-readable lines."""
    if not cross_ref.get("entities_dir_found", False):
        lines.append("\n\u274c Cross-reference skipped -- entities/ directory not found")
        return

    missing = cross_ref.get("missing_entity_pages", [])
    ip_drift = cross_ref.get("ip_drift", [])
    orphaned = cross_ref.get("orphaned_entity_pages", [])
    unmapped = cross_ref.get("unmapped_servers", [])

    issues = bool(missing or ip_drift or unmapped)
    if not issues and not orphaned:
        lines.append("\n\u2705 Cross-reference: all clear")
        return

    lines.append("")
    if missing:
        lines.append(f"\n\u26a0\ufe0f {len(missing)} service(s) without entity pages:")
        for svc in missing:
            href = svc.get("href", "")
            h = f" ({href})" if href else ""
            lines.append(f"   \u2022 {svc['name']}{h}")
            lines.append(f'     \u2192 /update-infrastructure create "{svc["name"]}"')

    if ip_drift:
        lines.append(f"\n\u26a0\ufe0f {len(ip_drift)} IP drift(s) detected:")
        for item in ip_drift:
            lines.append(
                f"   \u2022 {item['name']}:"
                f" siteMonitor={item['site_monitor']},"
                f" entity ip={item['entity_ip']} (MISMATCH)"
            )
            lines.append(f'     \u2192 /update-infrastructure fix-ip "{item["name"]}"')
        if len(ip_drift) > 1:
            lines.append("     \u2192 /update-infrastructure fix all")

    if unmapped:
        lines.append(f"\n\u26a0\ufe0f Unmapped server fields:")
        for item in unmapped:
            sug = item.get("suggestions", [])
            sug_str = f" -- did you mean {', '.join(sug)}?" if sug else ""
            lines.append(f"   \u2022 {item['name']}: server={item['server']}{sug_str}")

    if orphaned:
        lines.append(f"\n\u2139\ufe0f {len(orphaned)} orphaned entity page(s):")
        for p in orphaned:
            lines.append(f"   \u2022 {p}")
        lines.append("   \u2192 /update-infrastructure cleanup")


def _render_status(data: dict) -> str:
    """Render a /update-infrastructure status check."""
    cross_ref = data.get("cross_reference", {})
    source = data.get("source", {})
    lines = [
        "\U0001f50d Infrastructure status check",
        f"   Services in homepage: {source.get('services', 0)}",
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
        f"   In homepage: {result.get('in_homepage', False)}",
    ]
    if result.get("homepage_group"):
        lines.append(f"   Group: {result['homepage_group']}")
    if result.get("homepage_href"):
        lines.append(f"   Href: {result['homepage_href']}")
    if result.get("homepage_site_monitor"):
        lines.append(f"   Site monitor: {result['homepage_site_monitor']}")
    if result.get("homepage_server"):
        lines.append(f"   Server: {result['homepage_server']}")
    lines.append(f"   Entity page: {result.get('has_entity_page', False)}")
    if result.get("entity_page"):
        lines.append(f"   Entity file: {result['entity_page']}")
    if result.get("entity_ip"):
        lines.append(f"   Entity IP: {result['entity_ip']}")
    drift = result.get("ip_drift")
    if drift:
        lines.append(
            f"\n\u26a0\ufe0f IP drift:"
            f" siteMonitor={drift['site_monitor']},"
            f" entity={drift['entity_ip']}"
        )
        lines.append(f'   \u2192 /update-infrastructure fix-ip "{name}"')
    elif result.get("entity_ip"):
        lines.append("\n\u2705 No IP drift detected")
    return "\n".join(lines)


def _render_cleanup(result: dict) -> str:
    """Render orphaned entity page list."""
    orphaned = result.get("orphaned_pages", [])
    if not orphaned:
        return "\u2705 No orphaned entity pages found"
    lines = [f"\u2139\ufe0f {len(orphaned)} orphaned entity page(s):"]
    for p in orphaned:
        lines.append(f"   \u2795 {p}")
    return "\n".join(lines)


def _render_echo(intent: dict) -> str:
    """Render an echo / unrecognised-command intent."""
    return f"> {intent.get('result', {}).get('echoed_text', '')}"


# Dispatch table: intent_type -> renderer(result_dict) -> human-readable string
_INTENT_RENDERERS: dict[str, Any] = {
    "create": _render_create,
    "fix_all": _render_fix_all,
    "fix_ip": _render_fix_ip,
    "check": _render_check,
    "cleanup": _render_cleanup,
}


# ---------------------------------------------------------------------------
# Tool handler (returns JSON for LLM agent loop)
# ---------------------------------------------------------------------------

def _handle_update_tool(raw_args: str = "") -> str:
    """Tool handler for update_infrastructure -- returns structured JSON.

    Runs the same sync pipeline as the slash command but returns a JSON
    dict so the LLM can process, summarise, and follow up naturally.
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
            config_dir = parser.fetch_repo()
            data = parser.parse_all(config_dir)
            cross_ref = parser.cross_reference(data, entity_pages)
            elapsed = round(time.monotonic() - t_start, 2)
            return json.dumps({
                "sync_completed_at": started_at,
                "elapsed_seconds": elapsed,
                "wiki_file": str(wiki_root / WIKI_PAGE),
                "changed": False,
                "source": {"services": len(data.get("services", [])),
                           "subdomains": len(data.get("subdomains", []))},
                "cross_reference": cross_ref,
                "intent": {"type": "status"},
            })

        # --- Full sync pipeline ---
        config_dir = parser.fetch_repo()
        data = parser.parse_all(config_dir)

        svc_count = len(data.get("services", []))
        sub_count = len(data.get("subdomains", []))

        entity_pages = parser.load_entity_pages(wiki_root)
        cross_ref = parser.cross_reference(data, entity_pages)
        data["cross_ref"] = cross_ref
        data["entities_dir"] = str(wiki_root / "entities")

        page_path = wiki_root / WIKI_PAGE
        log_path = wiki_root / "log.md"

        result = updater.update_wiki_page(str(page_path), data)
        updater.update_wiki_log(str(log_path), result)

        machines_data = parser_wiki.parse_entity_pages(str(wiki_root / "entities"))
        topology = _build_topology_dict(machines_data)

        # Execute intent and capture structured result
        intent_result = None
        if intent == "create":
            name = intent_params.get("name", "")
            services = data.get("services", [])
            svc = next((s for s in services if s["name"].lower() == name.lower()), None)
            href = (svc or {}).get("href", "")
            site_monitor = (svc or {}).get("site_monitor", "")
            server = (svc or {}).get("server", "")
            r = parser.scaffold_entity_page(wiki_root, name, href, site_monitor, server)
            intent_result = {"created": r["created"], "path": r["path"]}
            if not r["created"]:
                intent_result["reason"] = r.get("reason", "")

        elif intent == "fix_all":
            ip_drift = cross_ref.get("ip_drift", [])
            fixed = []
            errors = []
            for item in ip_drift:
                new_ip = parser._extract_ip_from_url(item["site_monitor"])
                if not new_ip:
                    errors.append({"name": item["name"], "reason": "could not extract IP"})
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
                new_ip = parser._extract_ip_from_url(item["site_monitor"])
                if not new_ip:
                    intent_result = {
                        "name": name,
                        "fixed": False,
                        "reason": f"could not extract IP from {item['site_monitor']}",
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
                "in_homepage": svc is not None,
                "homepage_group": svc.get("group") if svc else None,
                "homepage_href": svc.get("href") if svc else None,
                "homepage_site_monitor": svc.get("site_monitor") if svc else None,
                "homepage_server": svc.get("server") if svc else None,
                "has_entity_page": entity is not None,
                "entity_page": f"entities/{slug}.md" if entity else None,
                "entity_ip": entity.get("ip") if entity else None,
                "ip_drift": {
                    "site_monitor": drift_item["site_monitor"],
                    "entity_ip": drift_item["entity_ip"],
                } if drift_item else None,
            }

        elif intent == "cleanup":
            orphaned = cross_ref.get("orphaned_entity_pages", [])
            intent_result = {"orphaned_pages": orphaned}

        elif intent == "echo":
            intent_result = {"echoed_text": intent_params.get("text", "")}

        elapsed = round(time.monotonic() - t_start, 2)

        return json.dumps({
            "sync_completed_at": started_at,
            "elapsed_seconds": elapsed,
            "wiki_file": str(wiki_root / WIKI_PAGE),
            "changed": result["changed"],
            "sections": {
                "updated": result["sections_updated"],
                "added": result["sections_added"],
                "unchanged": result["sections_unchanged"],
            },
            "source": {"services": svc_count, "subdomains": sub_count},
            "cross_reference": cross_ref,
            "topology": topology,
            "intent": {
                "type": intent,
                "result": intent_result,
            },
        })

    except Exception as e:
        logger.exception("update_infrastructure tool failed")
        elapsed = round(time.monotonic() - t_start, 2)
        return json.dumps({
            "sync_completed_at": started_at,
            "elapsed_seconds": elapsed,
            "error": str(e),
        })


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

def register(ctx):
    """Register the /update-infrastructure slash command and update_infrastructure tool."""
    description = "Sync homelab wiki from homepage dashboard config"

    # ── Tool registration (primary interface for LLM agent loop) ──
    ctx.register_tool(
        name="update_infrastructure",
        toolset="infra",
        schema={
            "name": "update_infrastructure",
            "description": (
                "Sync homelab wiki from homepage dashboard config -- fetches homepage "
                "services, parses entity pages, cross-references, detects IP drift, "
                "and updates the wiki. Pass raw_args for sub-commands like "
                '"status", "check pihole", "create mediawiki", '
                '"fix-ip pihole", "fix all", "cleanup".'
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "raw_args": {
                        "type": "string",
                        "description": (
                            'Optional sub-command: "status", "check <name>", '
                            '"create <name>", "fix-ip <name>", '
                            '"fix all", "cleanup"'
                        ),
                    },
                },
            },
        },
        handler=_handle_update_tool,
        description="Sync homelab wiki from homepage dashboard config",
    )

    # ── Slash command (backward-compatible convenience) ──
    ctx.register_command(
        "update-infrastructure",
        handler=_handle_update,
        description=description,
    )

    # Register short aliases where the registrar permits; ignore silently if not.
    for alias in ("update-infra", "sync-infra"):
        try:
            ctx.register_command(alias, handler=_handle_update, description=description)
        except Exception:
            pass

    logger.info(
        "update-infrastructure plugin registered "
        "(tool: update_infrastructure, command: /update-infrastructure)"
    )

