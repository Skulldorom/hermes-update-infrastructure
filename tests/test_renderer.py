"""Tests for standalone rendering."""

from update_infrastructure import renderer


def test_help_reads_manifest_version():
    output = renderer.render_update_output({
        "intent": {"type": "help", "result": {}},
    })

    assert f"v{renderer._plugin_version()}" in output
    assert "v3.0.0" not in output


def test_renderer_import_has_no_sync_side_effects():
    assert callable(renderer.render_update_output)
    assert callable(renderer.build_review_prompt)
