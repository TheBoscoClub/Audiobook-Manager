#!/usr/bin/env python3
# /test:wiring-exception: systemd-timer-driven oneshot, not invoked by app code.
#                          Wired via audiobook-translation-monitor-sampler.{service,timer}.
"""Translation monitor — sampler/backlog tier (every 5min).

One pass:
  1. Reset stuck sampler/backlog segment claims (claimed >2h)
  2. Reset sampler_jobs stuck in status='running' >2h
  3. Sweep retry-exhausted sampler/backlog segments to state=failed
  4. (Future v8.3.10) Per-book + global daily-spend pause

Triggered by ``audiobook-translation-monitor-sampler.timer`` (every 5min).
Always exits 0 — errors are logged but never fail the timer.

The 5-minute cadence is intentional: sampler/backlog work has no
human-perceptible deadline (unlike live playback, which the user is
actively waiting on), so spending a few extra minutes on a stuck claim
is not worth the per-tick DB churn of a 30s cadence.

DB path resolution: see translation-monitor-live.py.

Exit codes:
  0 — success (zero or more resets)
  0 — DB missing or migration not yet applied (silent skip)
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Allow running from /opt/audiobooks/scripts or from the project tree
_HERE = Path(__file__).resolve().parent
for candidate in (_HERE.parent, _HERE.parent / "library"):
    if (candidate / "library" / "translation_monitor" / "__init__.py").exists():
        sys.path.insert(0, str(candidate))
        break
    if (candidate / "translation_monitor" / "__init__.py").exists():
        sys.path.insert(0, str(candidate.parent))
        break

from translation_monitor import (  # noqa: E402
    reset_stuck_sampler_claims,
    reset_stuck_sampler_jobs,
    sweep_retry_exhausted_segments,
)
from translation_monitor.db import (  # noqa: E402
    connect,
    db_exists,
    schema_has_monitor_table,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [translation-monitor-sampler] %(levelname)s %(message)s",
)
logger = logging.getLogger("translation-monitor-sampler")


def main() -> int:
    if not db_exists():
        logger.info("DB not present yet — skipping pass")
        return 0

    try:
        with connect() as conn:
            if not schema_has_monitor_table(conn):
                logger.info(
                    "translation_monitor_events table missing — pre-v8.3.9 DB, skipping"
                )
                return 0

            reset_segs = reset_stuck_sampler_claims(conn)
            reset_jobs = reset_stuck_sampler_jobs(conn)
            failed_segs = sweep_retry_exhausted_segments(
                conn,
                monitor="sampler",
                origins=("sampler", "backlog"),
            )

            # TODO(v8.3.10): per-book spent_cents aggregation + auto-pause
            # TODO(v8.3.10): daily total spend cap + global queue pause

            if reset_segs or reset_jobs or failed_segs:
                logger.info(
                    "pass complete: %d seg-claim reset(s), %d sampler_job reset(s), "
                    "%d retry-exhausted",
                    len(reset_segs),
                    len(reset_jobs),
                    len(failed_segs),
                )
            else:
                logger.debug("pass complete: no action needed")
        return 0
    except Exception as exc:  # noqa: BLE001 — never let a monitor crash break the timer
        logger.error("pass failed: %s", exc, exc_info=True)
        return 0


if __name__ == "__main__":
    sys.exit(main())
