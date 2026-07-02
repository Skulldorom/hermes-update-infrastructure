"""update-infrastructure plugin — /update-infrastructure slash command.

Syncs the homelab wiki from the homepage dashboard config.
Homepage is the authority for services it knows about.
Wiki content never in homepage is preserved untouched.
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from . import parser, updater

logger = logging.getLogger(__name__)

WIKI_PAGE = "guides/homelab-infrastructure.md"


def _get_wiki_path() -> Path:
    """Resolve the wiki root path."""
    wiki_root = os.environ.get("WIKI_PATH", os.path.expanduser("~/wiki"))
    return Path(wiki_root)


def _handle_update(raw_args: str) -> str:
    """Handler for /update-infrastructure."""
    try:
        # 1. Fetch and parse homepage config
        config_dir = parser.fetch_repo()
        data = parser.parse_all(config_dir)

        svc_count = len(data.get("services", []))
        sub_count = len(data.get("subdomains", []))

        # 2. Update wiki page
        wiki_root = _get_wiki_path()
        page_path = wiki_root / WIKI_PAGE
        log_path = wiki_root / "log.md"

        result = updater.update_wiki_page(str(page_path), data)

        # 3. Update wiki log
        updater.update_wiki_log(str(log_path), result)

        # 4. Build response
        lines = [
            "## Infrastructure Sync Complete",
            "",
            f"**Source:** {svc_count} services, {sub_count} subdomains from homepage config",
            "",
        ]

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
