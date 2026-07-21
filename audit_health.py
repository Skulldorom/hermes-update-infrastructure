"""Systems audit reporting from Pulse ``/api/state`` resources.

This module is intentionally read-only: it parses the state Pulse already
returns and renders a point-in-time health report. No wiki writes, no fresh
endpoints, no extra operational cleverness hiding behind a curtain.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

PRESSURE_THRESHOLD_PERCENT = 80.0


def _dig(data: dict, *path: str) -> Any:
    """Return a nested value from *data*, or ``None`` when absent."""
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _first_value(data: dict, *paths: tuple[str, ...]) -> Any:
    """Return the first non-empty value found at the supplied nested paths."""
    for path in paths:
        value = _dig(data, *path)
        if value not in (None, ""):
            return value
    return None


def _as_float(value: Any) -> float | None:
    """Best-effort numeric conversion."""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _percent(used: Any, total: Any) -> int | None:
    """Return rounded ``used / total`` percent, or ``None`` for missing data."""
    used_num = _as_float(used)
    total_num = _as_float(total)
    if used_num is None or total_num in (None, 0):
        return None
    return round((used_num / total_num) * 100)


def _fmt_amount(value: Any) -> str:
    """Render Pulse byte-ish quantities as GB while accepting already-GB fixtures."""
    num = _as_float(value)
    if num is None:
        return "?"
    if num > 1024**3:
        num = num / 1024**3
    return f"{num:.0f}GB"


def _fmt_size(value: Any) -> str:
    """Render a disk size value."""
    if value in (None, ""):
        return "?"
    if isinstance(value, str):
        return value
    return _fmt_amount(value)


def _resource_name(resource: dict) -> str:
    """Return a stable display name for a Pulse resource."""
    return str(
        resource.get("name")
        or _dig(resource, "identity", "hostname")
        or _dig(resource, "platformData", "hostName")
        or resource.get("id")
        or "unknown"
    )


def _host_name(resource: dict) -> str:
    """Return a host name associated with a Pulse resource."""
    return str(
        _dig(resource, "identity", "hostname")
        or _dig(resource, "platformData", "node")
        or _dig(resource, "platformData", "hostName")
        or resource.get("parentId")
        or "unknown"
    )


def _status_icon(flagged: bool) -> str:
    return "⚠️" if flagged else "✅"


def check_disk_health(resources: list[dict]) -> dict:
    """Summarise SMART/risk health for Pulse ``physical_disk`` resources.

    Returns a dict with healthy/degraded/unknown counts plus a ``disks`` list
    containing normalized per-disk details for rendering or scripting.
    """
    disks: list[dict] = []
    degraded: list[dict] = []
    healthy = 0
    unknown = 0

    for resource in resources:
        if resource.get("type") != "physical_disk":
            continue

        physical = resource.get("physicalDisk") or {}
        smart = physical.get("smart") or {}
        risk = physical.get("risk") or {}

        health = str(
            _first_value(
                resource,
                ("physicalDisk", "health"),
                ("physicalDisk", "smart", "health"),
            )
            or "UNKNOWN"
        ).upper()
        risk_level = str(risk.get("level") or "none").lower()
        percentage_used = _as_float(smart.get("percentageUsed"))
        disk = {
            "name": _resource_name(resource),
            "host": _host_name(resource),
            "type": str(physical.get("type") or physical.get("diskType") or "?"),
            "size": _fmt_size(physical.get("size") or physical.get("capacity")),
            "health": health,
            "risk_level": risk_level,
            "percentage_used": int(percentage_used) if percentage_used is not None else None,
            "temperature": physical.get("temperature") or smart.get("temperature"),
            "power_on_hours": smart.get("powerOnHours"),
        }
        disks.append(disk)

        if health == "UNKNOWN":
            unknown += 1
        elif risk_level in {"warning", "critical"}:
            degraded.append(disk)
        elif health == "PASSED" and risk_level in {"none", "ok", "healthy", ""}:
            healthy += 1
        else:
            degraded.append(disk)

    return {
        "healthy": healthy,
        "degraded": degraded,
        "unknown": unknown,
        "disks": disks,
    }


def _usage_entry(resource: dict, kind: str) -> dict:
    """Normalize memory/disk metrics for one Pulse resource."""
    memory = resource.get("memory") or {}
    disk = resource.get("disk") or {}
    mem_percent = _percent(memory.get("used"), memory.get("total"))
    disk_percent = _percent(disk.get("used"), disk.get("total"))
    mem_flagged = mem_percent is not None and mem_percent > PRESSURE_THRESHOLD_PERCENT
    disk_flagged = disk_percent is not None and disk_percent > PRESSURE_THRESHOLD_PERCENT
    flagged = mem_flagged or disk_flagged

    return {
        "name": _resource_name(resource),
        "host": _host_name(resource),
        "type": resource.get("type") or "unknown",
        "kind": kind,
        "memory_used": memory.get("used"),
        "memory_total": memory.get("total"),
        "memory_percent": mem_percent,
        "disk_used": disk.get("used"),
        "disk_total": disk.get("total"),
        "disk_percent": disk_percent,
        "status": _status_icon(flagged),
        "flags": [
            flag
            for flag, enabled in (
                ("memory", mem_flagged),
                ("disk", disk_flagged),
            )
            if enabled
        ],
    }


def check_resource_pressure(resources: list[dict]) -> dict:
    """Summarise host/container resource pressure from Pulse resources.

    Agents are treated as hosts. VMs and system containers include memory and
    disk. App containers only flag memory because disk commonly belongs to the
    host/volume layer rather than the container itself.
    """
    hosts: list[dict] = []
    containers: list[dict] = []
    flagged: list[dict] = []

    for resource in resources:
        rtype = resource.get("type")
        if rtype == "agent":
            entry = _usage_entry(resource, "host")
            hosts.append(entry)
        elif rtype in {"system-container", "vm"}:
            entry = _usage_entry(resource, "container")
            containers.append(entry)
        elif rtype == "app-container":
            entry = _usage_entry(resource, "container")
            # App-container disk is shared/volume-backed; do not flag it.
            entry["disk_percent"] = None
            entry["disk_used"] = None
            entry["disk_total"] = None
            entry["flags"] = [flag for flag in entry["flags"] if flag != "disk"]
            entry["status"] = _status_icon(bool(entry["flags"]))
            containers.append(entry)
        else:
            continue

        if entry["flags"]:
            flagged.append(entry)

    return {"hosts": hosts, "containers": containers, "flagged": flagged}


def _fmt_usage(used: Any, total: Any, percent: int | None) -> str:
    if percent is None:
        return "?"
    used_text = _fmt_amount(used).removesuffix("GB")
    total_text = _fmt_amount(total)
    return f"{used_text}/{total_text} ({percent}%)"


def _risk_text(disk: dict) -> str:
    risk = disk.get("risk_level") or "unknown"
    used = disk.get("percentage_used")
    if risk in {"warning", "critical"}:
        detail = f" {used}% endurance used" if used is not None else ""
        return f"⚠️{detail}".strip()
    if risk in {"none", "ok", "healthy", ""}:
        return "✅ none"
    return f"❓ {risk}"


def _render_alert(alert: Any) -> str:
    if isinstance(alert, dict):
        severity = alert.get("severity") or alert.get("level") or alert.get("type") or "alert"
        message = alert.get("message") or alert.get("title") or alert.get("name") or str(alert)
        return f"- **{severity}** — {message}"
    return f"- {alert}"


def _render_connection_health(connection_health: Any) -> list[str]:
    if not connection_health:
        return ["None reported ✅"]
    if isinstance(connection_health, dict):
        lines = []
        for name, value in sorted(connection_health.items()):
            if isinstance(value, dict):
                status = value.get("status") or value.get("state") or value.get("health") or value
            else:
                status = value
            lines.append(f"- {name}: {status}")
        return lines
    return [str(connection_health)]


def build_health_report(
    resources: list[dict],
    active_alerts: list | None = None,
    connection_health: dict | list | None = None,
) -> str:
    """Build a structured markdown systems audit report.

    ``resources`` should be the ``state["resources"]`` list from Pulse. Root
    ``activeAlerts`` and ``connectionHealth`` are passed separately by callers
    that fetched the full ``/api/state`` payload.
    """
    disk_health = check_disk_health(resources)
    pressure = check_resource_pressure(resources)
    alerts = active_alerts or []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    lines = [
        f"## Systems Audit — {today}",
        "",
        "### Active Alerts",
    ]
    if alerts:
        lines.extend(_render_alert(alert) for alert in alerts)
    else:
        lines.append("None ✅")

    lines.extend([
        "",
        "### Disk Health",
        (
            f"✅ {disk_health['healthy']} healthy | "
            f"⚠️ {len(disk_health['degraded'])} degraded | "
            f"❓ {disk_health['unknown']} unknown"
        ),
        "",
    ])
    if disk_health["disks"]:
        lines.extend([
            "| Drive | Host | Type | Size | Health | Risk |",
            "|-------|------|------|------|--------|------|",
        ])
        for disk in disk_health["disks"]:
            lines.append(
                f"| {disk['name']} | {disk['host']} | {disk['type']} | {disk['size']} | "
                f"{disk['health']} | {_risk_text(disk)} |"
            )
    else:
        lines.append("No physical disks reported by Pulse ❓")

    lines.extend([
        "",
        "### Host Resource Pressure",
    ])
    if pressure["hosts"]:
        lines.extend([
            "| Host | RAM | Disk | Status |",
            "|------|-----|------|--------|",
        ])
        for host in pressure["hosts"]:
            lines.append(
                f"| {host['name']} | "
                f"{_fmt_usage(host['memory_used'], host['memory_total'], host['memory_percent'])} | "
                f"{_fmt_usage(host['disk_used'], host['disk_total'], host['disk_percent'])} | "
                f"{host['status']} |"
            )
    else:
        lines.append("No host agent resource metrics reported ❓")

    lines.extend([
        "",
        "### Container Pressure (>80% flagged)",
    ])
    container_flags = [entry for entry in pressure["flagged"] if entry["kind"] == "container"]
    if container_flags:
        lines.extend([
            "| Container | Type | Host | RAM | Disk | Status |",
            "|-----------|------|------|-----|------|--------|",
        ])
        for entry in container_flags:
            lines.append(
                f"| {entry['name']} | {entry['type']} | {entry['host']} | "
                f"{_fmt_usage(entry['memory_used'], entry['memory_total'], entry['memory_percent'])} | "
                f"{_fmt_usage(entry['disk_used'], entry['disk_total'], entry['disk_percent'])} | "
                f"{entry['status']} {', '.join(entry['flags'])} |"
            )
    else:
        lines.append("None ✅")

    lines.extend([
        "",
        "### Connection Health",
        *_render_connection_health(connection_health),
        "",
        "### Summary",
        (
            f"{_status_icon(bool(alerts or disk_health['degraded'] or pressure['flagged']))} "
            f"{len(alerts)} alerts, {len(pressure['hosts'])} hosts with resource data, "
            f"{len(disk_health['degraded'])} degraded disks, "
            f"{len(pressure['flagged'])} pressure flags"
        ),
    ])
    return "\n".join(lines)
