"""Topology diagram generation for homelab-infrastructure.

Provides two outputs from the same machine-list data:

1. ``build_mermaid_topology(machines_data)`` — a Mermaid ``graph TD`` string
   suitable for embedding in wiki markdown (renders natively in Obsidian).

2. ``build_svg_topology(machines_data)`` — a standalone SVG string written to
   the wiki assets directory so it can be linked from any page.
"""

import re
import textwrap
from pathlib import Path


# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------

_COLOURS = {
    "infrastructure": {
        "bg": "#ede8f5",
        "border": "#7c6f9f",
        "header": "#7c6f9f",
        "header_text": "#ffffff",
        "node_bg": "#c5b8e0",
        "node_border": "#7c6f9f",
        "node_text": "#2a1a4a",
        "subgraph_label": "Infrastructure",
    },
    "networking": {
        "bg": "#fdf0dc",
        "border": "#c08040",
        "header": "#c08040",
        "header_text": "#ffffff",
        "node_bg": "#f5d9a8",
        "node_border": "#c08040",
        "node_text": "#3a1a00",
        "subgraph_label": "Networking",
    },
    "container": {
        "bg": "#e8f0fd",
        "border": "#4a7ac8",
        "header": "#4a7ac8",
        "header_text": "#ffffff",
        "node_bg": "#b8cef0",
        "node_border": "#4a7ac8",
        "node_text": "#0a1a3a",
        "subgraph_label": "Containers / VMs",
    },
    "device": {
        "bg": "#e8f5e8",
        "border": "#5a8a5a",
        "header": "#5a8a5a",
        "header_text": "#ffffff",
        "node_bg": "#b8d8b8",
        "node_border": "#5a8a5a",
        "node_text": "#0a2a0a",
        "subgraph_label": "Devices",
    },
}

_DEFAULT_COLOURS = {
    "bg": "#f5f5f5",
    "border": "#aaaaaa",
    "header": "#888888",
    "header_text": "#ffffff",
    "node_bg": "#dddddd",
    "node_border": "#aaaaaa",
    "node_text": "#111111",
    "subgraph_label": "Other",
}


def _colours(category: str) -> dict:
    return _COLOURS.get(category, _DEFAULT_COLOURS)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _safe_id(slug: str) -> str:
    """Convert a slug to a safe Mermaid/SVG identifier."""
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
            # Render child containers inline
            for child in children.get(m["slug"], []):
                cid = _safe_id(child["slug"])
                clabel = "\\n".join(_node_label_lines(child))
                lines.append(f'        {cid}["{clabel}"]')
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


# ---------------------------------------------------------------------------
# SVG builder
# ---------------------------------------------------------------------------

# SVG layout constants
_SVG_W = 1500
_SVG_H = 920
_FONT = "Segoe UI, Helvetica Neue, Arial, sans-serif"

_NODE_W = 180
_NODE_H = 52
_NODE_RX = 8
_HEADER_H = 22

_HOST_PADDING = 14
_HOST_HEADER_H = 32
_HOST_INNER_TOP = _HOST_HEADER_H + _HOST_PADDING
_HOST_COL_W = _NODE_W + 20
_HOST_COLS = 2

_ZONE_PADDING = 16
_ZONE_HEADER_H = 28
_ZONE_INNER_TOP = _ZONE_HEADER_H + _ZONE_PADDING

# Zone layout: key → (x, y, width, reserved_height_guess)
_ZONE_POSITIONS = {
    "infrastructure": (20, 20),
    "networking": (20, 20),       # overridden dynamically
    "container": (20, 20),
    "device": (20, 20),
}


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _svg_node(x: float, y: float, machine: dict, col: dict) -> tuple[str, float]:
    """Render a single machine node.  Returns (svg_fragment, actual_height)."""
    lines = _node_label_lines(machine)
    node_h = max(_NODE_H, _HEADER_H + len(lines) * 16 + 8)

    parts = [
        f'<rect x="{x:.0f}" y="{y:.0f}" width="{_NODE_W}" height="{node_h}"'
        f' rx="{_NODE_RX}" fill="{col["node_bg"]}" stroke="{col["node_border"]}" stroke-width="1.5"/>',
        f'<rect x="{x:.0f}" y="{y:.0f}" width="{_NODE_W}" height="{_HEADER_H}"'
        f' rx="{_NODE_RX}" fill="{col["node_border"]}" stroke="none"/>',
        # Clip the bottom rounded corners of the header bar
        f'<rect x="{x:.0f}" y="{y + _NODE_RX:.0f}" width="{_NODE_W}" height="{_HEADER_H - _NODE_RX}"'
        f' fill="{col["node_border"]}" stroke="none"/>',
        f'<text x="{x + _NODE_W / 2:.0f}" y="{y + _HEADER_H - 5:.0f}"'
        f' text-anchor="middle" font-family="{_FONT}" font-size="11"'
        f' font-weight="600" fill="{col["header_text"]}">{_xml_escape(lines[0])}</text>',
    ]
    for i, line in enumerate(lines[1:], start=1):
        ty = y + _HEADER_H + 4 + i * 14
        parts.append(
            f'<text x="{x + _NODE_W / 2:.0f}" y="{ty:.0f}" text-anchor="middle"'
            f' font-family="{_FONT}" font-size="10" fill="{col["node_text"]}">{_xml_escape(line)}</text>'
        )
    return "\n".join(parts), node_h


def _svg_host_block(
    x: float, y: float, host: dict, children: list[dict], col: dict
) -> tuple[str, float, float]:
    """Render a host box that contains its children.

    Returns (svg_fragment, block_width, block_height).
    """
    col_w = _HOST_COL_W
    n_children = len(children)
    n_cols = min(_HOST_COLS, max(1, n_children))
    n_rows = max(1, -(-n_children // n_cols))  # ceil division

    # Minimum child node height
    child_h = _NODE_H
    inner_w = n_cols * col_w + _HOST_PADDING
    inner_h = n_rows * (child_h + 10) + _HOST_PADDING

    block_w = max(_NODE_W + _HOST_PADDING * 2, inner_w + _HOST_PADDING)
    block_h = _HOST_INNER_TOP + inner_h + _HOST_PADDING

    host_col = col  # use same palette as zone

    parts: list[str] = [
        # Outer container box
        f'<rect x="{x:.0f}" y="{y:.0f}" width="{block_w:.0f}" height="{block_h:.0f}"'
        f' rx="12" fill="{host_col["bg"]}" stroke="{host_col["border"]}" stroke-width="2"/>',
        # Header bar
        f'<rect x="{x:.0f}" y="{y:.0f}" width="{block_w:.0f}" height="{_HOST_HEADER_H}"'
        f' rx="12" fill="{host_col["header"]}" stroke="none"/>',
        f'<rect x="{x:.0f}" y="{y + _NODE_RX:.0f}" width="{block_w:.0f}"'
        f' height="{_HOST_HEADER_H - _NODE_RX}" fill="{host_col["header"]}" stroke="none"/>',
    ]

    # Host label in header
    header_text_parts = [host["name"]]
    if host.get("ip"):
        header_text_parts.append(host["ip"])
    if host.get("os"):
        header_text_parts.append(host["os"])
    header_label = "  ·  ".join(header_text_parts)
    parts.append(
        f'<text x="{x + block_w / 2:.0f}" y="{y + _HOST_HEADER_H - 9:.0f}"'
        f' text-anchor="middle" font-family="{_FONT}" font-size="12"'
        f' font-weight="700" fill="{host_col["header_text"]}">{_xml_escape(header_label)}</text>'
    )

    # Child nodes
    child_col = _colours("container")
    cx = x + _HOST_PADDING
    cy_base = y + _HOST_INNER_TOP

    for i, child in enumerate(children):
        col_idx = i % n_cols
        row_idx = i // n_cols
        cx_pos = cx + col_idx * col_w
        cy_pos = cy_base + row_idx * (child_h + 10)
        frag, _ = _svg_node(cx_pos, cy_pos, child, child_col)
        parts.append(frag)

    # If no children, draw a placeholder
    if not children:
        label = host["name"]
        frag, _ = _svg_node(x + _HOST_PADDING, y + _HOST_INNER_TOP, host, host_col)
        parts.append(frag)
        block_h = _HOST_INNER_TOP + _NODE_H + _HOST_PADDING * 2

    return "\n".join(parts), block_w, block_h


def _svg_zone(
    x: float,
    y: float,
    zone_w: float,
    label: str,
    machines: list[dict],
    col: dict,
    slug_map: dict,
    children_map: dict,
    draw_hosts: bool = False,
) -> tuple[str, float]:
    """Render a category zone box containing node blocks.

    Returns (svg_fragment, total_height_used).
    """
    parts: list[str] = []
    row_gap = 14
    col_gap = 16

    inner_x = x + _ZONE_PADDING
    inner_y = y + _ZONE_INNER_TOP

    cur_x = inner_x
    cur_y = inner_y
    row_h = 0
    row_max_x = x + zone_w - _ZONE_PADDING

    for m in machines:
        if draw_hosts:
            kids = children_map.get(m["slug"], [])
            frag, bw, bh = _svg_host_block(cur_x, cur_y, m, kids, col)
            elem_w = bw
            elem_h = bh
        else:
            frag, elem_h = _svg_node(cur_x, cur_y, m, col)
            elem_w = _NODE_W

        # Wrap to next row if we'd overflow
        if cur_x + elem_w > row_max_x and cur_x > inner_x:
            cur_x = inner_x
            cur_y += row_h + row_gap
            row_h = 0

        # Re-render at corrected position
        if draw_hosts:
            frag, elem_w, elem_h = _svg_host_block(cur_x, cur_y, m, kids, col)
        else:
            frag, elem_h = _svg_node(cur_x, cur_y, m, col)

        parts.append(frag)
        row_h = max(row_h, elem_h)
        cur_x += elem_w + col_gap

    content_h = (cur_y - inner_y) + row_h + _ZONE_PADDING
    total_h = _ZONE_INNER_TOP + content_h

    # Draw zone background and border
    zone_bg = [
        f'<rect x="{x:.0f}" y="{y:.0f}" width="{zone_w:.0f}" height="{total_h:.0f}"'
        f' rx="14" fill="{col["bg"]}" stroke="{col["border"]}" stroke-width="2" opacity="0.9"/>',
        f'<rect x="{x:.0f}" y="{y:.0f}" width="{zone_w:.0f}" height="{_ZONE_HEADER_H}"'
        f' rx="14" fill="{col["header"]}" stroke="none"/>',
        f'<rect x="{x:.0f}" y="{y + _NODE_RX:.0f}" width="{zone_w:.0f}"'
        f' height="{_ZONE_HEADER_H - _NODE_RX}" fill="{col["header"]}" stroke="none"/>',
        f'<text x="{x + zone_w / 2:.0f}" y="{y + _ZONE_HEADER_H - 7:.0f}"'
        f' text-anchor="middle" font-family="{_FONT}" font-size="13"'
        f' font-weight="700" fill="{col["header_text"]}">{_xml_escape(label)}</text>',
    ]

    return "\n".join(zone_bg + parts), total_h


def build_svg_topology(machines_data: dict) -> str:
    """Generate a standalone SVG network topology diagram.

    Args:
        machines_data: Dict with ``"machines"`` list as returned by
            :func:`parser_wiki.parse_entity_pages`.

    Returns:
        A complete SVG document string.  Returns a minimal placeholder SVG
        when no machines are found.
    """
    machines = machines_data.get("machines", [])

    if not machines:
        return textwrap.dedent("""\
            <svg xmlns="http://www.w3.org/2000/svg" width="400" height="80">
              <rect width="400" height="80" rx="8" fill="#f5f5f5" stroke="#ccc"/>
              <text x="200" y="46" text-anchor="middle"
                    font-family="Arial,sans-serif" font-size="14" fill="#888">
                No entity pages found — run /update-infrastructure to populate.
              </text>
            </svg>
        """)

    slug_map = {m["slug"]: m for m in machines}

    # Separate children from top-level machines
    children_map: dict[str, list[dict]] = {}
    top_level: list[dict] = []
    for m in machines:
        host_slug = m.get("host", "")
        if host_slug and host_slug in slug_map:
            children_map.setdefault(host_slug, []).append(m)
        else:
            top_level.append(m)

    # Group top-level machines by category
    groups: dict[str, list[dict]] = {}
    for m in top_level:
        groups.setdefault(m["category"], []).append(m)

    # -----------------------------------------------------------------------
    # Compute zone dimensions and positions
    # -----------------------------------------------------------------------
    margin = 20
    zone_gap = 20
    INFRA_ZONE_W = 900
    NET_ZONE_W = 380
    DEV_ZONE_W = 500

    svg_parts: list[str] = []
    connections: list[str] = []  # drawn on top of zones

    cur_y = margin
    zone_x = margin

    # --- Infrastructure zone (left column, full width or partial) ---
    infra_machines = groups.get("infrastructure", [])
    if infra_machines:
        col = _colours("infrastructure")
        frag, zone_h = _svg_zone(
            zone_x, cur_y, INFRA_ZONE_W,
            col["subgraph_label"], infra_machines,
            col, slug_map, children_map, draw_hosts=True,
        )
        svg_parts.append(frag)
        infra_y = cur_y
        infra_h = zone_h
    else:
        infra_y = cur_y
        infra_h = 0

    # --- Networking zone (right of infrastructure) ---
    net_x = zone_x + INFRA_ZONE_W + zone_gap
    net_machines = groups.get("networking", [])
    if net_machines:
        col = _colours("networking")
        frag, net_h = _svg_zone(
            net_x, cur_y, NET_ZONE_W,
            col["subgraph_label"], net_machines,
            col, slug_map, children_map, draw_hosts=False,
        )
        svg_parts.append(frag)
    else:
        net_h = 0

    left_col_h = max(infra_h, net_h)
    cur_y += left_col_h + zone_gap

    # --- Device zone (below both) ---
    dev_machines = groups.get("device", [])
    if dev_machines:
        col = _colours("device")
        frag, dev_h = _svg_zone(
            zone_x, cur_y, DEV_ZONE_W,
            col["subgraph_label"], dev_machines,
            col, slug_map, children_map, draw_hosts=False,
        )
        svg_parts.append(frag)
    else:
        dev_h = 0

    # --- Other categories ---
    known_cats = {"infrastructure", "networking", "container", "device"}
    other_x = zone_x + DEV_ZONE_W + zone_gap if dev_machines else zone_x
    other_y = cur_y
    for cat, cat_machines in groups.items():
        if cat in known_cats:
            continue
        col = _colours(cat)
        label = col.get("subgraph_label", cat.title())
        frag, oh = _svg_zone(
            other_x, other_y, DEV_ZONE_W,
            label, cat_machines, col, slug_map, children_map, draw_hosts=False,
        )
        svg_parts.append(frag)
        other_x += DEV_ZONE_W + zone_gap

    bottom_y = cur_y + max(dev_h, 0)

    total_w = net_x + NET_ZONE_W + margin
    total_h = bottom_y + margin

    # -----------------------------------------------------------------------
    # Assemble SVG
    # -----------------------------------------------------------------------
    header = [
        f'<svg xmlns="http://www.w3.org/2000/svg"'
        f' width="{total_w}" height="{total_h}"'
        f' viewBox="0 0 {total_w} {total_h}">',
        "  <!-- Auto-generated by hermes-update-infrastructure -->",
        f'  <rect width="{total_w}" height="{total_h}" fill="#f8f8fc"/>',
        f'  <text x="{total_w // 2}" y="18" text-anchor="middle"'
        f'   font-family="{_FONT}" font-size="11" fill="#999">'
        "Homelab Network Topology — auto-generated</text>",
    ]

    footer = ["</svg>"]

    return "\n".join(header + svg_parts + footer)


# ---------------------------------------------------------------------------
# File export helper
# ---------------------------------------------------------------------------

def export_topology_svg(machines_data: dict, output_path: str) -> bool:
    """Write the topology SVG to *output_path*.

    Creates parent directories if needed.

    Returns:
        ``True`` on success, ``False`` if an error occurred (errors are
        silently swallowed to avoid breaking the main update flow).
    """
    try:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        svg = build_svg_topology(machines_data)
        out.write_text(svg, encoding="utf-8")
        return True
    except Exception:
        return False
