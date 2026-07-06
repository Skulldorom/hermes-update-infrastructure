"""Shared fixtures for update-infrastructure tests."""

import json
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_wiki():
    """Create a temporary wiki directory with entities/ subdir."""
    with tempfile.TemporaryDirectory() as tmpdir:
        wiki_root = Path(tmpdir)
        (wiki_root / "entities").mkdir()
        (wiki_root / "guides").mkdir(parents=True)
        yield wiki_root


@pytest.fixture
def mock_entity_page_factory(tmp_wiki):
    """Factory to create mock entity pages in tmp_wiki/entities/."""

    def _create(slug: str, frontmatter: dict):
        path = tmp_wiki / "entities" / f"{slug}.md"
        fm_lines = ["---"]
        for k, v in frontmatter.items():
            if isinstance(v, list):
                fm_lines.append(f"{k}: [{', '.join(v)}]")
            elif isinstance(v, str) and " " in v:
                fm_lines.append(f'{k}: "{v}"')
            else:
                fm_lines.append(f"{k}: {v}")
        fm_lines.append("---")
        fm_lines.append(f"\n# {slug}\n")
        path.write_text("\n".join(fm_lines))
        return path

    return _create


@pytest.fixture
def mock_homepage_data():
    """Return realistic parsed homepage data for testing."""
    return {
        "services": [
            {
                "name": "Pihole",
                "group": "DNS",
                "href": "https://pihole.skulldorom.xyz",
                "site_monitor": "http://192.168.0.100",
                "description": "DNS ad blocking",
                "server": "docker-host",
                "container": "pihole",
                "widget_type": "pihole",
            },
            {
                "name": "Authentik",
                "group": "Auth",
                "href": "https://auth.skulldorom.xyz",
                "site_monitor": "http://192.168.0.200",
                "description": "SSO provider",
                "server": "docker-host",
            },
            {
                "name": "Bento PDF",
                "group": "Tools",
                "href": "https://bento.skulldorom.xyz",
                "site_monitor": "http://192.168.0.150",
                "description": "PDF toolkit",
            },
        ],
        "bookmarks": [],
        "subdomains": [
            {
                "subdomain": "pihole",
                "name": "Pihole",
                "href": "https://pihole.skulldorom.xyz",
                "site_monitor": "http://192.168.0.100",
                "group": "DNS",
            },
            {
                "subdomain": "auth",
                "name": "Authentik",
                "href": "https://auth.skulldorom.xyz",
                "site_monitor": "http://192.168.0.200",
                "group": "Auth",
            },
        ],
    }


@pytest.fixture
def tool_json_output():
    """Parse JSON string returned by _handle_update_tool."""

    def _parse(json_str: str) -> dict:
        return json.loads(json_str)

    return _parse
