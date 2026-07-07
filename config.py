"""Configuration variables for the update-infrastructure plugin.

All configurable values are read from the ``infrastructure-plugin:`` section
of Hermes ``config.yaml``.  If the section or a key is absent the hard-coded
default defined in this module is used.

Use the public ``get_*()`` functions — they implement the resolution chain
automatically.  The raw constants below are the final fallback values.
"""

from pathlib import Path
import os


# ---------------------------------------------------------------------------
# Hermes config.yaml integration
# ---------------------------------------------------------------------------

def _load_infra_config() -> dict:
    """Load the ``infrastructure-plugin:`` section from Hermes ``config.yaml``.

    Returns an empty dict when Hermes is not installed, not running, or when
    the section is absent, so every getter falls through to the code default.
    """
    try:
        from hermes_cli.config import load_config  # type: ignore
        cfg = load_config()
        return cfg.get("infrastructure-plugin", {}) if isinstance(cfg, dict) else {}
    except Exception:  # noqa: BLE001 — Hermes absent or config unreadable
        return {}

# ---------------------------------------------------------------------------
# Pulse API source
# ---------------------------------------------------------------------------

#: Base URL of the Pulse monitoring API.
#: Set via ``infrastructure-plugin.pulse_url`` in Hermes ``config.yaml``.
PULSE_API_URL: str = ""

#: Name of the environment variable that holds the Pulse API token.
#: The token must have ``monitoring:read``, ``agent:read``, and ``settings:read`` scopes.
PULSE_API_TOKEN_ENV_VAR: str = "PULSE_API_TOKEN"

#: Resource types to fetch from Pulse when building the service list.
#: Comma-separated string matching Pulse ``/api/resources?type=`` parameter.
PULSE_RESOURCE_TYPES: str = "container,vm,node,docker-container"

# ---------------------------------------------------------------------------
# Wiki paths
# ---------------------------------------------------------------------------

#: Default wiki root when no value is configured.
DEFAULT_WIKI_ROOT: str = "~/wiki"

#: Path to the main infrastructure page, relative to the wiki root.
#: The Hermes agent updates this page surgically after reading the generated data file.
WIKI_PAGE: str = "guides/homelab-infrastructure.md"

# ---------------------------------------------------------------------------
# Generated data file (auto-generated content, separate from the wiki page)
# ---------------------------------------------------------------------------

#: Directory (relative to the wiki root) where the generated data file is written.
GENERATED_OUTPUT_DIR: str = "generated"

#: Filename of the auto-generated infrastructure data file.
GENERATED_DATA_FILE: str = "infrastructure-data.md"

# ---------------------------------------------------------------------------
# Subdomain extraction
# ---------------------------------------------------------------------------

#: Default subdomain regex patterns.  Each pattern must capture the subdomain
#: label in group 1.  Configure via ``infrastructure-plugin.subdomain_patterns``
#: in Hermes ``config.yaml``.
DEFAULT_SUBDOMAIN_PATTERNS: list[str] = []

# ---------------------------------------------------------------------------
# NGINX Proxy Manager (NPM) API source
# ---------------------------------------------------------------------------

#: Base URL of the NGINX Proxy Manager API.
#: Set via ``infrastructure-plugin.npm_url`` in Hermes ``config.yaml``.
NPM_URL: str = ""

#: Email address used to authenticate with the NPM API.
#: Set via the ``NPM_EMAIL`` environment variable.
NPM_EMAIL: str = ""

#: Password used to authenticate with the NPM API.
#: Set via the ``NPM_PASSWORD`` environment variable.
NPM_PASSWORD: str = ""

# ---------------------------------------------------------------------------
# Noise / cross-reference filters
# ---------------------------------------------------------------------------

#: Generic infrastructure / platform names that are *not* specific hosts.
#: Services whose slugified name matches one of these are excluded from the
#: "missing entity pages" report unless an entity page already exists.
#: Extend via ``infrastructure-plugin.generic_infra_names`` in config.yaml.
DEFAULT_GENERIC_INFRA_NAMES: frozenset[str] = frozenset({
    "synology", "glances", "dozzle", "proxmox", "nginx", "authentik",
    "traefik", "caddy", "haproxy", "portainer", "watchtower", "homepage",
    "grafana", "prometheus", "loki", "influxdb", "telegraf", "netdata",
    "unifi", "pihole", "adguard-home", "adguardhome", "wireguard", "tailscale",
    "docker", "kubernetes", "k8s", "vaultwarden", "bitwarden",
    "nextcloud", "jellyfin", "plex", "emby", "sonarr", "radarr",
    "lidarr", "readarr", "prowlarr", "sabnzbd", "qbittorrent", "transmission",
    "overseerr", "tautulli", "uptime-kuma", "changedetection",
    "gitea", "forgejo", "gitlab", "immich", "photoprism", "paperless-ngx", "paperless",
})

#: Frontmatter ``type`` values that indicate personal devices / people (not
#: services).  Entity pages with these types are excluded from orphaned-page
#: reports.
PERSONAL_DEVICE_TYPES: frozenset[str] = frozenset({
    "person", "people", "phone", "tablet", "device", "mobile", "laptop",
})


# ---------------------------------------------------------------------------
# Public getter functions (config.yaml → code default)
# ---------------------------------------------------------------------------

def get_wiki_root() -> str:
    """Return the wiki root path.

    Resolution order:
    1. ``infrastructure-plugin.wiki_root`` in Hermes ``config.yaml``
    2. ``~/wiki`` (default)
    """
    cfg = _load_infra_config()
    if "wiki_root" in cfg:
        return os.path.expanduser(str(cfg["wiki_root"]))
    return os.path.expanduser(DEFAULT_WIKI_ROOT)


def get_wiki_page() -> str:
    """Return the wiki infrastructure page path (relative to wiki root).

    Resolution order:
    1. ``infrastructure-plugin.wiki_page`` in Hermes ``config.yaml``
    2. ``guides/homelab-infrastructure.md`` (default)
    """
    cfg = _load_infra_config()
    return str(cfg["wiki_page"]) if "wiki_page" in cfg else WIKI_PAGE


def get_subdomain_patterns() -> list[str]:
    """Return the list of subdomain extraction regex patterns.

    Resolution order:
    1. ``infrastructure-plugin.subdomain_patterns`` in Hermes ``config.yaml``
    2. ``DEFAULT_SUBDOMAIN_PATTERNS`` (empty list by default)
    """
    cfg = _load_infra_config()
    if "subdomain_patterns" in cfg:
        raw = cfg["subdomain_patterns"]
        if isinstance(raw, list):
            return [str(p) for p in raw]
        return [str(raw)]
    return list(DEFAULT_SUBDOMAIN_PATTERNS)


def get_generic_infra_names() -> frozenset[str]:
    """Return the set of generic infrastructure names to filter from reports.

    Resolution order:
    1. ``infrastructure-plugin.generic_infra_names`` in Hermes ``config.yaml``
       (replaces the default set entirely — add your own list including any
       names you want to keep from the defaults)
    2. ``DEFAULT_GENERIC_INFRA_NAMES`` (default)
    """
    cfg = _load_infra_config()
    if "generic_infra_names" in cfg:
        raw = cfg["generic_infra_names"]
        if isinstance(raw, list):
            return frozenset(str(n).strip().lower() for n in raw if str(n).strip())
        return frozenset({str(raw).strip().lower()})
    return DEFAULT_GENERIC_INFRA_NAMES


def get_personal_device_types() -> frozenset[str]:
    """Return the set of entity ``type`` values treated as personal devices.

    Resolution order:
    1. ``infrastructure-plugin.personal_device_types`` in Hermes ``config.yaml``
    2. ``PERSONAL_DEVICE_TYPES`` (default)
    """
    cfg = _load_infra_config()
    if "personal_device_types" in cfg:
        raw = cfg["personal_device_types"]
        if isinstance(raw, list):
            return frozenset(str(t).strip().lower() for t in raw if str(t).strip())
        return frozenset({str(raw).strip().lower()})
    return PERSONAL_DEVICE_TYPES


def get_server_aliases() -> dict[str, list[str]]:
    """Return the server alias mapping (short name → list of entity slugs).

    Resolution order:
    1. ``infrastructure-plugin.server_aliases`` in Hermes ``config.yaml``
       (dict where values are strings or pipe-separated strings)
    2. Empty dict (default)
    """
    cfg = _load_infra_config()
    if "server_aliases" in cfg:
        raw = cfg["server_aliases"]
        if isinstance(raw, dict):
            result: dict[str, list[str]] = {}
            for k, v in raw.items():
                key = str(k).strip().lower()
                if not key:
                    continue
                if isinstance(v, list):
                    result[key] = [str(i).strip() for i in v if str(i).strip()]
                else:
                    result[key] = [i.strip() for i in str(v).split("|") if i.strip()]
            return result
    return {}


def get_suppressed_entity_names() -> frozenset[str]:
    """Return service names to skip in the missing-entity-page report.

    Resolution order:
    1. ``infrastructure-plugin.suppress_entity_check`` in Hermes ``config.yaml``
       (list of display names)
    2. Empty frozenset (default)
    """
    cfg = _load_infra_config()
    if "suppress_entity_check" in cfg:
        raw = cfg["suppress_entity_check"]
        if isinstance(raw, list):
            return frozenset(str(n).strip().lower() for n in raw if str(n).strip())
        val = str(raw).strip()
        if val:
            return frozenset(n.strip().lower() for n in val.split(",") if n.strip())
        return frozenset()
    return frozenset()


def get_pulse_url() -> str:
    """Return the Pulse API base URL.

    Resolution order:
    1. ``infrastructure-plugin.pulse_url`` in Hermes ``config.yaml``
    2. ``PULSE_API_URL`` constant (empty string default)
    """
    cfg = _load_infra_config()
    if "pulse_url" in cfg:
        return str(cfg["pulse_url"]).rstrip("/")
    return PULSE_API_URL


def get_pulse_timeout() -> int:
    """Return the Pulse API request timeout in seconds.

    Resolution order:
    1. ``infrastructure-plugin.pulse_timeout`` in Hermes ``config.yaml``
    2. ``30`` (default)
    """
    cfg = _load_infra_config()
    if "pulse_timeout" in cfg:
        try:
            return int(cfg["pulse_timeout"])
        except (ValueError, TypeError):
            pass
    return 30


def get_npm_url() -> str:
    """Return the NGINX Proxy Manager base URL.

    Resolution order:
    1. ``infrastructure-plugin.npm_url`` in Hermes ``config.yaml``
    2. ``NPM_URL`` constant (empty string default)
    """
    cfg = _load_infra_config()
    if "npm_url" in cfg:
        return str(cfg["npm_url"]).rstrip("/")
    return NPM_URL


def get_npm_timeout() -> int:
    """Return the NPM API request timeout in seconds.

    Resolution order:
    1. ``infrastructure-plugin.npm_timeout`` in Hermes ``config.yaml``
    2. ``15`` (default)
    """
    cfg = _load_infra_config()
    if "npm_timeout" in cfg:
        try:
            return int(cfg["npm_timeout"])
        except (ValueError, TypeError):
            pass
    return 15
