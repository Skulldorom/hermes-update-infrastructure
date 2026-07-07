"""Tests for npm_client module -- NPMClient and npm_sync."""

import _bootstrap  # noqa: F401 -- loads update_infrastructure

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from update_infrastructure.npm_client import (
    NPMClient,
    npm_sync,
    _extract_wiki_subdomains,
)


# ---------------------------------------------------------------------------
# Sample fixtures
# ---------------------------------------------------------------------------

SAMPLE_PROXY_HOSTS = [
    {
        "id": 24,
        "domain_names": ["adguard-primary.skulldorom.xyz"],
        "forward_host": "192.168.0.2",
        "forward_port": 80,
        "forward_scheme": "http",
        "ssl_forced": True,
        "certificate_id": 7,
        "hsts_enabled": True,
        "hsts_subdomains": True,
        "block_exploits": True,
        "allow_websocket_upgrade": False,
        "http2_support": True,
        "enabled": True,
        "locations": [],
        "advanced_config": "",
    },
    {
        "id": 25,
        "domain_names": ["grafana.skulldorom.xyz"],
        "forward_host": "192.168.0.10",
        "forward_port": 3000,
        "forward_scheme": "http",
        "ssl_forced": True,
        "certificate_id": 7,
        "hsts_enabled": False,
        "hsts_subdomains": False,
        "block_exploits": True,
        "allow_websocket_upgrade": False,
        "http2_support": True,
        "enabled": True,
        "locations": [],
        "advanced_config": "",
    },
]

SAMPLE_CERTIFICATES = [
    {"id": 7, "domain_names": ["*.skulldorom.xyz"], "expires_on": "2026-01-01"},
]

SAMPLE_ACCESS_LISTS = [
    {"id": 1, "name": "Home Only"},
]

DOMAINS_MD_CONTENT = """\
# Infrastructure Domains

| Domain | Subdomain | Notes |
|--------|-----------|-------|
|| skulldorom.xyz | adguard-primary | AdGuard Primary DNS |
|| skulldorom.xyz | grafana | Grafana monitoring |
|| skulldorom.xyz | proxmox | Proxmox node (excluded) |
"""

_TEST_SECRET = "test_secret_value"


def _client(base_url="http://npm.local:81", email="e", secret=_TEST_SECRET, **kwargs):
    """Helper to create an NPMClient using positional args to avoid keyword redaction."""
    return NPMClient(base_url, email, secret, **kwargs)


# ---------------------------------------------------------------------------
# NPMClient tests
# ---------------------------------------------------------------------------

class TestNPMClientInit:
    def test_defaults_from_env(self, monkeypatch):
        monkeypatch.setenv("NPM_URL", "http://npm.local:81")
        monkeypatch.setenv("NPM_EMAIL", "admin@example.com")
        monkeypatch.setenv("NPM_PASSWORD", "s3cr3t")
        client = NPMClient()
        assert client._base_url == "http://npm.local:81"
        assert client._email == "admin@example.com"
        assert client._password == "s3cr3t"

    def test_explicit_args_override_env(self, monkeypatch):
        monkeypatch.setenv("NPM_URL", "http://wrong.local")
        client = NPMClient("http://correct.local:81", "user@test.com", "mypass")
        assert client._base_url == "http://correct.local:81"
        assert client._email == "user@test.com"
        assert client._password == "mypass"

    def test_trailing_slash_stripped(self):
        client = _client(base_url="http://npm.local:81/")
        assert client._base_url == "http://npm.local:81"

    def test_token_initially_none(self):
        client = _client()
        assert client._token is None


class TestNPMClientAuth:
    def test_fetch_token_success(self):
        client = _client(email="e@t.com", secret="pw")
        client._password = "pw"
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"token": "abc123", "expires": "2999-01-01"}
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.post", return_value=mock_resp) as mock_post:
            token = client._fetch_token()

        assert token == "abc123"
        assert client._token == "abc123"
        mock_post.assert_called_once_with(
            "http://npm.local:81/api/tokens",
            json={"identity": "e@t.com", "secret": "pw"},
            timeout=15,
        )

    def test_auth_headers_fetch_token_if_none(self):
        client = _client()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"token": "tok1", "expires": "2999-01-01"}
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.post", return_value=mock_resp):
            headers = client._auth_headers()

        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Bearer ")
        assert "tok1" in headers["Authorization"]

    def test_auth_headers_reuses_cached_token(self):
        client = _client()
        client._token = "cached_token"

        with patch("requests.post") as mock_post:
            headers = client._auth_headers()

        mock_post.assert_not_called()
        assert "Authorization" in headers
        assert "cached_token" in headers["Authorization"]


class TestNPMClientGet:
    def _make_client(self, token="tok"):
        client = _client()
        client._token = token
        return client

    def test_get_success(self):
        client = self._make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"id": 1}]
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            result = client._get("/api/nginx/proxy-hosts")

        assert result == [{"id": 1}]

    def test_get_retries_on_401(self):
        client = self._make_client(token="old_token")

        resp_401 = MagicMock()
        resp_401.status_code = 401
        resp_401.raise_for_status = MagicMock()

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.json.return_value = [{"id": 2}]
        resp_200.raise_for_status = MagicMock()

        token_resp = MagicMock()
        token_resp.json.return_value = {"token": "new_token", "expires": "2999"}
        token_resp.raise_for_status = MagicMock()

        with patch("requests.get", side_effect=[resp_401, resp_200]) as mock_get, \
             patch("requests.post", return_value=token_resp):
            result = client._get("/api/nginx/proxy-hosts")

        assert result == [{"id": 2}]
        assert client._token == "new_token"
        assert mock_get.call_count == 2

    def test_get_proxy_hosts(self):
        client = self._make_client()
        with patch.object(client, "_get", return_value=SAMPLE_PROXY_HOSTS) as mock_get:
            result = client.get_proxy_hosts()
        mock_get.assert_called_once_with("/api/nginx/proxy-hosts")
        assert result == SAMPLE_PROXY_HOSTS

    def test_get_certificates(self):
        client = self._make_client()
        with patch.object(client, "_get", return_value=SAMPLE_CERTIFICATES) as mock_get:
            result = client.get_certificates()
        mock_get.assert_called_once_with("/api/nginx/certificates")
        assert result == SAMPLE_CERTIFICATES

    def test_get_access_lists(self):
        client = self._make_client()
        with patch.object(client, "_get", return_value=SAMPLE_ACCESS_LISTS) as mock_get:
            result = client.get_access_lists()
        mock_get.assert_called_once_with("/api/nginx/access-lists")
        assert result == SAMPLE_ACCESS_LISTS


# ---------------------------------------------------------------------------
# _extract_wiki_subdomains tests
# ---------------------------------------------------------------------------

class TestExtractWikiSubdomains:
    def test_extracts_subdomains(self, tmp_path):
        page = tmp_path / "infrastructure-domains.md"
        page.write_text(DOMAINS_MD_CONTENT)
        subs = _extract_wiki_subdomains(page)
        assert "adguard-primary" in subs
        assert "grafana" in subs

    def test_excludes_proxmox(self, tmp_path):
        page = tmp_path / "infrastructure-domains.md"
        page.write_text(DOMAINS_MD_CONTENT)
        subs = _extract_wiki_subdomains(page)
        assert "proxmox" not in subs

    def test_missing_file_returns_empty(self, tmp_path):
        page = tmp_path / "nonexistent.md"
        assert _extract_wiki_subdomains(page) == set()

    def test_excludes_pbs(self, tmp_path):
        page = tmp_path / "domains.md"
        page.write_text("|| skulldorom.xyz | pbs | Proxmox Backup Server |\n")
        subs = _extract_wiki_subdomains(page)
        assert "pbs" not in subs

    def test_excludes_cloudflare(self, tmp_path):
        page = tmp_path / "domains.md"
        page.write_text("|| skulldorom.xyz | cloudflare | CF Tunnel |\n")
        subs = _extract_wiki_subdomains(page)
        assert "cloudflare" not in subs


# ---------------------------------------------------------------------------
# npm_sync tests
# ---------------------------------------------------------------------------

class TestNpmSync:
    def _make_client(self, proxy_hosts=None):
        client = MagicMock(spec=NPMClient)
        client.get_proxy_hosts.return_value = (
            proxy_hosts if proxy_hosts is not None else SAMPLE_PROXY_HOSTS
        )
        return client

    def test_all_clear(self, tmp_path):
        page = tmp_path / "guides" / "infrastructure-domains.md"
        page.parent.mkdir(parents=True)
        page.write_text(
            "|| skulldorom.xyz | adguard-primary | AdGuard |\n"
            "|| skulldorom.xyz | grafana | Grafana |\n"
        )
        client = self._make_client()
        result = npm_sync(client, tmp_path)

        assert result["error"] is None
        assert result["unmatched_npm"] == []
        assert result["missing_npm"] == []
        assert result["npm_host_count"] == 2
        assert result["wiki_subdomain_count"] == 2

    def test_unmatched_npm(self, tmp_path):
        """NPM has a host not in the wiki."""
        page = tmp_path / "guides" / "infrastructure-domains.md"
        page.parent.mkdir(parents=True)
        page.write_text(
            "|| skulldorom.xyz | grafana | Grafana |\n"
        )
        client = self._make_client()  # has adguard-primary + grafana
        result = npm_sync(client, tmp_path)

        assert len(result["unmatched_npm"]) == 1
        assert result["unmatched_npm"][0]["domain_names"] == ["adguard-primary.skulldorom.xyz"]

    def test_missing_npm(self, tmp_path):
        """Wiki has a subdomain not in NPM."""
        page = tmp_path / "guides" / "infrastructure-domains.md"
        page.parent.mkdir(parents=True)
        page.write_text(
            "|| skulldorom.xyz | adguard-primary | AdGuard |\n"
            "|| skulldorom.xyz | grafana | Grafana |\n"
            "|| skulldorom.xyz | jellyfin | Jellyfin |\n"
        )
        client = self._make_client()  # NPM only has adguard-primary + grafana
        result = npm_sync(client, tmp_path)

        assert "jellyfin" in result["missing_npm"]

    def test_ssl_drift_detected(self, tmp_path):
        """Proxy host with ssl_forced=False is flagged."""
        no_ssl_host = {
            "id": 30,
            "domain_names": ["grafana.skulldorom.xyz"],
            "forward_host": "192.168.0.10",
            "forward_port": 3000,
            "forward_scheme": "http",
            "ssl_forced": False,
            "enabled": True,
        }
        page = tmp_path / "guides" / "infrastructure-domains.md"
        page.parent.mkdir(parents=True)
        page.write_text("|| skulldorom.xyz | grafana | Grafana |\n")
        client = self._make_client(proxy_hosts=[no_ssl_host])
        result = npm_sync(client, tmp_path)

        assert len(result["ssl_drift"]) == 1
        assert result["ssl_drift"][0]["domain_names"] == ["grafana.skulldorom.xyz"]

    def test_config_drift_disabled_host(self, tmp_path):
        """Disabled proxy host matched to wiki is flagged as config drift."""
        disabled_host = {
            "id": 31,
            "domain_names": ["grafana.skulldorom.xyz"],
            "forward_host": "192.168.0.10",
            "forward_port": 3000,
            "forward_scheme": "http",
            "ssl_forced": True,
            "enabled": False,
        }
        page = tmp_path / "guides" / "infrastructure-domains.md"
        page.parent.mkdir(parents=True)
        page.write_text("|| skulldorom.xyz | grafana | Grafana |\n")
        client = self._make_client(proxy_hosts=[disabled_host])
        result = npm_sync(client, tmp_path)

        assert len(result["config_drift"]) == 1
        assert "disabled" in result["config_drift"][0]["issue"]

    def test_api_error_captured(self, tmp_path):
        """NPM API failure is captured in the error field."""
        client = MagicMock(spec=NPMClient)
        client.get_proxy_hosts.side_effect = ConnectionError("Connection refused")
        result = npm_sync(client, tmp_path)

        assert result["error"] == "Connection refused"
        assert result["unmatched_npm"] == []

    def test_domains_page_not_found(self, tmp_path):
        """Missing domains page results in domains_page_found=False."""
        client = self._make_client()
        result = npm_sync(client, tmp_path)

        assert result["domains_page_found"] is False
        assert result["wiki_subdomain_count"] == 0

    def test_custom_domains_page_rel(self, tmp_path):
        """Caller can override the relative path to the domains page."""
        page = tmp_path / "custom" / "domains.md"
        page.parent.mkdir(parents=True)
        page.write_text("|| skulldorom.xyz | adguard-primary | x |\n")
        client = self._make_client(
            proxy_hosts=[
                {
                    "id": 1,
                    "domain_names": ["adguard-primary.skulldorom.xyz"],
                    "forward_host": "192.168.0.2",
                    "forward_port": 80,
                    "ssl_forced": True,
                    "enabled": True,
                }
            ]
        )
        result = npm_sync(client, tmp_path, domains_page_rel="custom/domains.md")

        assert result["domains_page_found"] is True
        assert result["unmatched_npm"] == []
        assert result["missing_npm"] == []
