"""Tests for the tool handler JSON output shape and error handling."""

import _bootstrap  # noqa: F401 — loads update_infrastructure

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from update_infrastructure import _handle_update_tool


class TestToolHandlerJSONShape:
    def _make_mock_parser(self):
        mock = MagicMock()
        mock.fetch_repo.return_value = "/tmp/fake-config"
        mock.parse_all.return_value = {
            "services": [
                {"name": "Pihole", "group": "DNS", "href": "https://pihole.example.com",
                 "site_monitor": "http://192.168.0.100", "server": "docker-host"},
            ],
            "bookmarks": [],
            "subdomains": [
                {"subdomain": "pihole", "name": "Pihole",
                 "href": "https://pihole.example.com",
                 "site_monitor": "http://192.168.0.100", "group": "DNS"},
            ],
        }
        mock.load_entity_pages.return_value = {
            "pihole": {"ip": "192.168.0.100", "type": "container",
                       "_slug": "pihole", "_path": "/fake/entities/pihole.md"},
        }
        mock.cross_reference.return_value = {
            "entities_dir_found": True,
            "missing_entity_pages": [],
            "ip_drift": [],
            "orphaned_entity_pages": [],
            "unmapped_servers": [],
        }
        mock._slugify = lambda s: s.lower().replace(" ", "-")
        mock._extract_ip_from_url = lambda u: "192.168.0.100" if "192.168" in (u or "") else None
        mock.scaffold_entity_page.return_value = {
            "created": True, "path": "/fake/entities/test.md", "slug": "test",
        }
        mock.fix_entity_ip.return_value = {
            "fixed": True, "path": "/fake/entities/pihole.md",
        }
        return mock

    def _make_mock_updater(self):
        mock = MagicMock()
        mock.update_wiki_page.return_value = {
            "changed": False,
            "sections_updated": [],
            "sections_added": [],
            "sections_unchanged": ["service_groups", "subdomains", "cross_reference", "topology"],
        }
        mock.update_wiki_log.return_value = None
        return mock

    def _make_mock_parser_wiki(self):
        mock = MagicMock()
        mock.parse_entity_pages.return_value = {"machines": []}
        return mock

    def test_default_returns_review_prompt(self, tmp_wiki):
        mp = self._make_mock_parser()
        mu = self._make_mock_updater()
        mw = self._make_mock_parser_wiki()

        with patch.dict(os.environ, {"WIKI_PATH": str(tmp_wiki)}), \
             patch("update_infrastructure.parser", mp), \
             patch("update_infrastructure.updater", mu), \
             patch("update_infrastructure.parser_wiki", mw):
            prompt = _handle_update_tool("")

        assert "# Infrastructure Sync Review" in prompt
        assert "follow up with the user" in prompt

    def test_returns_valid_json(self, tmp_wiki):
        mp = self._make_mock_parser()
        mu = self._make_mock_updater()
        mw = self._make_mock_parser_wiki()

        with patch.dict(os.environ, {"WIKI_PATH": str(tmp_wiki)}), \
             patch("update_infrastructure.parser", mp), \
             patch("update_infrastructure.updater", mu), \
             patch("update_infrastructure.parser_wiki", mw):
            data = json.loads(_handle_update_tool("json"))

        for key in ("sync_completed_at", "elapsed_seconds", "wiki_file",
                     "changed", "sections", "source", "cross_reference",
                     "topology", "intent"):
            assert key in data, f"missing key: {key}"
        assert isinstance(data["changed"], bool)

    def test_source_counts(self, tmp_wiki):
        mp = self._make_mock_parser()
        mu = self._make_mock_updater()
        mw = self._make_mock_parser_wiki()

        with patch.dict(os.environ, {"WIKI_PATH": str(tmp_wiki)}), \
             patch("update_infrastructure.parser", mp), \
             patch("update_infrastructure.updater", mu), \
             patch("update_infrastructure.parser_wiki", mw):
            data = json.loads(_handle_update_tool("json"))

        assert data["source"]["services"] == 1
        assert data["source"]["subdomains"] == 1

    def test_topology_empty(self, tmp_wiki):
        mp = self._make_mock_parser()
        mu = self._make_mock_updater()
        mw = self._make_mock_parser_wiki()

        with patch.dict(os.environ, {"WIKI_PATH": str(tmp_wiki)}), \
             patch("update_infrastructure.parser", mp), \
             patch("update_infrastructure.updater", mu), \
             patch("update_infrastructure.parser_wiki", mw):
            data = json.loads(_handle_update_tool("json"))

        assert data["topology"]["machine_count"] == 0
        assert data["topology"]["categories"] == {}
        assert data["topology"]["connections"] == []

    def test_topology_with_machines(self, tmp_wiki):
        mp = self._make_mock_parser()
        mu = self._make_mock_updater()
        mw = self._make_mock_parser_wiki()
        mw.parse_entity_pages.return_value = {
            "machines": [
                {"name": "Beast", "slug": "beast", "ip": "192.168.0.1",
                 "host": "", "category": "infrastructure", "os": "", "services": [],
                 "tailscale_ip": ""},
                {"name": "Dozzle", "slug": "dozzle", "ip": "192.168.0.1",
                 "host": "beast", "category": "container", "os": "", "services": [],
                 "tailscale_ip": ""},
            ],
        }

        with patch.dict(os.environ, {"WIKI_PATH": str(tmp_wiki)}), \
             patch("update_infrastructure.parser", mp), \
             patch("update_infrastructure.updater", mu), \
             patch("update_infrastructure.parser_wiki", mw):
            data = json.loads(_handle_update_tool("json"))

        assert data["topology"]["machine_count"] == 2
        assert data["topology"]["connections"] == [{"parent": "Beast", "child": "Dozzle"}]

    def test_json_escape_hatch_intent(self, tmp_wiki):
        mp = self._make_mock_parser()
        mu = self._make_mock_updater()
        mw = self._make_mock_parser_wiki()

        with patch.dict(os.environ, {"WIKI_PATH": str(tmp_wiki)}), \
             patch("update_infrastructure.parser", mp), \
             patch("update_infrastructure.updater", mu), \
             patch("update_infrastructure.parser_wiki", mw):
            data = json.loads(_handle_update_tool("json"))

        assert data["intent"]["type"] == "json"
        assert data["intent"]["result"] is None

    def test_error_handling(self, tmp_wiki):
        mp = self._make_mock_parser()
        mp.fetch_repo.side_effect = RuntimeError("network gone")

        with patch.dict(os.environ, {"WIKI_PATH": str(tmp_wiki)}), \
             patch("update_infrastructure.parser", mp):
            data = json.loads(_handle_update_tool(""))

        assert "error" in data
        assert data["error"] == "network gone"

    def test_status_skips_updater(self, tmp_wiki):
        mp = self._make_mock_parser()
        mu = self._make_mock_updater()

        with patch.dict(os.environ, {"WIKI_PATH": str(tmp_wiki)}), \
             patch("update_infrastructure.parser", mp), \
             patch("update_infrastructure.updater", mu):
            data = json.loads(_handle_update_tool("status"))

        assert data["changed"] is False
        assert data["intent"]["type"] == "status"
        mu.update_wiki_page.assert_not_called()

    def test_create_intent(self, tmp_wiki):
        mp = self._make_mock_parser()
        mu = self._make_mock_updater()
        mw = self._make_mock_parser_wiki()

        with patch.dict(os.environ, {"WIKI_PATH": str(tmp_wiki)}), \
             patch("update_infrastructure.parser", mp), \
             patch("update_infrastructure.updater", mu), \
             patch("update_infrastructure.parser_wiki", mw):
            data = json.loads(_handle_update_tool('create "TestService"'))

        assert data["intent"]["type"] == "create"
        assert data["intent"]["result"]["created"] is True

    def test_cleanup_intent(self, tmp_wiki):
        mp = self._make_mock_parser()
        mp.cross_reference.return_value["orphaned_entity_pages"] = ["entities/stale.md"]
        mu = self._make_mock_updater()
        mw = self._make_mock_parser_wiki()

        with patch.dict(os.environ, {"WIKI_PATH": str(tmp_wiki)}), \
             patch("update_infrastructure.parser", mp), \
             patch("update_infrastructure.updater", mu), \
             patch("update_infrastructure.parser_wiki", mw):
            data = json.loads(_handle_update_tool("cleanup"))

        assert data["intent"]["type"] == "cleanup"
        assert data["intent"]["result"]["orphaned_pages"] == ["entities/stale.md"]

    def test_echo_intent(self, tmp_wiki):
        mp = self._make_mock_parser()
        mu = self._make_mock_updater()
        mw = self._make_mock_parser_wiki()

        with patch.dict(os.environ, {"WIKI_PATH": str(tmp_wiki)}), \
             patch("update_infrastructure.parser", mp), \
             patch("update_infrastructure.updater", mu), \
             patch("update_infrastructure.parser_wiki", mw):
            data = json.loads(_handle_update_tool("random chatter"))

        assert data["intent"]["type"] == "echo"
        assert data["intent"]["result"]["echoed_text"] == "random chatter"
