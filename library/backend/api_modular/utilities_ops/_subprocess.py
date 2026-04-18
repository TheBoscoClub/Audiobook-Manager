"""
Non-blocking subprocess runner for gevent-compatible progress streaming.

Provides a single mechanism for running subprocesses with real-time stdout
parsing that yields to the gevent event loop. All utilities_ops modules use
this instead of implementing their own select()/read() loops.

Why this exists:
  gunicorn runs with `-k gevent -w 1`, so threading.Thread becomes a greenlet.
  Blocking I/O (readline, read(1)) in a greenlet starves the event loop,
  preventing the API from serving progress poll requests. This module uses
  select.select() with a 0.5s timeout to yield between reads.
"""

import select
import subprocess  # nosec B404 — import subprocess — subprocess usage is intentional; all calls use hardcoded system tool names
import time
from typing import Any


def _make_result(success, output_lines, stderr="", returncode=None, timed_out=False, error=None):
    """Build a standard result dict."""
    output = "\n".join(output_lines)
    return {
        "success": success,
        "output": output[-2000:] if len(output) > 2000 else output,
        "stderr": stderr,
        "returncode": returncode,
        "timed_out": timed_out,
        "error": error,
    }


def _process_chunk(chunk, buffer, output_lines, line_callback):
    """Process a chunk of stdout data, splitting on line endings.

    Returns the remaining buffer content after processing complete lines.
    """
    for ch in chunk:
        if ch in ("\r", "\n"):
            if buffer:
                stripped = buffer.strip()
                if stripped:
                    output_lines.append(stripped)
                line_callback(buffer)
                buffer = ""
        else:
            buffer += ch
    return buffer


def _flush_buffer(buffer, output_lines, line_callback):
    """Flush any remaining buffer content as a final line."""
    if buffer:
        stripped = buffer.strip()
        if stripped:
            output_lines.append(stripped)
        line_callback(buffer)


def _read_stdout_loop(process, fd, timeout_secs, operation_name, line_callback, output_lines):
    """Main read loop for subprocess stdout.

    Returns a result dict if terminated early (timeout), or None on normal EOF.
    """
    start_time = time.monotonic()
    buffer = ""

    while True:
        elapsed = time.monotonic() - start_time
        if elapsed > timeout_secs:
            process.kill()
            return _make_result(
                False,
                output_lines,
                timed_out=True,
                error=f"{operation_name} timed out after {int(elapsed // 60)} minutes",
            )

        # Use select() to yield to the gevent event loop every 0.5s
        if fd is not None:
            ready, _, _ = select.select([fd], [], [], 0.5)
            if not ready:
                continue

        chunk = process.stdout.read(4096)
        if not chunk:  # EOF
            _flush_buffer(buffer, output_lines, line_callback)
            return None

        buffer = _process_chunk(chunk, buffer, output_lines, line_callback)


def run_with_progress(cmd, *, line_callback, timeout_secs, operation_name="Operation", env=None):
    """Run a subprocess with non-blocking stdout streaming.

    Args:
        cmd: Command list for subprocess.Popen.
        line_callback: Called with each complete line (stripped). The callback
            should parse progress info and update the tracker.
        timeout_secs: Overall wall-clock timeout in seconds.
        operation_name: Human-readable name for timeout error messages.
        env: Optional environment dict for the subprocess.

    Returns:
        A Result dict with:
            - success: bool
            - output: str (all stdout lines joined, truncated to 2000 chars)
            - stderr: str
            - returncode: int or None
            - timed_out: bool
            - error: str or None
    """
    popen_kwargs: dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "bufsize": 1,
    }
    if env is not None:
        popen_kwargs["env"] = env

    process = subprocess.Popen(cmd, **popen_kwargs)  # noqa: S603,S607 — system-installed tool; args are config-controlled or hardcoded constants, not user input  # nosec B603 — subprocess call — cmd is a hardcoded system tool invocation with internal/config args; no user-controlled input
    # stdout/stderr are guaranteed non-None because popen_kwargs sets them to PIPE.
    if process.stdout is None or process.stderr is None:
        raise RuntimeError("Popen stdout/stderr unexpectedly None despite PIPE configuration")
    output_lines: list[str] = []

    try:
        fd = process.stdout.fileno()
    except AttributeError, OSError:
        fd = None

    try:
        early_result = _read_stdout_loop(
            process, fd, timeout_secs, operation_name, line_callback, output_lines
        )
        if early_result is not None:
            return early_result

        process.wait(timeout=60)
        stderr = process.stderr.read()

        return _make_result(
            success=process.returncode == 0,
            output_lines=output_lines,
            stderr=stderr,
            returncode=process.returncode,
            error=(stderr or f"{operation_name} failed") if process.returncode != 0 else None,
        )

    except subprocess.TimeoutExpired:
        process.kill()
        return _make_result(
            False,
            output_lines,
            timed_out=True,
            error=f"{operation_name} process did not exit cleanly",
        )
