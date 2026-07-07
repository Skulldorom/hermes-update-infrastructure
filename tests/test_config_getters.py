"""Tests for config.py public getter functions.

Validates the three-layer resolution: config.yaml → env var → code default.
Hermes is absent in the test environment, so the config.yaml layer is simulated
by monkey-patching ``config._load_infra_config``.
"""

import _bootstrap  # noqa: F401 — loads update_infrastructure

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
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("WIKI_PATH", None)
                result = config.get_wiki_root()
        assert result == os.path.expanduser("~/wiki")

    def test_env_var_overrides_default(self):
        with patch.object(config, "_load_infra_config", _no_infra_cfg):
            with patch.dict(os.environ, {"WIKI_PATH": "/tmp/test-wiki"}):
                result = config.get_wiki_root()
        assert result == "/tmp/test-wiki"

    def test_config_yaml_overrides_env(self):
        with patch.object(config, "_load_infra_config",
                          lambda: _infra_cfg(wiki_root="/cfg/wiki")):
            with patch.dict(os.environ, {"WIKI_PATH": "/env/wiki"}):
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
    def test_default(self):
        with patch.object(config, "_load_infra_config", _no_infra_cfg):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("SUBDOMAIN_PATTERNS", None)
                result = config.get_subdomain_patterns()
        assert result == config.DEFAULT_SUBDOMAIN_PATTERNS

    def test_env_var(self):
        with patch.object(config, "_load_infra_config", _no_infra_cfg):
            with patch.dict(os.environ, {"SUBDOMAIN_PATTERNS": r"https?://([a-z]+)\.example\.com"}):
                result = config.get_subdomain_patterns()
        assert result == [r"https?://([a-z]+)\.example\.com"]

    def test_config_yaml_list(self):
        patterns = [r"https?://([a-z]+)\.test\.io", r"https?://([a-z]+)\.example\.com"]
        with patch.object(config, "_load_infra_config",
                          lambda: _infra_cfg(subdomain_patterns=patterns)):
            result = config.get_subdomain_patterns()
        assert result == patterns

    def test_config_yaml_overrides_env(self):
        with patch.object(config, "_load_infra_config",
                          lambda: _infra_cfg(subdomain_patterns=[r"cfg-pattern"])):
            with patch.dict(os.environ, {"SUBDOMAIN_PATTERNS": "env-pattern"}):
                result = config.get_subdomain_patterns()
        assert result == ["cfg-pattern"]


# ---------------------------------------------------------------------------
# get_generic_infra_names
# ---------------------------------------------------------------------------

class TestGetGenericInfraNames:
    def test_default_contains_proxmox(self):
        with patch.object(config, "_load_infra_config", _no_infra_cfg):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("NOISE_FILTER_NAMES", None)
                result = config.get_generic_infra_names()
        assert "proxmox" in result

    def test_env_var_merges_into_default(self):
        with patch.object(config, "_load_infra_config", _no_infra_cfg):
            with patch.dict(os.environ, {"NOISE_FILTER_NAMES": "my-custom-tool,another-tool"}):
                result = config.get_generic_infra_names()
        assert "my-custom-tool" in result
        assert "another-tool" in result
        assert "proxmox" in result  # default still present

    def test_config_yaml_replaces_default(self):
        with patch.object(config, "_load_infra_config",
                          lambda: _infra_cfg(generic_infra_names=["toolA", "toolB"])):
            result = config.get_generic_infra_names()
        assert result == frozenset({"toola", "toolb"})
        assert "proxmox" not in result  # config replaces, not extends

    def test_config_yaml_overrides_env(self):
        with patch.object(config, "_load_infra_config",
                          lambda: _infra_cfg(generic_infra_names=["cfg-tool"])):
            with patch.dict(os.environ, {"NOISE_FILTER_NAMES": "env-tool"}):
                result = config.get_generic_infra_names()
        assert "cfg-tool" in result
        assert "env-tool" not in result


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
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("SERVER_ALIASES", None)
                result = config.get_server_aliases()
        assert result == {}

    def test_env_var_single(self):
        with patch.object(config, "_load_infra_config", _no_infra_cfg):
            with patch.dict(os.environ, {"SERVER_ALIASES": "nas=thehoard-nas"}):
                result = config.get_server_aliases()
        assert result == {"nas": ["thehoard-nas"]}

    def test_env_var_pipe_separated(self):
        with patch.object(config, "_load_infra_config", _no_infra_cfg):
            with patch.dict(os.environ,
                            {"SERVER_ALIASES": "proxmox=aegis-proxmox|thesentinel-proxmox"}):
                result = config.get_server_aliases()
        assert result == {"proxmox": ["aegis-proxmox", "thesentinel-proxmox"]}

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

    def test_config_yaml_overrides_env(self):
        with patch.object(config, "_load_infra_config",
                          lambda: _infra_cfg(server_aliases={"cfg-host": "cfg-target"})):
            with patch.dict(os.environ, {"SERVER_ALIASES": "env-host=env-target"}):
                result = config.get_server_aliases()
        assert "cfg-host" in result
        assert "env-host" not in result


# ---------------------------------------------------------------------------
# get_suppressed_entity_names
# ---------------------------------------------------------------------------

class TestGetSuppressedEntityNames:
    def test_default_empty(self):
        with patch.object(config, "_load_infra_config", _no_infra_cfg):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("SUPPRESS_ENTITY_CHECK", None)
                result = config.get_suppressed_entity_names()
        assert result == frozenset()

    def test_env_var(self):
        with patch.object(config, "_load_infra_config", _no_infra_cfg):
            with patch.dict(os.environ, {"SUPPRESS_ENTITY_CHECK": "Proxmox Node,Home Assistant"}):
                result = config.get_suppressed_entity_names()
        assert "proxmox node" in result
        assert "home assistant" in result

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

    def test_config_yaml_overrides_env(self):
        with patch.object(config, "_load_infra_config",
                          lambda: _infra_cfg(suppress_entity_check=["cfg-entity"])):
            with patch.dict(os.environ, {"SUPPRESS_ENTITY_CHECK": "env-entity"}):
                result = config.get_suppressed_entity_names()
        assert "cfg-entity" in result
        assert "env-entity" not in result


# ---------------------------------------------------------------------------
# get_pulse_url
# ---------------------------------------------------------------------------

class TestGetPulseUrl:
    def test_default(self):
        with patch.object(config, "_load_infra_config", _no_infra_cfg):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("PULSE_API_URL", None)
                result = config.get_pulse_url()
        assert result == config.PULSE_API_URL.rstrip("/")

    def test_env_var(self):
        with patch.object(config, "_load_infra_config", _no_infra_cfg):
            with patch.dict(os.environ, {"PULSE_API_URL": "http://10.0.0.5:7655"}):
                result = config.get_pulse_url()
        assert result == "http://10.0.0.5:7655"

    def test_config_yaml_overrides_env(self):
        with patch.object(config, "_load_infra_config",
                          lambda: _infra_cfg(pulse_url="http://cfg-host:7655")):
            with patch.dict(os.environ, {"PULSE_API_URL": "http://env-host:7655"}):
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
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("NPM_URL", None)
                result = config.get_npm_url()
        assert result == config.NPM_URL  # empty string

    def test_env_var(self):
        with patch.object(config, "_load_infra_config", _no_infra_cfg):
            with patch.dict(os.environ, {"NPM_URL": "http://192.168.0.13:81"}):
                result = config.get_npm_url()
        assert result == "http://192.168.0.13:81"

    def test_config_yaml_overrides_env(self):
        with patch.object(config, "_load_infra_config",
                          lambda: _infra_cfg(npm_url="http://cfg-npm:81")):
            with patch.dict(os.environ, {"NPM_URL": "http://env-npm:81"}):
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
