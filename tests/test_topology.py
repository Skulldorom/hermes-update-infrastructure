"""Tests for deterministic Mermaid topology rendering."""

from update_infrastructure.topology import build_mermaid_topology


def machine(name, slug, category="device", host="", ip="", os_name=""):
    return {
        "name": name, "slug": slug, "category": category, "host": host,
        "ip": ip, "os": os_name,
    }


def test_empty_topology():
    assert build_mermaid_topology({"machines": []}) == "graph TD\n    empty[No entity pages found]"


def test_topology_groups_nodes_and_connects_children():
    result = build_mermaid_topology({"machines": [
        machine("Host", "host-one", "infrastructure", ip="203.0.113.1", os_name="Debian"),
        machine("App", "app-one", "container", host="host-one", ip="203.0.113.2"),
    ]})

    assert 'subgraph Infrastructure' in result
    assert 'host_one["Host\\n203.0.113.1\\nDebian"]' in result
    assert 'app_one["App\\n203.0.113.2"]' in result
    assert "host_one --> app_one" in result
    assert "class host_one infra" in result
    assert "class app_one cont" in result


def test_numeric_duplicate_with_named_ip_is_suppressed():
    result = build_mermaid_topology({"machines": [
        machine("DNS Filter", "dns-filter", "container", ip="203.0.113.5"),
        machine("101", "101", "container", ip="203.0.113.5"),
    ]})

    assert "DNS Filter" in result
    assert '101["101' not in result
