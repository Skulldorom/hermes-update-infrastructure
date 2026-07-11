"""Tests for the tool handler JSON output shape and error handling."""


import json
import os
from unittest.mock import MagicMock, patch

import pytest

from update_infrastructure import _handle_update_tool


class TestToolHandlerJSONShape:
    def _make_mock_parser(self):
        mock = MagicMock()
        mock.parse_all.return_value = {
            "services": [
                {
                    "name": "pihole",
                    "group": "docker",
                    "ip": "192.168.0.100",
                    "host": "thehoard",
                    "vmid": "",
                    "os": "Debian GNU/Linux 12",
                    "status": "running",
                    "type": "app-container",
                    "parent_id": "",
                    "tags": [],
                },
            ],
            "subdomains": [],
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
            "unmapped_hosts": [],
        }
        mock._slugify = lambda s: s.lower().replace(" ", "-")
        mock.scaffold_entity_page.return_value = {
            "created": True, "path": "/fake/entities/test.md", "slug": "test",
        }
        mock.fix_entity_ip.return_value = {
            "fixed": True, "path": "/fake/entities/pihole.md",
        }
        return mock

    def _make_mock_updater(self):
        mock = MagicMock()
        mock.write_generated_file.return_value = {
            "changed": False,
            "path": "/fake/wiki/raw/sources/pulse-infrastructure-data.md",
            "hash_before": "abc123",
            "hash_after": "abc123",
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
                     "generated_file", "changed", "source", "cross_reference",
                     "topology", "intent"):
            assert key in data, f"missing key: {key}"
        assert isinstance(data["changed"], bool)


    def test_wiki_file_uses_configured_wiki_page(self, tmp_wiki):
        mp = self._make_mock_parser()
        mu = self._make_mock_updater()
        mw = self._make_mock_parser_wiki()

        with patch.dict(os.environ, {"WIKI_PATH": str(tmp_wiki)}), \
             patch("update_infrastructure.config.get_wiki_root", return_value=str(tmp_wiki)), \
             patch("update_infrastructure.config.get_wiki_page", return_value="docs/custom-infra.md"), \
             patch("update_infrastructure.parser", mp), \
             patch("update_infrastructure.updater", mu), \
             patch("update_infrastructure.parser_wiki", mw):
            data = json.loads(_handle_update_tool("json"))

        assert data["wiki_file"] == str(tmp_wiki / "docs/custom-infra.md")

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
        assert data["source"]["subdomains"] == 0

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
        mp.parse_all.side_effect = RuntimeError("network gone")

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
        mu.write_generated_file.assert_not_called()

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

    def test_fix_all_uses_pulse_ip(self, tmp_wiki):
        mp = self._make_mock_parser()
        mp.cross_reference.return_value = {
            "entities_dir_found": True,
            "missing_entity_pages": [],
            "ip_drift": [
                {"name": "pihole", "pulse_ip": "192.168.0.200", "entity_ip": "192.168.0.100"},
            ],
            "orphaned_entity_pages": [],
            "unmapped_hosts": [],
        }
        mu = self._make_mock_updater()
        mw = self._make_mock_parser_wiki()

        with patch.dict(os.environ, {"WIKI_PATH": str(tmp_wiki)}), \
             patch("update_infrastructure.parser", mp), \
             patch("update_infrastructure.updater", mu), \
             patch("update_infrastructure.parser_wiki", mw):
            data = json.loads(_handle_update_tool("fix all"))

        assert data["intent"]["type"] == "fix_all"
        fixed = data["intent"]["result"]["fixed"]
        assert len(fixed) == 1
        assert fixed[0]["ip"] == "192.168.0.200"
