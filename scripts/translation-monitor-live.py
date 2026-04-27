#!/usr/bin/env python3
# /test:wiring-exception: systemd-timer-driven oneshot, not invoked by app code.
#                          Wired via audiobook-translation-monitor-live.{service,timer}.
# pylint: disable=invalid-name,broad-exception-caught
"""Translation monitor — live tier (every 30s).

One pass:
  1. Reset stuck live-segment claims (claimed >60s, still not completed)
  2. Sweep retry-exhausted live segments (retry_count >= 3) to state=failed
  3. (Future) GPU instance health probe — stubbed in v8.3.9

Triggered by ``audiobook-translation-monitor-live.timer`` (every 30s).
Always exits 0 — errors are logged but never fail the timer, because a
bad monitor pass should not block the next one.

Why a oneshot, not a daemon?
  A timer-driven oneshot has the simplest failure model: a crashed run
  on tick N is auto-recovered by the next timer fire. No restart-storm
  logic, no PID files, no signal handlers. The 30s cadence costs ~1s of
  CPU per minute on the host — negligible.

DB path resolution:
  Reads ``AUDIOBOOKS_DATABASE`` env (set by the systemd unit) or falls
  back to the canonical default from lib/audiobook-config.sh
  (``${AUDIOBOOKS_VAR_DIR}/db/audiobooks.db``).

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
    alert_capacity_pressure,
    alert_old_live_segments,
    connect,
    probe_gpu_instance_health,
    reset_stuck_live_claims,
    sweep_retry_exhausted_segments,
)
from translation_monitor.db import db_exists, schema_has_monitor_table  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [translation-monitor-live] %(levelname)s %(message)s"
)
logger = logging.getLogger("translation-monitor-live")


def main() -> int:
    """Run a single monitor pass; return systemd-friendly exit code."""
    if not db_exists():
        logger.info("DB not present yet — skipping pass")
        return 0

    try:
        with connect() as conn:
            if not schema_has_monitor_table(conn):
                logger.info("translation_monitor_events table missing — pre-v8.3.9 DB, skipping")
                return 0

            reset_segs = reset_stuck_live_claims(conn)
            failed_segs = sweep_retry_exhausted_segments(conn, monitor="live", origins=("live",))
            aged_segs = alert_old_live_segments(conn)
            capacity_event = alert_capacity_pressure(conn)
            health = probe_gpu_instance_health(conn, monitor="live")

            if reset_segs or failed_segs or aged_segs or capacity_event:
                logger.info(
                    "pass complete: %d claim reset(s), %d retry-exhausted, "
                    "%d age-alert(s), capacity_warning=%s, gpu=%s",
                    len(reset_segs),
                    len(failed_segs),
                    len(aged_segs),
                    "yes" if capacity_event else "no",
                    "stub" if health.get("stub") else "ok",
                )
            else:
                logger.debug("pass complete: no action needed")
        return 0
    except Exception as exc:  # noqa: BLE001 — never let a monitor crash break the timer
        logger.error("pass failed: %s", exc, exc_info=True)
        return 0  # still return 0 — next tick will retry


if __name__ == "__main__":
    sys.exit(main())
