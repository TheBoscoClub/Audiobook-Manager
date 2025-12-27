"""
Collection definitions and helpers for predefined audiobook groups.
"""

from flask import Blueprint, jsonify, Response

from .core import get_db, FlaskResponse

collections_bp = Blueprint('collections', __name__)


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


# Predefined collection definitions
COLLECTIONS = {
    # === SPECIAL COLLECTIONS ===
    'great-courses': {
        'name': 'The Great Courses',
        'description': 'Educational lecture series from The Teaching Company',
        'query': "author LIKE '%The Great Courses%'",
        'icon': '🎓',
        'category': 'special'
    },

    # === MAIN GENRES ===
    'fiction': {
        'name': 'Fiction',
        'description': 'Literary fiction, genre fiction, and novels',
        'query': multi_genre_query(['Literature & Fiction', 'Literary Fiction', 'Genre Fiction']),
        'icon': '📖',
        'category': 'main'
    },
    'mystery-thriller': {
        'name': 'Mystery & Thriller',
        'description': 'Mystery, suspense, and thriller novels',
        'query': multi_genre_query(['Mystery', 'Thriller & Suspense', 'Suspense', 'Crime Fiction', 'Crime Thrillers']),
        'icon': '🔍',
        'category': 'main'
    },
    'scifi-fantasy': {
        'name': 'Sci-Fi & Fantasy',
        'description': 'Science fiction and fantasy',
        'query': multi_genre_query(['Science Fiction & Fantasy', 'Science Fiction', 'Fantasy']),
        'icon': '🚀',
        'category': 'main'
    },
    'horror': {
        'name': 'Horror',
        'description': 'Horror and supernatural fiction',
        'query': multi_genre_query(['Horror', 'Ghosts', 'Paranormal & Urban', 'Occult']),
        'icon': '👻',
        'category': 'main'
    },
    'classics': {
        'name': 'Classics',
        'description': 'Classic literature and timeless stories',
        'query': genre_query('Classics'),
        'icon': '📜',
        'category': 'main'
    },
    'comedy': {
        'name': 'Comedy & Humor',
        'description': 'Funny books and comedy',
        'query': genre_query('Comedy & Humor'),
        'icon': '😂',
        'category': 'main'
    },

    # === NONFICTION ===
    'biography-memoir': {
        'name': 'Biography & Memoir',
        'description': 'Biographies, autobiographies, and memoirs',
        'query': multi_genre_query(['Biographies & Memoirs', 'Memoirs', 'Biographical Fiction']),
        'icon': '👤',
        'category': 'nonfiction'
    },
    'history': {
        'name': 'History',
        'description': 'Historical nonfiction and world history',
        'query': multi_genre_query(['History', 'Historical', 'World']),
        'icon': '🏛️',
        'category': 'nonfiction'
    },
    'science': {
        'name': 'Science & Technology',
        'description': 'Science, technology, and nature',
        'query': multi_genre_query(['Science', 'Science & Engineering', 'Biological Sciences', 'Technothrillers']),
        'icon': '🔬',
        'category': 'nonfiction'
    },
    'health-wellness': {
        'name': 'Health & Wellness',
        'description': 'Health, psychology, and self-improvement',
        'query': multi_genre_query(['Health & Wellness', 'Psychology', 'Self-Help', 'Personal Development']),
        'icon': '🧘',
        'category': 'nonfiction'
    },

    # === SUBGENRES ===
    'historical-fiction': {
        'name': 'Historical Fiction',
        'description': 'Fiction set in historical periods',
        'query': genre_query('Historical Fiction'),
        'icon': '⚔️',
        'category': 'subgenre'
    },
    'action-adventure': {
        'name': 'Action & Adventure',
        'description': 'Action-packed adventure stories',
        'query': multi_genre_query(['Action & Adventure', 'Adventure']),
        'icon': '🗺️',
        'category': 'subgenre'
    },
    'anthologies': {
        'name': 'Short Stories',
        'description': 'Anthologies and short story collections',
        'query': genre_query('Anthologies & Short Stories'),
        'icon': '📚',
        'category': 'subgenre'
    },
}


def init_collections_routes(db_path):
    """Initialize routes with database path."""

    @collections_bp.route('/api/collections', methods=['GET'])
    def get_collections() -> Response:
        """Get available collections with counts, grouped by category"""
        conn = get_db(db_path)
        cursor = conn.cursor()

        category_order = ['special', 'main', 'nonfiction', 'subgenre']
        category_labels = {
            'special': 'Special Collections',
            'main': 'Main Genres',
            'nonfiction': 'Nonfiction',
            'subgenre': 'Subgenres'
        }

        result = []
        for collection_id, collection in COLLECTIONS.items():
            cursor.execute(f"SELECT COUNT(*) as count FROM audiobooks WHERE {collection['query']}")
            count = cursor.fetchone()['count']

            result.append({
                'id': collection_id,
                'name': collection['name'],
                'description': collection['description'],
                'icon': collection['icon'],
                'count': count,
                'category': collection.get('category', 'main'),
                'category_label': category_labels.get(collection.get('category', 'main'), 'Other')
            })

        def sort_key(item):
            cat_idx = category_order.index(item['category']) if item['category'] in category_order else 99
            return (cat_idx, item['name'])

        result.sort(key=sort_key)
        conn.close()

        return jsonify(result)

    return collections_bp
