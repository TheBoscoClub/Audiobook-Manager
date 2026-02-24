"""
Collection definitions and helpers for predefined audiobook groups.

Collections are organized as a tree: top-level genres contain subgenre children.
The API returns the tree structure; the audiobooks endpoint uses the flat COLLECTIONS
lookup for filtering by any collection ID (parent or child).
"""

from flask import Blueprint, Response, jsonify

from .core import get_db
from .auth import guest_allowed

collections_bp = Blueprint("collections", __name__)


def genre_query(genre_pattern: str) -> str:
    """Create a query for books matching a genre pattern."""
    return f"""id IN (
        SELECT ag.audiobook_id FROM audiobook_genres ag
        JOIN genres g ON ag.genre_id = g.id
        WHERE g.name LIKE '{genre_pattern}'
    )"""


def multi_genre_query(genre_patterns: list[str]) -> str:
    """Create a query for books matching any of the genre patterns."""
    conditions = " OR ".join([f"g.name LIKE '{p}'" for p in genre_patterns])
    return f"""id IN (
        SELECT DISTINCT ag.audiobook_id FROM audiobook_genres ag
        JOIN genres g ON ag.genre_id = g.id
        WHERE {conditions}
    )"""


# ─── Tree-structured collection definitions ──────────────────────────────────
# Each top-level entry may have "children" (subgenres displayed as branches).
# Genre names MUST match actual database values exactly.
# All IDs must be unique across both parents and children.

COLLECTION_TREE = [
    # === SPECIAL COLLECTIONS ===
    {
        "id": "podcasts",
        "name": "Podcasts & Shows",
        "description": "Podcasts, shows, and other non-audiobook Audible content",
        "query": "content_type NOT IN ('Product', 'Performance', 'Speech', 'Lecture') AND content_type IS NOT NULL",
        "icon": "🎙️",
        "category": "special",
        "bypasses_filter": True,  # Show non-audiobook content excluded by AUDIOBOOK_FILTER
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
    # === FICTION GENRES ===
    {
        "id": "fiction",
        "name": "Fiction",
        "description": "Literary fiction, genre fiction, and novels",
        "query": multi_genre_query(
            [
                "Literature & Fiction",
                "Literary Fiction",
                "Genre Fiction",
                "Contemporary Fiction",
                "Historical Fiction",
                "Women''s Fiction",
            ]
        ),
        "icon": "📖",
        "category": "main",
        "children": [
            {
                "id": "literary-fiction",
                "name": "Literary Fiction",
                "query": genre_query("Literary Fiction"),
            },
            {
                "id": "genre-fiction",
                "name": "Genre Fiction",
                "query": genre_query("Genre Fiction"),
            },
            {
                "id": "contemporary-fiction",
                "name": "Contemporary Fiction",
                "query": genre_query("Contemporary Fiction"),
            },
            {
                "id": "historical-fiction",
                "name": "Historical Fiction",
                "query": genre_query("Historical Fiction"),
            },
            {
                "id": "womens-fiction",
                "name": "Women's Fiction",
                "query": genre_query("Women''s Fiction"),
            },
            {
                "id": "world-literature",
                "name": "World Literature",
                "query": genre_query("World Literature"),
            },
        ],
    },
    {
        "id": "mystery-thriller",
        "name": "Mystery & Thriller",
        "description": "Mystery, suspense, and thriller novels",
        "query": multi_genre_query(
            [
                "Mystery",
                "Thriller & Suspense",
                "Suspense",
                "Crime Fiction",
                "Crime Thrillers",
                "Technothrillers",
                "International Mystery & Crime",
            ]
        ),
        "icon": "🔍",
        "category": "main",
        "children": [
            {
                "id": "mystery",
                "name": "Mystery",
                "query": genre_query("Mystery"),
            },
            {
                "id": "thriller-suspense",
                "name": "Thriller & Suspense",
                "query": genre_query("Thriller & Suspense"),
            },
            {
                "id": "suspense",
                "name": "Suspense",
                "query": genre_query("Suspense"),
            },
            {
                "id": "crime-fiction",
                "name": "Crime Fiction",
                "query": genre_query("Crime Fiction"),
            },
            {
                "id": "police-procedurals",
                "name": "Police Procedurals",
                "query": genre_query("Police Procedurals"),
            },
            {
                "id": "espionage",
                "name": "Espionage",
                "query": genre_query("Espionage"),
            },
            {
                "id": "hard-boiled",
                "name": "Hard-Boiled",
                "query": genre_query("Hard-Boiled"),
            },
            {
                "id": "noir",
                "name": "Noir",
                "query": genre_query("Noir"),
            },
        ],
    },
    {
        "id": "scifi-fantasy",
        "name": "Sci-Fi & Fantasy",
        "description": "Science fiction and fantasy",
        "query": multi_genre_query(
            [
                "Science Fiction & Fantasy",
                "Science Fiction",
                "Fantasy",
                "Hard Science Fiction",
            ]
        ),
        "icon": "🚀",
        "category": "main",
        "children": [
            {
                "id": "science-fiction",
                "name": "Science Fiction",
                "query": genre_query("Science Fiction"),
            },
            {
                "id": "fantasy",
                "name": "Fantasy",
                "query": genre_query("Fantasy"),
            },
            {
                "id": "hard-scifi",
                "name": "Hard Science Fiction",
                "query": genre_query("Hard Science Fiction"),
            },
            {
                "id": "epic-fantasy",
                "name": "Epic",
                "query": genre_query("Epic"),
            },
            {
                "id": "dystopian",
                "name": "Dystopian",
                "query": genre_query("Dystopian"),
            },
            {
                "id": "space-opera",
                "name": "Space Opera",
                "query": genre_query("Space Opera"),
            },
            {
                "id": "post-apocalyptic",
                "name": "Post-Apocalyptic",
                "query": genre_query("Post-Apocalyptic"),
            },
        ],
    },
    {
        "id": "horror",
        "name": "Horror",
        "description": "Horror and supernatural fiction",
        "query": multi_genre_query(
            [
                "Horror",
                "Paranormal & Urban",
                "Supernatural",
                "Ghosts",
                "Occult",
            ]
        ),
        "icon": "👻",
        "category": "main",
        "children": [
            {
                "id": "paranormal-urban",
                "name": "Paranormal & Urban",
                "query": genre_query("Paranormal & Urban"),
            },
            {
                "id": "supernatural",
                "name": "Supernatural",
                "query": genre_query("Supernatural"),
            },
            {
                "id": "ghosts",
                "name": "Ghosts",
                "query": genre_query("Ghosts"),
            },
            {
                "id": "occult",
                "name": "Occult",
                "query": genre_query("Occult"),
            },
        ],
    },
    {
        "id": "action-adventure",
        "name": "Action & Adventure",
        "description": "Action-packed and adventure stories",
        "query": multi_genre_query(
            [
                "Action & Adventure",
                "Adventure",
                "Sea Adventures",
                "Military",
            ]
        ),
        "icon": "⚔️",
        "category": "main",
        "children": [
            {
                "id": "adventure",
                "name": "Adventure",
                "query": genre_query("Adventure"),
            },
            {
                "id": "military",
                "name": "Military",
                "query": genre_query("Military"),
            },
            {
                "id": "sea-adventures",
                "name": "Sea Adventures",
                "query": genre_query("Sea Adventures"),
            },
            {
                "id": "westerns",
                "name": "Westerns",
                "query": genre_query("Westerns"),
            },
        ],
    },
    {
        "id": "classics",
        "name": "Classics",
        "description": "Classic literature and timeless stories",
        "query": genre_query("Classics"),
        "icon": "📜",
        "category": "main",
    },
    {
        "id": "comedy",
        "name": "Comedy & Humor",
        "description": "Funny books and comedy",
        "query": multi_genre_query(["Comedy & Humor", "Satire", "Humorous"]),
        "icon": "😂",
        "category": "main",
        "children": [
            {
                "id": "satire",
                "name": "Satire",
                "query": genre_query("Satire"),
            },
        ],
    },
    {
        "id": "romance",
        "name": "Romance",
        "description": "Romance and love stories",
        "query": genre_query("Romance"),
        "icon": "💕",
        "category": "main",
    },
    # === NONFICTION ===
    {
        "id": "biography-memoir",
        "name": "Biography & Memoir",
        "description": "Biographies, autobiographies, and memoirs",
        "query": genre_query("Biographies & Memoirs"),
        "icon": "👤",
        "category": "nonfiction",
    },
    {
        "id": "history",
        "name": "History",
        "description": "Historical nonfiction and world history",
        "query": multi_genre_query(["History", "Historical"]),
        "icon": "🏛️",
        "category": "nonfiction",
        "children": [
            {
                "id": "military-history",
                "name": "War & Military",
                "query": genre_query("War & Military"),
            },
            {
                "id": "american-history",
                "name": "Americas",
                "query": genre_query("Americas"),
            },
            {
                "id": "british-history",
                "name": "Great Britain",
                "query": genre_query("Great Britain"),
            },
        ],
    },
    {
        "id": "science",
        "name": "Science & Technology",
        "description": "Science, technology, and nature",
        "query": multi_genre_query(["Science", "Science & Engineering"]),
        "icon": "🔬",
        "category": "nonfiction",
    },
    {
        "id": "politics",
        "name": "Politics & Social Sciences",
        "description": "Political science, social issues, and government",
        "query": multi_genre_query(
            [
                "Politics & Social Sciences",
                "Social Sciences",
                "Politics & Government",
            ]
        ),
        "icon": "🏛️",
        "category": "nonfiction",
    },
    {
        "id": "health-wellness",
        "name": "Health & Wellness",
        "description": "Health, psychology, and self-improvement",
        "query": multi_genre_query(
            [
                "Health & Wellness",
                "Psychology & Mental Health",
                "Parenting & Personal Development",
            ]
        ),
        "icon": "🧘",
        "category": "nonfiction",
        "children": [
            {
                "id": "psychology",
                "name": "Psychology",
                "query": genre_query("Psychology"),
            },
            {
                "id": "personal-development",
                "name": "Personal Development",
                "query": genre_query("Personal Development"),
            },
        ],
    },
    {
        "id": "business",
        "name": "Business",
        "description": "Business, finance, and economics",
        "query": genre_query("Business & Careers"),
        "icon": "💼",
        "category": "nonfiction",
    },
    {
        "id": "religion-spirituality",
        "name": "Religion & Spirituality",
        "description": "Religion, faith, and spiritual topics",
        "query": genre_query("Religion & Spirituality"),
        "icon": "🕊️",
        "category": "nonfiction",
    },
    # === MORE GENRES ===
    {
        "id": "short-stories",
        "name": "Short Stories & Anthologies",
        "description": "Short story collections, anthologies, and compiled works",
        "query": multi_genre_query(
            [
                "Anthologies & Short Stories",
                "Anthologies",
                "Short Stories",
            ]
        ),
        "icon": "📑",
        "category": "subgenre",
    },
    {
        "id": "young-adult",
        "name": "Children & Young Adult",
        "description": "Books for younger audiences",
        "query": multi_genre_query(
            [
                "Children''s Audiobooks",
                "Teen & Young Adult",
                "Coming of Age",
            ]
        ),
        "icon": "📚",
        "category": "subgenre",
    },
]


def _build_flat_lookup() -> dict:
    """Build flat COLLECTIONS dict from COLLECTION_TREE for audiobooks endpoint."""
    flat = {}
    for node in COLLECTION_TREE:
        node_id = node["id"]
        flat[node_id] = {
            "name": node["name"],
            "description": node.get("description", ""),
            "query": node["query"],
            "icon": node.get("icon", "📁"),
            "category": node.get("category", "main"),
            "bypasses_filter": node.get("bypasses_filter", False),
        }
        for child in node.get("children", []):
            flat[child["id"]] = {
                "name": child["name"],
                "description": child.get("description", ""),
                "query": child["query"],
                "icon": node.get("icon", "📁"),
                "category": node.get("category", "main"),
                "bypasses_filter": node.get("bypasses_filter", False),
            }
    return flat


# Flat lookup used by audiobooks.py for collection filtering
COLLECTIONS = _build_flat_lookup()


def init_collections_routes(db_path):
    """Initialize routes with database path."""

    @collections_bp.route("/api/collections", methods=["GET"])
    @guest_allowed
    def get_collections() -> Response:
        """Get collections as a tree with counts at every level."""
        conn = get_db(db_path)
        cursor = conn.cursor()

        def get_count(query: str) -> int:
            cursor.execute(f"SELECT COUNT(*) as count FROM audiobooks WHERE {query}")
            return cursor.fetchone()["count"]

        category_order = ["special", "main", "nonfiction", "subgenre"]
        category_labels = {
            "special": "Special Collections",
            "main": "Fiction Genres",
            "nonfiction": "Nonfiction",
            "subgenre": "More Genres",
        }

        result = []
        for node in COLLECTION_TREE:
            children = []
            for child in node.get("children", []):
                child_count = get_count(child["query"])
                if child_count > 0:
                    children.append(
                        {
                            "id": child["id"],
                            "name": child["name"],
                            "count": child_count,
                        }
                    )

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
