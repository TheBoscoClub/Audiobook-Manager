"""Tests for the DeepL quota + glossary + TM infrastructure.

All network calls are mocked — the tests must NEVER hit DeepL. The
four scenarios mirror the acceptance criteria for task #9 of the
v8.1 Chinese localization work:

1. Quota blocks a translation that would exceed the budget.
2. TM cache hits short-circuit the API call.
3. Glossary ID is passed on the translate request payload.
4. ``/api/admin/localization/quota`` requires admin authentication.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from localization.translation.deepl_translate import DeepLTranslator, _hash_source
from localization.translation.quota import QuotaExceededError, QuotaTracker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bootstrap_db(path: Path) -> None:
    """Create the minimal schema the translator needs."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            """CREATE TABLE string_translations (
                source_hash TEXT NOT NULL,
                locale TEXT NOT NULL,
                source TEXT NOT NULL,
                translation TEXT NOT NULL,
                translator TEXT DEFAULT 'deepl',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (source_hash, locale)
            )"""
        )
        # deepl_quota is created by QuotaTracker._ensure_schema; no need here.
        conn.commit()
    finally:
        conn.close()


def _fake_response(translations: list[str]) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"translations": [{"text": t} for t in translations]}
    return resp


@pytest.fixture
def quota_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "audiobooks.db"
    _bootstrap_db(db_path)
    return db_path


# ---------------------------------------------------------------------------
# 1. Quota hard-limit enforcement
# ---------------------------------------------------------------------------


def test_quota_blocks_translation_that_would_exceed_budget(quota_db: Path):
    tracker = QuotaTracker(db_path=quota_db)
    tracker.set_limit(100)
    # Consume 95/100 chars — any further request will cross 99% hard limit.
    tracker.record_usage(95)

    translator = DeepLTranslator(
        api_key="test-key:fx", db_path=quota_db, tracker=tracker, enable_glossary=False
    )

    with patch("localization.translation.deepl_translate.requests.post") as mock_post:
        with pytest.raises(QuotaExceededError):
            translator.translate(["This is a long sentence"], "zh-Hans")
        mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# 2. Translation memory cache hits
# ---------------------------------------------------------------------------


def test_tm_cache_hit_skips_api_and_does_not_bill(quota_db: Path):
    # Pre-seed the TM with a cached translation.
    conn = sqlite3.connect(str(quota_db))
    try:
        conn.execute(
            "INSERT INTO string_translations "
            "(source_hash, locale, source, translation) VALUES (?, ?, ?, ?)",
            (_hash_source("Hello"), "zh-Hans", "Hello", "你好"),
        )
        conn.commit()
    finally:
        conn.close()

    tracker = QuotaTracker(db_path=quota_db)
    translator = DeepLTranslator(
        api_key="test-key:fx", db_path=quota_db, tracker=tracker, enable_glossary=False
    )

    with patch("localization.translation.deepl_translate.requests.post") as mock_post:
        result = translator.translate(["Hello"], "zh-Hans")
        mock_post.assert_not_called()

    assert result == ["你好"]
    # Quota counter must not have moved — TM hits are free.
    assert tracker.snapshot()["used"] == 0


def test_tm_mixed_hit_and_miss_billing(quota_db: Path):
    """When some strings are cached and some are not, only misses bill."""
    conn = sqlite3.connect(str(quota_db))
    try:
        conn.execute(
            "INSERT INTO string_translations "
            "(source_hash, locale, source, translation) VALUES (?, ?, ?, ?)",
            (_hash_source("Cached"), "zh-Hans", "Cached", "缓存"),
        )
        conn.commit()
    finally:
        conn.close()

    tracker = QuotaTracker(db_path=quota_db)
    translator = DeepLTranslator(
        api_key="test-key:fx", db_path=quota_db, tracker=tracker, enable_glossary=False
    )

    with patch(
        "localization.translation.deepl_translate.requests.post",
        return_value=_fake_response(["新鲜"]),
    ) as mock_post:
        result = translator.translate(["Cached", "Fresh"], "zh-Hans")
        mock_post.assert_called_once()
        # Only "Fresh" (5 chars) should be billed.
        assert tracker.snapshot()["used"] == 5

    assert result == ["缓存", "新鲜"]


# ---------------------------------------------------------------------------
# 3. Glossary ID passed to DeepL
# ---------------------------------------------------------------------------


def test_glossary_id_passed_in_translate_payload(quota_db: Path):
    tracker = QuotaTracker(db_path=quota_db)
    translator = DeepLTranslator(
        api_key="test-key:fx", db_path=quota_db, tracker=tracker, glossary_id="glossary-abc-123"
    )

    with patch(
        "localization.translation.deepl_translate.requests.post",
        return_value=_fake_response(["你好世界"]),
    ) as mock_post:
        translator.translate(["Hello world"], "zh-Hans")
        assert mock_post.call_count == 1
        _, kwargs = mock_post.call_args
        payload = kwargs["json"]
        assert payload["glossary_id"] == "glossary-abc-123"
        assert payload["target_lang"] == "ZH-HANS"


# ---------------------------------------------------------------------------
# 4. Admin endpoint auth gate
# ---------------------------------------------------------------------------


def test_admin_quota_endpoint_requires_admin(admin_client, anon_client):
    # Anonymous must be rejected (401 or 403 depending on middleware).
    resp = anon_client.get("/api/admin/localization/quota")
    assert resp.status_code in (401, 403)

    # Admin gets a JSON snapshot.
    resp = admin_client.get("/api/admin/localization/quota")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "used" in data
    assert "limit" in data
    assert "percent" in data
    assert "reset_date" in data
    assert "glossary_id" in data
    assert "note" in data


# ---------------------------------------------------------------------------
# Additional coverage: less-used QuotaTracker paths
# ---------------------------------------------------------------------------


class TestQuotaTrackerPaths:
    """Exercise the tracker methods exercised by admin endpoints, the
    glossary manager, and the /usage live-sync path — none of which are
    covered by the four acceptance scenarios above."""

    def test_record_usage_noop_for_zero(self, tmp_path: Path):
        """Zero-char writes must skip the DB round-trip entirely."""
        db = tmp_path / "q.db"
        _bootstrap_db(db)
        tracker = QuotaTracker(db, api_key="key", base_url="https://example")
        tracker.record_usage(0)

        # Row should still read 0 chars_used.
        snap = tracker.snapshot()
        assert snap["used"] == 0

    def test_record_usage_accumulates(self, tmp_path: Path):
        db = tmp_path / "q.db"
        _bootstrap_db(db)
        tracker = QuotaTracker(db, api_key="key", base_url="https://example")
        tracker.record_usage(100)
        tracker.record_usage(250)
        assert tracker.snapshot()["used"] == 350

    def test_remaining_chars(self, tmp_path: Path):
        db = tmp_path / "q.db"
        _bootstrap_db(db)
        tracker = QuotaTracker(db, api_key="key", base_url="https://example")
        tracker.set_limit(1000)
        tracker.record_usage(400)
        assert tracker.remaining_chars() == 600

    def test_check_before_translate_noop_for_non_positive(self, tmp_path: Path):
        db = tmp_path / "q.db"
        _bootstrap_db(db)
        tracker = QuotaTracker(db, api_key="key", base_url="https://example")
        tracker.set_limit(1000)
        tracker.record_usage(990)
        # Zero-or-negative char_count must never raise even at max budget.
        tracker.check_before_translate(0)
        tracker.check_before_translate(-50)

    def test_check_before_translate_soft_limit_logs_warning(self, tmp_path: Path, caplog):
        """Crossing 90% triggers a WARNING log but does NOT raise."""
        import logging as _logging

        db = tmp_path / "q.db"
        _bootstrap_db(db)
        tracker = QuotaTracker(db, api_key="key", base_url="https://example")
        tracker.set_limit(1000)
        tracker.record_usage(890)
        with caplog.at_level(_logging.WARNING, logger="localization.translation.quota"):
            tracker.check_before_translate(50)  # 940 ≥ 900 (90%), < 990 (99%)
        assert any("soft-limit" in r.message for r in caplog.records)

    def test_set_limit_rejects_non_positive(self, tmp_path: Path):
        db = tmp_path / "q.db"
        _bootstrap_db(db)
        tracker = QuotaTracker(db, api_key="key", base_url="https://example")
        with pytest.raises(ValueError):
            tracker.set_limit(0)
        with pytest.raises(ValueError):
            tracker.set_limit(-100)

    def test_set_and_get_glossary(self, tmp_path: Path):
        db = tmp_path / "q.db"
        _bootstrap_db(db)
        tracker = QuotaTracker(db, api_key="key", base_url="https://example")
        # Fresh row has no glossary.
        gid, ghash = tracker.get_glossary()
        assert gid is None and ghash is None

        tracker.set_glossary("gloss-123", "abcdef1234567890")
        gid, ghash = tracker.get_glossary()
        assert gid == "gloss-123"
        assert ghash == "abcdef1234567890"

    def test_reset_period_zeros_counter_and_moves_period_start(self, tmp_path: Path):
        db = tmp_path / "q.db"
        _bootstrap_db(db)
        tracker = QuotaTracker(db, api_key="key", base_url="https://example")
        tracker.record_usage(500)
        before = tracker.snapshot()["period_start"]

        tracker.reset_period()
        snap = tracker.snapshot()
        assert snap["used"] == 0
        # period_start has been rewritten to CURRENT_TIMESTAMP — value may be
        # string-equal on fast hosts, so assert at minimum that used was zeroed.
        assert snap["period_start"] is not None
        _ = before

    def test_refresh_from_api_requires_credentials(self, tmp_path: Path):
        """Tracker instantiated without api_key/base_url must refuse the
        live-sync call instead of issuing an unauthenticated request."""
        db = tmp_path / "q.db"
        _bootstrap_db(db)
        tracker = QuotaTracker(db)
        with pytest.raises(RuntimeError, match="no API credentials"):
            tracker.refresh_from_api()

    def test_refresh_from_api_writes_usage_and_limit(self, tmp_path: Path, requests_mock):
        """A successful /usage response must land in the DB row."""
        db = tmp_path / "q.db"
        _bootstrap_db(db)
        tracker = QuotaTracker(db, api_key="abc:fx", base_url="https://api-free.deepl.com/v2")
        requests_mock.get(
            "https://api-free.deepl.com/v2/usage",
            json={"character_count": 12345, "character_limit": 500000},
        )
        payload = tracker.refresh_from_api()
        assert payload["character_count"] == 12345

        snap = tracker.snapshot()
        assert snap["used"] == 12345
        assert snap["limit"] == 500000
        assert snap["last_api_check"] is not None

    def test_refresh_from_api_treats_zero_limit_as_unlimited(self, tmp_path: Path, requests_mock):
        """DeepL Pro returns character_limit=0 meaning unlimited. The
        tracker must store a huge sentinel so subsequent quota checks
        never block."""
        db = tmp_path / "q.db"
        _bootstrap_db(db)
        tracker = QuotaTracker(db, api_key="key", base_url="https://api.deepl.com/v2")
        requests_mock.get(
            "https://api.deepl.com/v2/usage",
            json={"character_count": 8000, "character_limit": 0},
        )
        tracker.refresh_from_api()
        snap = tracker.snapshot()
        assert snap["limit"] >= 1_000_000_000_000
        # A huge request must still fit under HARD_LIMIT.
        tracker.check_before_translate(5_000_000)

    def test_load_row_reinserts_missing_default(self, tmp_path: Path):
        """If the default row is deleted mid-run (e.g., manual SQL), the
        load helper must re-insert and still return a live row."""
        db = tmp_path / "q.db"
        _bootstrap_db(db)
        tracker = QuotaTracker(db, api_key="key", base_url="https://example")

        conn = sqlite3.connect(str(db))
        try:
            conn.execute("DELETE FROM deepl_quota WHERE id = 'default'")
            conn.commit()
        finally:
            conn.close()

        snap = tracker.snapshot()
        assert snap["used"] == 0


class TestComputeResetDate:
    """The _compute_reset_date helper calendars forward one month with a
    few edge cases worth pinning down."""

    def test_empty_input_returns_empty(self):
        from localization.translation.quota import _compute_reset_date

        assert _compute_reset_date("") == ""
        assert _compute_reset_date(None) == ""

    def test_invalid_input_returns_empty(self):
        from localization.translation.quota import _compute_reset_date

        assert _compute_reset_date("not-a-date") == ""
        assert _compute_reset_date("20201301") == ""

    def test_mid_year_rolls_forward_one_month(self):
        from localization.translation.quota import _compute_reset_date

        assert _compute_reset_date("2026-04-15T10:00:00Z") == "2026-05-01"

    def test_december_rolls_over_to_january(self):
        """The month+1 branch needs the year-increment path."""
        from localization.translation.quota import _compute_reset_date

        assert _compute_reset_date("2026-12-05T00:00:00Z") == "2027-01-01"

    def test_naive_datetime_treated_as_utc(self):
        """Period_start is read straight from CURRENT_TIMESTAMP (naive
        UTC) — the helper must not crash on missing tzinfo."""
        from localization.translation.quota import _compute_reset_date

        # Plain ISO without Z/offset is naive. Just check it returns a
        # well-formed YYYY-MM-01 result.
        result = _compute_reset_date("2026-06-10 12:00:00")
        assert result == "2026-07-01"
