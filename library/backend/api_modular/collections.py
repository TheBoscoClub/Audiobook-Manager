"""
Dynamic collection system (v8).

Collections are built from enrichment data (genres, eras, topics) and series
metadata. Fixed top-level categories with auto-generated subcategories.

The API returns a tree structure; the audiobooks endpoint consumes the flat
COLLECTIONS dict for filtering by any collection ID.
"""

import re

from flask import Blueprint, Response, jsonify, request

from .core import get_db
from .auth import guest_allowed

collections_bp = Blueprint("collections", __name__)


# ─── Genre classification ────────────────────────────────────────────────────
# Reverse map: display-name genre → "fiction" or "non-fiction".
# Genres not in this map are classified as "uncategorized" and appear under
# whichever top-level category they best fit, or are omitted.

# These come from scanner/metadata_utils.py GENRE_DISPLAY_NAMES values
FICTION_GENRES = frozenset({
    "Mystery", "Science Fiction", "Fantasy", "Literary Fiction",
    "Horror", "Romance",
})

NONFICTION_GENRES = frozenset({
    "Biographies & Memoirs", "History", "Science", "Philosophy",
    "Personal Development", "Business & Careers", "True Crime",
})


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
    )"""  # nosec B608


def _multi_genre_query(genre_names: list[str]) -> str:
    """SQL WHERE clause for books matching any of the given genre names."""
    conditions = " OR ".join(
        [f"g.name = '{n.replace(chr(39), chr(39)*2)}'" for n in genre_names]
    )
    return f"""id IN (
        SELECT DISTINCT ag.audiobook_id FROM audiobook_genres ag
        JOIN genres g ON ag.genre_id = g.id
        WHERE {conditions}
    )"""  # nosec B608


def _era_query(era_name: str) -> str:
    """SQL WHERE clause for books matching an era."""
    safe = era_name.replace("'", "''")
    return f"""id IN (
        SELECT ae.audiobook_id FROM audiobook_eras ae
        JOIN eras e ON ae.era_id = e.id
        WHERE e.name = '{safe}'
    )"""  # nosec B608


def _topic_query(topic_name: str) -> str:
    """SQL WHERE clause for books matching a topic."""
    safe = topic_name.replace("'", "''")
    return f"""id IN (
        SELECT at.audiobook_id FROM audiobook_topics at
        JOIN topics t ON at.topic_id = t.id
        WHERE t.name = '{safe}'
    )"""  # nosec B608


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


def _build_dynamic_collections(cursor) -> tuple[list[dict], dict]:
    """
    Build the collection tree and flat lookup from enrichment data.

    Returns (tree, flat_lookup).
    """
    tree = []
    flat = {}

    # --- Special collections ---
    for spec in SPECIAL_COLLECTIONS:
        tree.append(spec)
        flat[spec["id"]] = {
            "name": spec["name"],
            "description": spec.get("description", ""),
            "query": spec["query"],
            "icon": spec.get("icon", "📁"),
            "category": spec.get("category", "special"),
            "bypasses_filter": spec.get("bypasses_filter", False),
        }

    # --- Genres: Fiction and Nonfiction with auto-generated children ---
    cursor.execute("""
        SELECT g.name, COUNT(ag.audiobook_id) as cnt
        FROM genres g
        JOIN audiobook_genres ag ON g.id = ag.genre_id
        GROUP BY g.id
        HAVING cnt > 0
        ORDER BY g.name
    """)
    genre_rows = cursor.fetchall()

    fiction_children = []
    nonfiction_children = []
    fiction_genre_names = []
    nonfiction_genre_names = []

    for row in genre_rows:
        name = row["name"] if isinstance(row, dict) else row[0]
        count = row["cnt"] if isinstance(row, dict) else row[1]
        slug = f"genre-{_slugify(name)}"

        child = {
            "id": slug,
            "name": name,
            "query": _genre_query(name),
            "count": count,
        }

        if name in FICTION_GENRES:
            fiction_children.append(child)
            fiction_genre_names.append(name)
        elif name in NONFICTION_GENRES:
            nonfiction_children.append(child)
            nonfiction_genre_names.append(name)
        else:
            # Unknown genres go to fiction by default (most Audible genres are fiction)
            fiction_children.append(child)
            fiction_genre_names.append(name)

    # Fiction parent
    if fiction_genre_names:
        fiction_query = _multi_genre_query(fiction_genre_names)
        fiction_node = {
            "id": "fiction",
            "name": "Fiction",
            "description": "Novels, stories, and literary fiction",
            "query": fiction_query,
            "icon": "📖",
            "category": "fiction",
            "children": fiction_children,
        }
        tree.append(fiction_node)
        flat["fiction"] = {
            "name": "Fiction",
            "description": fiction_node["description"],
            "query": fiction_query,
            "icon": "📖",
            "category": "fiction",
            "bypasses_filter": False,
        }
        for child in fiction_children:
            flat[child["id"]] = {
                "name": child["name"],
                "description": "",
                "query": child["query"],
                "icon": "📖",
                "category": "fiction",
                "bypasses_filter": False,
            }

    # Nonfiction parent
    if nonfiction_genre_names:
        nonfiction_query = _multi_genre_query(nonfiction_genre_names)
        nonfiction_node = {
            "id": "nonfiction",
            "name": "Nonfiction",
            "description": "Biography, history, science, and more",
            "query": nonfiction_query,
            "icon": "📚",
            "category": "nonfiction",
            "children": nonfiction_children,
        }
        tree.append(nonfiction_node)
        flat["nonfiction"] = {
            "name": "Nonfiction",
            "description": nonfiction_node["description"],
            "query": nonfiction_query,
            "icon": "📚",
            "category": "nonfiction",
            "bypasses_filter": False,
        }
        for child in nonfiction_children:
            flat[child["id"]] = {
                "name": child["name"],
                "description": "",
                "query": child["query"],
                "icon": "📚",
                "category": "nonfiction",
                "bypasses_filter": False,
            }

    # --- Series ---
    cursor.execute("""
        SELECT series, content_type, COUNT(*) as cnt
        FROM audiobooks
        WHERE series IS NOT NULL AND series != ''
        GROUP BY series
        ORDER BY series
    """)
    series_rows = cursor.fetchall()

    series_children = []
    for row in series_rows:
        name = row["series"] if isinstance(row, dict) else row[0]
        content_type = row["content_type"] if isinstance(row, dict) else row[1]
        count = row["cnt"] if isinstance(row, dict) else row[2]
        slug = f"series-{_slugify(name)}"

        child = {
            "id": slug,
            "name": name,
            "query": _series_query(name),
            "count": count,
            "content_type": content_type or "Product",
        }
        series_children.append(child)
        flat[slug] = {
            "name": name,
            "description": "",
            "query": child["query"],
            "icon": "📕",
            "category": "series",
            "bypasses_filter": False,
        }

    if series_children:
        # Series parent matches all books that have a series
        series_node = {
            "id": "series",
            "name": "Series",
            "description": "Books organized by series",
            "query": "series IS NOT NULL AND series != ''",
            "icon": "📕",
            "category": "series",
            "children": series_children,
        }
        tree.append(series_node)
        flat["series"] = {
            "name": "Series",
            "description": series_node["description"],
            "query": series_node["query"],
            "icon": "📕",
            "category": "series",
            "bypasses_filter": False,
        }

    # --- Eras ---
    cursor.execute("""
        SELECT e.name, COUNT(ae.audiobook_id) as cnt
        FROM eras e
        JOIN audiobook_eras ae ON e.id = ae.era_id
        GROUP BY e.id
        HAVING cnt > 0
        ORDER BY e.name
    """)
    era_rows = cursor.fetchall()

    era_children = []
    era_names = []
    for row in era_rows:
        name = row["name"] if isinstance(row, dict) else row[0]
        count = row["cnt"] if isinstance(row, dict) else row[1]
        slug = f"era-{_slugify(name)}"

        child = {
            "id": slug,
            "name": name,
            "query": _era_query(name),
            "count": count,
        }
        era_children.append(child)
        era_names.append(name)
        flat[slug] = {
            "name": name,
            "description": "",
            "query": child["query"],
            "icon": "🕰️",
            "category": "eras",
            "bypasses_filter": False,
        }

    if era_children:
        era_node = {
            "id": "eras",
            "name": "Eras",
            "description": "Books by literary era and time period",
            "query": """id IN (
                SELECT ae.audiobook_id FROM audiobook_eras ae
            )""",
            "icon": "🕰️",
            "category": "eras",
            "children": era_children,
        }
        tree.append(era_node)
        flat["eras"] = {
            "name": "Eras",
            "description": era_node["description"],
            "query": era_node["query"],
            "icon": "🕰️",
            "category": "eras",
            "bypasses_filter": False,
        }

    # --- Topics ---
    cursor.execute("""
        SELECT t.name, COUNT(at.audiobook_id) as cnt
        FROM topics t
        JOIN audiobook_topics at ON t.id = at.topic_id
        GROUP BY t.id
        HAVING cnt > 0
        ORDER BY t.name
    """)
    topic_rows = cursor.fetchall()

    topic_children = []
    for row in topic_rows:
        name = row["name"] if isinstance(row, dict) else row[0]
        count = row["cnt"] if isinstance(row, dict) else row[1]
        slug = f"topic-{_slugify(name)}"

        child = {
            "id": slug,
            "name": name,
            "query": _topic_query(name),
            "count": count,
        }
        topic_children.append(child)
        flat[slug] = {
            "name": name,
            "description": "",
            "query": child["query"],
            "icon": "🏷️",
            "category": "topics",
            "bypasses_filter": False,
        }

    if topic_children:
        topic_node = {
            "id": "topics",
            "name": "Topics",
            "description": "Books by subject and theme",
            "query": """id IN (
                SELECT at.audiobook_id FROM audiobook_topics at
            )""",
            "icon": "🏷️",
            "category": "topics",
            "children": topic_children,
        }
        tree.append(topic_node)
        flat["topics"] = {
            "name": "Topics",
            "description": topic_node["description"],
            "query": topic_node["query"],
            "icon": "🏷️",
            "category": "topics",
            "bypasses_filter": False,
        }

    return tree, flat


# ─── Module-level COLLECTIONS for audiobooks.py ──────────────────────────────
# This is populated lazily on first request via get_collections_lookup().

_collections_cache = {}
_collections_db_path = None


def get_collections_lookup(db_path: str) -> dict:
    """Get the flat COLLECTIONS dict, building from DB if needed."""
    global _collections_cache, _collections_db_path
    if not _collections_cache or _collections_db_path != db_path:
        conn = get_db(db_path)
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
            cursor.execute(
                f"SELECT COUNT(*) as count FROM audiobooks WHERE {query}"  # nosec B608
            )
            return cursor.fetchone()["count"]

        category_order = [
            "special", "fiction", "nonfiction", "series", "eras", "topics",
        ]
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
                    child_entry = {
                        "id": child["id"],
                        "name": child["name"],
                        "count": child_count,
                    }
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
                "category_label": category_labels.get(
                    node.get("category", "main"), "Other"
                ),
                "children": children,
            }
            result.append(entry)

        # Sort by category order, then alphabetically within category
        def sort_key(item):
            cat_idx = (
                category_order.index(item["category"])
                if item["category"] in category_order
                else 99
            )
            return (cat_idx, item["name"])

        result.sort(key=sort_key)
        conn.close()

        return jsonify(result)

    return collections_bp
