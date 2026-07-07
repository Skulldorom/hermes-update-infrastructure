"""Tests for parser helpers and Pulse API integration."""

import _bootstrap  # noqa: F401 — loads update_infrastructure

import pytest

from update_infrastructure.parser import (
    _extract_ip_from_url,
    _is_personal_device_entity,
    _parse_frontmatter,
    _slugify,
    scaffold_entity_page,
)


class TestSlugify:
    def test_basic(self):
        assert _slugify("Hello World") == "hello-world"

    def test_special_chars(self):
        assert _slugify("AdGuard Primary") == "adguard-primary"

    def test_already_slug(self):
        assert _slugify("already-slug") == "already-slug"

    def test_unicode_stripped(self):
        slug = _slugify("café spécial")
        assert "é" not in slug
        assert slug == "caf-spcial"


class TestExtractIPFromURL:
    def test_standard_http_ip(self):
        assert _extract_ip_from_url("http://192.168.0.1") == "192.168.0.1"

    def test_standard_https_ip(self):
        assert _extract_ip_from_url("https://10.0.0.1") == "10.0.0.1"

    def test_ip_with_port(self):
        assert _extract_ip_from_url("http://192.168.0.1:8080") == "192.168.0.1"

    def test_ip_with_path(self):
        assert _extract_ip_from_url("http://192.168.0.1/admin") == "192.168.0.1"

    def test_domain_not_ip(self):
        assert _extract_ip_from_url("https://example.com") is None

    def test_empty_string(self):
        assert _extract_ip_from_url("") is None

    def test_none(self):
        assert _extract_ip_from_url(None) is None


class TestParseFrontmatter:
    def test_valid_frontmatter(self, tmp_wiki):
        path = tmp_wiki / "test.md"
        path.write_text("""---
title: Test Service
ip: 192.168.0.1
type: container
---
# Body
""")
        result = _parse_frontmatter(path)
        assert result["title"] == "Test Service"
        assert result["ip"] == "192.168.0.1"
        assert result["type"] == "container"

    def test_no_frontmatter(self, tmp_wiki):
        path = tmp_wiki / "test.md"
        path.write_text("# Just a heading\n")
        result = _parse_frontmatter(path)
        assert result == {}

    def test_file_not_found(self, tmp_wiki):
        result = _parse_frontmatter(tmp_wiki / "nonexistent.md")
        assert result == {}


class TestIsPersonalDeviceEntity:
    def test_phone_type(self):
        assert _is_personal_device_entity({"type": "phone"}) is True

    def test_person_type(self):
        assert _is_personal_device_entity({"type": "person"}) is True

    def test_laptop_type(self):
        assert _is_personal_device_entity({"type": "laptop"}) is True

    def test_device_tag(self):
        assert _is_personal_device_entity({"type": "other", "tags": ["device"]}) is True

    def test_container_type(self):
        assert _is_personal_device_entity({"type": "container"}) is False

    def test_empty(self):
        assert _is_personal_device_entity({}) is False


class TestScaffoldEntityPage:
    def test_creates_with_pulse_fields(self, tmp_wiki):
        result = scaffold_entity_page(
            tmp_wiki, "PiHole",
            ip="192.168.0.100",
            host="thehoard",
            os="Debian GNU/Linux 12",
            vmid="",
        )
        assert result["created"] is True
        content = (tmp_wiki / "entities" / "pihole.md").read_text()
        assert "ip: 192.168.0.100" in content
        assert "host: thehoard" in content
        assert "os: Debian GNU/Linux 12" in content

    def test_creates_with_vmid(self, tmp_wiki):
        result = scaffold_entity_page(
            tmp_wiki, "HAOS",
            ip="192.168.0.7",
            host="thesentinel",
            os="Home Assistant OS",
            vmid=103,
        )
        assert result["created"] is True
        content = (tmp_wiki / "entities" / "haos.md").read_text()
        assert "vmid: 103" in content

    def test_no_duplicate(self, tmp_wiki):
        scaffold_entity_page(tmp_wiki, "TestSvc", ip="192.168.0.1")
        result2 = scaffold_entity_page(tmp_wiki, "TestSvc", ip="192.168.0.1")
        assert result2["created"] is False
        assert result2["reason"] == "already exists"

    def test_minimal_no_optional_fields(self, tmp_wiki):
        result = scaffold_entity_page(tmp_wiki, "BasicSvc")
        assert result["created"] is True
        content = (tmp_wiki / "entities" / "basicsvc.md").read_text()
        assert "ip:" not in content
        assert "host:" not in content
