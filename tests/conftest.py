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
def mock_pulse_data():
    """Return realistic parsed Pulse API data for testing."""
    return {
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
                "parent_id": "fe93158d-abc",
                "tags": [],
            },
            {
                "name": "authentik",
                "group": "docker",
                "ip": "192.168.0.200",
                "host": "thehoard",
                "vmid": "",
                "os": "",
                "status": "running",
                "type": "app-container",
                "parent_id": "fe93158d-abc",
                "tags": [],
            },
            {
                "name": "haos15.2",
                "group": "proxmox-pve",
                "ip": "192.168.0.7",
                "host": "thesentinel",
                "vmid": 103,
                "os": "Home Assistant OS",
                "status": "running",
                "type": "vm",
                "parent_id": "",
                "tags": [],
            },
        ],
        "subdomains": [],
    }


@pytest.fixture
def tool_json_output():
    """Parse JSON string returned by _handle_update_tool."""

    def _parse(json_str: str) -> dict:
        return json.loads(json_str)

    return _parse
