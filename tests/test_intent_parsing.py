"""Tests for intent parsing in __init__.py."""

import _bootstrap  # noqa: F401 — loads update_infrastructure

import pytest

from update_infrastructure import _parse_intent


class TestParseIntent:
    def test_no_extra_text_returns_none(self):
        intent, params = _parse_intent("")
        assert intent == "none"
        assert params == {}

    def test_none_extra(self):
        intent, params = _parse_intent(None)
        assert intent == "none"

    def test_whitespace_only(self):
        intent, params = _parse_intent("   ")
        # Whitespace-only becomes echo (pattern matching on empty string fails)
        assert intent == "echo"
        assert params["text"] == "   "

    def test_status(self):
        intent, params = _parse_intent("status")
        assert intent == "status"

    def test_status_case_insensitive(self):
        intent, params = _parse_intent("STATUS")
        assert intent == "status"

    def test_cleanup(self):
        intent, params = _parse_intent("cleanup")
        assert intent == "cleanup"

    def test_fix_all(self):
        intent, params = _parse_intent("fix all")
        assert intent == "fix_all"

    def test_fix_all_case_insensitive(self):
        intent, params = _parse_intent("FIX ALL")
        assert intent == "fix_all"

    def test_fix_ip_quoted(self):
        intent, params = _parse_intent('fix-ip "Pihole"')
        assert intent == "fix_ip"
        assert params["name"] == "Pihole"
        assert not params["dry_run"]

    def test_fix_ip_quoted_dry_run(self):
        intent, params = _parse_intent('fix-ip "Pihole" --dry-run')
        assert intent == "fix_ip"
        assert params["name"] == "Pihole"
        assert params["dry_run"]

    def test_fix_ip_unquoted(self):
        intent, params = _parse_intent("fix-ip Pihole")
        assert intent == "fix_ip"
        assert params["name"] == "Pihole"

    def test_fix_ip_space_variant(self):
        intent, params = _parse_intent('fix ip "AdGuard Primary"')
        assert intent == "fix_ip"
        assert params["name"] == "AdGuard Primary"

    def test_create_quoted(self):
        intent, params = _parse_intent('create "Bento PDF"')
        assert intent == "create"
        assert params["name"] == "Bento PDF"

    def test_create_entity_quoted(self):
        intent, params = _parse_intent('create-entity "Bento PDF"')
        assert intent == "create"
        assert params["name"] == "Bento PDF"

    def test_create_unquoted(self):
        intent, params = _parse_intent("create Bento-PDF")
        assert intent == "create"
        assert params["name"] == "Bento-PDF"

    def test_check_quoted(self):
        intent, params = _parse_intent('check "Pihole"')
        assert intent == "check"
        assert params["name"] == "Pihole"

    def test_check_unquoted(self):
        intent, params = _parse_intent("check pihole")
        assert intent == "check"
        assert params["name"] == "pihole"

    def test_unrecognised_becomes_echo(self):
        intent, params = _parse_intent("some random text here")
        assert intent == "echo"
        assert params["text"] == "some random text here"

    def test_partial_match_no_false_positive(self):
        intent, params = _parse_intent("stat")
        assert intent == "echo"
