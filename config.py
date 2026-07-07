"""Configuration variables for the update-infrastructure plugin.

Override these values to adapt the plugin to a different homelab setup.
Environment variables take precedence over the defaults defined here at runtime
(see the individual ``_get_*`` helpers in ``parser.py``).
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Pulse API source
# ---------------------------------------------------------------------------

#: Base URL of the Pulse monitoring API.
#: Override at runtime via the ``PULSE_API_URL`` environment variable.
PULSE_API_URL: str = "http://192.168.0.20:7655"

#: Name of the environment variable that holds the Pulse API token.
#: The token must have ``monitoring:read``, ``agent:read``, and ``settings:read`` scopes.
PULSE_API_TOKEN_ENV_VAR: str = "PULSE_API_TOKEN"

#: Resource types to fetch from Pulse when building the service list.
#: Comma-separated string matching Pulse ``/api/resources?type=`` parameter.
PULSE_RESOURCE_TYPES: str = "container,vm,node,docker-container"

# ---------------------------------------------------------------------------
# Wiki paths
# ---------------------------------------------------------------------------

#: Default wiki root when the ``WIKI_PATH`` env var is not set.
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
#: label in group 1.  Override at runtime via the ``SUBDOMAIN_PATTERNS``
#: environment variable (comma-separated list of raw regex strings).
DEFAULT_SUBDOMAIN_PATTERNS: list[str] = [
    r"https?://([a-z0-9-]+)\.skulldorom\.(xyz|com)"
]

# ---------------------------------------------------------------------------
# Noise / cross-reference filters
# ---------------------------------------------------------------------------

#: Generic infrastructure / platform names that are *not* specific hosts.
#: Services whose slugified name matches one of these are excluded from the
#: "missing entity pages" report unless an entity page already exists.
#: Extend at runtime via the ``NOISE_FILTER_NAMES`` env var (comma-separated).
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
