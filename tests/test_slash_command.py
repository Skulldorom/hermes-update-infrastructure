"""Tests for slash command registration and agent handoff behavior."""

import _bootstrap  # noqa: F401 — loads update_infrastructure

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
