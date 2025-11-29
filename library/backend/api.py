#!/usr/bin/env python3
"""
Audiobook Library API - Flask Backend
Provides fast, paginated API for audiobook queries
"""

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import sqlite3
from pathlib import Path
import os

app = Flask(__name__)
CORS(app)  # Enable CORS for local development

DB_PATH = Path(__file__).parent / "audiobooks.db"
PROJECT_ROOT = Path(__file__).parent.parent


def get_db():
    """Get database connection"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # Return rows as dictionaries
    return conn


@app.route('/api/stats', methods=['GET'])
def get_stats():
    """Get library statistics"""
    conn = get_db()
    cursor = conn.cursor()

    # Total audiobooks
    cursor.execute("SELECT COUNT(*) as total FROM audiobooks")
    total_books = cursor.fetchone()['total']

    # Total hours
    cursor.execute("SELECT SUM(duration_hours) as total_hours FROM audiobooks")
    total_hours = cursor.fetchone()['total_hours'] or 0

    # Unique counts
    cursor.execute("SELECT COUNT(DISTINCT author) as count FROM audiobooks WHERE author IS NOT NULL")
    unique_authors = cursor.fetchone()['count']

    cursor.execute("SELECT COUNT(DISTINCT narrator) as count FROM audiobooks WHERE narrator IS NOT NULL")
    unique_narrators = cursor.fetchone()['count']

    cursor.execute("SELECT COUNT(DISTINCT publisher) as count FROM audiobooks WHERE publisher IS NOT NULL")
    unique_publishers = cursor.fetchone()['count']

    cursor.execute("SELECT COUNT(*) as count FROM genres")
    unique_genres = cursor.fetchone()['count']

    conn.close()

    return jsonify({
        'total_audiobooks': total_books,
        'total_hours': round(total_hours),
        'total_days': round(total_hours / 24),
        'unique_authors': unique_authors,
        'unique_narrators': unique_narrators,
        'unique_publishers': unique_publishers,
        'unique_genres': unique_genres
    })


@app.route('/api/audiobooks', methods=['GET'])
def get_audiobooks():
    """
    Get paginated audiobooks with optional filtering
    Query params:
    - page: Page number (default: 1)
    - per_page: Items per page (default: 50, max: 200)
    - search: Search query (full-text search)
    - author: Filter by author
    - narrator: Filter by narrator
    - publisher: Filter by publisher
    - genre: Filter by genre
    - format: Filter by format (opus, m4b, etc.)
    - sort: Sort field (title, author, duration_hours, created_at)
    - order: Sort order (asc, desc)
    """
    # Parse parameters
    page = max(1, int(request.args.get('page', 1)))
    per_page = min(200, max(1, int(request.args.get('per_page', 50))))
    search = request.args.get('search', '').strip()
    author = request.args.get('author', '').strip()
    narrator = request.args.get('narrator', '').strip()
    publisher = request.args.get('publisher', '').strip()
    genre = request.args.get('genre', '').strip()
    format_filter = request.args.get('format', '').strip()
    sort_field = request.args.get('sort', 'title')
    sort_order = request.args.get('order', 'asc').lower()

    # Validate sort field
    allowed_sorts = ['title', 'author', 'narrator', 'duration_hours', 'created_at', 'file_size_mb']
    if sort_field not in allowed_sorts:
        sort_field = 'title'

    # Validate sort order
    if sort_order not in ['asc', 'desc']:
        sort_order = 'asc'

    conn = get_db()
    cursor = conn.cursor()

    # Build query
    where_clauses = []
    params = []

    if search:
        # Full-text search
        where_clauses.append("id IN (SELECT rowid FROM audiobooks_fts WHERE audiobooks_fts MATCH ?)")
        params.append(search)

    if author:
        where_clauses.append("author LIKE ?")
        params.append(f"%{author}%")

    if narrator:
        where_clauses.append("narrator LIKE ?")
        params.append(f"%{narrator}%")

    if publisher:
        where_clauses.append("publisher LIKE ?")
        params.append(f"%{publisher}%")

    if format_filter:
        where_clauses.append("format = ?")
        params.append(format_filter.lower())

    if genre:
        where_clauses.append("""
            id IN (
                SELECT audiobook_id FROM audiobook_genres ag
                JOIN genres g ON ag.genre_id = g.id
                WHERE g.name LIKE ?
            )
        """)
        params.append(f"%{genre}%")

    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)

    # Count total matching audiobooks
    count_query = f"SELECT COUNT(*) as total FROM audiobooks {where_sql}"
    cursor.execute(count_query, params)
    total_count = cursor.fetchone()['total']

    # Get paginated audiobooks
    offset = (page - 1) * per_page

    query = f"""
        SELECT
            id, title, author, narrator, publisher, series,
            duration_hours, duration_formatted, file_size_mb,
            file_path, cover_path, format, quality, description
        FROM audiobooks
        {where_sql}
        ORDER BY {sort_field} {sort_order}
        LIMIT ? OFFSET ?
    """

    cursor.execute(query, params + [per_page, offset])
    rows = cursor.fetchall()

    # Convert to list of dicts
    audiobooks = []
    for row in rows:
        book = dict(row)

        # Get genres, eras, topics
        cursor.execute("""
            SELECT g.name FROM genres g
            JOIN audiobook_genres ag ON g.id = ag.genre_id
            WHERE ag.audiobook_id = ?
        """, (book['id'],))
        book['genres'] = [r['name'] for r in cursor.fetchall()]

        cursor.execute("""
            SELECT e.name FROM eras e
            JOIN audiobook_eras ae ON e.id = ae.era_id
            WHERE ae.audiobook_id = ?
        """, (book['id'],))
        book['eras'] = [r['name'] for r in cursor.fetchall()]

        cursor.execute("""
            SELECT t.name FROM topics t
            JOIN audiobook_topics at ON t.id = at.topic_id
            WHERE at.audiobook_id = ?
        """, (book['id'],))
        book['topics'] = [r['name'] for r in cursor.fetchall()]

        audiobooks.append(book)

    conn.close()

    # Calculate pagination metadata
    total_pages = (total_count + per_page - 1) // per_page

    return jsonify({
        'audiobooks': audiobooks,
        'pagination': {
            'page': page,
            'per_page': per_page,
            'total_count': total_count,
            'total_pages': total_pages,
            'has_next': page < total_pages,
            'has_prev': page > 1
        }
    })


@app.route('/api/filters', methods=['GET'])
def get_filters():
    """Get all available filter options"""
    conn = get_db()
    cursor = conn.cursor()

    # Get unique authors
    cursor.execute("""
        SELECT DISTINCT author FROM audiobooks
        WHERE author IS NOT NULL
        ORDER BY author
    """)
    authors = [row['author'] for row in cursor.fetchall()]

    # Get unique narrators
    cursor.execute("""
        SELECT DISTINCT narrator FROM audiobooks
        WHERE narrator IS NOT NULL
        ORDER BY narrator
    """)
    narrators = [row['narrator'] for row in cursor.fetchall()]

    # Get unique publishers
    cursor.execute("""
        SELECT DISTINCT publisher FROM audiobooks
        WHERE publisher IS NOT NULL
        ORDER BY publisher
    """)
    publishers = [row['publisher'] for row in cursor.fetchall()]

    # Get genres
    cursor.execute("SELECT name FROM genres ORDER BY name")
    genres = [row['name'] for row in cursor.fetchall()]

    # Get eras
    cursor.execute("SELECT name FROM eras ORDER BY name")
    eras = [row['name'] for row in cursor.fetchall()]

    # Get topics
    cursor.execute("SELECT name FROM topics ORDER BY name")
    topics = [row['name'] for row in cursor.fetchall()]

    # Get formats
    cursor.execute("""
        SELECT DISTINCT format FROM audiobooks
        WHERE format IS NOT NULL
        ORDER BY format
    """)
    formats = [row['format'] for row in cursor.fetchall()]

    conn.close()

    return jsonify({
        'authors': authors,
        'narrators': narrators,
        'publishers': publishers,
        'genres': genres,
        'eras': eras,
        'topics': topics,
        'formats': formats
    })


@app.route('/api/audiobooks/<int:audiobook_id>', methods=['GET'])
def get_audiobook(audiobook_id):
    """Get single audiobook details"""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM audiobooks WHERE id = ?
    """, (audiobook_id,))

    row = cursor.fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Audiobook not found'}), 404

    book = dict(row)

    # Get related data
    cursor.execute("""
        SELECT g.name FROM genres g
        JOIN audiobook_genres ag ON g.id = ag.genre_id
        WHERE ag.audiobook_id = ?
    """, (audiobook_id,))
    book['genres'] = [r['name'] for r in cursor.fetchall()]

    cursor.execute("""
        SELECT e.name FROM eras e
        JOIN audiobook_eras ae ON e.id = ae.era_id
        WHERE ae.audiobook_id = ?
    """, (audiobook_id,))
    book['eras'] = [r['name'] for r in cursor.fetchall()]

    cursor.execute("""
        SELECT t.name FROM topics t
        JOIN audiobook_topics at ON t.id = at.topic_id
        WHERE at.audiobook_id = ?
    """, (audiobook_id,))
    book['topics'] = [r['name'] for r in cursor.fetchall()]

    conn.close()

    return jsonify(book)


@app.route('/covers/<path:filename>')
def serve_cover(filename):
    """Serve cover images"""
    covers_dir = PROJECT_ROOT / 'web' / 'covers'
    return send_from_directory(covers_dir, filename)


@app.route('/api/stream/<int:audiobook_id>')
def stream_audiobook(audiobook_id):
    """Stream audiobook file"""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT file_path FROM audiobooks WHERE id = ?", (audiobook_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return jsonify({'error': 'Audiobook not found'}), 404

    file_path = Path(row['file_path'])
    if not file_path.exists():
        return jsonify({'error': 'File not found on disk'}), 404

    # Serve the file with range support for seeking
    return send_from_directory(file_path.parent, file_path.name, as_attachment=False)


@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({'status': 'ok', 'database': str(DB_PATH.exists())})


# ============================================
# DUPLICATE DETECTION ENDPOINTS
# ============================================

@app.route('/api/hash-stats', methods=['GET'])
def get_hash_stats():
    """Get hash generation statistics"""
    conn = get_db()
    cursor = conn.cursor()

    # Check if sha256_hash column exists
    cursor.execute("PRAGMA table_info(audiobooks)")
    columns = [row['name'] for row in cursor.fetchall()]

    if 'sha256_hash' not in columns:
        conn.close()
        return jsonify({
            'hash_column_exists': False,
            'total_audiobooks': 0,
            'hashed_count': 0,
            'unhashed_count': 0,
            'duplicate_groups': 0
        })

    cursor.execute("SELECT COUNT(*) as total FROM audiobooks")
    total = cursor.fetchone()['total']

    cursor.execute("SELECT COUNT(*) as count FROM audiobooks WHERE sha256_hash IS NOT NULL")
    hashed = cursor.fetchone()['count']

    cursor.execute("""
        SELECT COUNT(*) as count FROM (
            SELECT sha256_hash FROM audiobooks
            WHERE sha256_hash IS NOT NULL
            GROUP BY sha256_hash
            HAVING COUNT(*) > 1
        )
    """)
    duplicate_groups = cursor.fetchone()['count']

    conn.close()

    return jsonify({
        'hash_column_exists': True,
        'total_audiobooks': total,
        'hashed_count': hashed,
        'unhashed_count': total - hashed,
        'hashed_percentage': round(hashed * 100 / total, 1) if total > 0 else 0,
        'duplicate_groups': duplicate_groups
    })


@app.route('/api/duplicates', methods=['GET'])
def get_duplicates():
    """Get all duplicate audiobook groups"""
    conn = get_db()
    cursor = conn.cursor()

    # Check if sha256_hash column exists
    cursor.execute("PRAGMA table_info(audiobooks)")
    columns = [row['name'] for row in cursor.fetchall()]

    if 'sha256_hash' not in columns:
        conn.close()
        return jsonify({'error': 'Hash column not found. Run hash generation first.'}), 400

    # Get all duplicate groups
    cursor.execute("""
        SELECT sha256_hash, COUNT(*) as count
        FROM audiobooks
        WHERE sha256_hash IS NOT NULL
        GROUP BY sha256_hash
        HAVING count > 1
        ORDER BY count DESC
    """)
    groups = cursor.fetchall()

    duplicate_groups = []
    total_wasted_space = 0

    for group in groups:
        hash_val = group['sha256_hash']
        count = group['count']

        # Get all files in this group
        cursor.execute("""
            SELECT id, title, author, narrator, file_path, file_size_mb,
                   format, duration_formatted, cover_path
            FROM audiobooks
            WHERE sha256_hash = ?
            ORDER BY id ASC
        """, (hash_val,))

        files = [dict(row) for row in cursor.fetchall()]

        # First file (by ID) is the "keeper"
        for i, f in enumerate(files):
            f['is_keeper'] = (i == 0)
            f['is_duplicate'] = (i > 0)

        file_size = files[0]['file_size_mb'] if files else 0
        wasted = file_size * (count - 1)
        total_wasted_space += wasted

        duplicate_groups.append({
            'hash': hash_val,
            'count': count,
            'file_size_mb': file_size,
            'wasted_mb': round(wasted, 2),
            'files': files
        })

    conn.close()

    return jsonify({
        'duplicate_groups': duplicate_groups,
        'total_groups': len(duplicate_groups),
        'total_wasted_mb': round(total_wasted_space, 2),
        'total_duplicate_files': sum(g['count'] - 1 for g in duplicate_groups)
    })


@app.route('/api/duplicates/delete', methods=['POST'])
def delete_duplicates():
    """
    Delete selected duplicate audiobooks.
    SAFETY: Will NEVER delete the last remaining copy of any audiobook.

    Request body:
    {
        "audiobook_ids": [1, 2, 3]  // IDs to delete
    }
    """
    data = request.get_json()
    if not data or 'audiobook_ids' not in data:
        return jsonify({'error': 'Missing audiobook_ids'}), 400

    ids_to_delete = data['audiobook_ids']
    if not ids_to_delete:
        return jsonify({'error': 'No audiobook IDs provided'}), 400

    conn = get_db()
    cursor = conn.cursor()

    # CRITICAL SAFETY CHECK: For each hash, ensure we're not deleting ALL copies
    # Get hashes of all audiobooks to be deleted
    placeholders = ','.join('?' * len(ids_to_delete))
    cursor.execute(f"""
        SELECT id, sha256_hash, title, file_path
        FROM audiobooks
        WHERE id IN ({placeholders})
    """, ids_to_delete)

    to_delete = [dict(row) for row in cursor.fetchall()]

    # Group by hash
    hash_groups = {}
    for item in to_delete:
        h = item['sha256_hash']
        if h not in hash_groups:
            hash_groups[h] = []
        hash_groups[h].append(item)

    # For each hash, verify at least one copy will remain
    blocked_ids = []
    safe_to_delete = []

    for hash_val, items in hash_groups.items():
        if hash_val is None:
            # No hash - these are unique files, can't safely delete
            blocked_ids.extend([i['id'] for i in items])
            continue

        # Count total copies with this hash
        cursor.execute("""
            SELECT COUNT(*) as count FROM audiobooks WHERE sha256_hash = ?
        """, (hash_val,))
        total_copies = cursor.fetchone()['count']

        # How many are we trying to delete?
        deleting_count = len(items)

        if deleting_count >= total_copies:
            # Would delete all copies - block the last one
            # Sort by ID to keep the lowest (original)
            items_sorted = sorted(items, key=lambda x: x['id'])
            blocked_ids.append(items_sorted[0]['id'])
            safe_to_delete.extend([i['id'] for i in items_sorted[1:]])
        else:
            # Safe to delete all requested
            safe_to_delete.extend([i['id'] for i in items])

    # Now perform the actual deletions
    deleted_files = []
    errors = []

    for audiobook_id in safe_to_delete:
        cursor.execute("SELECT file_path, title FROM audiobooks WHERE id = ?", (audiobook_id,))
        row = cursor.fetchone()

        if not row:
            continue

        file_path = Path(row['file_path'])
        title = row['title']

        try:
            # Delete the physical file
            if file_path.exists():
                file_path.unlink()

            # Delete from database
            cursor.execute("DELETE FROM audiobook_topics WHERE audiobook_id = ?", (audiobook_id,))
            cursor.execute("DELETE FROM audiobook_eras WHERE audiobook_id = ?", (audiobook_id,))
            cursor.execute("DELETE FROM audiobook_genres WHERE audiobook_id = ?", (audiobook_id,))
            cursor.execute("DELETE FROM audiobooks WHERE id = ?", (audiobook_id,))

            deleted_files.append({
                'id': audiobook_id,
                'title': title,
                'path': str(file_path)
            })

        except Exception as e:
            errors.append({
                'id': audiobook_id,
                'title': title,
                'error': str(e)
            })

    conn.commit()
    conn.close()

    return jsonify({
        'success': True,
        'deleted_count': len(deleted_files),
        'deleted_files': deleted_files,
        'blocked_count': len(blocked_ids),
        'blocked_ids': blocked_ids,
        'blocked_reason': 'These IDs were blocked to prevent deleting the last copy',
        'errors': errors
    })


@app.route('/api/duplicates/verify', methods=['POST'])
def verify_deletion_safe():
    """
    Verify that a list of IDs can be safely deleted.
    Returns which IDs are safe and which would delete the last copy.
    """
    data = request.get_json()
    if not data or 'audiobook_ids' not in data:
        return jsonify({'error': 'Missing audiobook_ids'}), 400

    ids_to_check = data['audiobook_ids']

    conn = get_db()
    cursor = conn.cursor()

    placeholders = ','.join('?' * len(ids_to_check))
    cursor.execute(f"""
        SELECT id, sha256_hash, title
        FROM audiobooks
        WHERE id IN ({placeholders})
    """, ids_to_check)

    items = [dict(row) for row in cursor.fetchall()]

    # Group by hash
    hash_groups = {}
    for item in items:
        h = item['sha256_hash']
        if h not in hash_groups:
            hash_groups[h] = []
        hash_groups[h].append(item)

    safe_ids = []
    unsafe_ids = []

    for hash_val, group_items in hash_groups.items():
        if hash_val is None:
            # No hash - can't verify safety
            unsafe_ids.extend([{'id': i['id'], 'title': i['title'], 'reason': 'No hash - cannot verify duplicates'} for i in group_items])
            continue

        cursor.execute("SELECT COUNT(*) as count FROM audiobooks WHERE sha256_hash = ?", (hash_val,))
        total_copies = cursor.fetchone()['count']

        if len(group_items) >= total_copies:
            # Would delete all - block the first one (keeper)
            sorted_items = sorted(group_items, key=lambda x: x['id'])
            unsafe_ids.append({
                'id': sorted_items[0]['id'],
                'title': sorted_items[0]['title'],
                'reason': 'Last remaining copy - protected from deletion'
            })
            safe_ids.extend([i['id'] for i in sorted_items[1:]])
        else:
            safe_ids.extend([i['id'] for i in group_items])

    conn.close()

    return jsonify({
        'safe_ids': safe_ids,
        'unsafe_ids': unsafe_ids,
        'safe_count': len(safe_ids),
        'unsafe_count': len(unsafe_ids)
    })


if __name__ == '__main__':
    if not DB_PATH.exists():
        print(f"Error: Database not found at {DB_PATH}")
        print("Please run: python3 backend/import_to_db.py")
        exit(1)

    print(f"Starting Audiobook Library API...")
    print(f"Database: {DB_PATH}")
    print(f"API running on: http://localhost:5000")
    print(f"\nEndpoints:")
    print(f"  GET /api/stats - Library statistics")
    print(f"  GET /api/audiobooks - Paginated audiobooks")
    print(f"  GET /api/audiobooks/<id> - Single audiobook")
    print(f"  GET /api/filters - Available filter options")
    print(f"  GET /api/stream/<id> - Stream audiobook file")
    print(f"  GET /covers/<filename> - Cover images")
    print(f"\nExample queries:")
    print(f"  /api/audiobooks?page=1&per_page=50")
    print(f"  /api/audiobooks?search=tolkien")
    print(f"  /api/audiobooks?author=sanderson&sort=duration_hours&order=desc")

    app.run(debug=True, host='0.0.0.0', port=5001)
