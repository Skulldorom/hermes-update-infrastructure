"""Tests for Pulse systems audit health reporting."""

from update_infrastructure.audit_health import (
    build_health_report,
    check_disk_health,
    check_resource_pressure,
)


def _disk(
    name="nvme0n1", health="PASSED", risk="none", used: int | None = 12, host="proxmox-node-1"
):
    return {
        "type": "physical_disk",
        "name": name,
        "identity": {"hostname": host},
        "physicalDisk": {
            "type": "NVMe",
            "size": 256 * 1024**3,
            "health": health,
            "risk": {"level": risk},
            "temperature": 38,
            "smart": {"percentageUsed": used, "powerOnHours": 12345},
        },
    }


def _resource(name, rtype, mem_used, mem_total, disk_used=None, disk_total=None):
    resource = {
        "type": rtype,
        "name": name,
        "identity": {"hostname": name if rtype == "agent" else "host-alpha"},
        "memory": {"used": mem_used, "total": mem_total},
    }
    if disk_used is not None or disk_total is not None:
        resource["disk"] = {"used": disk_used, "total": disk_total}
    return resource


def test_check_disk_health_all_healthy():
    result = check_disk_health([_disk(), _disk("sda", used=20)])

    assert result["healthy"] == 2
    assert result["degraded"] == []
    assert result["unknown"] == 0
    assert result["disks"][0]["host"] == "proxmox-node-1"


def test_check_disk_health_degraded():
    result = check_disk_health([_disk(name="nvme1n1", risk="warning", used=95)])

    assert result["healthy"] == 0
    assert result["unknown"] == 0
    assert result["degraded"] == [result["disks"][0]]
    assert result["degraded"][0]["percentage_used"] == 95
    assert result["degraded"][0]["risk_level"] == "warning"


def test_check_disk_health_unknown():
    result = check_disk_health([_disk(health="UNKNOWN", risk="unknown", used=None)])

    assert result["healthy"] == 0
    assert result["degraded"] == []
    assert result["unknown"] == 1


def test_check_disk_health_empty():
    result = check_disk_health([])

    assert result["healthy"] == 0
    assert result["degraded"] == []
    assert result["unknown"] == 0
    assert result["disks"] == []


def test_check_resource_pressure_normal():
    resources = [
        _resource("host-alpha", "agent", 10, 32, 20, 100),
        _resource("app", "app-container", 100, 1000, 95, 100),
        _resource("vm-1", "vm", 2, 8, 10, 100),
    ]

    result = check_resource_pressure(resources)

    assert len(result["hosts"]) == 1
    assert len(result["containers"]) == 2
    assert result["flagged"] == []


def test_check_resource_pressure_flagged():
    resources = [
        _resource("host-alpha", "agent", 29, 32, 20, 100),
        _resource("ct-1", "system-container", 1, 4, 92, 100),
        _resource("app", "app-container", 850, 1000, 95, 100),
    ]

    result = check_resource_pressure(resources)

    assert len(result["flagged"]) == 3
    assert {entry["name"] for entry in result["flagged"]} == {"host-alpha", "ct-1", "app"}
    assert result["flagged"][0]["status"] == "⚠️"


def test_check_resource_pressure_no_resources():
    result = check_resource_pressure([])

    assert result == {"hosts": [], "containers": [], "flagged": []}


def test_build_health_report_integration():
    resources = [
        _disk(name="nvme0n1", risk="warning", used=92),
        _disk(name="sda", health="UNKNOWN", risk="unknown", used=None),
        _resource("host-alpha", "agent", 10, 32, 20, 100),
        _resource("ct-1", "system-container", 7, 8, 40, 100),
    ]

    report = build_health_report(
        resources,
        active_alerts=[{"severity": "critical", "message": "Disk wearing out, dramatically"}],
        connection_health={"proxmox": {"status": "connected"}},
    )

    assert report.startswith("## Systems Audit — ")
    assert "### Active Alerts" in report
    assert "Disk wearing out" in report
    assert "✅ 0 healthy | ⚠️ 1 degraded | ❓ 1 unknown" in report
    assert "| nvme0n1 | proxmox-node-1 | NVMe | 256GB | PASSED | ⚠️ 92% endurance used |" in report
    assert "| host-alpha | 10/32GB (31%) | 20/100GB (20%) | ✅ |" in report
    assert "ct-1" in report
    assert "### Connection Health" in report
