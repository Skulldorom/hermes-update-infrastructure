"""Pulse API client — fetches live infrastructure data from Pulse (rcourtman/Pulse).

Replaces the Homepage YAML → structured data pipeline.
Pulse is the single source of truth for IPs, OS, node assignments, and status.
"""

import os
from typing import Any

import requests

try:
    from . import config
except ImportError:  # pragma: no cover - supports direct module loading in tests
    import config  # type: ignore

PULSE_API_TOKEN_ENV_VAR = config.PULSE_API_TOKEN_ENV_VAR


def _headers() -> dict[str, str]:
    """Return auth headers for Pulse API requests."""
    token = os.environ.get(PULSE_API_TOKEN_ENV_VAR, "")
    return {"X-API-Token": token} if token else {}


def _base_url() -> str:
    """Return the Pulse base URL, resolving config.yaml → env var → default."""
    return config.get_pulse_url()


def fetch_state(timeout: int = 15) -> dict:
    """Fetch ``/api/state`` from Pulse.

    Returns the full infrastructure state with keys:
    ``nodes``, ``vms``, ``containers``, ``dockerHosts``, ``hosts``,
    ``storage``, ``activeAlerts``, ``metrics``, ``connectionHealth``.
    """
    url = f"{_base_url()}/api/state"
    response = requests.get(url, headers=_headers(), timeout=timeout)
    response.raise_for_status()
    return response.json()


def fetch_state_summary(timeout: int = 10) -> dict:
    """Fetch ``/api/state/summary`` from Pulse.

    Returns a lightweight rollup: node/VM/container counts, active alerts.
    """
    url = f"{_base_url()}/api/state/summary"
    response = requests.get(url, headers=_headers(), timeout=timeout)
    response.raise_for_status()
    return response.json()


def fetch_resources(type_filter: str = "", limit: int = 200, timeout: int = 15) -> list[dict]:
    """Fetch ``/api/resources`` from Pulse with an optional ``type=`` filter.

    Args:
        type_filter: Comma-separated Pulse resource types, e.g.
            ``"system-container,vm,agent"`` or ``"docker-service"``.
        limit: Maximum number of resources to return per request.
        timeout: HTTP timeout in seconds.

    Returns:
        List of resource dicts as returned by the Pulse API.
    """
    params: dict[str, Any] = {"limit": limit}
    if type_filter:
        params["type"] = type_filter
    url = f"{_base_url()}/api/resources"
    response = requests.get(url, headers=_headers(), params=params, timeout=timeout)
    response.raise_for_status()
    return response.json().get("resources", [])


def fetch_resource_detail(resource_id: str, timeout: int = 10) -> dict:
    """Fetch ``/api/resources/{id}`` for full ``platformData`` (IPs, OS, node).

    Args:
        resource_id: The Pulse resource ID.
        timeout: HTTP timeout in seconds.

    Returns:
        Full resource dict including ``platformData``.
    """
    url = f"{_base_url()}/api/resources/{resource_id}"
    response = requests.get(url, headers=_headers(), timeout=timeout)
    response.raise_for_status()
    return response.json()


def _pick_ip(addresses: list[str]) -> str:
    """Return the best IPv4 address from a list of IP addresses.

    Priority:
    1. First private 192.168.x.x address
    2. First private 10.x.x.x address
    3. First private 172.16-31.x.x address
    4. First non-Tailscale, non-link-local address
    5. Any address (fallback)
    """
    if not addresses:
        return ""

    for ip in addresses:
        if ip.startswith("192.168."):
            return ip
    for ip in addresses:
        if ip.startswith("10."):
            return ip
    for ip in addresses:
        parts = ip.split(".")
        if len(parts) == 4:
            try:
                second = int(parts[1])
                if parts[0] == "172" and 16 <= second <= 31:
                    return ip
            except ValueError:
                pass
    # Skip Tailscale (100.x) and link-local (169.254.x) and IPv6
    for ip in addresses:
        if not ip.startswith("100.") and not ip.startswith("169.254.") and ":" not in ip:
            return ip
    return addresses[0] if addresses else ""


def build_service_list(resources: list[dict]) -> list[dict]:
    """Map a list of Pulse resource dicts to the service dict shape used by the plugin.

    Each returned dict has the following keys:

    - ``name`` (str): Resource display name.
    - ``group`` (str): Derived from ``platformType`` (e.g. ``proxmox-pve``, ``docker``).
    - ``ip`` (str): Best private IPv4 address from ``platformData.ipAddresses``.
    - ``host`` (str): Proxmox node name or Docker host name.
    - ``vmid`` (int | str): CT/VM ID from Pulse, empty string for Docker containers.
    - ``os`` (str): OS name from ``platformData.osName``.
    - ``status`` (str): Runtime status (``running``, ``stopped``, etc.).
    - ``type`` (str): Pulse resource type (``vm``, ``system-container``, ``docker-service``, ``agent``).
    - ``parent_id`` (str): ``parentId`` from Pulse (for Docker containers → their host).
    - ``tags`` (list[str]): Tags from the Pulse resource.
    """
    services: list[dict] = []
    for resource in resources:
        pd = resource.get("platformData") or {}
        addresses: list[str] = pd.get("ipAddresses") or []
        ip = _pick_ip(addresses)

        # Determine host: Proxmox node for VMs/containers, hostName for Docker
        host = pd.get("node") or pd.get("hostName") or ""

        services.append({
            "name": resource.get("name") or pd.get("hostName") or "",
            "group": resource.get("platformType") or "unknown",
            "ip": ip,
            "host": host,
            "vmid": pd.get("vmid") or "",
            "os": pd.get("osName") or "",
            "status": resource.get("status") or "",
            "type": resource.get("type") or "",
            "parent_id": resource.get("parentId") or "",
            "tags": resource.get("tags") or [],
        })
    return services


def build_topology(resources: list[dict]) -> dict:
    """Build a ``machines`` structure for ``topology.py`` from Pulse resources.

    Returns a dict with a ``machines`` key containing a list of machine dicts.
    Each machine dict is compatible with the shape produced by ``parser_wiki.parse_entity_pages()``.
    """
    machines: list[dict] = []
    for resource in resources:
        pd = resource.get("platformData") or {}
        addresses: list[str] = pd.get("ipAddresses") or []
        ip = _pick_ip(addresses)
        name = resource.get("name") or pd.get("hostName") or ""
        rtype = resource.get("type") or ""

        if rtype in ("system-container", "docker-service"):
            category = "container"
        elif rtype in ("agent", "host"):
            category = "infrastructure"
        else:
            category = "device"

        # Tailscale IP is typically 100.x.x.x
        tailscale_ip = next(
            (addr for addr in addresses if addr.startswith("100.")), ""
        )

        machines.append({
            "name": name,
            "slug": name.lower().replace(" ", "-"),
            "ip": ip,
            "host": pd.get("node") or pd.get("hostName") or "",
            "category": category,
            "os": pd.get("osName") or "",
            "services": [],
            "tailscale_ip": tailscale_ip,
        })
    return {"machines": machines}
