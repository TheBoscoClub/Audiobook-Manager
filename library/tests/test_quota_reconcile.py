"""The quota row must be reconciled with DeepL, not merely tallied locally.

Audiobook-Manager-2s6. `refresh_from_api()` existed, was tested, and had no
caller anywhere outside its own tests — production's `last_api_check` had been
NULL since the table was created in April, so `check_before_translate()` gated
on a number nobody had ever verified against the vendor.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

_LIB = Path(__file__).resolve().parents[1]
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from localization.translation.quota import (  # noqa: E402
    REFRESH_RETRY_SECONDS,
    QuotaTracker,
)


@contextlib.contextmanager
def _warning_records():
    """Collect WARNING records emitted by the quota logger."""
    records: list[logging.LogRecord] = []

    class _H(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("localization.translation.quota")
    handler = _H(level=logging.WARNING)
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.WARNING)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


def _tracker(tmp_path, **kw):
    return QuotaTracker(db_path=tmp_path / "q.db", **kw)


def _last_check(db: Path):
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT last_api_check FROM deepl_quota WHERE id='default'").fetchone()[
            0
        ]
    finally:
        conn.close()


class TestReconcileIsWired:
    def test_check_before_translate_reconciles_when_never_checked(self, tmp_path):
        t = _tracker(tmp_path, api_key="k:fx", base_url="https://api-free.deepl.com/v2")
        with patch.object(QuotaTracker, "refresh_from_api", autospec=True) as ref:
            t.check_before_translate(100)
        assert ref.called, "refresh_from_api was never called — the zombie is back"

    def test_reconcile_updates_last_api_check(self, tmp_path):
        t = _tracker(tmp_path, api_key="k:fx", base_url="https://api-free.deepl.com/v2")
        assert _last_check(tmp_path / "q.db") is None

        class _Resp:
            status_code = 200

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"character_count": 4321, "character_limit": 500000}

        with patch("localization.translation.quota.requests.get", return_value=_Resp()):
            t.check_before_translate(10)

        assert _last_check(tmp_path / "q.db") is not None
        assert t.snapshot()["used"] == 4321

    def test_fresh_row_is_not_re_reconciled(self, tmp_path):
        t = _tracker(tmp_path, api_key="k:fx", base_url="https://api-free.deepl.com/v2")

        class _Resp:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"character_count": 1, "character_limit": 500000}

        with patch("localization.translation.quota.requests.get", return_value=_Resp()) as g:
            t.check_before_translate(10)
            first = g.call_count
            t._last_refresh_attempt = 0.0  # bypass the attempt floor
            t.check_before_translate(10)  # row is fresh now
        assert g.call_count == first, "a fresh row was reconciled again"

    def test_no_credentials_never_attempts(self, tmp_path):
        t = _tracker(tmp_path)  # the shape the /quota API endpoint constructs
        with patch("localization.translation.quota.requests.get") as g:
            t.check_before_translate(10)
        assert not g.called


class TestReconcileFailsOpen:
    def test_endpoint_failure_does_not_block_translation(self, tmp_path, caplog):
        import logging

        t = _tracker(tmp_path, api_key="k:fx", base_url="https://api-free.deepl.com/v2")
        with (
            patch("localization.translation.quota.requests.get", side_effect=OSError("down")),
            caplog.at_level(logging.WARNING),
        ):
            t.check_before_translate(10)  # must not raise

        assert any("reconcile failed" in r.getMessage() for r in caplog.records)

    def test_failure_is_not_retried_on_every_call(self, tmp_path):
        t = _tracker(tmp_path, api_key="k:fx", base_url="https://api-free.deepl.com/v2")
        with patch("localization.translation.quota.requests.get", side_effect=OSError("down")) as g:
            for _ in range(5):
                t.check_before_translate(10)
        assert g.call_count == 1, f"hammered the failing endpoint {g.call_count} times"


class TestTestSuiteIsolation:
    """d40: no test may resolve a path into a real installation."""

    def test_cover_dir_is_not_a_real_library(self):
        import config

        resolved = str(Path(config.COVER_DIR).resolve())
        assert "audiobook-test-tree-" in resolved, (
            f"COVER_DIR resolved to {resolved} — tests must never point at an "
            "installation, because _cleanup_orphaned_covers() deletes from it"
        )


class TestReconcileGuardsAreIndividuallyLoadBearing:
    """Each condition in _reconcile_if_stale() must be independently tested.

    These exist because mutation testing (mutmut) showed the original four
    tests left 13 mutants alive inside a function that had just been declared
    verified: flipping `or`->`and` in the credential check, `and`->`or` in the
    attempt floor and in the staleness check all still passed. Wiring was
    proven; the logic was not.
    """

    def _fresh(self, tmp_path, **kw):
        return QuotaTracker(db_path=tmp_path / "q.db", **kw)

    def test_half_configured_credentials_do_not_attempt(self, tmp_path):
        """api_key without base_url (or vice versa) must NOT call out.

        Kills the `or`->`and` mutant: with `and`, a half-configured tracker
        sails past the guard and issues a request with a missing endpoint.
        """
        for kw in (
            {"api_key": "k:fx", "base_url": ""},
            {"api_key": "", "base_url": "https://x/v2"},
        ):
            t = self._fresh(tmp_path, **kw)
            with (
                patch("localization.translation.quota.requests.get") as g,
                _warning_records() as records,
            ):
                t.check_before_translate(10)
            assert not g.called, f"attempted a reconcile with {kw}"
            # Silence is the distinguishing observation: the guard returns
            # before refresh_from_api(), which raises RuntimeError for a
            # half-configured tracker and would be logged as a failed reconcile.
            assert not [r for r in records if "reconcile failed" in r.getMessage()], (
                f"half-configured tracker attempted a reconcile with {kw}"
            )

    def test_stale_row_is_reconciled_again(self, tmp_path):
        """A row older than the interval must be refreshed.

        Kills the `and`->`or` mutant in the staleness check, which returns
        early for any non-NULL timestamp and so never refreshes again.
        """
        t = self._fresh(tmp_path, api_key="k:fx", base_url="https://api-free.deepl.com/v2")

        class _Resp:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"character_count": 7, "character_limit": 500000}

        # seed a check far older than REFRESH_INTERVAL_SECONDS
        conn = sqlite3.connect(tmp_path / "q.db")
        conn.execute(
            "UPDATE deepl_quota SET last_api_check = '2020-01-01 00:00:00' WHERE id='default'"
        )
        conn.commit()
        conn.close()

        with patch("localization.translation.quota.requests.get", return_value=_Resp()) as g:
            t.check_before_translate(10)
        assert g.called, "a stale row was not reconciled"
        assert t.snapshot()["used"] == 7

    def test_attempt_floor_expires(self, tmp_path):
        """Once the retry floor has passed, a failed reconcile is retried.

        Kills the `and`->`or` mutant in the floor check, which treats any
        prior attempt as permanently disqualifying and never retries.
        """
        import time as _time

        t = self._fresh(tmp_path, api_key="k:fx", base_url="https://api-free.deepl.com/v2")
        with patch("localization.translation.quota.requests.get", side_effect=OSError("down")) as g:
            t.check_before_translate(10)
            assert g.call_count == 1
            # pretend the floor has elapsed
            t._last_refresh_attempt = _time.monotonic() - (REFRESH_RETRY_SECONDS + 1)
            t.check_before_translate(10)
        assert g.call_count == 2, "the reconcile was never retried after the floor expired"
