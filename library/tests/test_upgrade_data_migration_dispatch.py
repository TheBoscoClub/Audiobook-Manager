"""Regression test: upgrade.sh data-migration dispatcher invokes run_migration.

Root cause of the v8.3.4 QA regression: migrations 003/006/007 define a
`run_migration` bash function but the dispatcher only did `source "$migration"`,
which runs top-level commands but does NOT call a defined function. So the
migration silently no-op'd and the streaming worker crashed on first request
with "no such column: s.retry_count".

This test forges a DB that's missing the retry_count column (mimicking QA's
pre-upgrade state), runs `apply_data_migrations` via upgrade.sh, and asserts
the ALTER TABLE actually fired. Before the dispatcher fix this test fails;
after the fix it passes.
"""

import sqlite3
import subprocess
import textwrap
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
UPGRADE_SH = PROJECT_ROOT / "upgrade.sh"


def _make_prior_schema_db(db_path: Path) -> None:
    """Create a DB with pre-v8.3.4 streaming_segments shape (no retry_count)."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS audiobooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asin TEXT,
            title TEXT
        );
        CREATE TABLE streaming_segments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            audiobook_id INTEGER NOT NULL,
            chapter_index INTEGER NOT NULL,
            segment_index INTEGER NOT NULL,
            locale TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending',
            priority INTEGER NOT NULL DEFAULT 2,
            worker_id TEXT,
            vtt_content TEXT,
            audio_path TEXT,
            error TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            source_vtt_content TEXT,
            UNIQUE(audiobook_id, chapter_index, segment_index, locale)
        );
        """
    )
    conn.commit()
    conn.close()


def _column_exists(db_path: Path, table: str, column: str) -> bool:
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(f"PRAGMA table_info({table});").fetchall()
    conn.close()
    return any(r[1] == column for r in rows)


def test_data_migration_dispatcher_invokes_function_pattern(tmp_path):
    """apply_data_migrations must invoke run_migration() functions defined
    by sourced migration scripts — otherwise function-pattern migrations
    silently no-op and schema drifts from code expectations.
    """
    # Stage DB at the fallback path (${AUDIOBOOKS_VAR_DIR}/db/audiobooks.db)
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    db_path = db_dir / "audiobooks.db"
    _make_prior_schema_db(db_path)
    assert not _column_exists(db_path, "streaming_segments", "retry_count"), (
        "test setup: prior-schema DB should NOT have retry_count"
    )

    # Minimal fake "installed" target at v8.3.3 so the 8.3.4 boundary triggers
    target_dir = tmp_path / "installed"
    (target_dir / "library").mkdir(parents=True)
    (target_dir / "VERSION").write_text("8.3.3\n")

    # Source upgrade.sh in source-only mode, then invoke the dispatcher
    # with DB_PATH pointing at our forged DB (takes precedence over any
    # /etc/audiobooks/audiobooks.conf on the host).
    harness = textwrap.dedent(
        f"""
        set -e
        export UPGRADE_SH_SOURCE_ONLY=1
        export DB_PATH={db_path}
        source {UPGRADE_SH}
        apply_data_migrations {PROJECT_ROOT} {target_dir} "" "false"
        """
    ).strip()

    result = subprocess.run(
        ["bash", "-c", harness],
        capture_output=True,
        text=True,
        timeout=30,
    )

    has_col = _column_exists(db_path, "streaming_segments", "retry_count")
    assert has_col, (
        f"apply_data_migrations did not add retry_count column.\n"
        f"exit={result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}\n"
    )
