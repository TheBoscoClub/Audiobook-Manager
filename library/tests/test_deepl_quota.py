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

from localization.translation.deepl_translate import (
    DeepLTranslator,
    _hash_source,
)
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
        api_key="test-key:fx",
        db_path=quota_db,
        tracker=tracker,
        enable_glossary=False,
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
        api_key="test-key:fx",
        db_path=quota_db,
        tracker=tracker,
        enable_glossary=False,
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
        api_key="test-key:fx",
        db_path=quota_db,
        tracker=tracker,
        enable_glossary=False,
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
        api_key="test-key:fx",
        db_path=quota_db,
        tracker=tracker,
        glossary_id="glossary-abc-123",
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
