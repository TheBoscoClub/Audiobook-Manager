"""Spend ledger for the scheduled translation drain — monthly ceiling.

Why a file and not a database table: the ledger is the record that authorises
spending real money, so it must outlive everything the drain touches. A table
in ``audiobooks.db`` would be rolled back by a database restore or a migration
replay and silently re-authorise a month's budget; a separate append-only file
is unaffected by either. It is plain TSV (``AUDIOBOOKS_DRAIN_LEDGER``, default
``${AUDIOBOOKS_VAR_DIR}/translation-drain/spend-ledger.tsv``), readable with
``column -t``, written under an exclusive ``flock``, and only ever appended to.

Line format (tab-separated)::

    <epoch>  <YYYY-MM>  <run_id>  <kind>  <dollars>  <note>

``kind`` is one of

``reserve``  written BEFORE a node is rented, for the run's effective cap. If
             the drain is then killed (SIGKILL, OOM, power loss) nothing else
             is ever written for the run and the full reservation counts, so a
             crash can only over-count, never under-count.
``final``    the run's measured spend (the larger of the account-credit delta
             and the instance's uptime x price). Written by the drain at the
             end, or by the reaper when it destroys a node a dead run left
             behind. With several, the LARGEST counts (order-independent and
             conservative).

A run's cost is ``max(finals)`` if it has any, else its reservation. Its month
is the month of its ``reserve`` line: the calendar month in LOCAL time when the
node was rented (a run that crosses midnight at a month boundary is charged to
the month it started in).

CLI (used by ``translation-drain.sh`` and ``translation-drain-reaper.sh``)::

    drain_ledger --ledger F budget --per-run 10 --monthly 100 [--min-run 1]
        -> prints "cap=<dollars> mtd=<dollars> remaining=<dollars>"; exit 3 (and
           a reason on stderr) when the month cannot afford a run
    drain_ledger --ledger F mtd [--exclude-run RUN]
    drain_ledger --ledger F reserve --run RUN --amount X
    drain_ledger --ledger F final --run RUN --amount X [--note TEXT]
    drain_ledger --ledger F settle --run RUN [--accrued X]
        -> close an orphaned run at max(reservation, accrued): never below its cap

``DRAIN_LEDGER_NOW`` (epoch seconds) overrides the clock — tests only.
"""

from __future__ import annotations

import argparse
import fcntl
import math
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

EXIT_REFUSED = 3


def _now() -> float:
    override = os.environ.get("DRAIN_LEDGER_NOW", "").strip()
    return float(override) if override else time.time()


def month_of(epoch: float) -> str:
    return time.strftime("%Y-%m", time.localtime(epoch))


@dataclass
class _Run:
    month: str = ""
    reserve: float | None = None
    finals: list[float] = field(default_factory=list)

    def cost(self) -> float:
        if self.finals:
            return max(self.finals)
        return self.reserve or 0.0


def _read(path: Path) -> dict[str, _Run]:
    runs: dict[str, _Run] = {}
    if not path.exists():
        return runs
    with path.open("r", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_SH)
        lines = fh.read().splitlines()
    for lineno, line in enumerate(lines, 1):
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 5:
            raise ValueError(f"{path}:{lineno}: malformed ledger line")
        _epoch, month, run_id, kind, amount = parts[:5]
        run = runs.setdefault(run_id, _Run())
        value = float(amount)
        if value < 0:
            raise ValueError(f"{path}:{lineno}: negative amount")
        if kind == "reserve":
            run.reserve = value
            run.month = month
        elif kind == "final":
            run.finals.append(value)
            run.month = run.month or month
        else:
            raise ValueError(f"{path}:{lineno}: unknown kind {kind!r}")
    return runs


def month_to_date(path: Path, *, exclude_run: str = "", now: float | None = None) -> float:
    month = month_of(_now() if now is None else now)
    return round(
        sum(
            run.cost()
            for run_id, run in _read(path).items()
            if run.month == month and run_id != exclude_run
        ),
        4,
    )


def _append(path: Path, run_id: str, kind: str, amount: float, note: str = "") -> None:
    if amount < 0:
        raise ValueError("amount must be >= 0")
    if "\t" in run_id or "\n" in run_id or "\t" in note or "\n" in note:
        raise ValueError("run id and note must not contain tabs or newlines")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    now = _now()
    line = f"{int(now)}\t{month_of(now)}\t{run_id}\t{kind}\t{amount:.4f}\t{note}\n"
    # Owner-only: the drain and the reaper both run as the service user
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    os.fchmod(fd, 0o600)  # also tightens a ledger created group-readable by an older release
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


def settle(path: Path, run_id: str, accrued: float = 0.0, note: str = "") -> float:
    """Close a run whose drain died: record max(reservation, accrued, earlier finals).

    Used for an orphaned run — the node was destroyed by the reaper, or by its
    own dead-man switch while this host was down — where no process measured
    the real spend. The reservation is the run's cap, i.e. the most the guard
    and the node's own timer allowed it to cost, so it is the floor: an
    orphan is never recorded as cheaper than its cap. Returns the amount.
    """
    run = _read(path).get(run_id)
    floor = 0.0
    if run is not None:
        floor = max([run.reserve or 0.0, *run.finals])
    amount = round(max(floor, accrued), 4)
    _append(path, run_id, "final", amount, note or "settled orphan (reservation floor)")
    return amount


def budget(
    path: Path, per_run: float, monthly: float, min_run: float
) -> tuple[float, float, float]:
    """Return (effective cap, month-to-date, remaining) or raise ``RuntimeError``."""
    if per_run <= 0 or monthly <= 0:
        raise RuntimeError("per-run and monthly budgets must be positive")
    mtd = month_to_date(path)
    remaining = round(monthly - mtd, 4)
    # Floor to whole cents so the printed cap can never exceed what is left.
    cap = math.floor(min(per_run, remaining) * 100) / 100
    if remaining <= 0:
        raise RuntimeError(
            f"monthly ceiling reached: ${mtd:.2f} spent of ${monthly:.2f} in {month_of(_now())}"
        )
    if cap < min_run:
        raise RuntimeError(
            f"only ${remaining:.2f} of the ${monthly:.2f} monthly ceiling left in "
            f"{month_of(_now())} — below the ${min_run:.2f} minimum worth renting a node for"
        )
    return cap, mtd, remaining


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="drain_ledger", description=__doc__.splitlines()[0])
    parser.add_argument("--ledger", required=True, type=Path)
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("budget")
    b.add_argument("--per-run", type=float, required=True)
    b.add_argument("--monthly", type=float, required=True)
    b.add_argument("--min-run", type=float, default=1.0)
    m = sub.add_parser("mtd")
    m.add_argument("--exclude-run", default="")
    st = sub.add_parser("settle")
    st.add_argument("--run", required=True)
    st.add_argument("--accrued", type=float, default=0.0)
    st.add_argument("--note", default="")
    for kind in ("reserve", "final"):
        k = sub.add_parser(kind)
        k.add_argument("--run", required=True)
        k.add_argument("--amount", type=float, required=True)
        k.add_argument("--note", default="")
    args = parser.parse_args(argv)

    try:
        if args.cmd == "budget":
            cap, mtd, remaining = budget(args.ledger, args.per_run, args.monthly, args.min_run)
            print(f"cap={cap:.2f} mtd={mtd:.2f} remaining={remaining:.2f}")
        elif args.cmd == "settle":
            print(f"{settle(args.ledger, args.run, args.accrued, args.note):.4f}")
        elif args.cmd == "mtd":
            print(f"{month_to_date(args.ledger, exclude_run=args.exclude_run):.4f}")
        else:
            _append(args.ledger, args.run, args.cmd, args.amount, args.note)
    except RuntimeError as exc:
        print(f"drain_ledger: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except (OSError, ValueError) as exc:
        print(f"drain_ledger: ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
