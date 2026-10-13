"""Time and price limits shared by the drain, its reaper and the node's dead-man switch.

One definition so the three never disagree about how long a drain node may
live or what it may cost (Audiobook-Manager-4j4).

Node lifetime
-------------
``lifetime`` = the longest a HEALTHY run can keep its node:

    up wait        UP_WAIT_SECONDS = 1200 s to "running" + 1200 s on-node pip
                   wait (GPU_NODE_PIP_TIMEOUT default) + 100 s scp/launch — the
                   drain also enforces this bound on `gpu-node.sh up` itself
  + bootstrap      AUDIOBOOKS_DRAIN_BOOTSTRAP_TIMEOUT (enforced by the drain)
  + batch          AUDIOBOOKS_DRAIN_MAX_MINUTES       (enforced by the drain)
  + stop grace     STOP_GRACE_SECONDS, then the drain's own `down`
  + margin         AUDIOBOOKS_DRAIN_REAPER_MARGIN_MINUTES, at least
                   MIN_MARGIN_MINUTES so teardown and clock skew always fit

Because every term is a bound the drain itself enforces, neither the reaper
(condition b) nor the node's dead-man switch can end a healthy run early.

Price
-----
With the host down, only the node's own timer ends it, so an orphan costs at
most ``price x lifetime``. ``plan`` makes that fit the run's cap:

* no explicit max price: max price = cap / lifetime-in-hours (offers above it
  are not rented);
* an explicit higher max price: the lifetime shrinks to cap / price, and the
  batch budget shrinks with it so the healthy-run guarantee still holds —
  or the run is refused when too little batch time would be left.

Labels
------
Every host's drain owns the Vast instances labelled ``abm-drain-<host>``. The
account is shared — production and the dev/test VMs rent with the same key —
and each host's reaper destroys every instance under its label that it does
not know to be alive, so a label shared between hosts would let one host's
reaper kill another host's live node. A plain ``abm-drain`` (the pre-fix
default) is therefore refused, as is anything outside ``abm-drain-<name>``.

CLI::

    python -m localization.drain_limits lifetime --max-minutes M --bootstrap-seconds B --margin-minutes G
    python -m localization.drain_limits plan --cap C --max-minutes M --bootstrap-seconds B \\
        --margin-minutes G [--max-price P]
        -> "lifetime=<s> max_price=<$/h> batch_seconds=<s>"   (exit 3 = refused)
    python -m localization.drain_limits default-label
    python -m localization.drain_limits check-label LABEL
"""

from __future__ import annotations

import argparse
import math
import re
import socket
import sys

RUNNING_WAIT_SECONDS = 1200  # gpu-node.sh up: poll until "running"
PIP_WAIT_SECONDS = 1200  # gpu-node.sh up: GPU_NODE_PIP_TIMEOUT default
LAUNCH_SECONDS = 100  # scp + ssh whisper launch
UP_WAIT_SECONDS = RUNNING_WAIT_SECONDS + PIP_WAIT_SECONDS + LAUNCH_SECONDS
STOP_GRACE_SECONDS = 300  # SIGTERM grace the drain gives a stopping batch
MIN_MARGIN_MINUTES = 10  # teardown (<= ~4 min) + clock skew
MIN_BATCH_SECONDS = 600  # below this a run is not worth renting a node for
EXIT_REFUSED = 3


def node_lifetime_seconds(max_minutes: int, bootstrap_seconds: int, margin_minutes: int) -> int:
    for name, value in (("max_minutes", max_minutes), ("bootstrap_seconds", bootstrap_seconds)):
        if value < 0:
            raise ValueError(f"{name} must be >= 0")
    if margin_minutes < MIN_MARGIN_MINUTES:
        raise ValueError(
            f"AUDIOBOOKS_DRAIN_REAPER_MARGIN_MINUTES must be >= {MIN_MARGIN_MINUTES} "
            f"(got {margin_minutes}): with less, the reaper or the node's own timer could end "
            "a healthy run during teardown"
        )
    return (
        UP_WAIT_SECONDS
        + bootstrap_seconds
        + max_minutes * 60
        + STOP_GRACE_SECONDS
        + margin_minutes * 60
    )


def plan(
    cap: float,
    max_minutes: int,
    bootstrap_seconds: int,
    margin_minutes: int,
    max_price: float | None = None,
) -> tuple[int, float, int]:
    """Return (node lifetime s, max price $/h, batch wall-clock s) for one run.

    Raises ``RuntimeError`` when the run must be refused.
    """
    if cap <= 0:
        raise RuntimeError("the run's cap must be positive")
    lifetime = node_lifetime_seconds(max_minutes, bootstrap_seconds, margin_minutes)
    batch = max_minutes * 60
    fixed = lifetime - batch  # everything but the batch
    if max_price is None:
        # Floor to a tenth of a cent so price x lifetime never exceeds the cap.
        price = math.floor(cap / (lifetime / 3600) * 1000) / 1000
        if price <= 0:
            raise RuntimeError(f"a ${cap:.2f} cap cannot pay for a {lifetime}s node at any price")
        return lifetime, price, batch
    if max_price <= 0:
        raise RuntimeError("GPU_NODE_MAX_PRICE must be positive")
    affordable = math.floor(cap / max_price * 3600)
    if affordable >= lifetime:
        return lifetime, max_price, batch
    batch = affordable - fixed
    if batch < MIN_BATCH_SECONDS:
        raise RuntimeError(
            f"at GPU_NODE_MAX_PRICE ${max_price}/h the ${cap:.2f} cap buys {affordable}s of node "
            f"time, leaving {max(batch, 0)}s for the batch (< {MIN_BATCH_SECONDS}s) — lower the "
            "max price or raise the cap"
        )
    return affordable, max_price, batch


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
    for name in ("lifetime", "plan"):
        p = sub.add_parser(name)
        p.add_argument("--max-minutes", type=int, required=True)
        p.add_argument("--bootstrap-seconds", type=int, required=True)
        p.add_argument("--margin-minutes", type=int, required=True)
        if name == "plan":
            p.add_argument("--cap", type=float, required=True)
            p.add_argument("--max-price", type=float, default=None)
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
        if args.cmd == "lifetime":
            print(
                node_lifetime_seconds(args.max_minutes, args.bootstrap_seconds, args.margin_minutes)
            )
        else:
            life, price, batch = plan(
                args.cap,
                args.max_minutes,
                args.bootstrap_seconds,
                args.margin_minutes,
                args.max_price,
            )
            print(f"lifetime={life} max_price={price:.3f} batch_seconds={batch}")
    except (ValueError, RuntimeError) as exc:
        print(f"drain_limits: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    return 0


if __name__ == "__main__":
    sys.exit(main())
