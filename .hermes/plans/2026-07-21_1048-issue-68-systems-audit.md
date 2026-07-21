# Systems Audit Implementation Plan

Goal: add a read-only `/systems-audit` slash command and `systems_audit` infra tool that report Pulse disk SMART health, resource pressure, active alerts, and connection health.

Approach:
1. Create `audit_health.py` with pure dict-processing helpers:
   - `check_disk_health(resources)` for `physical_disk` SMART/risk data.
   - `check_resource_pressure(resources)` for agent/VM/system-container/app-container usage over 80%.
   - `build_health_report(resources, active_alerts=None, connection_health=None)` for markdown output.
2. Add tests in `tests/test_audit_health.py` using realistic Pulse resource fixtures. Cover healthy, degraded, unknown, empty, normal pressure, flagged pressure, and integrated markdown.
3. Wire `__init__.py`:
   - import `audit_health` and `pulse_client`.
   - add `audit` intent parsing.
   - handle audit intent by fetching Pulse `/api/state`, passing `resources`, `activeAlerts`, and `connectionHealth` into the report renderer, without wiki writes.
   - register `systems_audit` infra tool.
   - register `/systems-audit`, `/audit`, and `/health` slash commands.
4. Add slash/tool registration tests and handler tests.
5. Validate with `python3 -m pytest`, `python3 -m ruff check .`, and `python3 -m pyright` if available.
6. Commit, push `hermes/issue-68-systems-audit`, and open a draft PR closing #68.

Acceptance criteria:
- `/systems-audit` returns a markdown report without changing wiki files.
- `systems_audit` tool returns the same report.
- `update_infrastructure audit` also works as an intent alias.
- Tests cover disk health, pressure thresholds, and integration wiring.
