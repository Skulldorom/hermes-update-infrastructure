"""Tests for config.py public getter functions.

Validates the two-layer resolution: config.yaml → code default.
Hermes is absent in the test environment, so the config.yaml layer is simulated
by monkey-patching ``config._load_infra_config``.
"""


import os
from unittest.mock import patch

import pytest

from update_infrastructure import config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _no_infra_cfg():
    """Simulate absent / empty infrastructure-plugin section."""
    return {}


def _infra_cfg(**kwargs):
    """Return a mock infra config dict."""
    return dict(kwargs)


# ---------------------------------------------------------------------------
# get_wiki_root
# ---------------------------------------------------------------------------

class TestGetWikiRoot:
    def test_default(self):
        with patch.object(config, "_load_infra_config", _no_infra_cfg):
            result = config.get_wiki_root()
        assert result == os.path.expanduser("~/wiki")

    def test_config_yaml_override(self):
        with patch.object(config, "_load_infra_config",
                          lambda: _infra_cfg(wiki_root="/cfg/wiki")):
            result = config.get_wiki_root()
        assert result == "/cfg/wiki"

    def test_config_yaml_tilde_expanded(self):
        with patch.object(config, "_load_infra_config",
                          lambda: _infra_cfg(wiki_root="~/my-wiki")):
            result = config.get_wiki_root()
        assert result == os.path.expanduser("~/my-wiki")
        assert "~" not in result


# ---------------------------------------------------------------------------
# get_wiki_page
# ---------------------------------------------------------------------------

class TestGetWikiPage:
    def test_default(self):
        with patch.object(config, "_load_infra_config", _no_infra_cfg):
            assert config.get_wiki_page() == "guides/homelab-infrastructure.md"

    def test_config_yaml_override(self):
        with patch.object(config, "_load_infra_config",
                          lambda: _infra_cfg(wiki_page="docs/infra.md")):
            assert config.get_wiki_page() == "docs/infra.md"


# ---------------------------------------------------------------------------
# get_subdomain_patterns
# ---------------------------------------------------------------------------

class TestGetSubdomainPatterns:
    def test_default_empty(self):
        with patch.object(config, "_load_infra_config", _no_infra_cfg):
            result = config.get_subdomain_patterns()
        assert result == []

    def test_config_yaml_list(self):
        patterns = [r"https?://([a-z]+)\.test\.io", r"https?://([a-z]+)\.example\.com"]
        with patch.object(config, "_load_infra_config",
                          lambda: _infra_cfg(subdomain_patterns=patterns)):
            result = config.get_subdomain_patterns()
        assert result == patterns

    def test_config_yaml_single_string(self):
        with patch.object(config, "_load_infra_config",
                          lambda: _infra_cfg(subdomain_patterns=r"cfg-pattern")):
            result = config.get_subdomain_patterns()
        assert result == ["cfg-pattern"]


# ---------------------------------------------------------------------------
# get_generic_infra_names
# ---------------------------------------------------------------------------

class TestGetGenericInfraNames:
    def test_default_contains_proxmox(self):
        with patch.object(config, "_load_infra_config", _no_infra_cfg):
            result = config.get_generic_infra_names()
        assert "proxmox" in result

    def test_config_yaml_replaces_default(self):
        with patch.object(config, "_load_infra_config",
                          lambda: _infra_cfg(generic_infra_names=["toolA", "toolB"])):
            result = config.get_generic_infra_names()
        assert result == frozenset({"toola", "toolb"})
        assert "proxmox" not in result  # config replaces, not extends


# ---------------------------------------------------------------------------
# get_personal_device_types
# ---------------------------------------------------------------------------

class TestGetPersonalDeviceTypes:
    def test_default_contains_phone(self):
        with patch.object(config, "_load_infra_config", _no_infra_cfg):
            assert "phone" in config.get_personal_device_types()

    def test_config_yaml_override(self):
        with patch.object(config, "_load_infra_config",
                          lambda: _infra_cfg(personal_device_types=["gadget", "wearable"])):
            result = config.get_personal_device_types()
        assert result == frozenset({"gadget", "wearable"})
        assert "phone" not in result


# ---------------------------------------------------------------------------
# get_server_aliases
# ---------------------------------------------------------------------------

class TestGetServerAliases:
    def test_default_empty(self):
        with patch.object(config, "_load_infra_config", _no_infra_cfg):
            result = config.get_server_aliases()
        assert result == {}

    def test_config_yaml_dict(self):
        with patch.object(config, "_load_infra_config",
                          lambda: _infra_cfg(server_aliases={"nas": "thehoard-nas",
                                                              "pve": "aegis-proxmox|sentinel-proxmox"})):
            result = config.get_server_aliases()
        assert result["nas"] == ["thehoard-nas"]
        assert result["pve"] == ["aegis-proxmox", "sentinel-proxmox"]

    def test_config_yaml_list_values(self):
        with patch.object(config, "_load_infra_config",
                          lambda: _infra_cfg(server_aliases={"nas": ["thehoard-nas", "backup-nas"]})):
            result = config.get_server_aliases()
        assert result["nas"] == ["thehoard-nas", "backup-nas"]


# ---------------------------------------------------------------------------
# get_suppressed_entity_names
# ---------------------------------------------------------------------------

class TestGetSuppressedEntityNames:
    def test_default_empty(self):
        with patch.object(config, "_load_infra_config", _no_infra_cfg):
            result = config.get_suppressed_entity_names()
        assert result == frozenset()

    def test_config_yaml_list(self):
        with patch.object(config, "_load_infra_config",
                          lambda: _infra_cfg(suppress_entity_check=["MyRouter", "OldHost"])):
            result = config.get_suppressed_entity_names()
        assert "myrouter" in result
        assert "oldhost" in result

    def test_config_yaml_empty_list(self):
        with patch.object(config, "_load_infra_config",
                          lambda: _infra_cfg(suppress_entity_check=[])):
            result = config.get_suppressed_entity_names()
        assert result == frozenset()


# ---------------------------------------------------------------------------
# get_pulse_url
# ---------------------------------------------------------------------------

class TestGetPulseUrl:
    def test_default_empty(self):
        with patch.object(config, "_load_infra_config", _no_infra_cfg):
            result = config.get_pulse_url()
        assert result == ""

    def test_config_yaml_override(self):
        with patch.object(config, "_load_infra_config",
                          lambda: _infra_cfg(pulse_url="http://cfg-host:7655")):
            result = config.get_pulse_url()
        assert result == "http://cfg-host:7655"

    def test_trailing_slash_stripped(self):
        with patch.object(config, "_load_infra_config",
                          lambda: _infra_cfg(pulse_url="http://host:7655/")):
            result = config.get_pulse_url()
        assert not result.endswith("/")


# ---------------------------------------------------------------------------
# get_pulse_timeout
# ---------------------------------------------------------------------------

class TestGetPulseTimeout:
    def test_default(self):
        with patch.object(config, "_load_infra_config", _no_infra_cfg):
            assert config.get_pulse_timeout() == 30

    def test_config_yaml_override(self):
        with patch.object(config, "_load_infra_config",
                          lambda: _infra_cfg(pulse_timeout=60)):
            assert config.get_pulse_timeout() == 60

    def test_invalid_value_falls_back_to_default(self):
        with patch.object(config, "_load_infra_config",
                          lambda: _infra_cfg(pulse_timeout="not-a-number")):
            assert config.get_pulse_timeout() == 30


# ---------------------------------------------------------------------------
# get_npm_url
# ---------------------------------------------------------------------------

class TestGetNpmUrl:
    def test_default_empty(self):
        with patch.object(config, "_load_infra_config", _no_infra_cfg):
            result = config.get_npm_url()
        assert result == ""

    def test_config_yaml_override(self):
        with patch.object(config, "_load_infra_config",
                          lambda: _infra_cfg(npm_url="http://cfg-npm:81")):
            result = config.get_npm_url()
        assert result == "http://cfg-npm:81"


# ---------------------------------------------------------------------------
# get_npm_timeout
# ---------------------------------------------------------------------------

class TestGetNpmTimeout:
    def test_default(self):
        with patch.object(config, "_load_infra_config", _no_infra_cfg):
            assert config.get_npm_timeout() == 15

    def test_config_yaml_override(self):
        with patch.object(config, "_load_infra_config",
                          lambda: _infra_cfg(npm_timeout=30)):
            assert config.get_npm_timeout() == 30
