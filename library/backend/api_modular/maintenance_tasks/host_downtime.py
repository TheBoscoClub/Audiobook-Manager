"""Host-downtime window: announces that the host will be unavailable, runs nothing.

Every maintenance window names a task and the scheduler runs it at the start
time. A notice that the machine itself is going down (a restart, hardware
work) must not set off any of the library's own jobs, so this task is a
deliberate no-op: the window exists for its lead-time announcement only.
"""

from . import registry
from .base import ExecutionResult, MaintenanceTask, ValidationResult


@registry.register
class HostDowntimeTask(MaintenanceTask):
    name = "host_downtime"
    display_name = "Host Downtime Notice"
    description = "Announce that the host will be unavailable; runs no maintenance job"

    def validate(self, params: dict) -> ValidationResult:
        return ValidationResult(ok=True)

    def execute(self, params: dict, progress_callback=None) -> ExecutionResult:
        return ExecutionResult(success=True, message="Host downtime window reached")

    def estimate_duration(self):
        return 0
