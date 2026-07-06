"""Tests for parser helpers."""

import _bootstrap  # noqa: F401 — loads update_infrastructure

import pytest

from update_infrastructure.parser import (
    _extract_ip_from_url,
    _is_personal_device_entity,
    _parse_frontmatter,
    _parse_yaml,
    _slugify,
    extract_subdomain,
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


class TestExtractSubdomain:
    def test_skulldorom_xyz(self):
        assert extract_subdomain({"href": "https://pihole.skulldorom.xyz"}) == "pihole"

    def test_skulldorom_com(self):
        assert extract_subdomain({"href": "https://auth.skulldorom.com"}) == "auth"

    def test_no_href(self):
        assert extract_subdomain({}) is None

    def test_unrelated_domain(self):
        assert extract_subdomain({"href": "https://github.com/Skulldorom"}) is None


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


class TestParseYAML:
    def test_strips_comments(self, tmp_wiki):
        path = tmp_wiki / "test.yaml"
        path.write_text("""# This is a comment
key: value
# Another comment
list:
  - item1  # inline comment
""")
        result = _parse_yaml(path)
        assert result["key"] == "value"

    def test_empty_file(self, tmp_wiki):
        path = tmp_wiki / "test.yaml"
        path.write_text("")
        result = _parse_yaml(path)
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
