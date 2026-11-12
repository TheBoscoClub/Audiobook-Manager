#!/usr/bin/env python3
"""
audiobook-maintenance — maintenance from the command line.

Talks to the API's local door (/api/local/maintenance/*) on loopback with the
token the API wrote into its run directory. Standard library only, so it
starts even when the application's own dependencies are broken.

`status` is built to be trusted by a script that is about to restart the
host, so its exit code fails closed:

    0   nobody connected, or the app is not running at all
    10  someone connected, nobody listening
    20  someone listening (or a browser the app cannot see into)
    30  unknown: the app is running but gave no usable answer, or it started
        less than 90 seconds ago and reports nobody (browsers reconnect late)

Only 0 means "nobody". Every other outcome, including this program crashing,
is non-zero. "Not running" is claimed only on positive evidence: the service
manager reports the API unit inactive or failed AND its port refuses the
connection.

Design: docs/superpowers/specs/2026-11-12-maintenance-cli-design.md
"""

import argparse
import json
import socket
import subprocess  # nosec B404 — fixed argv, no shell; see service_state()
import sys
import urllib.error
import urllib.request
from datetime import datetime

EXIT_NOBODY = 0
EXIT_CONNECTED = 10
EXIT_LISTENING = 20
EXIT_UNKNOWN = 30

HOST = "127.0.0.1"
BASE_PATH = "/api/local/maintenance"
TIMEOUT_SECONDS = 10
DOWN_STATES = frozenset({"inactive", "failed"})
# After the API starts its connection list is empty until browsers reconnect
# (retries at 1, 2 and 4 seconds, then a poll every 30). "Nobody" from a
# process younger than this is not evidence.
SETTLE_SECONDS = 90


class Unknown(Exception):
    """The app could not be asked, or its answer cannot be relied on."""


class Refused(Exception):
    """Nothing is listening on the API port."""


# --------------------------------------------------------------- plumbing


def read_token(token_file):
    try:
        with open(token_file, encoding="ascii") as handle:
            token = handle.read().strip()
    except (OSError, ValueError) as e:
        raise Unknown(f"cannot read the maintenance token ({token_file}): {e}") from e
    if not token:
        raise Unknown(f"the maintenance token file is empty ({token_file})")
    return token


def port_refuses(port):
    """True only when a connection to the API port is actively refused."""
    try:
        with socket.create_connection((HOST, port), timeout=TIMEOUT_SECONDS):
            return False
    except ConnectionRefusedError:
        return True
    except OSError:
        return False


def service_state(unit):
    """The service manager's word for the API unit, or None if it has none."""
    try:
        result = subprocess.run(  # nosec B603 B607 — fixed argv, unit name from our own argument parser default or the operator
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def call(port, token, method, path, body=None):
    """One request to the local door. Returns (status, parsed JSON or None)."""
    data = None
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(  # noqa: S310  # nosec B310 — fixed http://127.0.0.1 URL built here, never from input
        f"http://{HOST}:{port}{BASE_PATH}{path}", data=data, headers=headers, method=method
    )
    # No environment proxies: this request must never leave the host.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    except urllib.error.URLError as e:
        if isinstance(e.reason, ConnectionRefusedError):
            raise Refused() from e
        raise Unknown(f"no answer from the API on port {port}: {e.reason}") from e
    except (OSError, ValueError) as e:
        raise Unknown(f"no answer from the API on port {port}: {e}") from e
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except ValueError:
        parsed = None
    return status, parsed


def _error_text(status, parsed):
    detail = parsed.get("error") if isinstance(parsed, dict) else None
    return f"the API answered {status}" + (f": {detail}" if detail else "")


# ----------------------------------------------------------------- status


def _count(body, key):
    value = body.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise Unknown(f"the API's status answer has no usable '{key}'")
    return value


def read_status(body):
    """Validate a status answer → (uptime, connected, listening, polling, users)."""
    if not isinstance(body, dict):
        raise Unknown("the API's status answer is not an object")
    uptime = _count(body, "uptime_seconds")
    connected = _count(body, "connected")
    listening = _count(body, "listening")
    polling = _count(body, "polling")
    users = body.get("users")
    if not isinstance(users, list) or len(users) != connected:
        raise Unknown("the API's status answer is inconsistent (users vs connected)")
    rows = []
    for user in users:
        if not isinstance(user, dict):
            raise Unknown("the API's status answer has a malformed user entry")
        rows.append((str(user.get("username", "?")), str(user.get("state", "?"))))
    return uptime, connected, listening, polling, rows


def exit_code_for(connected, listening, polling, rows):
    """Fail closed: any state other than browsing/connected counts as listening."""
    quiet = {"browsing", "connected"}
    if listening or polling or any(state not in quiet for _, state in rows):
        return EXIT_LISTENING
    if connected:
        return EXIT_CONNECTED
    return EXIT_NOBODY


def app_is_down(port, unit):
    return port_refuses(port) and service_state(unit) in DOWN_STATES


def cmd_status(args):
    try:
        token = read_token(args.token_file)
        status, body = call(args.port, token, "GET", "/status")
    except Refused:
        if service_state(args.unit) in DOWN_STATES:
            print("app=down connected=0")
            return EXIT_NOBODY
        print(
            f"unknown: port {args.port} refuses connections but {args.unit} is not "
            "reported inactive or failed",
            file=sys.stderr,
        )
        return EXIT_UNKNOWN
    except Unknown as e:
        # A token that cannot be read says nothing about users. The app being
        # verifiably down does.
        if app_is_down(args.port, args.unit):
            print("app=down connected=0")
            return EXIT_NOBODY
        print(f"unknown: {e}", file=sys.stderr)
        return EXIT_UNKNOWN

    if status != 200:
        print(f"unknown: {_error_text(status, body)}", file=sys.stderr)
        return EXIT_UNKNOWN
    try:
        uptime, connected, listening, polling, rows = read_status(body)
    except Unknown as e:
        print(f"unknown: {e}", file=sys.stderr)
        return EXIT_UNKNOWN

    print(f"connected={connected} listening={listening} polling={polling}")
    for username, state in rows:
        print(f"{username} {state}")
    code = exit_code_for(connected, listening, polling, rows)
    if code == EXIT_NOBODY and uptime < SETTLE_SECONDS:
        print(
            f"unknown: the app started {uptime} seconds ago; browsers may not have "
            f"reconnected yet (ask again after {SETTLE_SECONDS} seconds of uptime)",
            file=sys.stderr,
        )
        return EXIT_UNKNOWN
    return code


# ------------------------------------------------------------ other verbs


def _act(args, method, path, body, ok_statuses):
    """Run a write verb. Returns the parsed answer, or None after printing why."""
    try:
        token = read_token(args.token_file)
        status, parsed = call(args.port, token, method, path, body)
    except Refused:
        print(f"error: nothing is listening on port {args.port}", file=sys.stderr)
        return None
    except Unknown as e:
        print(f"error: {e}", file=sys.stderr)
        return None
    if status not in ok_statuses or not isinstance(parsed, dict):
        print(f"error: {_error_text(status, parsed)}", file=sys.stderr)
        return None
    return parsed


def cmd_announce(args):
    answer = _act(args, "POST", "/announce", {"message": args.message}, {201})
    if answer is None:
        return 1
    print(answer["id"])
    return 0


def cmd_clear(args):
    body = {} if args.id is None else {"id": args.id}
    answer = _act(args, "POST", "/clear", body, {200})
    if answer is None:
        return 1
    print(f"cleared={answer.get('cleared')}")
    return 0


def start_with_zone(text):
    """ISO time → ISO time with an offset. No zone given means this host's."""
    parsed = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.isoformat(timespec="seconds")


def cmd_window_add(args):
    try:
        start = start_with_zone(args.start)
    except ValueError:
        print(f"error: --start is not an ISO time: {args.start!r}", file=sys.stderr)
        return 1
    body = {
        "start": start,
        "minutes": args.minutes,
        "lead_hours": args.lead_hours,
        "message": args.message,
    }
    if args.name:
        body["name"] = args.name
    answer = _act(args, "POST", "/window", body, {201})
    if answer is None:
        return 1
    print(answer["id"])
    return 0


def cmd_window_cancel(args):
    answer = _act(args, "POST", "/window/cancel", {"id": args.id}, {200})
    if answer is None:
        return 1
    print(f"cancelled={answer.get('cancelled')}")
    return 0


# ------------------------------------------------------------------- main


def build_parser():
    parser = argparse.ArgumentParser(
        prog="audiobook-maintenance",
        description="Who is connected, announcements, and host-downtime windows.",
        epilog="status exit codes: 0 nobody (or app not running), 10 connected but "
        "nobody listening, 20 someone listening, anything else unknown.",
    )
    parser.add_argument("--port", type=int, required=True, help=argparse.SUPPRESS)
    parser.add_argument("--token-file", required=True, help=argparse.SUPPRESS)
    parser.add_argument("--unit", default="audiobook-api.service", help=argparse.SUPPRESS)
    verbs = parser.add_subparsers(dest="verb", required=True)

    verbs.add_parser("status", help="who is connected and who is listening").set_defaults(
        func=cmd_status
    )

    announce = verbs.add_parser("announce", help="show a notice to every user now")
    announce.add_argument("message")
    announce.set_defaults(func=cmd_announce)

    clear = verbs.add_parser("clear", help="remove notices this command posted")
    clear.add_argument("id", nargs="?", type=int, help="one notice; default: all of them")
    clear.set_defaults(func=cmd_clear)

    window = verbs.add_parser("window", help="host-downtime windows (announced, run nothing)")
    window_verbs = window.add_subparsers(dest="window_verb", required=True)

    add = window_verbs.add_parser("add", help="schedule a host-downtime window")
    add.add_argument("--start", required=True, help="ISO time; no zone means this host's")
    add.add_argument("--minutes", required=True, type=int, help="expected length")
    add.add_argument("--lead-hours", type=int, default=24, help="announce this far ahead")
    add.add_argument("--message", required=True)
    add.add_argument("--name", help="window title (default: Host maintenance)")
    add.set_defaults(func=cmd_window_add)

    cancel = window_verbs.add_parser("cancel", help="cancel a window this command made")
    cancel.add_argument("id", type=int)
    cancel.set_defaults(func=cmd_window_cancel)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # last resort: never let a crash look like "nobody"
        print(f"unknown: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(EXIT_UNKNOWN)
