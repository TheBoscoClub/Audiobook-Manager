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
import subprocess
import time


def run_with_progress(
    cmd,
    *,
    line_callback,
    timeout_secs,
    operation_name="Operation",
    env=None,
):
    """Run a subprocess with non-blocking stdout streaming.

    Args:
        cmd: Command list for subprocess.Popen.
        line_callback: Called with each complete line (stripped). The callback
            should parse progress info and update the tracker.
        timeout_secs: Overall wall-clock timeout in seconds.
        operation_name: Human-readable name for timeout error messages.
        env: Optional environment dict for the subprocess.

    Returns:
        A Result namedtuple-like dict with:
            - success: bool
            - output: str (all stdout lines joined, truncated to 2000 chars)
            - stderr: str
            - returncode: int or None
            - timed_out: bool
            - error: str or None
    """
    start_time = time.monotonic()

    popen_kwargs = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "bufsize": 1,
    }
    if env is not None:
        popen_kwargs["env"] = env

    process = subprocess.Popen(cmd, **popen_kwargs)

    output_lines = []
    buffer = ""

    try:
        fd = process.stdout.fileno()
    except (AttributeError, OSError):
        fd = None

    try:
        while True:
            elapsed = time.monotonic() - start_time
            if elapsed > timeout_secs:
                process.kill()
                return {
                    "success": False,
                    "output": "\n".join(output_lines),
                    "stderr": "",
                    "returncode": None,
                    "timed_out": True,
                    "error": (
                        f"{operation_name} timed out after "
                        f"{int(elapsed // 60)} minutes"
                    ),
                }

            # Use select() to yield to the gevent event loop every 0.5s
            if fd is not None:
                ready, _, _ = select.select([fd], [], [], 0.5)
                if not ready:
                    continue

            chunk = process.stdout.read(4096)
            if not chunk:  # EOF
                if buffer:
                    buffer_stripped = buffer.strip()
                    if buffer_stripped:
                        output_lines.append(buffer_stripped)
                    line_callback(buffer)
                break

            for ch in chunk:
                if ch in ("\r", "\n"):
                    if buffer:
                        buffer_stripped = buffer.strip()
                        if buffer_stripped:
                            output_lines.append(buffer_stripped)
                        line_callback(buffer)
                        buffer = ""
                else:
                    buffer += ch

        process.wait(timeout=60)
        stderr = process.stderr.read()
        output = "\n".join(output_lines)

        return {
            "success": process.returncode == 0,
            "output": output[-2000:] if len(output) > 2000 else output,
            "stderr": stderr,
            "returncode": process.returncode,
            "timed_out": False,
            "error": stderr or f"{operation_name} failed" if process.returncode != 0 else None,
        }

    except subprocess.TimeoutExpired:
        process.kill()
        return {
            "success": False,
            "output": "\n".join(output_lines),
            "stderr": "",
            "returncode": None,
            "timed_out": True,
            "error": f"{operation_name} process did not exit cleanly",
        }
