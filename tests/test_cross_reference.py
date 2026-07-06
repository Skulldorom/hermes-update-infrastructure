"""Tests for cross_reference() in parser.py."""

import _bootstrap  # noqa: F401 — loads update_infrastructure

import pytest

from update_infrastructure.parser import cross_reference


class TestCrossReference:
    def test_no_entities_dir(self):
        data = {"services": [{"name": "Pihole", "href": "https://pihole.example.com"}]}
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
        assert result["unmapped_servers"] == []

    def test_missing_entity_page(self):
        data = {"services": [{"name": "NewService", "href": "https://new.example.com"}]}
        entity_pages = {}
        result = cross_reference(data, entity_pages)
        assert len(result["missing_entity_pages"]) == 1
        assert result["missing_entity_pages"][0]["name"] == "NewService"

    def test_generic_infra_not_flagged(self):
        data = {"services": [{"name": "Proxmox", "href": "https://proxmox.example.com"}]}
        entity_pages = {}
        result = cross_reference(data, entity_pages)
        assert len(result["missing_entity_pages"]) == 0

    def test_service_with_entity_page_not_missing(self):
        data = {"services": [{"name": "Pihole", "href": "https://pihole.example.com"}]}
        entity_pages = {
            "pihole": {"ip": "192.168.0.1", "type": "container",
                       "_slug": "pihole", "_path": "/fake/pihole.md"},
        }
        result = cross_reference(data, entity_pages)
        assert len(result["missing_entity_pages"]) == 0

    def test_ip_drift_detected(self):
        data = {
            "services": [{"name": "Pihole", "href": "https://pihole.example.com",
                          "site_monitor": "http://192.168.0.200"}],
        }
        entity_pages = {
            "pihole": {"ip": "192.168.0.100", "type": "container",
                       "_slug": "pihole", "_path": "/fake/pihole.md"},
        }
        result = cross_reference(data, entity_pages)
        assert len(result["ip_drift"]) == 1
        assert result["ip_drift"][0]["name"] == "Pihole"
        assert result["ip_drift"][0]["entity_ip"] == "192.168.0.100"

    def test_ip_match_no_drift(self):
        data = {
            "services": [{"name": "Pihole", "href": "https://pihole.example.com",
                          "site_monitor": "http://192.168.0.100"}],
        }
        entity_pages = {
            "pihole": {"ip": "192.168.0.100", "type": "container",
                       "_slug": "pihole", "_path": "/fake/pihole.md"},
        }
        result = cross_reference(data, entity_pages)
        assert len(result["ip_drift"]) == 0

    def test_no_entity_ip_no_drift(self):
        data = {
            "services": [{"name": "Pihole", "href": "https://pihole.example.com",
                          "site_monitor": "http://192.168.0.200"}],
        }
        entity_pages = {
            "pihole": {"type": "container",
                       "_slug": "pihole", "_path": "/fake/pihole.md"},
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
        assert "entities/stale-service.md" in result["orphaned_entity_pages"]

    def test_personal_device_not_orphaned(self):
        data = {"services": []}
        entity_pages = {
            "my-phone": {"type": "phone", "_slug": "my-phone", "_path": "/fake/phone.md"},
            "me": {"type": "person", "_slug": "me", "_path": "/fake/me.md"},
        }
        result = cross_reference(data, entity_pages)
        assert len(result["orphaned_entity_pages"]) == 0

    def test_unmapped_server_field(self):
        data = {
            "services": [{"name": "Glances", "href": "https://glances.example.com",
                          "server": "mystery-box"}],
        }
        entity_pages = {
            "docker-host": {"type": "infrastructure",
                            "_slug": "docker-host", "_path": "/fake/docker.md"},
        }
        result = cross_reference(data, entity_pages)
        assert len(result["unmapped_servers"]) == 1
        assert result["unmapped_servers"][0]["name"] == "Glances"
        assert result["unmapped_servers"][0]["server"] == "mystery-box"

    def test_mapped_server_not_flagged(self):
        data = {
            "services": [{"name": "Glances", "href": "https://glances.example.com",
                          "server": "docker-host"}],
        }
        entity_pages = {
            "docker-host": {"type": "infrastructure",
                            "_slug": "docker-host", "_path": "/fake/docker.md"},
        }
        result = cross_reference(data, entity_pages)
        assert len(result["unmapped_servers"]) == 0
