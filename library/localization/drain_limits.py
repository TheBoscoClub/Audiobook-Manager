"""Time limits shared by the drain, its reaper and the on-node dead-man switch.

One definition so the three never disagree about how long a drain node may
legitimately live (Audiobook-Manager-4j4):

* ``translation-drain.sh`` passes it to ``gpu-node.sh`` as
  ``GPU_NODE_MAX_LIFETIME``, which arms the on-node dead-man switch;
* ``translation-drain-reaper.sh`` destroys any drain node older than it.

``lifetime`` = ``up`` wait (instance create + boot + whisper deploy, bounded by
``gpu-node.sh``) + bootstrap timeout + batch wall-clock + batch stop grace +
operator margin.

Labels (``default-label``, ``check-label``): every host's drain owns the Vast
instances labelled ``abm-drain-<host>``. The account is shared — production
and the dev/test VMs rent with the same key — and each host's reaper destroys
every instance under its label that it does not know to be alive, so a label
shared between hosts would let one host's reaper kill another host's live
node. A plain ``abm-drain`` (the pre-fix default) is therefore refused, as is
anything outside ``abm-drain-<name>`` (the operator's ``abm-translate`` CLI
label included).

Usage: python -m localization.drain_limits lifetime \
           --max-minutes M --bootstrap-seconds B --margin-minutes G
       python -m localization.drain_limits default-label
       python -m localization.drain_limits check-label LABEL
"""

from __future__ import annotations

import argparse
import re
import socket
import sys

UP_WAIT_SECONDS = 1500  # gpu-node.sh up: <=1200 s to "running" + scp/launch
STOP_GRACE_SECONDS = 300  # SIGTERM grace the drain gives a stopping batch


def node_lifetime_seconds(max_minutes: int, bootstrap_seconds: int, margin_minutes: int) -> int:
    for name, value in (
        ("max_minutes", max_minutes),
        ("bootstrap_seconds", bootstrap_seconds),
        ("margin_minutes", margin_minutes),
    ):
        if value < 0:
            raise ValueError(f"{name} must be >= 0")
    return (
        UP_WAIT_SECONDS
        + bootstrap_seconds
        + max_minutes * 60
        + STOP_GRACE_SECONDS
        + margin_minutes * 60
    )


LABEL_PREFIX = "abm-drain-"
LABEL_RE = re.compile(r"^abm-drain-[a-z0-9][a-z0-9-]{0,40}$")


def default_label(hostname: str | None = None) -> str:
    """``abm-drain-<short hostname>``, lower-cased, reduced to [a-z0-9-]."""
    host = (hostname if hostname is not None else socket.gethostname()).split(".")[0].lower()
    host = re.sub(r"[^a-z0-9-]+", "-", host).strip("-")[:40] or "host"
    return LABEL_PREFIX + host


def check_label(label: str) -> str | None:
    """Return a refusal reason, or None when ``label`` is acceptable."""
    if not LABEL_RE.fullmatch(label or ""):
        return (
            f"drain label {label!r} must look like 'abm-drain-<host>' (lower-case letters, "
            "digits, '-'): the Vast account is shared between hosts, and a label without "
            "a host identity lets one host's reaper destroy another host's live node"
        )
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="drain_limits")
    sub = parser.add_subparsers(dest="cmd", required=True)
    life = sub.add_parser("lifetime")
    life.add_argument("--max-minutes", type=int, required=True)
    life.add_argument("--bootstrap-seconds", type=int, required=True)
    life.add_argument("--margin-minutes", type=int, required=True)
    sub.add_parser("default-label")
    chk = sub.add_parser("check-label")
    chk.add_argument("label")
    args = parser.parse_args(argv)
    if args.cmd == "default-label":
        print(default_label())
        return 0
    if args.cmd == "check-label":
        reason = check_label(args.label)
        if reason:
            print(f"drain_limits: {reason}", file=sys.stderr)
            return 2
        return 0
    try:
        print(node_lifetime_seconds(args.max_minutes, args.bootstrap_seconds, args.margin_minutes))
    except ValueError as exc:
        print(f"drain_limits: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
