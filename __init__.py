"""update-infrastructure plugin — update_infrastructure tool + /update-infrastructure command.

Syncs the homelab wiki from the homepage dashboard config.
Homepage is the authority for services it knows about.
Wiki content never in homepage is preserved untouched.

Provides two interfaces:
- update_infrastructure tool (infra toolset) — returns structured JSON for LLM processing
- /update-infrastructure slash command — returns formatted markdown for direct display
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

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
        ``"check"``, ``"cleanup"``, ``"status"``, ``"echo"``, or ``"none"``.
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
# Intent handlers
# ---------------------------------------------------------------------------

def _handle_intent_create(name: str, wiki_root: Path, data: dict) -> str:
    """Scaffold an entity page for *name*."""
    services = data.get("services", [])
    svc = next((s for s in services if s["name"].lower() == name.lower()), None)

    href = (svc or {}).get("href", "")
    site_monitor = (svc or {}).get("site_monitor", "")
    server = (svc or {}).get("server", "")

    result = parser.scaffold_entity_page(wiki_root, name, href, site_monitor, server)

    if result["created"]:
        return f"✅ Scaffolded entity page: `{result['path']}`"
    return (
        f"ℹ️ Entity page already exists: `{result['path']}`"
        f" ({result.get('reason', '')})"
    )


def _handle_intent_fix_all(wiki_root: Path, cross_ref: dict) -> str:
    """Fix every IP-drift item found in *cross_ref*."""
    ip_drift = cross_ref.get("ip_drift", [])
    if not ip_drift:
        return "✅ No IP drift to fix."

    lines = ["**IP drift repairs:**", ""]
    for item in ip_drift:
        name = item["name"]
        new_ip = parser._extract_ip_from_url(item["site_monitor"])
        if not new_ip:
            lines.append(
                f"  - {name}: could not extract IP from"
                f" `{item['site_monitor']}` — skipped"
            )
            continue
        result = parser.fix_entity_ip(wiki_root, name, new_ip)
        if result["fixed"]:
            lines.append(f"  - ✅ {name}: ip updated to `{new_ip}`")
        else:
            lines.append(f"  - ❌ {name}: {result.get('reason', 'unknown error')}")

    return "\n".join(lines)


def _handle_intent_fix_ip(
    name: str,
    wiki_root: Path,
    cross_ref: dict,
    dry_run: bool = False,
) -> str:
    """Fix IP drift for *name* (optionally dry-run)."""
    ip_drift = cross_ref.get("ip_drift", [])
    item = next((i for i in ip_drift if i["name"].lower() == name.lower()), None)

    if item is None:
        return f"ℹ️ No IP drift detected for `{name}`."

    new_ip = parser._extract_ip_from_url(item["site_monitor"])
    if not new_ip:
        return f"❌ Could not extract IP from `{item['site_monitor']}` for `{name}`."

    result = parser.fix_entity_ip(wiki_root, name, new_ip, dry_run=dry_run)

    if dry_run:
        lines = [f"**Dry run — would fix `{name}`** (entity ip → `{new_ip}`):", ""]
        diff = result.get("diff", "")
        if diff:
            lines += ["```diff", diff, "```"]
        return "\n".join(lines)

    if result["fixed"]:
        return f"✅ `{name}`: ip updated to `{new_ip}` in `{result['path']}`"
    return f"❌ `{name}`: {result.get('reason', 'unknown error')}"


def _handle_intent_check(
    name: str,
    wiki_root: Path,
    data: dict,
    cross_ref: dict,
) -> str:
    """Deep-check one service across homepage, entity page, and cross-ref."""
    services = data.get("services", [])
    svc = next((s for s in services if s["name"].lower() == name.lower()), None)

    entity_pages = parser.load_entity_pages(wiki_root)
    slug = parser._slugify(name)
    entity = (entity_pages or {}).get(slug)

    lines = [f"**Deep check: {name}**", ""]

    if svc:
        lines.append(f"  📋 Homepage: ✅ present (group: {svc.get('group', '?')})")
        if svc.get("href"):
            lines.append(f"     href: `{svc['href']}`")
        if svc.get("site_monitor"):
            lines.append(f"     siteMonitor: `{svc['site_monitor']}`")
        if svc.get("server"):
            lines.append(f"     server: `{svc['server']}`")
    else:
        lines.append("  📋 Homepage: ❌ not found in current services")

    if entity:
        lines.append(f"  📄 Entity page: ✅ `entities/{slug}.md`")
        for field in ("ip", "status", "type", "host"):
            if entity.get(field):
                lines.append(f"     {field}: `{entity[field]}`")
    else:
        lines.append(f"  📄 Entity page: ❌ not found (`entities/{slug}.md`)")
        if svc:
            lines.append(
                f'     → Run `/update-infrastructure create "{name}"` to scaffold one'
            )

    drift_item = next(
        (i for i in cross_ref.get("ip_drift", []) if i["name"].lower() == name.lower()),
        None,
    )
    if drift_item:
        lines.append(
            f"  🔀 IP drift: ⚠️ siteMonitor=`{drift_item['site_monitor']}`,"
            f" entity ip=`{drift_item['entity_ip']}`"
        )
        lines.append(
            f'     → Run `/update-infrastructure fix-ip "{name}"` to repair'
        )
    elif entity and svc:
        lines.append("  🔀 IP: ✅ no drift detected")

    return "\n".join(lines)


def _handle_intent_cleanup(cross_ref: dict) -> str:
    """List orphaned entity pages with clear-action suggestions."""
    orphaned = cross_ref.get("orphaned_entity_pages", [])
    if not orphaned:
        return "✅ No orphaned entity pages found."

    lines = [f"**Orphaned entity pages ({len(orphaned)}):**", ""]
    for page in orphaned:
        lines.append(f"  - `{page}` — no matching homepage service")
    lines += [
        "",
        "To remove an orphaned page, delete it from the `entities/` directory.",
        "These pages are never auto-deleted by this plugin.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Cross-reference formatter (command response)
# ---------------------------------------------------------------------------

def _format_topology_summary(machines_data: dict) -> str:
    """Return a markdown summary of the network topology from entity pages."""
    machines = machines_data.get("machines", [])
    if not machines:
        return ""

    # Count by category
    counts: dict[str, int] = {}
    for m in machines:
        cat = m.get("category", "other")
        counts[cat] = counts.get(cat, 0) + 1

    # Map category to emoji + label
    cat_display = {
        "infrastructure": ("🖥️", "Infrastructure"),
        "networking": ("🌐", "Networking"),
        "container": ("📦", "Containers / VMs"),
        "device": ("📱", "Devices"),
    }

    lines = ["## Network Topology", ""]
    lines.append(f"**{len(machines)} machines** across {len(counts)} categories:")
    lines.append("")

    for cat in ["infrastructure", "networking", "container", "device"]:
        if cat in counts:
            emoji, label = cat_display.get(cat, ("•", cat.title()))
            lines.append(f"  {emoji} **{label}:** {counts[cat]}")

    # Show remaining categories
    for cat, count in sorted(counts.items()):
        if cat not in cat_display:
            lines.append(f"  • **{cat.title()}:** {count}")

    # Show host→child connections
    slug_map = {m["slug"]: m for m in machines}
    connections: list[tuple[str, str]] = []
    for m in machines:
        host_slug = m.get("host", "")
        if host_slug and host_slug in slug_map:
            connections.append((slug_map[host_slug]["name"], m["name"]))

    if connections:
        lines.append("")
        lines.append("**Connections:**")
        for parent, child in sorted(connections, key=lambda x: x[0]):
            lines.append(f"  {parent} → {child}")

    lines.append("")
    return "\n".join(lines)


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


def _format_cross_reference(cross_ref: dict) -> str:
    """Return a markdown string summarising cross-reference findings."""
    lines = ["## Cross-Reference Findings", ""]

    if not cross_ref.get("entities_dir_found", False):
        lines.append(
            "*(Skipped — entity pages directory not found. "
            "Create `entities/<name>.md` files with YAML frontmatter to enable checks.)*"
        )
        return "\n".join(lines)

    missing = cross_ref.get("missing_entity_pages", [])
    ip_drift = cross_ref.get("ip_drift", [])
    orphaned = cross_ref.get("orphaned_entity_pages", [])
    unmapped_servers = cross_ref.get("unmapped_servers", [])

    if not missing and not ip_drift and not orphaned and not unmapped_servers:
        lines.append("✅ All clear — no issues found")
        return "\n".join(lines)

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


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

def _get_wiki_path() -> Path:
    """Resolve the wiki root path."""
    wiki_root = os.environ.get("WIKI_PATH", os.path.expanduser(config.DEFAULT_WIKI_ROOT))
    return Path(wiki_root)


def _handle_update(raw_args: str) -> str:
    """Handler for /update-infrastructure."""
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
            elapsed = time.monotonic() - t_start
            return (
                f"⏱ Status check started: {started_at}\n\n"
                + _format_cross_reference(cross_ref)
                + f"\n\n✅ Completed in {elapsed:.2f}s"
            )

        # --- Steps 1–4: full sync ---
        lines_progress = [f"⏱ Sync started: {started_at}", ""]

        # 1. Fetch and parse homepage config
        config_dir = parser.fetch_repo()
        data = parser.parse_all(config_dir)

        svc_count = len(data.get("services", []))
        sub_count = len(data.get("subdomains", []))

        # 2. Cross-reference against entity pages
        entity_pages = parser.load_entity_pages(wiki_root)
        cross_ref = parser.cross_reference(data, entity_pages)
        data["cross_ref"] = cross_ref
        # Pass entity directory path so topology builder can read entity pages
        data["entities_dir"] = str(wiki_root / "entities")

        # 3. Update wiki page
        page_path = wiki_root / WIKI_PAGE
        log_path = wiki_root / "log.md"

        result = updater.update_wiki_page(str(page_path), data)

        # 4. Update wiki log
        updater.update_wiki_log(str(log_path), result)

        # 5. Build base response
        lines = lines_progress + [
            "## Infrastructure Sync Complete",
            "",
            f"**Source:** {svc_count} services, {sub_count} subdomains from homepage config",
            "",
        ]

        # Topology summary
        machines_data = parser_wiki.parse_entity_pages(str(wiki_root / "entities"))
        topo_summary = _format_topology_summary(machines_data)
        if topo_summary:
            lines.append(topo_summary)

        # Cross-reference findings
        lines.append(_format_cross_reference(cross_ref))
        lines.append("")
        lines.append("---")
        lines.append("")

        if result["changed"]:
            if result["sections_updated"]:
                lines.append(f"**Updated:** {', '.join(result['sections_updated'])}")
            if result["sections_added"]:
                lines.append(f"**Added:** {', '.join(result['sections_added'])}")
            if result["sections_unchanged"]:
                lines.append(f"**Unchanged:** {', '.join(result['sections_unchanged'])}")
            lines.append("")
            lines.append(f"Wiki page updated: `{wiki_root / WIKI_PAGE}`")
        else:
            lines.append("**No changes needed** — wiki already matches homepage config.")

        # 6. Execute known intents
        if intent == "create":
            name = intent_params.get("name", "")
            lines += ["", "---", "", _handle_intent_create(name, wiki_root, data)]

        elif intent == "fix_all":
            lines += ["", "---", "", _handle_intent_fix_all(wiki_root, cross_ref)]

        elif intent == "fix_ip":
            name = intent_params.get("name", "")
            dry_run = intent_params.get("dry_run", False)
            lines += ["", "---", "", _handle_intent_fix_ip(name, wiki_root, cross_ref, dry_run)]

        elif intent == "check":
            name = intent_params.get("name", "")
            lines += ["", "---", "", _handle_intent_check(name, wiki_root, data, cross_ref)]

        elif intent == "cleanup":
            lines += ["", "---", "", _handle_intent_cleanup(cross_ref)]

        elif intent == "echo":
            lines += ["", "---", "", f"> **Extra instructions:** {intent_params['text']}"]

        elapsed = time.monotonic() - t_start
        lines += ["", f"✅ Completed in {elapsed:.2f}s"]
        return "\n".join(lines)

    except Exception as e:
        logger.exception("update-infrastructure failed")
        elapsed = time.monotonic() - t_start
        return f"**Error** (after {elapsed:.2f}s): {e}"


# ---------------------------------------------------------------------------
# Tool handler (returns JSON for LLM agent loop)
# ---------------------------------------------------------------------------

def _handle_update_tool(raw_args: str = "") -> str:
    """Tool handler for update_infrastructure — returns structured JSON.

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
                intent_result = {"fixed": False, "reason": f"no IP drift detected for {name}"}
            else:
                new_ip = parser._extract_ip_from_url(item["site_monitor"])
                if not new_ip:
                    intent_result = {
                        "fixed": False,
                        "reason": f"could not extract IP from {item['site_monitor']}",
                    }
                else:
                    r = parser.fix_entity_ip(wiki_root, name, new_ip, dry_run=dry_run)
                    intent_result = {"fixed": r["fixed"], "dry_run": dry_run}
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
                "Sync homelab wiki from homepage dashboard config — fetches homepage "
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

