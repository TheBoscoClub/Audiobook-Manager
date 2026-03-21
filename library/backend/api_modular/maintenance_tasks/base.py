"""
Base class and data types for maintenance task handlers.

Each handler implements validate() and execute() and is registered
via the @registry.register decorator in its module.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class ValidationResult:
    """Result of a task validation check."""
    ok: bool
    message: str = ""


@dataclass
class ExecutionResult:
    """Result of a task execution."""
    success: bool
    message: str = ""
    data: dict = field(default_factory=dict)


class MaintenanceTask(ABC):
    """Abstract base for maintenance task handlers."""

    name: str = ""
    display_name: str = ""
    description: str = ""

    @abstractmethod
    def validate(self, params: dict) -> ValidationResult:
        """Pre-flight checks. Called at creation and before execution."""
        ...

    @abstractmethod
    def execute(
        self, params: dict, progress_callback: Optional[Callable] = None
    ) -> ExecutionResult:
        """Perform the maintenance task."""
        ...

    def estimate_duration(self) -> Optional[int]:
        """Estimated duration in seconds. Override in subclass if known."""
        return None

    def to_dict(self) -> dict:
        """Serialize for API response."""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "estimated_duration": self.estimate_duration(),
        }


class MaintenanceRegistry:
    """Registry of maintenance task handlers."""

    def __init__(self):
        self._tasks: dict[str, MaintenanceTask] = {}

    def register(self, cls):
        """Decorator to register a task handler class."""
        instance = cls()
        if not instance.name:
            raise ValueError(f"{cls.__name__} must define a 'name' attribute")
        self._tasks[instance.name] = instance
        return cls

    def get(self, name: str) -> Optional[MaintenanceTask]:
        """Get a task handler by name."""
        return self._tasks.get(name)

    def list_all(self) -> list[dict]:
        """List all registered tasks as dicts."""
        return [t.to_dict() for t in self._tasks.values()]
