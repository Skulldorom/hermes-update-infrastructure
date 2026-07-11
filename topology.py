"""Topology diagram generation for homelab-infrastructure.

``build_mermaid_topology(machines_data)`` — a Mermaid ``graph TD`` string
suitable for embedding in wiki markdown (renders natively in Obsidian).
"""

import re

# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------

_COLOURS = {
    "infrastructure": {
        "bg": "#1e1b2e",
        "border": "#8b7ab8",
        "header": "#6d5aa3",
        "header_text": "#ffffff",
        "node_bg": "#2a2340",
        "node_border": "#8b7ab8",
        "node_text": "#e6defa",
        "subgraph_label": "Infrastructure",
    },
    "networking": {
        "bg": "#2a2016",
        "border": "#d49a5f",
        "header": "#b8793f",
        "header_text": "#ffffff",
        "node_bg": "#3a2a1c",
        "node_border": "#d49a5f",
        "node_text": "#fde7c5",
        "subgraph_label": "Networking",
    },
    "container": {
        "bg": "#162235",
        "border": "#6f9fe6",
        "header": "#3f6fb8",
        "header_text": "#ffffff",
        "node_bg": "#21324d",
        "node_border": "#6f9fe6",
        "node_text": "#dbeaff",
        "subgraph_label": "Containers / VMs",
    },
    "device": {
        "bg": "#16281b",
        "border": "#74a878",
        "header": "#4f8a59",
        "header_text": "#ffffff",
        "node_bg": "#23392a",
        "node_border": "#74a878",
        "node_text": "#d8f0dc",
        "subgraph_label": "Devices",
    },
}

_DEFAULT_COLOURS = {
    "bg": "#1b2232",
    "border": "#7f8ca6",
    "header": "#5f6f8d",
    "header_text": "#ffffff",
    "node_bg": "#2a344a",
    "node_border": "#7f8ca6",
    "node_text": "#e3e9f5",
    "subgraph_label": "Other",
}


def _colours(category: str) -> dict:
    return _COLOURS.get(category, _DEFAULT_COLOURS)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _safe_id(slug: str) -> str:
    """Convert a slug to a safe Mermaid identifier."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", slug)


def _node_label_lines(machine: dict) -> list[str]:
    """Return display label lines for a machine node."""
    parts = [machine["name"]]
    if machine.get("ip"):
        parts.append(machine["ip"])
    if machine.get("os"):
        parts.append(machine["os"])
    return parts


# ---------------------------------------------------------------------------
# Mermaid builder
# ---------------------------------------------------------------------------


def build_mermaid_topology(machines_data: dict) -> str:
    """Generate a Mermaid ``graph TD`` diagram from entity machine data.

    Args:
        machines_data: Dict with ``"machines"`` list as returned by
            :func:`parser_wiki.parse_entity_pages`.

    Returns:
        A Mermaid ``graph TD`` code string (without the fenced code block
        delimiters — callers wrap it as needed).
    """
    machines = machines_data.get("machines", [])
    if not machines:
        return "graph TD\n    empty[No entity pages found]"

    # Deduplicate: suppress purely-numeric slugs that share an IP with a
    # named entity.  These are typically CT/VM IDs auto-parsed from inline
    # tables whose real identity already has a dedicated entity page.
    _numeric_slugs = {m["slug"]: m for m in machines if re.fullmatch(r"\d+", m["slug"])}
    if _numeric_slugs:
        _named_by_ip: dict[str, str] = {}
        for m in machines:
            ip = m.get("ip", "")
            if ip and not re.fullmatch(r"\d+", m["slug"]):
                _named_by_ip[ip] = m["slug"]
        if _named_by_ip:
            machines = [
                m
                for m in machines
                if not (
                    m["slug"] in _numeric_slugs
                    and m.get("ip", "") in _named_by_ip
                )
            ]

    # Build slug → machine lookup
    slug_map = {m["slug"]: m for m in machines}

    # Separate top-level hosts from child containers
    # A machine is a "child" if its host field resolves to another known machine
    children: dict[str, list[dict]] = {}  # parent_slug → list of child machines
    top_level: list[dict] = []
    for m in machines:
        host_slug = m.get("host", "")
        if host_slug and host_slug in slug_map:
            children.setdefault(host_slug, []).append(m)
        else:
            top_level.append(m)

    # Group top-level machines by category
    groups: dict[str, list[dict]] = {}
    for m in top_level:
        cat = m["category"]
        groups.setdefault(cat, []).append(m)

    lines: list[str] = ["graph TD"]

    # Render subgraphs for each category
    cat_order = ["infrastructure", "networking", "device", "container"]
    rendered_cats = cat_order + [c for c in groups if c not in cat_order]

    for cat in rendered_cats:
        cat_machines = groups.get(cat)
        if not cat_machines:
            continue
        col = _colours(cat)
        label = col.get("subgraph_label", cat.title())
        lines.append(f"\n    subgraph {label}")
        for m in cat_machines:
            mid = _safe_id(m["slug"])
            label_text = "\\n".join(_node_label_lines(m))
            lines.append(f'        {mid}["{label_text}"]')
            # Render direct children inline
            for child in children.get(m["slug"], []):
                cid = _safe_id(child["slug"])
                clabel = "\\n".join(_node_label_lines(child))
                lines.append(f'        {cid}["{clabel}"]')
                # Render grandchildren inline too
                for grandchild in children.get(child["slug"], []):
                    gcid = _safe_id(grandchild["slug"])
                    gclabel = "\\n".join(_node_label_lines(grandchild))
                    lines.append(f'        {gcid}["{gclabel}"]')
        lines.append("    end")

    # Machines that had a host but host was not found — add to "other"
    orphan_children: list[dict] = []
    for m in machines:
        host_slug = m.get("host", "")
        if host_slug and host_slug not in slug_map:
            orphan_children.append(m)
    if orphan_children:
        lines.append("\n    subgraph Other")
        for m in orphan_children:
            mid = _safe_id(m["slug"])
            label_text = "\\n".join(_node_label_lines(m))
            lines.append(f'        {mid}["{label_text}"]')
        lines.append("    end")

    # Edges: host → child
    lines.append("")
    for parent_slug, child_list in children.items():
        if parent_slug in slug_map:
            pid = _safe_id(parent_slug)
            for child in child_list:
                cid = _safe_id(child["slug"])
                lines.append(f"    {pid} --> {cid}")

    # Class definitions and assignments
    lines.append("")
    lines.append("    classDef infra  fill:#7c6f9f,stroke:#5a5080,color:#fff")
    lines.append("    classDef net    fill:#c08040,stroke:#8a5a20,color:#fff")
    lines.append("    classDef cont   fill:#4a7ac8,stroke:#2a5aa0,color:#fff")
    lines.append("    classDef dev    fill:#5a8a5a,stroke:#3a6a3a,color:#fff")

    lines.append("")
    for m in machines:
        mid = _safe_id(m["slug"])
        cat = m["category"]
        if cat == "infrastructure":
            lines.append(f"    class {mid} infra")
        elif cat == "networking":
            lines.append(f"    class {mid} net")
        elif cat == "container":
            lines.append(f"    class {mid} cont")
        elif cat == "device":
            lines.append(f"    class {mid} dev")

    return "\n".join(lines)
