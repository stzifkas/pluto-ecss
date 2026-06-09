"""asyncio-flavoured runtime for transpiled PLUTO procedures.

Symmetric to pluto_ecss.runtime, but:
  - `initiate_and_confirm`, `parallel_until_all/one`, `wait_for_event`,
    `wait_until`, and `Procedure.raise_event` are coroutines.
  - Events wrap `asyncio.Event` so waits are non-busy.
  - Sync activity handlers are dispatched via `asyncio.to_thread` so
    blocking telecommand stacks integrate without poisoning the loop.

Transpiled output with `--runtime async` imports from this module.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

log = logging.getLogger("pluto-ecss")


# Time constants — kept self-contained (the --no-runtime inliner ships this
# module verbatim), so they mirror the threaded runtime rather than import it.
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


async def wait_for_duration(duration: Any) -> None:
    """Asynchronously block for a relative time (a `timedelta`) or seconds."""
    secs = duration.total_seconds() if isinstance(duration, timedelta) else float(duration)
    if secs > 0:
        await asyncio.sleep(secs)


class PlutoRuntimeError(RuntimeError):
    pass


class PlutoAborted(Exception):
    """`abort` continuation action — propagates out of the procedure body."""


class PlutoTerminated(Exception):
    """`terminate` continuation action — jumps to the confirmation body."""


CallableOrCoro = Union[Callable[[], Any], Awaitable[Any]]


@dataclass
class Event:
    name: str
    description: Optional[str] = None
    _event: asyncio.Event = field(default_factory=asyncio.Event)

    def raise_(self) -> None:
        self._event.set()
        log.info("event raised: %s", self.name)

    @property
    def raised(self) -> bool:
        return self._event.is_set()


@dataclass
class ReportingData:
    """Telemetry parameter / reporting data (PLUTO spec A.3.9.8)."""
    name: str
    value: Any = None
    engineering_value: Any = None
    validity_status: str = "invalid"
    sampling_time: Optional[datetime] = None

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
        return self.engineering_value if self.engineering_value is not None else self.value


_reporting_data: Dict[str, "ReportingData"] = {}


def register_reporting_data(rd: ReportingData) -> ReportingData:
    _reporting_data[rd.name] = rd
    return rd


def resolve_reporting_data(ref: str) -> Optional[ReportingData]:
    if ref in _reporting_data:
        return _reporting_data[ref]
    head = ref.split(" of ")[0].strip()
    if head in _reporting_data:
        return _reporting_data[head]
    return None


@dataclass
class SystemElement:
    name: str
    kind: str = "part"
    description: str = ""


@dataclass
class Activity:
    name: str
    target: str
    handler: Callable[..., Any]
    criticality: str = "routine"

    async def invoke(self, **arguments: Any) -> Any:
        log.info("activity: %s on %s", self.name, self.target)
        if inspect.iscoroutinefunction(self.handler):
            try:
                return await self.handler(self, **arguments)
            except TypeError:
                return await self.handler(self)
        try:
            return await asyncio.to_thread(self.handler, self, **arguments)
        except TypeError:
            return await asyncio.to_thread(self.handler, self)


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


_activities: Dict[tuple, Activity] = {}


def register_activity(act: Activity) -> Activity:
    _activities[(act.name, act.target)] = act
    return act


def resolve_activity(name: str, target: str) -> Activity:
    key = (name, target)
    if key in _activities:
        return _activities[key]
    head = target.split(" of ")[0].strip()
    if (name, head) in _activities:
        return _activities[(name, head)]
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


def switch_on(target: str, *, arguments: Optional[Dict[str, Any]] = None) -> Callable[[], Awaitable[Any]]:
    args = arguments or {}
    async def _call():
        try:
            act = resolve_activity("Switch on", target)
        except PlutoRuntimeError:
            act = Activity("Switch on", target, _default_switch_on)
        return await act.invoke(**args)
    _call.__pluto_name__ = f"Switch on {target}"
    _call.arguments = args
    return _call


def switch_off(target: str, *, arguments: Optional[Dict[str, Any]] = None) -> Callable[[], Awaitable[Any]]:
    args = arguments or {}
    async def _call():
        try:
            act = resolve_activity("Switch off", target)
        except PlutoRuntimeError:
            act = Activity("Switch off", target, _default_switch_off)
        return await act.invoke(**args)
    _call.__pluto_name__ = f"Switch off {target}"
    _call.arguments = args
    return _call


def initiate(proc: "Procedure", call: Callable[[], Awaitable[Any]],
             instance_name: Optional[str] = None) -> asyncio.Task:
    """Fire-and-forget: schedule the call but do not await it here.

    When `instance_name` is given (PLUTO `refer by`), the activity is
    registered under that name so `<property> of MY_INSTANCE`
    references resolve to its execution state (spec A.3.9.27 / A.3.9.8).
    """
    activity_name = instance_name or getattr(call, "__pluto_name__", "activity")
    act_exec = proc.register_activity(activity_name)

    async def wrapper():
        act_exec.start_time = datetime.now()
        act_exec.execution_status = "executing"
        try:
            coro = call() if inspect.iscoroutinefunction(call) else asyncio.to_thread(call)
            result = await coro
            act_exec.confirmation_status = result
            act_exec.execution_status = "success"
        except Exception as e:
            act_exec.execution_status = "failure"
            act_exec.confirmation_status = str(e)
            raise
        finally:
            act_exec.completion_time = datetime.now()

    return asyncio.create_task(wrapper())


async def initiate_and_confirm(proc: "Procedure", call: CallableOrCoro,
                               *, instance_name: Optional[str] = None) -> Any:
    """Initiate-and-confirm with optional `refer by` activity-statement name."""
    activity_name = (
        instance_name
        or (getattr(call, "__pluto_name__", "activity") if callable(call) else "activity")
    )
    act_exec = proc.register_activity(activity_name)

    act_exec.start_time = datetime.now()
    act_exec.execution_status = "executing"
    try:
        if inspect.iscoroutinefunction(call):
            result = await call()
        elif inspect.iscoroutine(call):
            result = await call
        else:
            result = await asyncio.to_thread(call)
        act_exec.confirmation_status = result
        act_exec.execution_status = "success"
        return result
    except Exception as e:
        act_exec.execution_status = "failure"
        act_exec.confirmation_status = str(e)
        raise
    finally:
        act_exec.completion_time = datetime.now()


@dataclass
class Step:
    name: str
    body: Callable[[], Awaitable[None]]
    status: str = "not initiated"

    async def run(self) -> None:
        self.status = "executing"
        log.info("step start: %s", self.name)
        try:
            await self.body()
            self.status = "completed"
            log.info("step complete: %s", self.name)
        except Exception as e:
            self.status = "failed"
            log.error("step failed: %s: %s", self.name, e)
            raise


async def initiate_and_confirm_step(proc: "Procedure", name: str,
                                    body: Callable[[], Awaitable[None]]) -> Step:
    """Run a named step. The step is registered with the Procedure so its
    execution properties can be queried via PLUTO `<property> of <step_name>`
    references."""
    act_exec = proc.register_activity(name)
    step = Step(name, body)
    act_exec.start_time = datetime.now()
    act_exec.execution_status = "executing"
    try:
        await step.run()
        act_exec.execution_status = "success"
        act_exec.confirmation_status = "success"
    except Exception as e:
        act_exec.execution_status = "failure"
        act_exec.confirmation_status = str(e)
        raise
    finally:
        act_exec.completion_time = datetime.now()
    return step


async def parallel_until_all(calls: List[Callable[[], Awaitable[Any]]]) -> None:
    coros = [c() if inspect.iscoroutinefunction(c) else asyncio.to_thread(c) for c in calls]
    await asyncio.gather(*coros)


async def parallel_until_one(calls: List[Callable[[], Awaitable[Any]]]) -> None:
    coros = [
        asyncio.ensure_future(c() if inspect.iscoroutinefunction(c) else asyncio.to_thread(c))
        for c in calls
    ]
    done, pending = await asyncio.wait(coros, return_when=asyncio.FIRST_COMPLETED)
    for p in pending:
        p.cancel()
    # surface exceptions from the completed ones
    for task in done:
        exc = task.exception()
        if exc is not None:
            raise exc


async def wait_for_event(proc: "Procedure", event_name: str, timeout: Optional[float] = None,
                         timeout_event: Optional[str] = None) -> None:
    evt = proc.events.get(event_name)
    if evt is None:
        raise PlutoRuntimeError(f"event not declared: {event_name!r}")
    if timeout is None:
        await evt._event.wait()
        return
    try:
        await asyncio.wait_for(evt._event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        if timeout_event is not None:
            await proc.raise_event(timeout_event)
            return
        raise PlutoRuntimeError(f"timeout waiting for event {event_name!r}") from None


async def wait_until(predicate: Callable[[], bool], timeout: Optional[float] = None,
                     timeout_event: Optional[str] = None, proc: "Optional[Procedure]" = None) -> None:
    async def _poll():
        while not predicate():
            await asyncio.sleep(0.01)
    if timeout is None:
        await _poll()
        return
    try:
        await asyncio.wait_for(_poll(), timeout=timeout)
    except asyncio.TimeoutError:
        if timeout_event is not None and proc is not None:
            await proc.raise_event(timeout_event)
            return
        raise PlutoRuntimeError("timeout in wait until") from None


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
    watchdog_handlers: Dict[str, Callable[[], Awaitable[None]]] = field(default_factory=dict)
    _activities: Dict[str, ActivityExecution] = field(default_factory=dict)
    reporting_data: Dict[str, "ReportingData"] = field(default_factory=dict)

    def declare_event(self, event: Event) -> Event:
        self.events[event.name] = event
        return event

    def register_watchdog(self, event_name: str, handler: Callable[[], Awaitable[None]]) -> None:
        self.watchdog_handlers[event_name] = handler

    async def raise_event(self, name: str) -> None:
        if name not in self.events:
            raise PlutoRuntimeError(f"event not declared: {name!r}")
        self.events[name].raise_()
        handler = self.watchdog_handlers.get(name)
        if handler is not None:
            log.info("watchdog: handling %s", name)
            if inspect.iscoroutinefunction(handler):
                await handler()
            else:
                handler()

    def register_activity(self, activity_name: str) -> ActivityExecution:
        act = ActivityExecution(activity_name)
        self._activities[activity_name] = act
        return act

    def get_property(self, ref: str, property_name: str) -> Any:
        """Read a property of an object reference: activity instance,
        local reporting-data snapshot, or the global registry."""
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
        if name in self.variables:
            return self.variables[name]
        if name in self.reporting_data:
            return self.reporting_data[name].current_value()
        rd = resolve_reporting_data(name)
        if rd is not None:
            return rd.current_value()
        return default if default is not None else name

    def save_context(self, entries: List[tuple]) -> None:
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
