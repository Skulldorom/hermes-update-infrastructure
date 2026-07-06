"""Configuration variables for the update-infrastructure plugin.

Override these values to adapt the plugin to a different homelab setup.
Environment variables take precedence over the defaults defined here at runtime
(see the individual ``_get_*`` helpers in ``parser.py``).
"""

import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Homepage source repo
# ---------------------------------------------------------------------------

#: Git URL of the homepage dashboard config repository.
HOMEPAGE_REPO: str = "https://github.com/Skulldorom/homepage"

#: Local directory used to cache the cloned homepage repo between runs.
REPO_CACHE: Path = Path(tempfile.gettempdir()) / "hermes-homepage-sync"

# ---------------------------------------------------------------------------
# Wiki paths
# ---------------------------------------------------------------------------

#: Default wiki root when the ``WIKI_PATH`` env var is not set.
DEFAULT_WIKI_ROOT: str = "~/wiki"

#: Path to the main infrastructure page, relative to the wiki root.
WIKI_PAGE: str = "guides/homelab-infrastructure.md"

# ---------------------------------------------------------------------------
# Auto-section markers (used in wiki pages to delimit generated content)
# ---------------------------------------------------------------------------

#: Opening marker prefix for auto-generated wiki sections.
MARKER_START: str = "<!-- HOMEPAGE_AUTO:"

#: Closing marker prefix for auto-generated wiki sections.
MARKER_END: str = "<!-- /HOMEPAGE_AUTO:"

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
