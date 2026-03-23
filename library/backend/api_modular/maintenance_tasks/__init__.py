"""
Maintenance task registry.

Auto-discovers and registers all task handler modules in this package.
Import this module to get the singleton `registry` instance.
"""

import importlib
import pkgutil
from pathlib import Path

from .base import (
    ExecutionResult,
    MaintenanceRegistry,
    MaintenanceTask,
    ValidationResult,
)

# Singleton registry
registry = MaintenanceRegistry()

# Auto-import all modules in this package so their @registry.register decorators fire
_pkg_dir = Path(__file__).parent
for _importer, _modname, _ispkg in pkgutil.iter_modules([str(_pkg_dir)]):
    if _modname != "base":
        importlib.import_module(f".{_modname}", __package__)

__all__ = [
    "registry",
    "MaintenanceTask",
    "MaintenanceRegistry",
    "ValidationResult",
    "ExecutionResult",
]
