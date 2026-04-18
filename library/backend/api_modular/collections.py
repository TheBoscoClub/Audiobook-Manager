"""
Dynamic collection system (v8).

Collections are built from enrichment data (genres, eras, topics) and series
metadata. Fixed top-level categories with auto-generated subcategories.

The API returns a tree structure; the audiobooks endpoint consumes the flat
COLLECTIONS dict for filtering by any collection ID.
"""

import re
from pathlib import Path

from flask import Blueprint, Response, jsonify

from .auth import guest_allowed
from .core import get_db

collections_bp = Blueprint("collections", __name__)


# ─── Genre classification ────────────────────────────────────────────────────
# Reverse map: display-name genre → "fiction" or "non-fiction".
# Genres not in this map are classified as "uncategorized" and appear under
# whichever top-level category they best fit, or are omitted.

# These come from scanner/metadata_utils.py GENRE_DISPLAY_NAMES values
FICTION_GENRES = frozenset(
    {"Mystery", "Science Fiction", "Fantasy", "Literary Fiction", "Horror", "Romance"}
)

NONFICTION_GENRES = frozenset(
    {
        "Biographies & Memoirs",
        "History",
        "Science",
        "Philosophy",
        "Personal Development",
        "Business & Careers",
        "True Crime",
    }
)


def _slugify(name: str) -> str:
    """Convert a genre/era/topic name to a URL-safe collection ID."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def _genre_query(genre_name: str) -> str:
    """SQL WHERE clause for books matching an exact genre name."""
    safe = genre_name.replace("'", "''")
    return f"""id IN (
        SELECT ag.audiobook_id FROM audiobook_genres ag
        JOIN genres g ON ag.genre_id = g.id
        WHERE g.name = '{safe}'
    )"""  # nosec B608  # noqa: S608


def _multi_genre_query(genre_names: list[str]) -> str:
    """SQL WHERE clause for books matching any of the given genre names."""
    conditions = " OR ".join([f"g.name = '{n.replace(chr(39), chr(39) * 2)}'" for n in genre_names])
    return f"""id IN (
        SELECT DISTINCT ag.audiobook_id FROM audiobook_genres ag
        JOIN genres g ON ag.genre_id = g.id
        WHERE {conditions}
    )"""  # nosec B608  # noqa: S608


def _era_query(era_name: str) -> str:
    """SQL WHERE clause for books matching an era."""
    safe = era_name.replace("'", "''")
    return f"""id IN (
        SELECT ae.audiobook_id FROM audiobook_eras ae
        JOIN eras e ON ae.era_id = e.id
        WHERE e.name = '{safe}'
    )"""  # nosec B608  # noqa: S608


def _topic_query(topic_name: str) -> str:
    """SQL WHERE clause for books matching a topic."""
    safe = topic_name.replace("'", "''")
    return f"""id IN (
        SELECT at.audiobook_id FROM audiobook_topics at
        JOIN topics t ON at.topic_id = t.id
        WHERE t.name = '{safe}'
    )"""  # nosec B608  # noqa: S608


def _series_query(series_name: str) -> str:
    """SQL WHERE clause for books in a specific series."""
    safe = series_name.replace("'", "''")
    return f"series = '{safe}'"  # nosec B608


# ─── Special collections (fixed SQL) ─────────────────────────────────────────

SPECIAL_COLLECTIONS = [
    {
        "id": "podcasts",
        "name": "Podcasts & Shows",
        "description": "Podcasts, shows, and other non-audiobook Audible content",
        "query": (
            "content_type NOT IN ('Product', 'Performance', 'Speech', 'Lecture')"
            " AND content_type IS NOT NULL"
        ),
        "icon": "🎙️",
        "category": "special",
        "bypasses_filter": True,
    },
    {
        "id": "great-courses",
        "name": "The Great Courses",
        "description": "Educational lecture series from The Teaching Company",
        "query": "author LIKE '%The Great Courses%'",
        "icon": "🎓",
        "category": "special",
        "bypasses_filter": True,
    },
    {
        "id": "lectures",
        "name": "Lectures",
        "description": "Educational lectures and academic content",
        "query": "content_type = 'Lecture' AND author NOT LIKE '%The Great Courses%'",
        "icon": "🎤",
        "category": "special",
        "bypasses_filter": True,
    },
]


# ─── Dynamic collection builder ──────────────────────────────────────────────


def _row_field(row, name, index):
    """Extract a field from a row that may be a dict or tuple."""
    return row[name] if isinstance(row, dict) else row[index]


def _build_special_collections(tree, flat):
    """Add special (fixed-query) collections to tree and flat lookup."""
    for spec in SPECIAL_COLLECTIONS:
        tree.append(spec)
        flat[str(spec["id"])] = {
            "name": spec["name"],
            "description": spec.get("description", ""),
            "query": spec["query"],
            "icon": spec.get("icon", "📁"),
            "category": spec.get("category", "special"),
            "bypasses_filter": spec.get("bypasses_filter", False),
        }


def _build_children_from_rows(rows, slug_prefix, query_fn, name_field="name", extra_fields=None):
    """Build child collection dicts from DB rows.

    Args:
        rows: DB result rows
        slug_prefix: Prefix for collection IDs (e.g. "genre", "era")
        query_fn: Function(name) -> SQL WHERE clause
        name_field: Row field name for the entity name
        extra_fields: Optional dict mapping child_key -> (row_field, row_index)

    Returns list of child dicts.
    """
    children = []
    for row in rows:
        name = _row_field(row, name_field, 0)
        count = _row_field(row, "cnt", len(row) - 1 if not isinstance(row, dict) else "cnt")
        child = {
            "id": f"{slug_prefix}-{_slugify(name)}",
            "name": name,
            "query": query_fn(name),
            "count": count,
        }
        if extra_fields:
            for key, (field, idx) in extra_fields.items():
                child[key] = _row_field(row, field, idx) or "Product"
        children.append(child)
    return children


def _add_parent_node(tree, flat, node_id, name, description, query, icon, category, children):
    """Add a parent collection node with its children to tree and flat."""
    node = {
        "id": node_id,
        "name": name,
        "description": description,
        "query": query,
        "icon": icon,
        "category": category,
        "children": children,
    }
    tree.append(node)
    flat[node_id] = {
        "name": name,
        "description": description,
        "query": query,
        "icon": icon,
        "category": category,
        "bypasses_filter": False,
    }
    for child in children:
        flat[child["id"]] = {
            "name": child["name"],
            "description": "",
            "query": child["query"],
            "icon": icon,
            "category": category,
            "bypasses_filter": False,
        }


def _classify_genre_children(genre_rows):
    """Classify genre rows into fiction and nonfiction buckets.

    Returns (fiction_children, nonfiction_children,
             fiction_genre_names, nonfiction_genre_names).
    """
    fiction_children = []
    nonfiction_children = []
    fiction_names = []
    nonfiction_names = []

    for row in genre_rows:
        name = _row_field(row, "name", 0)
        count = _row_field(row, "cnt", 1)
        child = {
            "id": f"genre-{_slugify(name)}",
            "name": name,
            "query": _genre_query(name),
            "count": count,
        }
        if name in NONFICTION_GENRES:
            nonfiction_children.append(child)
            nonfiction_names.append(name)
        else:
            # Unknown genres default to fiction (most Audible genres are fiction)
            fiction_children.append(child)
            fiction_names.append(name)

    return fiction_children, nonfiction_children, fiction_names, nonfiction_names


def _build_genre_collections(cursor, tree, flat):
    """Build fiction and nonfiction genre collections."""
    cursor.execute("""
        SELECT g.name, COUNT(ag.audiobook_id) as cnt
        FROM genres g
        JOIN audiobook_genres ag ON g.id = ag.genre_id
        GROUP BY g.id
        HAVING cnt > 0
        ORDER BY g.name
    """)
    genre_rows = cursor.fetchall()

    fic_ch, nfic_ch, fic_names, nfic_names = _classify_genre_children(genre_rows)

    if fic_names:
        _add_parent_node(
            tree,
            flat,
            "fiction",
            "Fiction",
            "Novels, stories, and literary fiction",
            _multi_genre_query(fic_names),
            "📖",
            "fiction",
            fic_ch,
        )
    if nfic_names:
        _add_parent_node(
            tree,
            flat,
            "nonfiction",
            "Nonfiction",
            "Biography, history, science, and more",
            _multi_genre_query(nfic_names),
            "📚",
            "nonfiction",
            nfic_ch,
        )


def _build_series_collections(cursor, tree, flat):
    """Build series collections."""
    cursor.execute("""
        SELECT series, content_type, COUNT(*) as cnt
        FROM audiobooks
        WHERE series IS NOT NULL AND series != ''
        GROUP BY series
        ORDER BY series
    """)
    children = _build_children_from_rows(
        cursor.fetchall(),
        "series",
        _series_query,
        name_field="series",
        extra_fields={"content_type": ("content_type", 1)},
    )
    if children:
        _add_parent_node(
            tree,
            flat,
            "series",
            "Series",
            "Books organized by series",
            "series IS NOT NULL AND series != ''",
            "📕",
            "series",
            children,
        )


def _build_era_collections(cursor, tree, flat):
    """Build era collections."""
    cursor.execute("""
        SELECT e.name, COUNT(ae.audiobook_id) as cnt
        FROM eras e
        JOIN audiobook_eras ae ON e.id = ae.era_id
        GROUP BY e.id
        HAVING cnt > 0
        ORDER BY e.name
    """)
    children = _build_children_from_rows(cursor.fetchall(), "era", _era_query)
    if children:
        _add_parent_node(
            tree,
            flat,
            "eras",
            "Eras",
            "Books by literary era and time period",
            "id IN (SELECT ae.audiobook_id FROM audiobook_eras ae)",
            "🕰️",
            "eras",
            children,
        )


def _build_topic_collections(cursor, tree, flat):
    """Build topic collections."""
    cursor.execute("""
        SELECT t.name, COUNT(at.audiobook_id) as cnt
        FROM topics t
        JOIN audiobook_topics at ON t.id = at.topic_id
        GROUP BY t.id
        HAVING cnt > 0
        ORDER BY t.name
    """)
    children = _build_children_from_rows(cursor.fetchall(), "topic", _topic_query)
    if children:
        _add_parent_node(
            tree,
            flat,
            "topics",
            "Topics",
            "Books by subject and theme",
            "id IN (SELECT at.audiobook_id FROM audiobook_topics at)",
            "🏷️",
            "topics",
            children,
        )


def _build_dynamic_collections(cursor) -> tuple[list[dict], dict]:
    """
    Build the collection tree and flat lookup from enrichment data.

    Returns (tree, flat_lookup).
    """
    tree: list[dict] = []
    flat: dict[str, dict] = {}

    _build_special_collections(tree, flat)
    _build_genre_collections(cursor, tree, flat)
    _build_series_collections(cursor, tree, flat)
    _build_era_collections(cursor, tree, flat)
    _build_topic_collections(cursor, tree, flat)

    return tree, flat


# ─── Module-level COLLECTIONS for audiobooks.py ──────────────────────────────
# This is populated lazily on first request via get_collections_lookup().

_collections_cache: dict[str, dict] = {}
_collections_db_path: str | None = None


def get_collections_lookup(db_path: str) -> dict:
    """Get the flat COLLECTIONS dict, building from DB if needed."""
    global _collections_cache, _collections_db_path
    if not _collections_cache or _collections_db_path != db_path:
        conn = get_db(Path(db_path))
        cursor = conn.cursor()
        _, _collections_cache = _build_dynamic_collections(cursor)
        _collections_db_path = db_path
        conn.close()
    return _collections_cache


def invalidate_collections_cache():
    """Clear the collections cache (call after enrichment/scan)."""
    global _collections_cache, _collections_db_path
    _collections_cache = {}
    _collections_db_path = None


# Backward compatibility: COLLECTIONS is initially empty, populated on first use
COLLECTIONS = {}


def init_collections_routes(db_path):
    """Initialize routes with database path."""

    @collections_bp.route("/api/collections", methods=["GET"])
    @guest_allowed
    def get_collections() -> Response:
        """Get collections as a tree with counts from enrichment data."""
        conn = get_db(db_path)
        cursor = conn.cursor()

        tree, flat_lookup = _build_dynamic_collections(cursor)

        # Update module-level cache
        global _collections_cache, _collections_db_path, COLLECTIONS
        _collections_cache = flat_lookup
        _collections_db_path = db_path
        COLLECTIONS.update(flat_lookup)

        def get_count(query: str) -> int:
            # WHERE-clause fragments here come exclusively from internal
            # SPECIAL_COLLECTIONS entries and from helpers (_genre_query,
            # _era_query, _topic_query, _series_query, _multi_genre_query)
            # that derive names from DB-sourced metadata and single-quote
            # escape them. They never contain user HTTP input. Defense in
            # depth: reject any fragment containing SQL statement terminators
            # or comment introducers before execution.
            if not isinstance(query, str) or ";" in query or "--" in query:
                raise ValueError("rejected unsafe collection WHERE fragment")
            # nosec B608 below: WHERE fragments are internal-only (see comment above), validated for ; and --
            sql = "SELECT COUNT(*) as count FROM audiobooks WHERE " + query  # nosec B608  # noqa: S608
            cursor.execute(sql)  # nosec B608  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            return cursor.fetchone()["count"]

        category_order = ["special", "fiction", "nonfiction", "series", "eras", "topics"]
        category_labels = {
            "special": "Special Collections",
            "fiction": "Fiction",
            "nonfiction": "Nonfiction",
            "series": "Series",
            "eras": "Eras",
            "topics": "Topics",
        }

        result = []
        for node in tree:
            children = []
            for child in node.get("children", []):
                child_count = child.get("count") or get_count(child["query"])
                if child_count > 0:
                    child_entry = {"id": child["id"], "name": child["name"], "count": child_count}
                    # Series children include content_type badge
                    if "content_type" in child:
                        child_entry["content_type"] = child["content_type"]
                    children.append(child_entry)

            entry = {
                "id": node["id"],
                "name": node["name"],
                "description": node.get("description", ""),
                "icon": node.get("icon", "📁"),
                "count": get_count(node["query"]),
                "category": node.get("category", "main"),
                "category_label": category_labels.get(node.get("category", "main"), "Other"),
                "children": children,
            }
            result.append(entry)

        # Sort by category order, then alphabetically within category
        def sort_key(item):
            cat_idx = (
                category_order.index(item["category"]) if item["category"] in category_order else 99
            )
            return (cat_idx, item["name"])

        result.sort(key=sort_key)
        conn.close()

        return jsonify(result)

    return collections_bp
