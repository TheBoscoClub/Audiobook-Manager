"""Library scan task -- triggers a rescan for new/changed audiobook files."""

import logging
import subprocess  # nosec B404 — import subprocess — subprocess usage is intentional; all calls use hardcoded system tool names

from . import registry
from .base import ExecutionResult, MaintenanceTask, ValidationResult

logger = logging.getLogger(__name__)


@registry.register
class LibraryScanTask(MaintenanceTask):
    name = "library_scan"
    display_name = "Library Rescan"
    description = "Scan for new or changed audiobook files"

    def validate(self, params: dict) -> ValidationResult:
        return ValidationResult(ok=True)

    def execute(self, params: dict, progress_callback=None) -> ExecutionResult:
        try:
            if progress_callback:
                progress_callback(0.1, "Starting library scan...")

            # Invoke the existing scanner via the API utility endpoint
            # The scanner runs in-process via the utilities blueprint
            result = subprocess.run(  # nosec B607,B603 — partial path — system tools (ffmpeg, systemctl, etc.) must be on PATH for cross-distro compatibility
                ["curl", "-s", "-X", "POST", "http://127.0.0.1:5001/api/admin/scan"],  # noqa: S603,S607 — curl target is 127.0.0.1 localhost API; no user-controlled input in URL or args
                capture_output=True,
                text=True,
                timeout=600,
            )

            if progress_callback:
                progress_callback(1.0, "Complete")

            if result.returncode == 0:
                return ExecutionResult(
                    success=True,
                    message="Library scan completed",
                    data={"output": result.stdout[:500]},
                )
            return ExecutionResult(success=False, message=f"Scan failed: {result.stderr[:200]}")
        except subprocess.TimeoutExpired:
            return ExecutionResult(success=False, message="Scan timed out after 600s")
        except Exception as e:
            return ExecutionResult(success=False, message=str(e))

    def estimate_duration(self):
        return 300
