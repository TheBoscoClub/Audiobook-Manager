"""Permanent translation exclusions must survive queue churn.

The mechanism exists because marking a book 'failed' in translation_queue
does NOT keep it out: every recovery step during the 2026-09-12 repair
campaign reset failed rows to pending, and an unwanted book came back each
time. An exclusion that a routine reset can undo is not an exclusion.
"""

from __future__ import annotations

import sqlite3

import pytest
from localization.exclusions import excluded_ids, is_excluded, load_exclusions


@pytest.fixture
def exclude_file(tmp_path):
    p = tmp_path / "translation-exclude.txt"
    p.write_text(
        "# comment line\n"
        "\n"
        "114213  # 266-hour omnibus, single 166-hour chapter\n"
        "B00EXAMPLE  # excluded by ASIN\n"
        "   \n",
        encoding="utf-8",
    )
    return p


class TestLoading:
    def test_parses_ids_asins_and_reasons(self, exclude_file):
        table = load_exclusions(exclude_file)
        assert set(table) == {"114213", "B00EXAMPLE"}
        assert "166-hour chapter" in table["114213"]

    def test_missing_file_is_empty_not_an_error(self, tmp_path):
        """An absent config must never halt the pipeline — nor silently
        start translating what the operator excluded (there is nothing to
        exclude when the file is absent)."""
        assert load_exclusions(tmp_path / "nope.txt") == {}

    def test_blank_and_comment_only_lines_are_ignored(self, tmp_path):
        p = tmp_path / "e.txt"
        p.write_text("# only a comment\n\n   \n", encoding="utf-8")
        assert load_exclusions(p) == {}


class TestMatching:
    def test_matches_by_id_and_by_asin(self, exclude_file):
        table = load_exclusions(exclude_file)
        assert is_excluded(114213, exclusions=table)
        assert is_excluded("114213", exclusions=table)
        assert is_excluded(999, asin="B00EXAMPLE", exclusions=table)

    def test_does_not_match_unrelated_books(self, exclude_file):
        table = load_exclusions(exclude_file)
        assert not is_excluded(115817, exclusions=table)
        assert not is_excluded(115817, asin="B00OTHER", exclusions=table)

    def test_empty_table_excludes_nothing(self):
        assert not is_excluded(114213, exclusions={})


class TestResolutionAgainstDb:
    @pytest.fixture
    def conn(self):
        c = sqlite3.connect(":memory:")
        c.execute("CREATE TABLE audiobooks (id INTEGER PRIMARY KEY, asin TEXT)")
        c.executemany(
            "INSERT INTO audiobooks VALUES (?, ?)",
            [(114213, "B00DOSTO"), (555, "B00EXAMPLE"), (777, "B00KEEP")],
        )
        c.commit()
        return c

    def test_resolves_both_forms_to_ids(self, conn, exclude_file):
        table = load_exclusions(exclude_file)
        assert excluded_ids(conn, table) == {114213, 555}

    def test_unknown_asin_resolves_to_nothing(self, conn):
        assert excluded_ids(conn, {"B00NOTHERE": "x"}) == set()

    def test_schema_without_asin_degrades_quietly(self, exclude_file):
        """A DB lacking the asin column must not break the drain — numeric
        ids still resolve."""
        c = sqlite3.connect(":memory:")
        c.execute("CREATE TABLE audiobooks (id INTEGER PRIMARY KEY)")
        c.commit()
        assert excluded_ids(c, load_exclusions(exclude_file)) == {114213}


class TestSurvivesQueueReset:
    """The property the whole mechanism exists for."""

    def test_reset_of_failed_rows_cannot_resurrect_an_exclusion(self, exclude_file):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE audiobooks (id INTEGER PRIMARY KEY, asin TEXT)")
        conn.execute("INSERT INTO audiobooks VALUES (114213, 'B00DOSTO')")
        conn.execute(
            "CREATE TABLE translation_queue (audiobook_id INTEGER, locale TEXT, state TEXT)"
        )
        conn.execute("INSERT INTO translation_queue VALUES (114213, 'zh-Hans', 'failed')")
        conn.commit()

        # The recovery step that kept resurrecting it:
        conn.execute("UPDATE translation_queue SET state='pending' WHERE state='failed'")
        conn.commit()
        assert conn.execute("SELECT state FROM translation_queue").fetchone()[0] == "pending"

        # The drain's retirement pass (mirrors scripts/batch-translate.py):
        table = load_exclusions(exclude_file)
        for ex_id in excluded_ids(conn, table):
            conn.execute(
                "UPDATE translation_queue SET state='excluded' "
                "WHERE audiobook_id = ? AND state IN ('pending','failed','processing')",
                (ex_id,),
            )
        conn.commit()
        assert conn.execute("SELECT state FROM translation_queue").fetchone()[0] == "excluded"

        # And a claim query for pending work now finds nothing.
        assert (
            conn.execute("SELECT COUNT(*) FROM translation_queue WHERE state='pending'").fetchone()[
                0
            ]
            == 0
        )


class TestAdminEndpoint:
    """The in-app control an admin actually uses."""

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        from flask import Flask

        from backend.api_modular import auth_shared
        from backend.api_modular import translations as tr

        # admin_required resolves the caller through get_current_user(); the
        # endpoint's authorization is exercised separately in the auth suite,
        # so here we stand in an admin and test the behaviour behind the gate.
        class _Admin:
            is_admin = True
            username = "testadmin"

        monkeypatch.setattr(auth_shared, "get_current_user", lambda: _Admin())

        db = tmp_path / "a.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE audiobooks (id INTEGER PRIMARY KEY, title TEXT)")
        conn.execute("INSERT INTO audiobooks VALUES (1, 'Some Book')")
        conn.execute(
            "CREATE TABLE translation_queue (audiobook_id INTEGER, locale TEXT, "
            "priority INTEGER DEFAULT 0, state TEXT, started_at TIMESTAMP, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "UNIQUE(audiobook_id, locale))"
        )
        conn.execute("INSERT INTO translation_queue VALUES (1,'zh-Hans',5,'pending',NULL,NULL)")
        conn.commit()
        conn.close()
        tr.init_translations_routes(str(db))  # applies migration 022
        app = Flask(__name__)
        app.register_blueprint(tr.translations_bp)
        app.config["TESTING"] = True
        return app.test_client(), str(db)

    def test_excluding_sets_the_flag_and_retires_the_queue_row(self, client):
        c, db = client
        r = c.post(
            "/api/audiobooks/1/translation-exclusion",
            json={"excluded": True, "reason": "266-hour omnibus, single 166-hour chapter"},
        )
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["queue_rows_updated"] == 1
        conn = sqlite3.connect(db)
        assert conn.execute("SELECT translation_excluded FROM audiobooks").fetchone()[0] == 1
        assert conn.execute("SELECT state FROM translation_queue").fetchone()[0] == "excluded"
        conn.close()

    def test_exclusion_requires_a_reason(self, client):
        c, _ = client
        r = c.post("/api/audiobooks/1/translation-exclusion", json={"excluded": True})
        assert r.status_code == 400

    def test_unknown_book_is_404(self, client):
        c, _ = client
        r = c.post(
            "/api/audiobooks/999/translation-exclusion", json={"excluded": True, "reason": "x"}
        )
        assert r.status_code == 404

    def test_clearing_restores_the_book_to_the_queue(self, client):
        c, db = client
        c.post("/api/audiobooks/1/translation-exclusion", json={"excluded": True, "reason": "x"})
        r = c.post("/api/audiobooks/1/translation-exclusion", json={"excluded": False})
        assert r.status_code == 200
        conn = sqlite3.connect(db)
        assert conn.execute("SELECT translation_excluded FROM audiobooks").fetchone()[0] == 0
        assert conn.execute("SELECT state FROM translation_queue").fetchone()[0] == "pending"
        conn.close()

    def test_status_endpoint_reports_the_mark(self, client):
        c, _ = client
        c.post(
            "/api/audiobooks/1/translation-exclusion",
            json={"excluded": True, "reason": "unsuitable source"},
        )
        body = c.get("/api/audiobooks/1/translation-exclusion").get_json()
        assert body["excluded"] is True
        assert body["reason"] == "unsuitable source"
        assert body["excluded_at"]

    def test_db_flag_alone_excludes_without_any_config_file(self, client, tmp_path):
        """The app-set flag must work with no /etc file present at all."""
        c, db = client
        c.post("/api/audiobooks/1/translation-exclusion", json={"excluded": True, "reason": "x"})
        conn = sqlite3.connect(db)
        assert excluded_ids(conn, load_exclusions(tmp_path / "absent.txt")) == {1}
        conn.close()
