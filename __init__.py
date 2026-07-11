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

try:
    from . import config, parser, parser_wiki, renderer, updater
    from . import npm_client as _npm_client
except ImportError:  # pragma: no cover - supports direct module loading in tests
    import config  # type: ignore
    import npm_client as _npm_client  # type: ignore
    import parser  # type: ignore
    import parser_wiki  # type: ignore
    import renderer  # type: ignore
    import updater  # type: ignore

logger = logging.getLogger(__name__)


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


def _get_wiki_page_path(wiki_root: Path) -> Path:
    """Resolve the configured infrastructure wiki page under *wiki_root*."""
    return wiki_root / config.get_wiki_page()


# ---------------------------------------------------------------------------
# Rendering lives in renderer.py so importing it does not run a sync.
# Private aliases remain available here for backward compatibility.
# ---------------------------------------------------------------------------

_render_update_output = renderer.render_update_output
_build_review_prompt = renderer.build_review_prompt


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
                "wiki_file": str(_get_wiki_page_path(wiki_root)),
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
                "wiki_file": str(_get_wiki_page_path(wiki_root)),
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
            "wiki_file": str(_get_wiki_page_path(wiki_root)),
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
