"""The quota row must be reconciled with DeepL, not merely tallied locally.

Audiobook-Manager-2s6. `refresh_from_api()` existed, was tested, and had no
caller anywhere outside its own tests — production's `last_api_check` had been
NULL since the table was created in April, so `check_before_translate()` gated
on a number nobody had ever verified against the vendor.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

_LIB = Path(__file__).resolve().parents[1]
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from localization.translation.quota import QuotaTracker  # noqa: E402


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
