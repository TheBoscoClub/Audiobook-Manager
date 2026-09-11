"""Teardown of legacy vendor artifacts (Audiobook-Manager-4uj).

This module is the ONE place in runtime code allowed to name the removed
machine-translation vendor — it must, to drop that vendor's table. It is
exempted by name in ``tests/test_source_guards.py``; nothing else is.

Mirrors migration file ``backend/migrations/021_drop_deepl_quota.sql``.
"""


def migrate_drop_legacy_quota(conn):
    """Migration 021: drop the removed vendor's quota table.

    Quota tracking was a property of that vendor's billing model; nothing
    reads the table any more. Idempotent — runs at every API startup like
    the other in-code migrations.
    """
    conn.execute("DROP TABLE IF EXISTS deepl_quota")
