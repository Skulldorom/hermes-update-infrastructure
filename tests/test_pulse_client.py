"""Tests for pulse_client module — IP picking and service list building."""

import _bootstrap  # noqa: F401 — loads update_infrastructure

import pytest
from unittest.mock import MagicMock, patch

from update_infrastructure.pulse_client import _pick_ip, build_service_list, build_topology


class TestPickIP:
    def test_prefers_192_168(self):
        assert _pick_ip(["10.0.0.5", "192.168.0.7", "100.64.0.1"]) == "192.168.0.7"

    def test_falls_back_to_10(self):
        assert _pick_ip(["10.0.0.5", "100.64.0.1"]) == "10.0.0.5"

    def test_falls_back_to_172_16(self):
        assert _pick_ip(["172.16.0.5"]) == "172.16.0.5"

    def test_skips_tailscale(self):
        result = _pick_ip(["100.64.0.1", "192.168.0.10"])
        assert result == "192.168.0.10"

    def test_empty_list(self):
        assert _pick_ip([]) == ""

    def test_single_ipv6(self):
        # IPv6 only — falls through to fallback
        result = _pick_ip(["fd0c:ac1e::1"])
        assert result == "fd0c:ac1e::1"


class TestBuildServiceList:
    def test_vm_resource(self):
        resources = [
            {
                "id": "thesentinel:thesentinel:103",
                "type": "vm",
                "name": "haos15.2",
                "platformType": "proxmox-pve",
                "status": "running",
                "parentId": "",
                "tags": ["192.168.0.7", "home-assistant"],
                "platformData": {
                    "vmid": 103,
                    "node": "thesentinel",
                    "osName": "Home Assistant OS",
                    "ipAddresses": ["172.30.232.1", "192.168.0.7"],
                },
            }
        ]
        services = build_service_list(resources)
        assert len(services) == 1
        svc = services[0]
        assert svc["name"] == "haos15.2"
        assert svc["ip"] == "192.168.0.7"
        assert svc["host"] == "thesentinel"
        assert svc["vmid"] == 103
        assert svc["os"] == "Home Assistant OS"
        assert svc["status"] == "running"
        assert svc["type"] == "vm"
        assert svc["group"] == "proxmox-pve"

    def test_docker_container_resource(self):
        resources = [
            {
                "id": "abc123",
                "type": "docker-container",
                "name": "qbittorrent",
                "platformType": "docker",
                "status": "running",
                "parentId": "fe93158d",
                "tags": [],
                "platformData": {
                    "hostId": "fe93158d",
                    "hostName": "TheHoard",
                    "image": "lscr.io/linuxserver/qbittorrent:latest",
                    "state": "running",
                    "ipAddresses": [],
                },
            }
        ]
        services = build_service_list(resources)
        assert len(services) == 1
        svc = services[0]
        assert svc["name"] == "qbittorrent"
        assert svc["ip"] == ""
        assert svc["host"] == "TheHoard"
        assert svc["type"] == "docker-container"
        assert svc["parent_id"] == "fe93158d"

    def test_empty_resources(self):
        assert build_service_list([]) == []

    def test_missing_platform_data(self):
        resources = [{"id": "x", "type": "vm", "name": "test", "status": "stopped"}]
        services = build_service_list(resources)
        assert services[0]["name"] == "test"
        assert services[0]["ip"] == ""
        assert services[0]["host"] == ""


class TestBuildTopology:
    def test_container_category(self):
        resources = [
            {
                "id": "abc",
                "type": "container",
                "name": "pihole",
                "platformType": "proxmox-pve",
                "status": "running",
                "parentId": "",
                "tags": [],
                "platformData": {"node": "aegis", "ipAddresses": ["192.168.0.100"]},
            }
        ]
        topo = build_topology(resources)
        machines = topo["machines"]
        assert len(machines) == 1
        assert machines[0]["category"] == "container"
        assert machines[0]["ip"] == "192.168.0.100"
        assert machines[0]["host"] == "aegis"

    def test_node_category(self):
        resources = [
            {
                "id": "node1",
                "type": "node",
                "name": "aegis",
                "platformType": "proxmox-pve",
                "status": "online",
                "parentId": "",
                "tags": [],
                "platformData": {"ipAddresses": ["192.168.0.5"]},
            }
        ]
        topo = build_topology(resources)
        assert topo["machines"][0]["category"] == "infrastructure"

    def test_tailscale_ip_extracted(self):
        resources = [
            {
                "id": "abc",
                "type": "vm",
                "name": "myvm",
                "platformType": "proxmox-pve",
                "status": "running",
                "parentId": "",
                "tags": [],
                "platformData": {
                    "node": "aegis",
                    "ipAddresses": ["192.168.0.10", "100.64.0.5"],
                },
            }
        ]
        topo = build_topology(resources)
        assert topo["machines"][0]["tailscale_ip"] == "100.64.0.5"
        assert topo["machines"][0]["ip"] == "192.168.0.10"
