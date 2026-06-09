"""pluto-ecss: a PLUTO (ECSS-E-ST-70-32C) to Python transpiler and runtime."""

import importlib.metadata

try:
    __version__ = importlib.metadata.version("pluto-ecss")
except importlib.metadata.PackageNotFoundError:
    __version__ = "unknown"

from pluto_ecss.runtime import (
    Procedure,
    Event,
    SystemElement,
    Activity,
    ReportingData,
    register_system,
    register_activity,
    register_reporting_data,
    resolve_system,
    resolve_activity,
    resolve_reporting_data,
)

__all__ = [
    "__version__",
    "Procedure",
    "Event",
    "SystemElement",
    "Activity",
    "ReportingData",
    "register_system",
    "register_activity",
    "register_reporting_data",
    "resolve_system",
    "resolve_activity",
    "resolve_reporting_data",
]
