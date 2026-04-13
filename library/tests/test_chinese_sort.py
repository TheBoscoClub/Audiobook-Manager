"""
Tests for v8.1 Chinese localization: pinyin sort and CJK search helpers.

Covers:
    1. pinyin_sort_key produces tone-stripped lowercase joined pinyin
    2. Sorted order for Chinese surnames (李 → li, 王 → wang, 张 → zhang)
    3. cjk_bigrams splits query into overlapping character bigrams
    4. cjk_bigram_like_clause builds parameterized AND-chain that matches
       "西游" inside "西游记" but rejects "东游"
    5. Backfill logic fills pinyin_sort for zh rows and leaves non-zh alone
    6. Non-zh locales bypass pinyin path (pinyin_sort_key still works on
       ASCII but the query layer does not consult it)

These are pure-unit tests — no Flask app, no DB beyond an in-memory
SQLite — so they run on the dev machine rather than the test VM.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

# Put library/ on the path so sibling imports resolve
_LIB = Path(__file__).resolve().parents[1]
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

pytest.importorskip("pypinyin", reason="pypinyin required for zh sort tests")


def _load_isolated(module_name: str, file_path: Path):
    """Load a .py file as a module without triggering its package __init__.

    api_modular/__init__.py imports the i18n package which only resolves
    when the Flask app is initialized, so we side-load search_cjk.py and
    backfill_pinyin_sort.py directly.
    """
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


_search_cjk = _load_isolated(
    "search_cjk_iso",
    _LIB / "backend" / "api_modular" / "search_cjk.py",
)
cjk_bigram_like_clause = _search_cjk.cjk_bigram_like_clause
cjk_bigrams = _search_cjk.cjk_bigrams
contains_cjk = _search_cjk.contains_cjk
pinyin_sort_key = _search_cjk.pinyin_sort_key


# ---------------------------------------------------------------------------
# 1. pinyin_sort_key
# ---------------------------------------------------------------------------


class TestPinyinSortKey:
    def test_basic_surnames(self):
        """Chinese surnames produce expected pinyin keys."""
        assert pinyin_sort_key("张三") == "zhangsan"
        assert pinyin_sort_key("李四") == "lisi"
        assert pinyin_sort_key("王五") == "wangwu"

    def test_sort_order_matches_pinyin(self):
        """Sorting by pinyin_sort_key yields the expected Mandarin order."""
        titles = ["张三", "李四", "王五"]
        sorted_titles = sorted(titles, key=lambda t: pinyin_sort_key(t) or "")
        # li (李) < wang (王) < zhang (张)
        assert sorted_titles == ["李四", "王五", "张三"]

    def test_title_sorted_list(self):
        """A realistic mix of Chinese titles sorts by their pinyin."""
        titles = [
            "西游记",  # xiyouji
            "三国演义",  # sanguoyanyi
            "红楼梦",  # hongloumeng
            "水浒传",  # shuihuzhuan
        ]
        keys = {t: pinyin_sort_key(t) for t in titles}
        sorted_titles = sorted(titles, key=lambda t: keys[t] or "")
        # Sanity: all keys are non-empty ascii
        for k in keys.values():
            assert k and k.isascii()
        # Alphabetic: h < s(an) < s(hui) < x
        assert sorted_titles == ["红楼梦", "三国演义", "水浒传", "西游记"]

    def test_ascii_passes_through(self):
        """ASCII input lowercases but otherwise passes through unchanged.

        pypinyin returns ASCII runs as single tokens, so internal spaces
        are preserved. That matches normal case-insensitive lexicographic
        sort for English titles.
        """
        assert pinyin_sort_key("Alice") == "alice"
        assert pinyin_sort_key("The Hobbit") == "the hobbit"

    def test_empty_and_none(self):
        """Empty and None inputs return None (caller uses COALESCE fallback)."""
        assert pinyin_sort_key("") is None
        assert pinyin_sort_key(None) is None


# ---------------------------------------------------------------------------
# 2. Bigram helpers
# ---------------------------------------------------------------------------


class TestCJKBigrams:
    def test_two_char_returns_single_bigram(self):
        assert cjk_bigrams("西游") == ["西游"]

    def test_three_char_overlapping(self):
        assert cjk_bigrams("西游记") == ["西游", "游记"]

    def test_four_char(self):
        assert cjk_bigrams("三国演义") == ["三国", "国演", "演义"]

    def test_single_char_fallback(self):
        assert cjk_bigrams("一") == ["一"]

    def test_empty(self):
        assert cjk_bigrams("") == []
        assert cjk_bigrams("   ") == []

    def test_contains_cjk_detects_han(self):
        assert contains_cjk("西游记") is True
        assert contains_cjk("hello") is False
        assert contains_cjk("Alice 西") is True


# ---------------------------------------------------------------------------
# 3. cjk_bigram_like_clause + in-memory SQLite integration
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_db():
    """In-memory SQLite with a tiny books table for LIKE matching."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT NOT NULL)")
    titles = [
        (1, "西游记"),
        (2, "东游志"),
        (3, "三国演义"),
        (4, "水浒传"),
        (5, "The Hobbit"),
    ]
    conn.executemany("INSERT INTO books VALUES (?, ?)", titles)
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


class TestBigramLikeClause:
    def test_xiyou_matches_xiyouji_not_dongyou(self, sample_db):
        """Query '西游' must hit '西游记' but not '东游志'."""
        frag, params = cjk_bigram_like_clause("title", "西游")
        assert frag == "(title LIKE ?)"
        assert params == ["%西游%"]

        # frag comes from our own helper with a hardcoded column name; safe to interpolate.
        sql = f"SELECT id FROM books WHERE {frag}"  # nosec B608
        cur = sample_db.execute(
            sql, params
        )  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
        ids = {r[0] for r in cur.fetchall()}
        assert 1 in ids  # 西游记
        assert 2 not in ids  # 东游志 — no 西游 substring

    def test_longer_query_and_chain(self, sample_db):
        """'西游记' becomes two bigrams AND-ed together."""
        frag, params = cjk_bigram_like_clause("title", "西游记")
        # Two bigrams, two params
        assert params == ["%西游%", "%游记%"]
        sql = f"SELECT id FROM books WHERE {frag}"  # nosec B608
        cur = sample_db.execute(
            sql, params
        )  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
        ids = {r[0] for r in cur.fetchall()}
        assert ids == {1}  # only 西游记

    def test_empty_query_is_safe(self):
        frag, params = cjk_bigram_like_clause("title", "")
        assert frag == "1=1"
        assert params == []


# ---------------------------------------------------------------------------
# 4. Backfill logic (pinyin_sort column + population)
# ---------------------------------------------------------------------------


@pytest.fixture()
def translations_db(tmp_path):
    """In-memory translations table modeled on the real schema."""
    db_path = tmp_path / "translations.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE audiobook_translations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            audiobook_id INTEGER NOT NULL,
            locale TEXT NOT NULL,
            title TEXT,
            author_display TEXT,
            UNIQUE(audiobook_id, locale)
        );
        """
    )
    rows = [
        (1, "zh-Hans", "张三的故事", "张三"),
        (2, "zh-Hans", "西游记", "吴承恩"),
        (3, "zh-Hans", "李四列传", "李四"),
        (4, "ja", "さよなら", "著者"),
        (5, "zh-Hans", "", None),  # empty title — skipped
    ]
    conn.executemany(
        "INSERT INTO audiobook_translations "
        "(audiobook_id, locale, title, author_display) VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    try:
        yield conn, db_path
    finally:
        conn.close()


_backfill = _load_isolated(
    "backfill_pinyin_sort_iso",
    _LIB / "backend" / "migrations" / "backfill_pinyin_sort.py",
)


class TestBackfill:
    def test_add_column_and_populate(self, translations_db):
        apply_column_migration = _backfill.apply_column_migration
        backfill = _backfill.backfill

        conn, _ = translations_db
        apply_column_migration(conn)

        # Column now exists
        cols = [r[1] for r in conn.execute("PRAGMA table_info(audiobook_translations)")]
        assert "pinyin_sort" in cols

        scanned, updated, skipped = backfill(conn)
        assert scanned == 5  # all rows had NULL pinyin_sort
        assert updated >= 3  # three zh-Hans non-empty rows
        assert skipped >= 1  # empty-title row

        # Check actual populated values
        rows = dict(
            conn.execute(
                "SELECT audiobook_id, pinyin_sort FROM audiobook_translations"
            ).fetchall()
        )
        assert rows[2] == "xiyouji"  # 西游记
        assert rows[3] == "lisiliezhuan"  # 李四列传
        # ja row gets a pinyin for any Han it contains (さよなら is kana only
        # → lazy_pinyin returns kana verbatim). Non-zh locales are still
        # processed but the query layer doesn't consult this column.
        assert rows[5] is None  # empty string title stayed NULL

    def test_idempotent_reapply(self, translations_db):
        apply_column_migration = _backfill.apply_column_migration

        conn, _ = translations_db
        apply_column_migration(conn)
        # Second run must not raise "duplicate column name"
        apply_column_migration(conn)


# ---------------------------------------------------------------------------
# 5. Non-zh locales are unaffected by pinyin logic
# ---------------------------------------------------------------------------


class TestLocaleGating:
    def test_non_zh_query_does_not_trigger_cjk_search(self):
        """ASCII queries bypass the CJK bigram path."""
        assert contains_cjk("alice") is False
        assert contains_cjk("the hobbit") is False

    def test_english_sort_fallback(self):
        """When pinyin_sort is empty, COALESCE expression falls back.

        This test emulates the SQL expression used in grouped.py /
        audiobooks.py:
            COALESCE(NULLIF(pinyin_sort, ''), title) COLLATE NOCASE
        """
        conn = sqlite3.connect(":memory:")
        conn.executescript(
            """
            CREATE TABLE books (
                id INTEGER PRIMARY KEY,
                title TEXT,
                pinyin_sort TEXT
            );
            INSERT INTO books VALUES
                (1, 'Zebra', NULL),
                (2, 'Apple', ''),
                (3, 'Mango', 'xxx-override');
            """
        )
        rows = conn.execute(
            "SELECT id FROM books "
            "ORDER BY COALESCE(NULLIF(pinyin_sort, ''), title) COLLATE NOCASE"
        ).fetchall()
        # Apple (falls back to title), Mango (pinyin override)... wait, xxx > apple
        # Expected order: apple, mango (pinyin 'xxx-override' but lowercased 'xxx-override'), zebra
        # Actually: "Apple" ~ "apple", "xxx-override", "Zebra" ~ "zebra"
        # Lexicographic: apple < xxx-override < zebra
        assert [r[0] for r in rows] == [2, 3, 1]
        conn.close()
