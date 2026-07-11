"""Tests for parsing entity pages and inline machine tables."""

from update_infrastructure.parser_wiki import parse_entity_pages


def test_parse_entity_pages_normalises_frontmatter(tmp_path):
    (tmp_path / "host.md").write_text(
        "---\ntitle: Host One\nip: 10.0.0.2\ntype: proxmox\nservices: dns, backups\n---\n"
    )

    machines = parse_entity_pages(str(tmp_path))["machines"]

    assert machines == [{
        "name": "Host One", "slug": "host", "ip": "10.0.0.2", "host": "",
        "category": "infrastructure", "os": "", "services": ["dns", "backups"],
        "tailscale_ip": "",
    }]


def test_parse_inline_tables_and_suffixes_colliding_child_slugs(tmp_path):
    body = """---
title: {title}
ip: {ip}
type: server
---
## Containers
| Name | Purpose |
|---|---|
| [[Dozzle]] | [[Logs]] |
"""
    (tmp_path / "alpha.md").write_text(body.format(title="Alpha", ip="10.0.0.1"))
    (tmp_path / "beta.md").write_text(body.format(title="Beta", ip="10.0.0.2"))

    machines = parse_entity_pages(str(tmp_path))["machines"]
    children = [machine for machine in machines if machine["category"] == "container"]

    assert [child["slug"] for child in children] == ["dozzle", "dozzle-beta"]
    assert children[0]["host"] == "alpha"
    assert children[0]["services"] == ["Logs"]
    assert children[1]["name"] == "Dozzle (beta)"


def test_parse_vm_table_extracts_ip_and_service(tmp_path):
    (tmp_path / "host.md").write_text("""---
title: Host
ip: 10.0.0.1
type: server
---
## VMs / LXCs
| VMID | IP | Service |
|---|---|---|
| 101 | 10.0.0.20 | [[AdGuard|DNS]] |
""")

    child = parse_entity_pages(str(tmp_path))["machines"][1]

    assert child["slug"] == "101"
    assert child["ip"] == "10.0.0.20"
    assert child["services"] == ["AdGuard"]
