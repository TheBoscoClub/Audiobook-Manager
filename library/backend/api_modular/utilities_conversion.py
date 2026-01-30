"""
Conversion monitoring for audiobook format conversion.
Provides real-time status of FFmpeg conversion processes.
"""

import re
import subprocess
import sys
from pathlib import Path

from flask import Blueprint, jsonify

from .auth import auth_if_enabled
from .core import FlaskResponse

utilities_conversion_bp = Blueprint("utilities_conversion", __name__)


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
    except Exception:
        pass  # Non-critical: process listing is best-effort

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
    except Exception:
        pass  # Non-critical: FFmpeg PID detection is best-effort
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
        df_result = subprocess.run(["df", "-h", "/tmp"], capture_output=True, text=True)
        if df_result.returncode == 0:
            lines = df_result.stdout.strip().split("\n")
            if len(lines) > 1:
                parts = lines[1].split()
                if len(parts) >= 5:
                    tmpfs_usage = parts[4]  # e.g., "15%"
                    tmpfs_avail = parts[3]  # e.g., "7.5G"
    except Exception:
        pass  # Non-critical stats; return None values

    return {
        "load_avg": load_avg,
        "tmpfs_usage": tmpfs_usage,
        "tmpfs_avail": tmpfs_avail,
    }


def init_conversion_routes(project_root: str | Path):
    """Initialize conversion monitoring routes with project root."""

    @utilities_conversion_bp.route("/api/conversion/status", methods=["GET"])
    @auth_if_enabled
    def get_conversion_status() -> FlaskResponse:
        """
        Get current audiobook conversion status.
        Returns file counts, active processes, and statistics for the monitor.
        """
        # Import config paths
        sys.path.insert(0, str(project_root))
        from config import (AUDIOBOOKS_LIBRARY, AUDIOBOOKS_SOURCES,
                            AUDIOBOOKS_STAGING)

        staging_dir = AUDIOBOOKS_STAGING

        try:
            # Count source AAXC files (recursive to handle nested download batches)
            sources_dir = AUDIOBOOKS_SOURCES
            aaxc_count = (
                len(list(sources_dir.rglob("*.aaxc"))) if sources_dir.exists() else 0
            )

            # Count staged opus files (excluding covers) - recursively search subdirs
            staged_count = 0
            if staging_dir.exists():
                for f in staging_dir.rglob("*.opus"):
                    if not f.name.endswith(".cover.opus"):
                        staged_count += 1

            # Count library opus files (excluding covers)
            library_count = 0
            if AUDIOBOOKS_LIBRARY.exists():
                for f in AUDIOBOOKS_LIBRARY.rglob("*.opus"):
                    if not f.name.endswith(".cover.opus"):
                        library_count += 1

            # Total converted
            total_converted = library_count + staged_count

            # Remaining calculation - prefer queue file for accurate count
            # The queue uses smart title matching to avoid false positives
            queue_file = AUDIOBOOKS_SOURCES.parent / ".index" / "queue.txt"
            if queue_file.exists():
                with open(queue_file) as qf:
                    queue_lines = [line.strip() for line in qf if line.strip()]
                    remaining = len(queue_lines)
            else:
                # Fallback to simple arithmetic
                remaining = max(0, aaxc_count - total_converted)

            # Get active ffmpeg opus conversion processes with per-job stats
            ffmpeg_pids, pid_cmdlines = get_ffmpeg_processes()
            ffmpeg_count = len(ffmpeg_pids)
            ffmpeg_nice = get_ffmpeg_nice_value()

            active_conversions = []  # Legacy: just filenames for backward compat
            conversion_jobs = []  # Detailed per-job info
            total_read_bytes = 0
            total_write_bytes = 0

            # Process each FFmpeg job
            for pid in ffmpeg_pids:
                cmdline = pid_cmdlines.get(pid, "")
                job_info = parse_conversion_job(pid, cmdline)

                if job_info:
                    active_conversions.append(job_info["display_name"])
                    conversion_jobs.append(job_info)
                    total_read_bytes += job_info["read_bytes"]
                    total_write_bytes += job_info["write_bytes"]

            # Get system stats
            system_stats = get_system_stats()

            # Calculate completion percentage
            percent = int(total_converted * 100 / aaxc_count) if aaxc_count > 0 else 0

            # Effective remaining: if FFmpeg is actively converting, those files
            # haven't been written to staging yet, so count them as remaining
            effective_remaining = max(remaining, ffmpeg_count)

            # Only complete when no remaining AND no active conversions
            is_complete = remaining == 0 and ffmpeg_count == 0 and aaxc_count > 0

            return jsonify(
                {
                    "success": True,
                    "status": {
                        "source_count": aaxc_count,
                        "library_count": library_count,
                        "staged_count": staged_count,
                        "total_converted": total_converted,
                        "queue_count": effective_remaining,  # Use effective for consistency
                        "remaining": effective_remaining,
                        "percent_complete": percent,
                        "is_complete": is_complete,
                    },
                    "processes": {
                        "ffmpeg_count": ffmpeg_count,
                        "ffmpeg_nice": ffmpeg_nice,
                        "active_conversions": active_conversions[
                            :12
                        ],  # Limit to 12 (legacy)
                        "conversion_jobs": conversion_jobs[
                            :12
                        ],  # Detailed per-job info
                        "io_read_bytes": total_read_bytes,
                        "io_write_bytes": total_write_bytes,
                    },
                    "system": system_stats,
                }
            )

        except Exception:
            import logging

            logging.exception("Error getting conversion status")
            return (
                jsonify({"success": False, "error": "Failed to get conversion status"}),
                500,
            )

    return utilities_conversion_bp
