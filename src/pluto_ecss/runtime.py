"""Runtime library used by transpiled PLUTO procedures.

Transpiled output imports `Runtime`, `Procedure`, and helper functions
from this module. Keeping the runtime explicit means the emitted Python
is readable and debuggable on its own.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("pluto-ecss")


_DURATION_RE = re.compile(
    r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)min)?(?:(\d+(?:\.\d+)?)s)?$"
)


def pluto_time(text: str) -> datetime:
    """Parse a PLUTO absolute time constant (ISO-8601, optional trailing Z)."""
    iso = text[:-1] + "+00:00" if text.endswith("Z") else text
    return datetime.fromisoformat(iso)


def pluto_duration(text: str) -> timedelta:
    """Parse a PLUTO relative time constant (`2h30min`, `10.5s`, ...) to a timedelta."""
    m = _DURATION_RE.fullmatch(text)
    if not m or not any(m.groups()):
        raise PlutoRuntimeError(f"invalid duration: {text!r}")
    days, hours, minutes, seconds = m.groups()
    return timedelta(
        days=int(days or 0),
        hours=int(hours or 0),
        minutes=int(minutes or 0),
        seconds=float(seconds or 0),
    )


def wait_for_duration(duration: Any) -> None:
    """Block for a relative time (a `timedelta`) or a number of seconds."""
    secs = duration.total_seconds() if isinstance(duration, timedelta) else float(duration)
    if secs > 0:
        time.sleep(secs)


class PlutoRuntimeError(RuntimeError):
    pass


class PlutoAborted(Exception):
    """Raised by an `abort` continuation action. Propagates out of the
    procedure body, terminating execution with confirmation_status
    'aborted' (spec A.2.5, A.3.9.33)."""


class PlutoTerminated(Exception):
    """Raised by a `terminate` continuation action. The procedure (or
    step) main body ends prematurely; the confirmation body runs next
    (spec A.3.9.33)."""


@dataclass
class Event:
    name: str
    description: Optional[str] = None
    raised: bool = False

    def raise_(self) -> None:
        self.raised = True
        log.info("event raised: %s", self.name)


@dataclass
class SystemElement:
    name: str
    kind: str = "part"
    description: str = ""
    children: List["SystemElement"] = field(default_factory=list)
    redundant: List[str] = field(default_factory=list)

    def qualified(self) -> str:
        return self.name


@dataclass
class ReportingData:
    """Telemetry parameter / reporting data (PLUTO spec A.3.9.8).

    Properties queryable via PLUTO `<property> of <ref>` expressions:
        value, engineering_value, validity_status, sampling_time.
    """
    name: str
    value: Any = None
    engineering_value: Any = None
    validity_status: str = "invalid"
    sampling_time: "Optional[datetime]" = None

    def get_property(self, prop: str) -> Any:
        if prop == "value":
            return self.value
        if prop == "engineering_value":
            return self.engineering_value if self.engineering_value is not None else self.value
        if prop == "validity_status":
            return self.validity_status
        if prop == "sampling_time":
            return self.sampling_time
        raise PlutoRuntimeError(f"unknown reporting data property: {prop!r}")

    def current_value(self) -> Any:
        """Return the reading used when the parameter is named in an
        expression (engineering value if set, otherwise raw value)."""
        return self.engineering_value if self.engineering_value is not None else self.value


@dataclass
class Activity:
    name: str
    target: str
    handler: Callable[..., Any]
    criticality: str = "routine"

    def invoke(self, **arguments: Any) -> Any:
        log.info("activity: %s on %s", self.name, self.target)
        try:
            return self.handler(self, **arguments)
        except TypeError:
            # Handler that doesn't accept kwargs — fall back to no-arg call.
            return self.handler(self)


@dataclass
class ActivityExecution:
    """Tracks execution state of an activity instance."""
    name: str
    execution_status: str = "pending"  # pending, executing, success, failure
    start_time: Optional[datetime] = None
    completion_time: Optional[datetime] = None
    confirmation_status: Optional[Any] = None

    def get_property(self, property_name: str) -> Any:
        """Get a property of this activity execution."""
        props = {
            "execution_status": self.execution_status,
            "start_time": self.start_time,
            "completion_time": self.completion_time,
            "confirmation_status": self.confirmation_status,
        }
        if property_name not in props:
            raise PlutoRuntimeError(f"unknown property: {property_name!r}")
        return props[property_name]


_systems: Dict[str, SystemElement] = {}
_activities: Dict[tuple, Activity] = {}
_reporting_data: Dict[str, ReportingData] = {}


def register_reporting_data(rd: ReportingData) -> ReportingData:
    """Make a parameter available to PLUTO references and save_context."""
    _reporting_data[rd.name] = rd
    return rd


def resolve_reporting_data(ref: str) -> Optional[ReportingData]:
    """Look up by exact name first, then by the leading qualifier
    (`Temperature of Battery1` -> falls back to `Temperature` if needed)."""
    if ref in _reporting_data:
        return _reporting_data[ref]
    head = ref.split(" of ")[0].strip()
    if head in _reporting_data:
        return _reporting_data[head]
    return None


def register_system(elem: SystemElement) -> SystemElement:
    _systems[elem.name] = elem
    return elem


def register_activity(act: Activity) -> Activity:
    _activities[(act.name, act.target)] = act
    return act


def resolve_system(qualified_name: str) -> SystemElement:
    parts = [p.strip() for p in qualified_name.split(" of ")]
    head = parts[0]
    if head not in _systems:
        raise PlutoRuntimeError(f"unknown system element: {head!r} (from {qualified_name!r})")
    return _systems[head]


def resolve_activity(name: str, target: str) -> Activity:
    key = (name, target)
    if key in _activities:
        return _activities[key]
    head_target = target.split(" of ")[0].strip()
    if (name, head_target) in _activities:
        return _activities[(name, head_target)]
    raise PlutoRuntimeError(f"unknown activity: {name!r} on {target!r}")


def _format_args(arguments: Optional[Dict[str, Any]]) -> str:
    if not arguments:
        return ""
    parts = ", ".join(f"{k}={v!r}" for k, v in arguments.items())
    return f" with {parts}"


def _default_switch_on(activity: Activity, **arguments: Any) -> str:
    msg = f"[ACTIVITY] Switch on {activity.target}{_format_args(arguments)}"
    print(msg)
    return msg


def _default_switch_off(activity: Activity, **arguments: Any) -> str:
    msg = f"[ACTIVITY] Switch off {activity.target}{_format_args(arguments)}"
    print(msg)
    return msg


def switch_on(target: str, *, arguments: Optional[Dict[str, Any]] = None) -> Callable[[], Any]:
    args = arguments or {}
    def _call():
        try:
            act = resolve_activity("Switch on", target)
        except PlutoRuntimeError:
            act = Activity("Switch on", target, _default_switch_on)
        return act.invoke(**args)
    _call.__pluto_name__ = f"Switch on {target}"
    _call.arguments = args
    return _call


def switch_off(target: str, *, arguments: Optional[Dict[str, Any]] = None) -> Callable[[], Any]:
    args = arguments or {}
    def _call():
        try:
            act = resolve_activity("Switch off", target)
        except PlutoRuntimeError:
            act = Activity("Switch off", target, _default_switch_off)
        return act.invoke(**args)
    _call.__pluto_name__ = f"Switch off {target}"
    _call.arguments = args
    return _call


def initiate(*args, instance_name: Optional[str] = None) -> threading.Thread:
    """Fire-and-forget an activity.

    Signatures:
      initiate(call)
      initiate(proc, call)
      initiate(proc, call, instance_name="MY_INSTANCE")   # from `refer by`

    When `instance_name` is given (PLUTO `refer by`), the activity is
    registered under that name on the procedure so subsequent
    `<property> of MY_INSTANCE` references resolve to its execution
    state (spec A.3.9.27 + A.3.9.8).
    """
    if len(args) == 1:
        call = args[0]
        t = threading.Thread(target=call, name=getattr(call, "__pluto_name__", "activity"))
        t.start()
        return t
    if len(args) == 2:
        proc, call = args
        activity_name = instance_name or getattr(call, "__pluto_name__", "activity")
        act_exec = proc.register_activity(activity_name)

        def wrapper():
            act_exec.start_time = datetime.now()
            act_exec.execution_status = "executing"
            try:
                result = call()
                act_exec.confirmation_status = result
                act_exec.execution_status = "success"
            except Exception as e:
                act_exec.execution_status = "failure"
                act_exec.confirmation_status = str(e)
                raise
            finally:
                act_exec.completion_time = datetime.now()

        t = threading.Thread(target=wrapper, name=activity_name)
        t.start()
        return t
    raise TypeError("initiate takes 1 or 2 positional args")


def initiate_and_confirm(*args, instance_name: Optional[str] = None) -> Any:
    """Initiate-and-confirm an activity, awaiting completion.

    Signatures:
      initiate_and_confirm(call)
      initiate_and_confirm(proc, call)
      initiate_and_confirm(proc, call, instance_name="MY_INSTANCE")  # from `refer by`
    """
    if len(args) == 1:
        return args[0]()
    if len(args) == 2:
        proc, call = args
        activity_name = instance_name or getattr(call, "__pluto_name__", "activity")
        act_exec = proc.register_activity(activity_name)
        act_exec.start_time = datetime.now()
        act_exec.execution_status = "executing"
        try:
            result = call()
            act_exec.confirmation_status = result
            act_exec.execution_status = "success"
            return result
        except Exception as e:
            act_exec.execution_status = "failure"
            act_exec.confirmation_status = str(e)
            raise
        finally:
            act_exec.completion_time = datetime.now()
    raise TypeError("initiate_and_confirm takes 1 or 2 positional args")


@dataclass
class Step:
    name: str
    body: Callable[[], None]
    status: str = "not initiated"

    def run(self) -> None:
        self.status = "executing"
        log.info("step start: %s", self.name)
        try:
            self.body()
            self.status = "completed"
            log.info("step complete: %s", self.name)
        except Exception as e:
            self.status = "failed"
            log.error("step failed: %s: %s", self.name, e)
            raise


def initiate_and_confirm_step(proc, name: str, body: Callable[[], None]) -> Step:
    """Run a named step. The step is registered with the Procedure so its
    execution_status / start_time / completion_time / confirmation_status
    can be queried via PLUTO `<property> of <step_name>` references."""
    act_exec = proc.register_activity(name)
    step = Step(name, body)
    from datetime import datetime
    act_exec.start_time = datetime.now()
    act_exec.execution_status = "executing"
    try:
        step.run()
        act_exec.execution_status = "success"
        act_exec.confirmation_status = "success"
    except Exception as e:
        act_exec.execution_status = "failure"
        act_exec.confirmation_status = str(e)
        raise
    finally:
        act_exec.completion_time = datetime.now()
    return step


def parallel_until_all(calls: List[Callable[[], Any]]) -> None:
    threads = [threading.Thread(target=c, name=getattr(c, "__pluto_name__", "branch")) for c in calls]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def parallel_until_one(calls: List[Callable[[], Any]]) -> None:
    done = threading.Event()
    results: List[Any] = []
    lock = threading.Lock()

    def wrap(c):
        def runner():
            try:
                r = c()
            except Exception as e:
                r = e
            with lock:
                results.append(r)
            done.set()
        return runner

    threads = [threading.Thread(target=wrap(c), daemon=True) for c in calls]
    for t in threads:
        t.start()
    done.wait()


def wait_for_event(proc: "Procedure", event_name: str, timeout: Optional[float] = None,
                   timeout_event: Optional[str] = None) -> None:
    deadline = None if timeout is None else time.time() + timeout
    while True:
        evt = proc.events.get(event_name)
        if evt and evt.raised:
            return
        if deadline is not None and time.time() >= deadline:
            if timeout_event is not None:
                proc.raise_event(timeout_event)
                return
            raise PlutoRuntimeError(f"timeout waiting for event {event_name!r}")
        time.sleep(0.01)


def wait_until(predicate: Callable[[], bool], timeout: Optional[float] = None,
               timeout_event: Optional[str] = None, proc: "Optional[Procedure]" = None) -> None:
    deadline = None if timeout is None else time.time() + timeout
    while not predicate():
        if deadline is not None and time.time() >= deadline:
            if timeout_event is not None and proc is not None:
                proc.raise_event(timeout_event)
                return
            raise PlutoRuntimeError("timeout in wait until")
        time.sleep(0.01)


def inform_user(*parts: Any) -> None:
    print("[INFORM]", " ".join(str(p) for p in parts))


def pluto_log(*parts: Any) -> None:
    log.info("[LOG] %s", " ".join(str(p) for p in parts))


@dataclass
class Procedure:
    name: str = "procedure"
    execution_status: str = "not initiated"
    confirmation_status: str = "not available"
    events: Dict[str, Event] = field(default_factory=dict)
    variables: Dict[str, Any] = field(default_factory=dict)
    watchdog_handlers: Dict[str, Callable[[], None]] = field(default_factory=dict)
    _activities: Dict[str, ActivityExecution] = field(default_factory=dict)
    # Local snapshots created by `save context refer to <ref> by <local_name>`
    # (PLUTO spec A.3.9.5 / A.3.9.25).
    reporting_data: Dict[str, ReportingData] = field(default_factory=dict)

    def declare_event(self, event: Event) -> Event:
        self.events[event.name] = event
        return event

    def register_watchdog(self, event_name: str, handler: Callable[[], None]) -> None:
        self.watchdog_handlers[event_name] = handler

    def raise_event(self, name: str) -> None:
        if name not in self.events:
            raise PlutoRuntimeError(f"event not declared: {name!r}")
        self.events[name].raise_()
        handler = self.watchdog_handlers.get(name)
        if handler is not None:
            log.info("watchdog: handling %s", name)
            handler()

    def register_activity(self, activity_name: str) -> ActivityExecution:
        """Register and track an activity execution."""
        act = ActivityExecution(activity_name)
        self._activities[activity_name] = act
        return act

    def get_property(self, ref: str, property_name: str) -> Any:
        """Read a property of an object reference.

        Resolution order (simplified A.3.9.8 precedence):
          1. tracked activity / step instance on this procedure;
          2. local reporting-data snapshot;
          3. global reporting-data registry (leading-name fallback).

        For `execution_status`, an unknown reference returns
        "not_initiated" — a step that hasn't run yet is a valid PLUTO
        state, not an error.
        """
        if ref in self._activities:
            return self._activities[ref].get_property(property_name)
        if ref in self.reporting_data:
            return self.reporting_data[ref].get_property(property_name)
        rd = resolve_reporting_data(ref)
        if rd is not None:
            return rd.get_property(property_name)
        if property_name == "execution_status":
            return "not_initiated"
        raise PlutoRuntimeError(f"unknown reference: {ref!r}")

    def resolve_ref(self, name: str, default: Any = None) -> Any:
        """Resolve a qualified name used as a value in an expression.

        Looks up local variables, then local reporting-data snapshots,
        then the global registry. Returns `default` (or the name itself)
        if nothing matches.
        """
        if name in self.variables:
            return self.variables[name]
        if name in self.reporting_data:
            return self.reporting_data[name].current_value()
        rd = resolve_reporting_data(name)
        if rd is not None:
            return rd.current_value()
        return default if default is not None else name

    def save_context(self, entries: List[tuple]) -> None:
        """`save context refer to <ref> by <local_name> (, to <ref> by <name>)*`

        Snapshots one or more reporting data so subsequent reads see
        consistent values from the same point in time (spec A.3.9.5).
        If a referenced parameter is not registered, an "invalid"
        snapshot with value=None is stored so the procedure can still
        query validity_status without erroring out.
        """
        for ref, local in entries:
            src = resolve_reporting_data(ref)
            if src is None:
                snap = ReportingData(
                    name=local, value=None,
                    validity_status="not available",
                    sampling_time=datetime.now(),
                )
            else:
                snap = ReportingData(
                    name=local,
                    value=src.value,
                    engineering_value=src.engineering_value,
                    validity_status=src.validity_status,
                    sampling_time=src.sampling_time or datetime.now(),
                )
            self.reporting_data[local] = snap

    def start(self) -> None:
        self.execution_status = "executing"
        log.info("procedure start: %s", self.name)

    def finish(self) -> None:
        self.execution_status = "completed"
        self.confirmation_status = "confirmed"
        log.info("procedure complete: %s", self.name)
