"""Tests for slash command registration and agent handoff behavior."""


import json
from unittest.mock import patch

from update_infrastructure import register


class _FakeCtx:
    def __init__(self):
        self.commands = {}
        self.tools = {}
        self.injected = []

    def register_tool(self, name, **kwargs):
        self.tools[name] = kwargs["handler"]

    def register_command(self, name, handler, description):
        self.commands[name] = handler

    def inject_message(self, message):
        self.injected.append(message)
        return True


def _mk_none_payload(review_prompt):
    """Build a JSON payload matching what _handle_update_tool returns for
    the default (no-args / 'none' intent) sync path."""
    return json.dumps({
        "intent": {"type": "none"},
        "review_prompt": review_prompt,
        "source": {"services": 0, "subdomains": 0},
        "cross_reference": {},
        "topology": {"machine_count": 0, "categories": {}, "connections": []},
    })


def test_default_slash_command_injects_review_prompt():
    ctx = _FakeCtx()
    register(ctx)
    handler = ctx.commands["update-infrastructure"]

    review_prompt = "## Infrastructure Sync Review\n- Ask: \"...\""
    payload = _mk_none_payload(review_prompt)
    with patch("update_infrastructure._handle_update_tool", return_value=payload):
        result = handler("")

    assert result == "🔄 Sync complete. Handing off results to agent for review..."
    assert ctx.injected == [review_prompt]


def test_slash_command_json_escape_hatch_returns_raw_json():
    ctx = _FakeCtx()
    register(ctx)
    handler = ctx.commands["update-infrastructure"]

    payload = json.dumps({"intent": {"type": "json", "result": None}})
    with patch("update_infrastructure._handle_update_tool", return_value=payload):
        result = handler("json")

    assert result == payload
    assert ctx.injected == []


def test_status_subcommand_keeps_direct_output():
    ctx = _FakeCtx()
    register(ctx)
    handler = ctx.commands["update-infrastructure"]

    payload = json.dumps({
        "intent": {"type": "status"},
        "source": {"services": 1, "subdomains": 1},
        "cross_reference": {"entities_dir_found": False},
    })
    with patch("update_infrastructure._handle_update_tool", return_value=payload):
        result = handler("status")

    assert "Infrastructure status check" in result
    assert ctx.injected == []


def test_aliases_use_same_handoff_behavior():
    ctx = _FakeCtx()
    register(ctx)

    review_prompt = "## Infrastructure Sync Review\n- Ask: \"...\""
    payload = _mk_none_payload(review_prompt)
    with patch("update_infrastructure._handle_update_tool", return_value=payload):
        result_a = ctx.commands["update-infra"]("")
        result_b = ctx.commands["sync-infra"]("")

    assert result_a == "🔄 Sync complete. Handing off results to agent for review..."
    assert result_b == "🔄 Sync complete. Handing off results to agent for review..."
    assert ctx.injected == [review_prompt, review_prompt]


def test_systems_audit_tool_and_command_registered():
    ctx = _FakeCtx()
    register(ctx)

    assert "systems_audit" in ctx.tools
    for command in ("systems-audit", "audit", "health"):
        assert command in ctx.commands


def test_systems_audit_command_returns_report():
    ctx = _FakeCtx()
    register(ctx)

    payload = json.dumps({
        "intent": {"type": "audit", "result": {"report": "## Systems Audit — today"}},
    })

    with patch("update_infrastructure._handle_update_tool", return_value=payload):
        result = ctx.commands["systems-audit"]("")

    assert result == "## Systems Audit — today"


def test_update_infrastructure_audit_subcommand_renders_report():
    ctx = _FakeCtx()
    register(ctx)
    handler = ctx.commands["update-infrastructure"]

    payload = json.dumps({
        "intent": {"type": "audit", "result": {"report": "## Systems Audit — today"}},
    })
    with patch("update_infrastructure._handle_update_tool", return_value=payload):
        result = handler("audit")

    assert result == "## Systems Audit — today"


class _FakeGatewayCtx:
    """Fake PluginContext that simulates gateway mode: inject_message is
    callable but always returns False (no CLI ref available)."""

    def __init__(self):
        self.commands = {}
        self.tools = {}
        self.injected = []

    def register_tool(self, name, **kwargs):
        self.tools[name] = kwargs["handler"]

    def register_command(self, name, handler, description):
        self.commands[name] = handler

    def inject_message(self, message):
        self.injected.append(message)
        return False


def test_gateway_mode_falls_back_to_rendered_summary():
    """When inject_message returns False (gateway/WebUI), the handler
    renders a concise human-readable summary instead of dumping the
    raw markdown review prompt."""
    ctx = _FakeGatewayCtx()
    register(ctx)
    handler = ctx.commands["update-infrastructure"]

    review_prompt = "## Infrastructure Sync Review\n- Ask: \"...\""
    payload = _mk_none_payload(review_prompt)
    with patch("update_infrastructure._handle_update_tool", return_value=payload):
        result = handler("")

    # Gateway mode: inject_message returned False, so we get a rendered
    # summary — not the raw markdown prompt.
    assert "Infrastructure Sync Review" not in result
    assert "✅" in result
    assert "Synced" in result or "synced" in result
    # inject_message was still called (it's callable), just returned False.
    assert ctx.injected == [review_prompt]


def test_no_inject_message_attr_falls_back_to_rendered_summary():
    """When ctx has no inject_message attribute at all, render a concise
    human-readable summary (same behavior as gateway mode)."""
    ctx = _FakeCtx()
    # Remove inject_message entirely to simulate a PluginContext without it.
    type(ctx).inject_message = None
    register(ctx)
    handler = ctx.commands["update-infrastructure"]

    review_prompt = "## Infrastructure Sync Review\n- Ask: \"...\""
    payload = _mk_none_payload(review_prompt)
    with patch("update_infrastructure._handle_update_tool", return_value=payload):
        result = handler("")

    # No inject_message at all: we get a rendered summary,
    # not the raw markdown prompt.
    assert "Infrastructure Sync Review" not in result
    assert "✅" in result
    assert "Synced" in result or "synced" in result
