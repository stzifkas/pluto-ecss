"""Smoke test for the TUI demo. Skipped if rich isn't installed."""
import pathlib

import pytest

rich = pytest.importorskip("rich")

from pluto_ecss.demo import DashboardState, _render  # noqa: E402


def test_dashboard_state_mutations():
    state = DashboardState()
    state.set_component("Star Tracker1", "ON")
    state.push_activity("Switch on X")
    state.push_event("boom")
    assert state.components["Star Tracker1"] == "ON"
    assert "Switch on X" in state.activity_feed
    assert "boom" in state.event_log


def test_render_returns_group():
    state = DashboardState()
    state.set_component("a", "ON")
    state.push_activity("a")
    state.push_event("e")
    group = _render(state, "demo.pluto")
    # rich.console.Group is iterable; just confirm it builds without error
    assert group is not None


def test_demo_runs_to_completion(tmp_path):
    """Run pluto_ecss.demo.run_demo on the original script; assert it returns 0."""
    from pluto_ecss.demo import run_demo
    script = pathlib.Path(__file__).parent.parent / "examples" / "01_original.pluto"
    rc = run_demo(script)
    assert rc == 0
