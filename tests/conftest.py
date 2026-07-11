"""Shared fixtures for update-infrastructure tests."""

import json
import tempfile
from pathlib import Path

import importlib.util
import sys

import pytest


def _ensure_source_package_importable() -> None:
    """Allow running pytest directly from the plugin checkout.

    The package is intentionally stored at the repository root for Hermes plugin
    installation, while editable installs map that root to the
    ``update_infrastructure`` package. Direct ``pytest`` runs do not get that
    editable-install mapping, so install an equivalent package alias for tests.
    """
    if "update_infrastructure" in sys.modules:
        return

    repo_root = Path(__file__).resolve().parents[1]
    init_file = repo_root / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "update_infrastructure",
        init_file,
        submodule_search_locations=[str(repo_root)],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load update_infrastructure from {init_file}")

    module = importlib.util.module_from_spec(spec)
    sys.modules["update_infrastructure"] = module
    spec.loader.exec_module(module)


_ensure_source_package_importable()


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
                "name": "dns-service",
                "group": "docker",
                "ip": "198.51.100.100",
                "host": "host-alpha",
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
                "ip": "198.51.100.200",
                "host": "host-alpha",
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
                "ip": "198.51.100.7",
                "host": "host-beta",
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
