"""
Admin Author/Narrator Correction API Module

Provides admin-only endpoints for correcting author and narrator data:
- Rename: Update name and sort_name for an author or narrator
- Merge: Merge duplicate authors/narrators into a single target
- Reassign: Replace a book's entire author or narrator list

All endpoints require admin privileges (@admin_if_enabled).
After any modification, flat author/narrator columns on affected audiobooks
rows are regenerated from the normalized junction tables.

Endpoints:
    PUT  /api/admin/authors/<id>          - Rename author
    POST /api/admin/authors/merge         - Merge duplicate authors
    PUT  /api/admin/books/<id>/authors    - Reassign book's authors
    PUT  /api/admin/narrators/<id>        - Rename narrator
    POST /api/admin/narrators/merge       - Merge duplicate narrators
    PUT  /api/admin/books/<id>/narrators  - Reassign book's narrators
"""

import sqlite3

from flask import Blueprint, current_app, jsonify, request

from .auth import admin_if_enabled

# Blueprint for admin author/narrator correction routes
admin_authors_bp = Blueprint("admin_authors", __name__, url_prefix="/api/admin")


def init_admin_authors_routes(database_path: str) -> None:
    """Initialize admin author routes (no-op, kept for API compatibility).

    Database path is now resolved at request time via current_app.config.
    """
    pass


def _get_db() -> sqlite3.Connection:
    """Get library database connection from current Flask app config."""
    db_path = current_app.config.get("DATABASE_PATH")
    if db_path is None:
        raise RuntimeError("DATABASE_PATH not configured in Flask app.")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


# ============================================================
# Flat column regeneration helpers
# ============================================================


def regenerate_flat_author(conn: sqlite3.Connection, book_id: int) -> None:
    """Rebuild flat author string from junction table.

    Updates the audiobooks.author column from book_authors join,
    which triggers the existing FTS update trigger.
    """
    rows = conn.execute(
        "SELECT a.name FROM authors a "
        "JOIN book_authors ba ON a.id = ba.author_id "
        "WHERE ba.book_id = ? ORDER BY ba.position",
        (book_id,),
    ).fetchall()
    flat = ", ".join(r["name"] for r in rows) if rows else None
    conn.execute("UPDATE audiobooks SET author = ? WHERE id = ?", (flat, book_id))


def regenerate_flat_narrator(conn: sqlite3.Connection, book_id: int) -> None:
    """Rebuild flat narrator string from junction table.

    Updates the audiobooks.narrator column from book_narrators join,
    which triggers the existing FTS update trigger.
    """
    rows = conn.execute(
        "SELECT n.name FROM narrators n "
        "JOIN book_narrators bn ON n.id = bn.narrator_id "
        "WHERE bn.book_id = ? ORDER BY bn.position",
        (book_id,),
    ).fetchall()
    flat = ", ".join(r["name"] for r in rows) if rows else None
    conn.execute("UPDATE audiobooks SET narrator = ? WHERE id = ?", (flat, book_id))


def _get_affected_book_ids(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: int,
) -> list[int]:
    """Get all book IDs associated with an author or narrator."""
    if entity_type == "author":
        table = "book_authors"
        col = "author_id"
    else:
        table = "book_narrators"
        col = "narrator_id"
    rows = conn.execute(  # nosec B608  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
        f"SELECT book_id FROM {table} WHERE {col} = ?",  # noqa: S608  # nosec B608
        (entity_id,),
    ).fetchall()
    return [r["book_id"] for r in rows]


def _author_to_dict(row: sqlite3.Row) -> dict:
    """Convert an author row to a dict."""
    return {"id": row["id"], "name": row["name"], "sort_name": row["sort_name"]}


def _narrator_to_dict(row: sqlite3.Row) -> dict:
    """Convert a narrator row to a dict."""
    return {"id": row["id"], "name": row["name"], "sort_name": row["sort_name"]}


def _book_with_authors(conn: sqlite3.Connection, book_id: int) -> dict | None:
    """Get a book dict with its authors array."""
    book = conn.execute(
        "SELECT id, title, author FROM audiobooks WHERE id = ?",
        (book_id,),
    ).fetchone()
    if not book:
        return None
    authors = conn.execute(
        "SELECT a.id, a.name, a.sort_name, ba.position "
        "FROM authors a JOIN book_authors ba ON a.id = ba.author_id "
        "WHERE ba.book_id = ? ORDER BY ba.position",
        (book_id,),
    ).fetchall()
    return {
        "id": book["id"],
        "title": book["title"],
        "author": book["author"],
        "authors": [
            {
                "id": a["id"],
                "name": a["name"],
                "sort_name": a["sort_name"],
                "position": a["position"],
            }
            for a in authors
        ],
    }


def _book_with_narrators(conn: sqlite3.Connection, book_id: int) -> dict | None:
    """Get a book dict with its narrators array."""
    book = conn.execute(
        "SELECT id, title, narrator FROM audiobooks WHERE id = ?",
        (book_id,),
    ).fetchone()
    if not book:
        return None
    narrators = conn.execute(
        "SELECT n.id, n.name, n.sort_name, bn.position "
        "FROM narrators n JOIN book_narrators bn ON n.id = bn.narrator_id "
        "WHERE bn.book_id = ? ORDER BY bn.position",
        (book_id,),
    ).fetchall()
    return {
        "id": book["id"],
        "title": book["title"],
        "narrator": book["narrator"],
        "narrators": [
            {
                "id": n["id"],
                "name": n["name"],
                "sort_name": n["sort_name"],
                "position": n["position"],
            }
            for n in narrators
        ],
    }


# ============================================================
# Author endpoints
# ============================================================


@admin_authors_bp.route("/authors/<int:author_id>", methods=["PUT"])
@admin_if_enabled
def rename_author(author_id: int):
    """Rename an author and update sort_name.

    Request JSON:
        {"name": "Stephen King", "sort_name": "King, Stephen"}

    Returns 200 with updated author object, or 404 if not found.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    name = data.get("name")
    sort_name = data.get("sort_name")
    if not name and not sort_name:
        return jsonify({"error": "At least one of 'name' or 'sort_name' required"}), 400

    conn = _get_db()
    try:
        author = conn.execute(
            "SELECT * FROM authors WHERE id = ?", (author_id,)
        ).fetchone()
        if not author:
            return jsonify({"error": "Author not found"}), 404

        # Build update
        updates = []
        params = []
        if name:
            updates.append("name = ?")
            params.append(name)
        if sort_name:
            updates.append("sort_name = ?")
            params.append(sort_name)
        params.append(author_id)

        conn.execute(  # nosec B608  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            f"UPDATE authors SET {', '.join(updates)} WHERE id = ?",  # noqa: S608  # nosec B608
            params,
        )

        # Regenerate flat author column on all affected books
        book_ids = _get_affected_book_ids(conn, "author", author_id)
        for bid in book_ids:
            regenerate_flat_author(conn, bid)

        conn.commit()

        updated = conn.execute(
            "SELECT * FROM authors WHERE id = ?", (author_id,)
        ).fetchone()
        return jsonify(_author_to_dict(updated)), 200
    finally:
        conn.close()


def _merge_entities(
    conn,
    source_ids,
    target_id,
    junction_table,
    entity_id_col,
    entity_table,
    regenerate_fn,
):
    """Merge duplicate authors/narrators by reassigning junctions.

    Returns (books_reassigned, affected_book_ids).
    """
    books_reassigned = 0
    affected_book_ids = set()

    for sid in source_ids:
        links = conn.execute(
            f"SELECT book_id, position FROM {junction_table} "  # nosec B608
            f"WHERE {entity_id_col} = ?",
            (sid,),
        ).fetchall()

        for link in links:
            bid = link["book_id"]
            affected_book_ids.add(bid)

            existing = conn.execute(
                f"SELECT 1 FROM {junction_table} "  # nosec B608
                f"WHERE book_id = ? AND {entity_id_col} = ?",
                (bid, target_id),
            ).fetchone()

            if existing:
                conn.execute(
                    f"DELETE FROM {junction_table} "  # nosec B608
                    f"WHERE book_id = ? AND {entity_id_col} = ?",
                    (bid, sid),
                )
            else:
                conn.execute(
                    f"UPDATE {junction_table} SET {entity_id_col} = ? "  # nosec B608
                    f"WHERE book_id = ? AND {entity_id_col} = ?",
                    (target_id, bid, sid),
                )
            books_reassigned += 1

        conn.execute(  # nosec B608  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            f"DELETE FROM {entity_table} WHERE id = ?",  # nosec B608
            (sid,),
        )

    for bid in affected_book_ids:
        regenerate_fn(conn, bid)

    return books_reassigned


def _validate_merge_request(data, entity_label):
    """Validate merge request body. Returns (source_ids, target_id, error_response)."""
    if not data:
        return None, None, (jsonify({"error": "Request body required"}), 400)

    source_ids = data.get("source_ids", [])
    target_id = data.get("target_id")

    if not source_ids or target_id is None:
        return (
            None,
            None,
            (
                jsonify(
                    {
                        "error": f"'source_ids' and 'target_id' required for {entity_label} merge"
                    }
                ),
                400,
            ),
        )
    if target_id in source_ids:
        return (
            None,
            None,
            (
                jsonify(
                    {
                        "error": f"target_id cannot be in source_ids for {entity_label} merge"
                    }
                ),
                400,
            ),
        )
    return source_ids, target_id, None


def _verify_entities_exist(conn, entity_table, target_id, source_ids, label):
    """Verify target and all source entities exist. Returns error response or None."""
    target = conn.execute(  # nosec B608  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
        f"SELECT * FROM {entity_table} WHERE id = ?",  # nosec B608
        (target_id,),
    ).fetchone()
    if not target:
        return jsonify({"error": f"Target {label} not found"}), 404

    for sid in source_ids:
        src = conn.execute(  # nosec B608  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            f"SELECT id FROM {entity_table} WHERE id = ?",  # nosec B608
            (sid,),
        ).fetchone()
        if not src:
            return jsonify({"error": f"Source {label} {sid} not found"}), 404

    return None


@admin_authors_bp.route("/authors/merge", methods=["POST"])
@admin_if_enabled
def merge_authors():
    """Merge duplicate authors into a single target.

    Request JSON:
        {"source_ids": [3, 7], "target_id": 1}

    Reassigns all book_authors from source authors to target,
    then deletes source authors.

    Returns 200 with target author and count of books reassigned.
    """
    source_ids, target_id, err = _validate_merge_request(request.get_json(), "author")
    if err:
        return err

    conn = _get_db()
    try:
        err = _verify_entities_exist(conn, "authors", target_id, source_ids, "author")
        if err:
            return err

        books_reassigned = _merge_entities(
            conn,
            source_ids,
            target_id,
            "book_authors",
            "author_id",
            "authors",
            regenerate_flat_author,
        )
        conn.commit()

        updated_target = conn.execute(
            "SELECT * FROM authors WHERE id = ?", (target_id,)
        ).fetchone()

        return jsonify(
            {
                "author": _author_to_dict(updated_target),
                "books_reassigned": books_reassigned,
            }
        ), 200
    finally:
        conn.close()


@admin_authors_bp.route("/books/<int:book_id>/authors", methods=["PUT"])
@admin_if_enabled
def reassign_book_authors(book_id: int):
    """Reassign a book's authors (full replacement).

    Request JSON:
        {"author_ids": [1, 2], "positions": [0, 1]}

    Returns 200 with updated book object including authors array.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    author_ids = data.get("author_ids", [])
    positions = data.get("positions", [])

    if not author_ids:
        return jsonify({"error": "'author_ids' required"}), 400
    if len(positions) != len(author_ids):
        return jsonify({"error": "'positions' must match 'author_ids' length"}), 400

    conn = _get_db()
    try:
        # Verify book exists
        book = conn.execute(
            "SELECT id FROM audiobooks WHERE id = ?", (book_id,)
        ).fetchone()
        if not book:
            return jsonify({"error": "Book not found"}), 404

        # Verify all authors exist
        for aid in author_ids:
            author = conn.execute(
                "SELECT id FROM authors WHERE id = ?", (aid,)
            ).fetchone()
            if not author:
                return jsonify({"error": f"Author {aid} not found"}), 404

        # Remove all existing author links for this book
        conn.execute("DELETE FROM book_authors WHERE book_id = ?", (book_id,))

        # Insert new links
        for aid, pos in zip(author_ids, positions):
            conn.execute(
                "INSERT INTO book_authors"
                " (book_id, author_id, position) VALUES (?, ?, ?)",
                (book_id, aid, pos),
            )

        # Regenerate flat author column
        regenerate_flat_author(conn, book_id)

        conn.commit()

        result = _book_with_authors(conn, book_id)
        return jsonify(result), 200
    finally:
        conn.close()


# ============================================================
# Narrator endpoints (identical pattern)
# ============================================================


@admin_authors_bp.route("/narrators/<int:narrator_id>", methods=["PUT"])
@admin_if_enabled
def rename_narrator(narrator_id: int):
    """Rename a narrator and update sort_name.

    Request JSON:
        {"name": "Frank Muller", "sort_name": "Muller, Frank"}

    Returns 200 with updated narrator object, or 404 if not found.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    name = data.get("name")
    sort_name = data.get("sort_name")
    if not name and not sort_name:
        return jsonify({"error": "At least one of 'name' or 'sort_name' required"}), 400

    conn = _get_db()
    try:
        narrator = conn.execute(
            "SELECT * FROM narrators WHERE id = ?", (narrator_id,)
        ).fetchone()
        if not narrator:
            return jsonify({"error": "Narrator not found"}), 404

        updates = []
        params = []
        if name:
            updates.append("name = ?")
            params.append(name)
        if sort_name:
            updates.append("sort_name = ?")
            params.append(sort_name)
        params.append(narrator_id)

        conn.execute(  # nosec B608  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            f"UPDATE narrators SET {', '.join(updates)} WHERE id = ?",  # noqa: S608  # nosec B608
            params,
        )

        # Regenerate flat narrator column on all affected books
        book_ids = _get_affected_book_ids(conn, "narrator", narrator_id)
        for bid in book_ids:
            regenerate_flat_narrator(conn, bid)

        conn.commit()

        updated = conn.execute(
            "SELECT * FROM narrators WHERE id = ?", (narrator_id,)
        ).fetchone()
        return jsonify(_narrator_to_dict(updated)), 200
    finally:
        conn.close()


@admin_authors_bp.route("/narrators/merge", methods=["POST"])
@admin_if_enabled
def merge_narrators():
    """Merge duplicate narrators into a single target.

    Request JSON:
        {"source_ids": [3, 7], "target_id": 1}

    Reassigns all book_narrators from source narrators to target,
    then deletes source narrators.

    Returns 200 with target narrator and count of books reassigned.
    """
    source_ids, target_id, err = _validate_merge_request(request.get_json(), "narrator")
    if err:
        return err

    conn = _get_db()
    try:
        err = _verify_entities_exist(
            conn, "narrators", target_id, source_ids, "narrator"
        )
        if err:
            return err

        books_reassigned = _merge_entities(
            conn,
            source_ids,
            target_id,
            "book_narrators",
            "narrator_id",
            "narrators",
            regenerate_flat_narrator,
        )
        conn.commit()

        updated_target = conn.execute(
            "SELECT * FROM narrators WHERE id = ?", (target_id,)
        ).fetchone()

        return jsonify(
            {
                "narrator": _narrator_to_dict(updated_target),
                "books_reassigned": books_reassigned,
            }
        ), 200
    finally:
        conn.close()


@admin_authors_bp.route("/books/<int:book_id>/narrators", methods=["PUT"])
@admin_if_enabled
def reassign_book_narrators(book_id: int):
    """Reassign a book's narrators (full replacement).

    Request JSON:
        {"narrator_ids": [1, 2], "positions": [0, 1]}

    Returns 200 with updated book object including narrators array.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    narrator_ids = data.get("narrator_ids", [])
    positions = data.get("positions", [])

    if not narrator_ids:
        return jsonify({"error": "'narrator_ids' required"}), 400
    if len(positions) != len(narrator_ids):
        return jsonify({"error": "'positions' must match 'narrator_ids' length"}), 400

    conn = _get_db()
    try:
        book = conn.execute(
            "SELECT id FROM audiobooks WHERE id = ?", (book_id,)
        ).fetchone()
        if not book:
            return jsonify({"error": "Book not found"}), 404

        for nid in narrator_ids:
            narrator = conn.execute(
                "SELECT id FROM narrators WHERE id = ?", (nid,)
            ).fetchone()
            if not narrator:
                return jsonify({"error": f"Narrator {nid} not found"}), 404

        conn.execute("DELETE FROM book_narrators WHERE book_id = ?", (book_id,))

        for nid, pos in zip(narrator_ids, positions):
            conn.execute(
                "INSERT INTO book_narrators"
                " (book_id, narrator_id, position) VALUES (?, ?, ?)",
                (book_id, nid, pos),
            )

        regenerate_flat_narrator(conn, book_id)

        conn.commit()

        result = _book_with_narrators(conn, book_id)
        return jsonify(result), 200
    finally:
        conn.close()
