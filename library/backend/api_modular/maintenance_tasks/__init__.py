"""
Maintenance task registry.

Auto-discovers and registers all task handler modules in this package.
Import this module to get the singleton `registry` instance.
"""

import importlib
import pkgutil
import re
from pathlib import Path

from .base import (
    ExecutionResult,
    MaintenanceRegistry,
    MaintenanceTask,
    ValidationResult,
)

# Singleton registry
registry = MaintenanceRegistry()

# Allow-list: only module names matching this pattern from THIS package dir may
# be imported. Prevents importlib from loading arbitrary module names even if
# pkgutil.iter_modules ever yielded unexpected entries (defense in depth).
_ALLOWED_TASK_MODULES = frozenset(
    {
        "auth_cleanup",
        "cleanup",
        "db_backup",
        "db_integrity",
        "db_vacuum",
        "hash_verify",
        "library_scan",
    }
)
_MODNAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

# Auto-import modules in this package so their @registry.register decorators fire
_pkg_dir = Path(__file__).parent
for _importer, _modname, _ispkg in pkgutil.iter_modules([str(_pkg_dir)]):
    if _modname == "base":
        continue
    if _modname not in _ALLOWED_TASK_MODULES or not _MODNAME_RE.match(_modname):
        raise ValueError(
            f"Refusing to import unrecognized maintenance task module: {_modname!r}"
        )
    # nosemgrep: python.lang.security.audit.non-literal-import.non-literal-import  # Reason: _modname is validated against a static allow-list (_ALLOWED_TASK_MODULES frozenset) AND a strict regex (_MODNAME_RE = ^[a-z][a-z0-9_]{0,63}$) before reaching this line; it is discovered via pkgutil.iter_modules of this package dir only, never user input
    importlib.import_module(f".{_modname}", __package__)

__all__ = [
    "registry",
    "MaintenanceTask",
    "MaintenanceRegistry",
    "ValidationResult",
    "ExecutionResult",
]
