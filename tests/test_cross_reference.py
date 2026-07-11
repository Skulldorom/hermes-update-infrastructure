"""Tests for cross_reference() in parser.py."""


import pytest

from update_infrastructure.parser import cross_reference


class TestCrossReference:
    def test_no_entities_dir(self):
        data = {"services": [{"name": "dns-service", "ip": "198.51.100.100", "host": ""}]}
        result = cross_reference(data, None)
        assert result == {"entities_dir_found": False}

    def test_empty_entities_no_services(self):
        data = {"services": []}
        entity_pages = {}
        result = cross_reference(data, entity_pages)
        assert result["entities_dir_found"] is True
        assert result["missing_entity_pages"] == []
        assert result["ip_drift"] == []
        assert result["orphaned_entity_pages"] == []
        assert result["unmapped_hosts"] == []

    def test_missing_entity_page(self):
        data = {"services": [{"name": "NewService", "ip": "198.51.100.50", "host": ""}]}
        entity_pages = {}
        result = cross_reference(data, entity_pages)
        assert len(result["missing_entity_pages"]) == 1
        assert result["missing_entity_pages"][0]["name"] == "NewService"

    def test_generic_infra_not_flagged(self):
        data = {"services": [{"name": "Proxmox", "ip": "198.51.100.1", "host": ""}]}
        entity_pages = {}
        result = cross_reference(data, entity_pages)
        assert len(result["missing_entity_pages"]) == 0

    def test_service_with_entity_page_not_missing(self):
        data = {"services": [{"name": "dns-service", "ip": "198.51.100.100", "host": ""}]}
        entity_pages = {
            "dns-service": {"ip": "198.51.100.100", "type": "container",
                       "_slug": "dns-service", "_path": "/fake/dns-service.md"},
        }
        result = cross_reference(data, entity_pages)
        assert len(result["missing_entity_pages"]) == 0

    def test_ip_drift_detected(self):
        data = {
            "services": [{"name": "dns-service", "ip": "198.51.100.200", "host": ""}],
        }
        entity_pages = {
            "dns-service": {"ip": "198.51.100.100", "type": "container",
                       "_slug": "dns-service", "_path": "/fake/dns-service.md"},
        }
        result = cross_reference(data, entity_pages)
        assert len(result["ip_drift"]) == 1
        assert result["ip_drift"][0]["name"] == "dns-service"
        assert result["ip_drift"][0]["pulse_ip"] == "198.51.100.200"
        assert result["ip_drift"][0]["entity_ip"] == "198.51.100.100"

    def test_ip_match_no_drift(self):
        data = {
            "services": [{"name": "dns-service", "ip": "198.51.100.100", "host": ""}],
        }
        entity_pages = {
            "dns-service": {"ip": "198.51.100.100", "type": "container",
                       "_slug": "dns-service", "_path": "/fake/dns-service.md"},
        }
        result = cross_reference(data, entity_pages)
        assert len(result["ip_drift"]) == 0

    def test_no_entity_ip_no_drift(self):
        data = {
            "services": [{"name": "dns-service", "ip": "198.51.100.200", "host": ""}],
        }
        entity_pages = {
            "dns-service": {"type": "container",
                       "_slug": "dns-service", "_path": "/fake/dns-service.md"},
        }
        result = cross_reference(data, entity_pages)
        assert len(result["ip_drift"]) == 0

    def test_no_pulse_ip_no_drift(self):
        data = {
            "services": [{"name": "dns-service", "ip": "", "host": ""}],
        }
        entity_pages = {
            "dns-service": {"ip": "198.51.100.100", "type": "container",
                       "_slug": "dns-service", "_path": "/fake/dns-service.md"},
        }
        result = cross_reference(data, entity_pages)
        assert len(result["ip_drift"]) == 0

    def test_orphaned_entity_pages(self):
        data = {"services": []}
        entity_pages = {
            "stale-service": {"type": "container",
                              "_slug": "stale-service", "_path": "/fake/stale.md"},
        }
        result = cross_reference(data, entity_pages)
        assert len(result["orphaned_entity_pages"]) == 1
        orphan = result["orphaned_entity_pages"][0]
        assert orphan["path"] == "entities/stale-service.md"
        assert orphan["category"] == "truly_orphaned"

    def test_personal_device_not_orphaned(self):
        data = {"services": []}
        entity_pages = {
            "my-phone": {"type": "phone", "_slug": "my-phone", "_path": "/fake/phone.md"},
            "me": {"type": "person", "_slug": "me", "_path": "/fake/me.md"},
        }
        result = cross_reference(data, entity_pages)
        assert len(result["orphaned_entity_pages"]) == 0

    def test_unmapped_host_field(self):
        data = {
            "services": [{"name": "Metrics Agent", "ip": "198.51.100.50",
                          "host": "mystery-box"}],
        }
        entity_pages = {
            "docker-host": {"type": "infrastructure",
                            "_slug": "docker-host", "_path": "/fake/docker.md"},
        }
        result = cross_reference(data, entity_pages)
        assert len(result["unmapped_hosts"]) == 1
        assert result["unmapped_hosts"][0]["name"] == "Metrics Agent"
        assert result["unmapped_hosts"][0]["host"] == "mystery-box"

    def test_pulse_host_fallback_resolves_entity(self):
        """Service named host-alpha should match entity host-alpha-proxmox via pulse_host."""
        data = {"services": [{"name": "host-alpha", "ip": "198.51.100.6", "host": ""}]}
        entity_pages = {
            "host-alpha-proxmox": {
                "pulse_host": "host-alpha",
                "ip": "198.51.100.6",
                "category": "infrastructure",
                "_slug": "host-alpha-proxmox",
                "_path": "/fake/host-alpha-proxmox.md",
            },
        }
        result = cross_reference(data, entity_pages)
        assert len(result["missing_entity_pages"]) == 0
        assert len(result["ip_drift"]) == 0

    def test_mapped_host_not_flagged(self):
        data = {
            "services": [{"name": "Metrics Agent", "ip": "198.51.100.50",
                          "host": "docker-host"}],
        }
        entity_pages = {
            "docker-host": {"type": "infrastructure",
                            "_slug": "docker-host", "_path": "/fake/docker.md"},
        }
        result = cross_reference(data, entity_pages)
        assert len(result["unmapped_hosts"]) == 0

    def test_same_name_different_hosts_no_drift(self):
        """Two same-named services on different hosts — each matches its
        entity via pulse_host. No false IP drift, no missing pages."""
        data = {
            "services": [
                {"name": "dns-filter", "ip": "198.51.100.2", "host": "host-alpha"},
                {"name": "dns-filter", "ip": "198.51.100.8", "host": "host-beta"},
            ],
        }
        entity_pages = {
            "dns-filter": {
                "pulse_host": "host-alpha",
                "ip": "198.51.100.2",
                "type": "container",
                "_slug": "dns-filter",
                "_path": "/fake/dns-filter.md",
            },
            "dns-secondary": {
                "pulse_host": "host-beta",
                "ip": "198.51.100.8",
                "type": "container",
                "_slug": "dns-secondary",
                "_path": "/fake/dns-secondary.md",
            },
        }
        result = cross_reference(data, entity_pages)
        assert len(result["ip_drift"]) == 0, (
            "same-named services on different hosts must not trigger IP drift"
        )
        assert len(result["missing_entity_pages"]) == 0, (
            "services with known hosts must not be flagged as missing"
        )

    def test_pulse_host_mismatch_prevents_false_drift(self):
        """A service slug-matches an entity but has the wrong pulse_host.
        It must not be reported as IP drift against that entity."""
        data = {
            "services": [
                {"name": "dns-filter", "ip": "198.51.100.8", "host": "host-beta"},
            ],
        }
        entity_pages = {
            "dns-filter": {
                "pulse_host": "host-alpha",
                "ip": "198.51.100.2",
                "type": "container",
                "_slug": "dns-filter",
                "_path": "/fake/dns-filter.md",
            },
        }
        result = cross_reference(data, entity_pages)
        # Must NOT be in ip_drift — the entity is for a different host
        assert len(result["ip_drift"]) == 0, (
            "pulse_host mismatch should prevent false IP drift"
        )
