"""NGINX Proxy Manager (NPM) API client.

Provides a thin client around the NPM REST API for fetching proxy hosts,
certificates, and access lists.  Authentication uses a bearer token obtained
via ``POST /api/tokens``; the token is cached for the lifetime of the
``NPMClient`` instance and refreshed automatically on 401 responses.

Configuration is read from ``config.py`` (populated via Hermes ``config.yaml``):

- ``infrastructure-plugin.npm_url``      — Base URL of the NPM instance
  (e.g. ``http://npm.local:81``)
- ``NPM_EMAIL``    — Email used to authenticate (environment variable)
- ``NPM_PASSWORD`` — Password used to authenticate (environment variable)

Also provides ``npm_sync()`` which cross-references NPM proxy hosts against
subdomain entries in the wiki's ``guides/infrastructure-domains.md`` table
and returns a structured diff dict.
"""

import os
import re
from pathlib import Path
from typing import Any

import requests

try:
    from . import config
except ImportError:  # pragma: no cover - supports direct module loading in tests
    import config  # type: ignore


# ---------------------------------------------------------------------------
# NPMClient
# ---------------------------------------------------------------------------

class NPMClient:
    """Thin client for the NGINX Proxy Manager REST API.

    Auth flow:
        1. ``POST /api/tokens`` with email + password → bearer token
        2. Token is sent as ``Authorization: ****** on all requests
        3. On a 401 response the token is cleared and the request is retried
           once with a fresh token.

    Args:
        base_url: Base URL of the NPM instance (e.g. ``http://npm.local:81``).
            Defaults to ``config.get_npm_url()``.
        email: Email for authentication.
            Defaults to the ``NPM_EMAIL`` env var, then ``config.NPM_EMAIL``.
        password: Password for authentication.
            Defaults to the ``NPM_PASSWORD`` env var, then ``config.NPM_PASSWORD``.
        timeout: HTTP request timeout in seconds (default: 15).
    """

    def __init__(
        self,
        base_url: str | None = None,
        email: str | None = None,
        password: str | None = None,
        timeout: int = 15,
    ) -> None:
        self._base_url = (
            base_url
            or config.get_npm_url()
        ).rstrip("/")
        self._email = email or os.environ.get("NPM_EMAIL", config.NPM_EMAIL)
        self._password = password or os.environ.get("NPM_PASSWORD", config.NPM_PASSWORD)
        self._timeout = timeout
        self._token: str | None = None

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def _fetch_token(self) -> str:
        """Obtain a new bearer token from ``POST /api/tokens``."""
        url = f"{self._base_url}/api/tokens"
        response = requests.post(
            url,
            json={"identity": self._email, "secret": self._password},
            timeout=self._timeout,
        )
        response.raise_for_status()
        self._token = response.json()["token"]
        return self._token

    def _auth_headers(self) -> dict[str, str]:
        """Return Authorization headers, fetching a token if needed."""
        if not self._token:
            self._fetch_token()
        auth_scheme = "Bearer"
        return {"Authorization": f"{auth_scheme} {self._token}"}

    def _get(self, path: str) -> Any:
        """Perform an authenticated GET request, retrying once on 401."""
        url = f"{self._base_url}{path}"
        response = requests.get(url, headers=self._auth_headers(), timeout=self._timeout)
        if response.status_code == 401:
            # Token may have expired — clear and retry once
            self._token = None
            response = requests.get(url, headers=self._auth_headers(), timeout=self._timeout)
        response.raise_for_status()
        return response.json()

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    def get_proxy_hosts(self) -> list[dict]:
        """Return all proxy hosts from ``GET /api/nginx/proxy-hosts``."""
        return self._get("/api/nginx/proxy-hosts")

    def get_certificates(self) -> list[dict]:
        """Return all certificates from ``GET /api/nginx/certificates``."""
        return self._get("/api/nginx/certificates")

    def get_access_lists(self) -> list[dict]:
        """Return all access lists from ``GET /api/nginx/access-lists``."""
        return self._get("/api/nginx/access-lists")


# ---------------------------------------------------------------------------
# Wiki subdomain extraction
# ---------------------------------------------------------------------------

# Matches table rows of the form:
#   || example.com | subdomain | ...
# or (pipe-table rows with a subdomain column)
#   | example.com | subdomain | ...
# The domain column is matched generically — any domain name is accepted.
_SUBDOMAIN_ROW_RE = re.compile(
    r"^\|+\s*[a-z0-9.-]+\s*\|\s*([a-z0-9-]+)\s*\|",
    re.IGNORECASE | re.MULTILINE,
)

# NPM-managed entries are assumed to be any subdomain that does NOT match
# these exclusion keywords (Proxmox node names, PBS, raw IP references, etc.)
_NPM_EXCLUSION_RE = re.compile(
    r"^(pve|proxmox|pbs|backup|cloudflare|cf-tunnel|tailscale)$",
    re.IGNORECASE,
)


def _extract_wiki_subdomains(domains_page: Path) -> set[str]:
    """Extract subdomain labels from the wiki ``infrastructure-domains.md`` table.

    Reads rows of the form ``|| example.com | subdomain | ...`` and
    returns the set of subdomain labels, excluding entries that belong to
    Proxmox nodes, PBS, Cloudflare Tunnel, or Tailscale.

    Args:
        domains_page: Path to the wiki domains markdown file.

    Returns:
        Set of lowercase subdomain labels (e.g. ``{"adguard-primary", "grafana"}``).
    """
    if not domains_page.exists():
        return set()
    text = domains_page.read_text()
    subdomains: set[str] = set()
    for match in _SUBDOMAIN_ROW_RE.finditer(text):
        label = match.group(1).strip().lower()
        if label and not _NPM_EXCLUSION_RE.match(label):
            subdomains.add(label)
    return subdomains


# ---------------------------------------------------------------------------
# npm_sync
# ---------------------------------------------------------------------------

def npm_sync(
    client: NPMClient,
    wiki_root: Path,
    domains_page_rel: str = "guides/infrastructure-domains.md",
) -> dict:
    """Cross-reference NPM proxy hosts against wiki subdomain entries.

    Fetches live proxy host data from NPM and compares the domain names
    against subdomain labels extracted from the wiki domains table.

    Args:
        client: An authenticated ``NPMClient`` instance.
        wiki_root: Root of the wiki directory.
        domains_page_rel: Path to the domains page relative to *wiki_root*.

    Returns:
        A dict with the following keys:

        - ``unmatched_npm`` (list[dict]): Proxy hosts in NPM that have no
          corresponding subdomain entry in the wiki.  Each item has
          ``domain_names`` (list), ``forward_host``, ``forward_port``,
          ``enabled`` keys.
        - ``missing_npm`` (list[str]): Wiki subdomain labels that have no
          corresponding proxy host in NPM.
        - ``config_drift`` (list[dict]): Proxy hosts where NPM config
          diverges from wiki (currently detects ``enabled=False``).
        - ``ssl_drift`` (list[dict]): Proxy hosts where SSL is not forced
          despite the wiki entry implying HTTPS access.
        - ``npm_host_count`` (int): Total number of proxy hosts in NPM.
        - ``wiki_subdomain_count`` (int): Total number of wiki subdomains.
        - ``domains_page_found`` (bool): Whether the domains page was found.
        - ``error`` (str | None): Error message if the NPM API call failed.
    """
    domains_page = wiki_root / domains_page_rel
    wiki_subs = _extract_wiki_subdomains(domains_page)

    result: dict = {
        "unmatched_npm": [],
        "missing_npm": [],
        "config_drift": [],
        "ssl_drift": [],
        "npm_host_count": 0,
        "wiki_subdomain_count": len(wiki_subs),
        "domains_page_found": domains_page.exists(),
        "error": None,
    }

    try:
        proxy_hosts = client.get_proxy_hosts()
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
        return result

    result["npm_host_count"] = len(proxy_hosts)

    # Build a set of all subdomain labels present in NPM
    npm_labels: set[str] = set()
    for host in proxy_hosts:
        for domain in host.get("domain_names", []):
            # Extract the subdomain label (everything before the first dot)
            label = domain.split(".")[0].lower()
            npm_labels.add(label)

        # Check for unmatched NPM entries (no wiki subdomain)
        host_labels = [d.split(".")[0].lower() for d in host.get("domain_names", [])]
        if not any(lbl in wiki_subs for lbl in host_labels):
            result["unmatched_npm"].append({
                "domain_names": host.get("domain_names", []),
                "forward_host": host.get("forward_host", ""),
                "forward_port": host.get("forward_port", 0),
                "enabled": host.get("enabled", True),
            })

        # Config drift: proxy host is disabled
        if not host.get("enabled", True):
            for lbl in host_labels:
                if lbl in wiki_subs:
                    result["config_drift"].append({
                        "domain_names": host.get("domain_names", []),
                        "issue": "proxy host is disabled in NPM",
                    })
                    break

        # SSL drift: SSL not forced on an otherwise matched host
        if not host.get("ssl_forced", False):
            for lbl in host_labels:
                if lbl in wiki_subs:
                    result["ssl_drift"].append({
                        "domain_names": host.get("domain_names", []),
                        "issue": "ssl_forced is False",
                    })
                    break

    # Wiki subdomains with no NPM proxy host
    result["missing_npm"] = sorted(wiki_subs - npm_labels)

    return result
