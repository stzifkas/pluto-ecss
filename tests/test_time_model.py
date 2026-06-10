"""Time and duration model (spec A.3.9.4/6, A.4.2 time constants).

Covers the runtime parsers, time/duration literals in expressions, the
`wait for <duration>` statement, and `with timeout T raise event E`.
"""
import contextlib
import io
from datetime import datetime, timedelta

from pluto_ecss.formatter import format_source
from pluto_ecss.json_emit import transpile_to_dict
from pluto_ecss.runtime import pluto_duration, pluto_time
from pluto_ecss.transpiler import transpile


def _run(src: str) -> str:
    py = transpile(src)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(compile(py, "<transpiled>", "exec"), {"__name__": "__main__"})
    return buf.getvalue()


# ---- runtime parsers ----
def test_pluto_time_parses_iso_with_and_without_z():
    assert pluto_time("2008-07-31T12:00:00Z") == datetime.fromisoformat("2008-07-31T12:00:00+00:00")
    assert pluto_time("2026-06-09T08:30:15.5") == datetime(2026, 6, 9, 8, 30, 15, 500000)


def test_pluto_duration_forms():
    assert pluto_duration("5d") == timedelta(days=5)
    assert pluto_duration("2h30min") == timedelta(hours=2, minutes=30)
    assert pluto_duration("10.5s") == timedelta(seconds=10.5)
    assert pluto_duration("1d2h30min10s") == timedelta(days=1, hours=2, minutes=30, seconds=10)


# ---- literals in expressions ----
def test_time_literals_transpile_and_evaluate():
    out = _run("""
    procedure
      main
        t := 2008-07-31T12:00:00Z
        d := 2h30min
        inform user t
        inform user d
      end main
    end procedure
    """)
    assert "2008-07-31" in out      # datetime str
    assert "2:30:00" in out         # timedelta str


# ---- wait for duration ----
def test_wait_for_duration_emits_call_and_runs():
    src = """
    procedure
      main
        wait for 0.01s
        inform user "done"
      end main
    end procedure
    """
    assert "wait_for_duration(" in transpile(src)
    assert "done" in _run(src)


# ---- timeout raise event ----
def test_while_timeout_raises_event():
    out = _run("""
    procedure
      declare
        event late described by took too long
      end declare
      watchdog
        on late do
          inform user "watchdog: late fired"
        end on
      end watchdog
      main
        while 1 = 1 do
          x := 1
        with timeout 0.05 raise event late
        end while
      end main
    end procedure
    """)
    assert "watchdog: late fired" in out


def test_wait_for_event_timeout_raises_event():
    out = _run("""
    procedure
      declare
        event timed described by deadline passed
      end declare
      watchdog
        on timed do
          inform user "wd: timed out"
        end on
      end watchdog
      main
        wait for event never with timeout 0.05 raise event timed
      end main
    end procedure
    """)
    assert "wd: timed out" in out


# ---- round-trips ----
def test_round_trips_through_formatter_and_json():
    src = """
    procedure
      main
        t := 2008-07-31T12:00:00Z;
        wait for 2h30min;
        while 1 = 1 do x := 1 with timeout 5 raise event boom end while
      end main
    end procedure
    """
    once = format_source(src)
    assert format_source(once) == once
    assert "t := 2008-07-31T12:00:00Z" in once
    assert "wait for 2h30min" in once
    assert "with timeout 5 raise event boom" in once

    d = transpile_to_dict(src)
    kinds = [s["kind"] for s in d["main"]]
    assert "wait_for_duration" in kinds


# ---- regression: review findings on the original PR ----
def test_duration_literal_as_timeout_does_not_crash():
    """`with timeout 0.2s` used to emit `time.time() + pluto_duration(...)`,
    a float + timedelta TypeError. pluto_seconds() coerces it."""
    out = _run("""
    procedure
      main
        i := 0
        repeat
          i := i + 1
        until 1 = 0 with timeout 0.05s
        end repeat
        inform user "survived"
      end main
    end procedure
    """)
    assert "survived" in out


def test_json_emit_includes_timeout_event():
    """The raise-event name must survive JSON serialisation (review finding:
    it was silently dropped from while/repeat/wait records)."""
    d = transpile_to_dict("""
    procedure
      main
        while 1 = 1 do x := 1 with timeout 5 raise event boom end while;
        wait for event go with timeout 3 raise event late
      end main
    end procedure
    """)
    w, wfe = d["main"][0], d["main"][1]
    assert w["timeout_event"] == "boom"
    assert wfe["timeout_event"] == "late"


def test_pluto_seconds_coercion():
    from pluto_ecss.runtime import pluto_seconds
    assert pluto_seconds(timedelta(minutes=2)) == 120.0
    assert pluto_seconds(5) == 5.0
    assert pluto_seconds(0.5) == 0.5
