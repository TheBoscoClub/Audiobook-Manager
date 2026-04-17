"""
Conversion monitoring for audiobook format conversion.
Provides real-time status of FFmpeg conversion processes.
"""

import logging
import re
import subprocess
import sys
from pathlib import Path

from flask import Blueprint, jsonify

from .auth import auth_if_enabled
from .core import FlaskResponse

utilities_conversion_bp = Blueprint("utilities_conversion", __name__)
logger = logging.getLogger(__name__)


def get_ffmpeg_processes() -> tuple[list[int], dict[int, str]]:
    """
    Get list of FFmpeg opus conversion PIDs and their command lines.

    Returns:
        Tuple of (list of PIDs, dict mapping PID to command line)
    """
    pids = []
    cmdlines = {}

    try:
        ps_aux = subprocess.run(["ps", "aux"], capture_output=True, text=True)

        for line in ps_aux.stdout.split("\n"):
            if "ffmpeg" in line and "libopus" in line:
                parts = line.split(None, 10)  # Split into at most 11 parts
                if len(parts) >= 11:
                    try:
                        pid = int(parts[1])
                        pids.append(pid)
                        cmdlines[pid] = parts[10]  # The command line
                    except (ValueError, IndexError):
                        pass  # Non-critical: skip malformed line
    except Exception as e:
        logger.debug("Process listing failed (non-critical): %s", e)

    return pids, cmdlines


def get_ffmpeg_nice_value() -> str | None:
    """Get the nice value of ffmpeg processes."""
    try:
        ps_ni = subprocess.run(["ps", "-eo", "ni,comm"], capture_output=True, text=True)
        for line in ps_ni.stdout.split("\n"):
            if "ffmpeg" in line:
                parts = line.strip().split()
                if parts:
                    return parts[0]
    except Exception as e:
        logger.debug("FFmpeg PID detection failed (non-critical): %s", e)
    return None


def parse_job_io(pid: int) -> tuple[int, int]:
    """
    Read I/O stats for a process from /proc.

    Uses rchar/wchar instead of read_bytes/write_bytes because the latter
    only counts actual disk I/O, not cached reads. FFmpeg often reads from
    cached files, so read_bytes would show 0 even when actively processing.

    Returns:
        Tuple of (read_bytes, write_bytes) - actually rchar/wchar values
    """
    read_bytes = 0
    write_bytes = 0

    try:
        with open(f"/proc/{pid}/io", "r") as f:
            for line in f:
                if line.startswith("rchar:"):
                    read_bytes = int(line.split(":")[1].strip())
                elif line.startswith("wchar:"):
                    write_bytes = int(line.split(":")[1].strip())
    except (FileNotFoundError, PermissionError):
        pass  # Process may have exited; return zeros

    return read_bytes, write_bytes


def parse_conversion_job(pid: int, cmdline: str) -> dict | None:
    """
    Parse a single FFmpeg conversion job's status.

    Args:
        pid: Process ID
        cmdline: Command line string

    Returns:
        Job info dict or None if parsing failed
    """
    job_filename: str | None = None
    job_percent: int = 0
    job_source_size: int = 0
    job_output_size: int = 0

    # Extract source AAXC file path
    source_match = re.search(r"-i\s+(\S+\.aaxc)", cmdline)
    if source_match:
        source_path = Path(source_match.group(1))
        if source_path.exists():
            job_source_size = source_path.stat().st_size

    # Extract output opus file path (quoted or unquoted)
    output_match = re.search(r'-f ogg "([^"]+)"', cmdline)
    if not output_match:
        output_match = re.search(r"-f ogg (.+\.opus)$", cmdline)
    if output_match:
        output_path = Path(output_match.group(1))
        job_filename = output_path.name
        if output_path.exists():
            job_output_size = output_path.stat().st_size

    # Get per-process I/O stats
    job_read_bytes, job_write_bytes = parse_job_io(pid)

    # Calculate percent complete based on bytes read vs source size
    if job_source_size > 0 and job_read_bytes > 0:
        job_percent = min(99, int(job_read_bytes * 100 / job_source_size))

    if not job_filename:
        return None

    # Truncate filename for display
    display_name = job_filename
    if len(display_name) > 50:
        display_name = display_name[:47] + "..."

    return {
        "pid": pid,
        "filename": job_filename,
        "display_name": display_name,
        "percent": job_percent,
        "read_bytes": job_read_bytes,
        "write_bytes": job_write_bytes,
        "source_size": job_source_size,
        "output_size": job_output_size,
    }


def get_system_stats() -> dict:
    """Get system statistics for conversion monitoring."""
    load_avg = None
    tmpfs_usage = None
    tmpfs_avail = None

    try:
        # CPU load average
        with open("/proc/loadavg") as f:
            load_avg = f.read().strip().split()[0]

        # tmpfs usage
        df_result = subprocess.run(["df", "-h", "/tmp"], capture_output=True, text=True)  # nosec B108 — reading tmpfs stats, not creating temp files
        if df_result.returncode == 0:
            lines = df_result.stdout.strip().split("\n")
            if len(lines) > 1:
                parts = lines[1].split()
                if len(parts) >= 5:
                    tmpfs_usage = parts[4]  # e.g., "15%"
                    tmpfs_avail = parts[3]  # e.g., "7.5G"
    except Exception as e:
        logger.debug("Failed to get tmpfs stats (non-critical): %s", e)

    return {"load_avg": load_avg, "tmpfs_usage": tmpfs_usage, "tmpfs_avail": tmpfs_avail}


def _count_opus_files(directory: Path) -> int:
    """Count opus files in a directory, excluding cover files."""
    if not directory.exists():
        return 0
    count = 0
    for f in directory.rglob("*.opus"):
        if not f.name.endswith(".cover.opus"):
            count += 1
    return count


def _get_remaining_count(sources_dir: Path, aaxc_count: int, total_converted: int) -> int:
    """Get remaining conversion count from queue file or arithmetic fallback."""
    queue_file = sources_dir.parent / ".index" / "queue.txt"
    if queue_file.exists():
        with open(queue_file) as qf:
            return len([line.strip() for line in qf if line.strip()])
    return max(0, aaxc_count - total_converted)


def _collect_job_stats(
    ffmpeg_pids: list[int], pid_cmdlines: dict[int, str]
) -> tuple[list[str], list[dict], int, int]:
    """Collect per-job stats from active FFmpeg processes.

    Returns:
        Tuple of (active_conversions, conversion_jobs, total_read, total_write)
    """
    active_conversions: list[str] = []
    conversion_jobs: list[dict] = []
    total_read = 0
    total_write = 0

    for pid in ffmpeg_pids:
        cmdline = pid_cmdlines.get(pid, "")
        job_info = parse_conversion_job(pid, cmdline)
        if job_info:
            active_conversions.append(job_info["display_name"])
            conversion_jobs.append(job_info)
            total_read += job_info["read_bytes"]
            total_write += job_info["write_bytes"]

    return active_conversions, conversion_jobs, total_read, total_write


def _build_conversion_response(
    sources_dir: Path, staging_dir: Path, library_dir: Path
) -> FlaskResponse:
    """Build the full conversion status response payload."""
    aaxc_count = len(list(sources_dir.rglob("*.aaxc"))) if sources_dir.exists() else 0
    staged_count = _count_opus_files(staging_dir)
    library_count = _count_opus_files(library_dir)
    total_converted = library_count + staged_count

    remaining = _get_remaining_count(sources_dir, aaxc_count, total_converted)

    ffmpeg_pids, pid_cmdlines = get_ffmpeg_processes()
    ffmpeg_count = len(ffmpeg_pids)
    ffmpeg_nice = get_ffmpeg_nice_value()

    active_conversions, conversion_jobs, total_read, total_write = _collect_job_stats(
        ffmpeg_pids, pid_cmdlines
    )

    percent = int(total_converted * 100 / aaxc_count) if aaxc_count > 0 else 0
    effective_remaining = max(remaining, ffmpeg_count)
    is_complete = remaining == 0 and ffmpeg_count == 0 and aaxc_count > 0

    return jsonify(
        {
            "success": True,
            "status": {
                "source_count": aaxc_count,
                "library_count": library_count,
                "staged_count": staged_count,
                "total_converted": total_converted,
                "queue_count": effective_remaining,
                "remaining": effective_remaining,
                "percent_complete": percent,
                "is_complete": is_complete,
            },
            "processes": {
                "ffmpeg_count": ffmpeg_count,
                "ffmpeg_nice": ffmpeg_nice,
                "active_conversions": active_conversions[:12],
                "conversion_jobs": conversion_jobs[:12],
                "io_read_bytes": total_read,
                "io_write_bytes": total_write,
            },
            "system": get_system_stats(),
        }
    )


def init_conversion_routes(project_root: str | Path):
    """Initialize conversion monitoring routes with project root."""
    _root = str(project_root)
    if _root not in sys.path:
        sys.path.insert(0, _root)
    from config import AUDIOBOOKS_LIBRARY, AUDIOBOOKS_SOURCES, AUDIOBOOKS_STAGING

    @utilities_conversion_bp.route("/api/conversion/status", methods=["GET"])
    @auth_if_enabled
    def get_conversion_status() -> FlaskResponse:
        """
        Get current audiobook conversion status.
        Returns file counts, active processes, and statistics for the monitor.
        """
        try:
            return _build_conversion_response(
                AUDIOBOOKS_SOURCES, AUDIOBOOKS_STAGING, AUDIOBOOKS_LIBRARY
            )
        except Exception as e:
            logger.exception("Error getting conversion status: %s", e)
            return (jsonify({"success": False, "error": "Failed to get conversion status"}), 500)

    return utilities_conversion_bp
