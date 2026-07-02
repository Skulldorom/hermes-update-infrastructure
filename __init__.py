"""update-infrastructure plugin — /update-infrastructure slash command.

Syncs the homelab wiki from the homepage dashboard config.
Homepage is the authority for services it knows about.
Wiki content never in homepage is preserved untouched.
"""

import logging
import os
from pathlib import Path

from . import parser, updater

logger = logging.getLogger(__name__)

WIKI_PAGE = "guides/homelab-infrastructure.md"


def _get_wiki_path() -> Path:
    """Resolve the wiki root path."""
    wiki_root = os.environ.get("WIKI_PATH", os.path.expanduser("~/wiki"))
    return Path(wiki_root)


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
        lines.append("")

    if ip_drift:
        lines.append("⚠️ IP drift detected:")
        for item in ip_drift:
            lines.append(
                f"  - {item['name']}: siteMonitor={item['site_monitor']},"
                f" entity ip={item['entity_ip']} (MISMATCH)"
            )
        lines.append("")

    if orphaned:
        lines.append("ℹ️ Orphaned entity pages (not in homepage):")
        for page in orphaned:
            lines.append(f"  - {page}")
        lines.append("")

    if unmapped_servers:
        lines.append("⚠️ Unmapped server fields:")
        for item in unmapped_servers:
            lines.append(f"  - {item['name']}: server=`{item['server']}` — no matching entity")
        lines.append("")

    return "\n".join(lines)


def _handle_update(raw_args: str) -> str:
    """Handler for /update-infrastructure."""
    extra = raw_args.strip() if raw_args else ""

    try:
        wiki_root = _get_wiki_path()

        # 1. Fetch and parse homepage config
        config_dir = parser.fetch_repo()
        data = parser.parse_all(config_dir)

        svc_count = len(data.get("services", []))
        sub_count = len(data.get("subdomains", []))

        # 2. Cross-reference against entity pages
        entity_pages = parser.load_entity_pages(wiki_root)
        cross_ref = parser.cross_reference(data, entity_pages)
        data["cross_ref"] = cross_ref

        # 3. Update wiki page
        page_path = wiki_root / WIKI_PAGE
        log_path = wiki_root / "log.md"

        result = updater.update_wiki_page(str(page_path), data)

        # 4. Update wiki log
        updater.update_wiki_log(str(log_path), result)

        # 5. Build response
        lines = [
            "## Infrastructure Sync Complete",
            "",
            f"**Source:** {svc_count} services, {sub_count} subdomains from homepage config",
            "",
        ]

        # Cross-reference findings
        lines.append(_format_cross_reference(cross_ref))
        lines.append("")
        lines.append("---")
        lines.append("")

        if result["changed"]:
            if result["sections_updated"]:
                lines.append(
                    f"**Updated:** {', '.join(result['sections_updated'])}"
                )
            if result["sections_added"]:
                lines.append(
                    f"**Added:** {', '.join(result['sections_added'])}"
                )
            if result["sections_unchanged"]:
                lines.append(
                    f"**Unchanged:** {', '.join(result['sections_unchanged'])}"
                )
            lines.append("")
            lines.append(f"Wiki page updated: `{wiki_root / WIKI_PAGE}`")
        else:
            lines.append("**No changes needed** — wiki already matches homepage config.")

        # Append any extra instructions supplied by the user
        if extra:
            lines.append("")
            lines.append("---")
            lines.append("")
            lines.append(f"> **Extra instructions:** {extra}")

        return "\n".join(lines)

    except Exception as e:
        logger.exception("update-infrastructure failed")
        return f"**Error:** {e}"


def register(ctx):
    """Register the /update-infrastructure slash command."""
    ctx.register_command(
        "update-infrastructure",
        handler=_handle_update,
        description="Sync homelab wiki from homepage dashboard config",
    )
    logger.info("update-infrastructure plugin registered (/update-infrastructure)")
