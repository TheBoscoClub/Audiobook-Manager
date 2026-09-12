"""Tests for scripts/verify-zh-corpus.py (bd Audiobook-Manager-536 tooling).

The classifier is the load-bearing part: it decides what gets DELETED. Both
directions are exercised — real Chinese must classify ok, English-as-Chinese
must classify corrupt — and the purge is proven surgical (only corrupt rows
go; ok/suspect rows and files survive).
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "verify_zh_corpus",
    Path(__file__).resolve().parents[2] / "scripts" / "verify-zh-corpus.py",
)
assert _SPEC is not None and _SPEC.loader is not None
vzc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(vzc)


ZH_VTT = "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n船长伸手去拿罗盘。\n\n00:00:03.000 --> 00:00:05.000\n你好，世界。\n"
EN_VTT = "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nThe captain reached for the compass.\n"
MIXED_VTT = (
    "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n"
    "Chapter One 第一章 spoken by the narrator in mostly English text here\n"
)


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


class TestClassifier:
    def test_real_chinese_is_ok(self, tmp_path: Path):
        verdict, frac = vzc.classify(_write(tmp_path, "a.zh-Hans.vtt", ZH_VTT))
        assert verdict == "ok"
        assert frac > 0.5

    def test_english_as_chinese_is_corrupt(self, tmp_path: Path):
        verdict, frac = vzc.classify(_write(tmp_path, "b.zh-Hans.vtt", EN_VTT))
        assert verdict == "corrupt"
        assert frac < vzc.CORRUPT_MAX_CJK_FRACTION

    def test_mixed_content_is_suspect_not_corrupt(self, tmp_path: Path):
        verdict, _frac = vzc.classify(_write(tmp_path, "c.zh-Hans.vtt", MIXED_VTT))
        assert verdict == "suspect"

    def test_headers_and_timestamps_do_not_dilute_the_fraction(self, tmp_path: Path):
        """WEBVTT scaffolding is Latin — counting it would misclassify real
        Chinese as suspect."""
        text = vzc.vtt_cue_text(_write(tmp_path, "d.vtt", ZH_VTT))
        assert "WEBVTT" not in text
        assert "-->" not in text
        # 12 hanzi of 15 chars — CJK punctuation (。，) deliberately doesn't
        # count, so the fraction is exactly 0.8 here.
        assert vzc.cjk_fraction(text) >= 0.8


@pytest.fixture
def corpus(tmp_path: Path):
    """A tiny DB + files: one ok, one corrupt, one suspect zh row + an en row."""
    db = tmp_path / "a.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE chapter_subtitles (audiobook_id INT, chapter_index INT, "
        "locale TEXT, vtt_path TEXT, stt_provider TEXT, translation_provider TEXT)"
    )
    ok = _write(tmp_path, "ok.zh-Hans.vtt", ZH_VTT)
    bad = _write(tmp_path, "bad.zh-Hans.vtt", EN_VTT)
    sus = _write(tmp_path, "sus.zh-Hans.vtt", MIXED_VTT)
    en = _write(tmp_path, "ok.en.vtt", EN_VTT)
    rows = [
        (1, 0, "zh-Hans", str(ok)),
        (1, 1, "zh-Hans", str(bad)),
        (2, 3, "zh-Hans", str(sus)),
        (1, 0, "en", str(en)),
    ]
    conn.executemany("INSERT INTO chapter_subtitles VALUES (?, ?, ?, ?, 'x', 'y')", rows)
    conn.commit()
    return conn, tmp_path, {"ok": ok, "bad": bad, "sus": sus, "en": en}


class TestScanAndPurge:
    def test_scan_buckets_correctly(self, corpus):
        conn, _tmp, _files = corpus
        result = vzc.scan(conn)
        assert [(b, c) for b, c, _p, _f in result["corrupt"]] == [(1, 1)]
        assert [(b, c) for b, c, _p, _f in result["suspect"]] == [(2, 3)]

    def test_purge_is_surgical(self, corpus):
        """Only the corrupt row and file go; ok/suspect/en all survive."""
        conn, _tmp, files = corpus
        result = vzc.scan(conn)
        deleted = vzc.purge(conn, result["corrupt"])
        conn.commit()
        assert deleted == 1
        assert not files["bad"].exists()
        assert files["ok"].exists() and files["sus"].exists() and files["en"].exists()
        remaining = conn.execute(
            "SELECT audiobook_id, chapter_index, locale FROM chapter_subtitles ORDER BY 1,2,3"
        ).fetchall()
        assert (1, 1, "zh-Hans") not in remaining
        assert len(remaining) == 3

    def test_chapter0_suspect_is_exempt(self, corpus):
        """Canned intro boilerplate on chapter 0 must not be flagged."""
        conn, tmp, _files = corpus
        ch0 = _write(tmp, "intro.zh-Hans.vtt", MIXED_VTT)
        conn.execute(
            "INSERT INTO chapter_subtitles VALUES (3, 0, 'zh-Hans', ?, 'x', 'y')",
            (str(ch0),),
        )
        conn.commit()
        result = vzc.scan(conn)
        assert all(b != 3 for b, _c, _p, _f in result["suspect"])
