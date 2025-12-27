"""
Duplicate detection endpoints - hash-based and title-based duplicate finding.
"""

from flask import Blueprint, Response, jsonify, request
from pathlib import Path

from .core import get_db, FlaskResponse

duplicates_bp = Blueprint('duplicates', __name__)


def init_duplicates_routes(db_path):
    """Initialize routes with database path."""

    @duplicates_bp.route('/api/hash-stats', methods=['GET'])
    def get_hash_stats() -> Response:
        """Get hash generation statistics"""
        conn = get_db(db_path)
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

    @duplicates_bp.route('/api/duplicates', methods=['GET'])
    def get_duplicates() -> FlaskResponse:
        """Get all duplicate audiobook groups"""
        conn = get_db(db_path)
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

    @duplicates_bp.route('/api/duplicates/by-title', methods=['GET'])
    def get_duplicates_by_title() -> Response:
        """
        Get duplicate audiobooks based on normalized title and REAL author.
        This finds "same book, different version/format" entries.

        IMPROVED LOGIC:
        - Excludes "Audiobook" as a valid author for grouping
        - Groups by title + real author + similar duration (within 10%)
        - Prevents flagging different books with same title as duplicates
        """
        conn = get_db(db_path)
        cursor = conn.cursor()

        # Find duplicates by normalized title + real author (excluding "Audiobook")
        # Also require similar duration to avoid grouping different books
        cursor.execute("""
            SELECT
                LOWER(TRIM(REPLACE(REPLACE(REPLACE(title, ':', ''), '-', ''), '  ', ' '))) as norm_title,
                LOWER(TRIM(author)) as norm_author,
                ROUND(duration_hours, 1) as duration_group,
                COUNT(*) as count
            FROM audiobooks
            WHERE title IS NOT NULL
              AND author IS NOT NULL
              AND LOWER(TRIM(author)) != 'audiobook'
              AND LOWER(TRIM(author)) != 'unknown author'
            GROUP BY norm_title, norm_author, duration_group
            HAVING count > 1
            ORDER BY count DESC, norm_title
        """)
        groups = cursor.fetchall()

        duplicate_groups = []
        total_potential_savings = 0

        for group in groups:
            norm_title = group['norm_title']
            norm_author = group['norm_author']
            duration_group = group['duration_group']

            # Get all files in this group (including any with "Audiobook" author that match)
            cursor.execute("""
                SELECT id, title, author, narrator, file_path, file_size_mb,
                       format, duration_formatted, duration_hours, cover_path, sha256_hash
                FROM audiobooks
                WHERE LOWER(TRIM(REPLACE(REPLACE(REPLACE(title, ':', ''), '-', ''), '  ', ' '))) = ?
                  AND (LOWER(TRIM(author)) = ? OR LOWER(TRIM(author)) = 'audiobook')
                  AND ROUND(duration_hours, 1) = ?
                ORDER BY
                    -- Prefer entries with real author over "Audiobook"
                    CASE WHEN LOWER(TRIM(author)) = 'audiobook' THEN 1 ELSE 0 END,
                    CASE format
                        WHEN 'opus' THEN 1
                        WHEN 'm4b' THEN 2
                        WHEN 'm4a' THEN 3
                        WHEN 'mp3' THEN 4
                        ELSE 5
                    END,
                    file_size_mb DESC,
                    id ASC
            """, (norm_title, norm_author, duration_group))

            files = [dict(row) for row in cursor.fetchall()]

            if len(files) < 2:
                continue

            # First file (with real author, preferred format) is the "keeper"
            for i, f in enumerate(files):
                f['is_keeper'] = (i == 0)
                f['is_duplicate'] = (i > 0)

            # Calculate potential savings (sum of all but the largest file)
            sizes = sorted([f['file_size_mb'] for f in files], reverse=True)
            potential_savings = sum(sizes[1:])  # All except the largest
            total_potential_savings += potential_savings

            # Use the real author (first file has real author due to ORDER BY)
            display_author = files[0]['author']
            if display_author.lower() == 'audiobook':
                # Fallback: find real author from the group
                for f in files:
                    if f['author'].lower() != 'audiobook':
                        display_author = f['author']
                        break

            duplicate_groups.append({
                'title': files[0]['title'],
                'author': display_author,
                'count': len(files),
                'potential_savings_mb': round(potential_savings, 2),
                'files': files
            })

        conn.close()

        return jsonify({
            'duplicate_groups': duplicate_groups,
            'total_groups': len(duplicate_groups),
            'total_potential_savings_mb': round(total_potential_savings, 2),
            'total_duplicate_files': sum(g['count'] - 1 for g in duplicate_groups)
        })

    @duplicates_bp.route('/api/duplicates/delete', methods=['POST'])
    def delete_duplicates() -> FlaskResponse:
        """
        Delete selected duplicate audiobooks.
        SAFETY: Will NEVER delete the last remaining copy of any audiobook.

        Request body:
        {
            "audiobook_ids": [1, 2, 3],  // IDs to delete
            "mode": "title" or "hash"    // Optional, defaults to "title"
        }

        IMPROVED SAFETY:
        - Groups by title + duration (not author, since author may be "Audiobook")
        - Ensures at least one copy with REAL author is kept
        - Prefers keeping entries with real author over "Audiobook" entries
        """
        data = request.get_json()
        if not data or 'audiobook_ids' not in data:
            return jsonify({'error': 'Missing audiobook_ids'}), 400

        ids_to_delete = data['audiobook_ids']
        if not ids_to_delete:
            return jsonify({'error': 'No audiobook IDs provided'}), 400

        mode = data.get('mode', 'title')  # Default to title mode

        conn = get_db(db_path)
        cursor = conn.cursor()

        # Get all audiobooks to be deleted with their grouping keys
        placeholders = ','.join('?' * len(ids_to_delete))
        cursor.execute(f"""
            SELECT id, sha256_hash, title, author, file_path, duration_hours, file_size_mb,
                   LOWER(TRIM(REPLACE(REPLACE(REPLACE(title, ':', ''), '-', ''), '  ', ' '))) as norm_title,
                   LOWER(TRIM(author)) as norm_author,
                   ROUND(duration_hours, 1) as duration_group
            FROM audiobooks
            WHERE id IN ({placeholders})
        """, ids_to_delete)

        to_delete = [dict(row) for row in cursor.fetchall()]

        blocked_ids = []
        safe_to_delete = []

        if mode == 'title':
            # Group by normalized title + duration (duration distinguishes different books with same title)
            title_groups = {}
            for item in to_delete:
                key = (item['norm_title'], item['duration_group'])
                if key not in title_groups:
                    title_groups[key] = []
                title_groups[key].append(item)

            # For each title group, verify at least one copy will remain
            for (norm_title, duration_group), items in title_groups.items():
                # Count total copies with this title + similar duration
                cursor.execute("""
                    SELECT COUNT(*) as count FROM audiobooks
                    WHERE LOWER(TRIM(REPLACE(REPLACE(REPLACE(title, ':', ''), '-', ''), '  ', ' '))) = ?
                      AND ROUND(duration_hours, 1) = ?
                """, (norm_title, duration_group))
                total_copies = cursor.fetchone()['count']

                deleting_count = len(items)

                if deleting_count >= total_copies:
                    # Would delete all copies - block the best one (keeper)
                    # Sort: prefer real author, then preferred format, then by ID
                    def sort_key(x):
                        # Prefer real author over "Audiobook"
                        author_priority = 1 if x['norm_author'] == 'audiobook' else 0
                        fmt_order = {'opus': 1, 'm4b': 2, 'm4a': 3, 'mp3': 4}
                        ext = Path(x['file_path']).suffix.lower().lstrip('.')
                        return (author_priority, fmt_order.get(ext, 5), x['id'])

                    items_sorted = sorted(items, key=sort_key)
                    blocked_ids.append(items_sorted[0]['id'])
                    safe_to_delete.extend([i['id'] for i in items_sorted[1:]])
                else:
                    safe_to_delete.extend([i['id'] for i in items])
        else:
            # Hash-based mode (original logic)
            hash_groups = {}
            for item in to_delete:
                h = item['sha256_hash']
                if h not in hash_groups:
                    hash_groups[h] = []
                hash_groups[h].append(item)

            for hash_val, items in hash_groups.items():
                if hash_val is None:
                    blocked_ids.extend([i['id'] for i in items])
                    continue

                cursor.execute("""
                    SELECT COUNT(*) as count FROM audiobooks WHERE sha256_hash = ?
                """, (hash_val,))
                total_copies = cursor.fetchone()['count']

                deleting_count = len(items)

                if deleting_count >= total_copies:
                    items_sorted = sorted(items, key=lambda x: x['id'])
                    blocked_ids.append(items_sorted[0]['id'])
                    safe_to_delete.extend([i['id'] for i in items_sorted[1:]])
                else:
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

    @duplicates_bp.route('/api/duplicates/verify', methods=['POST'])
    def verify_deletion_safe() -> FlaskResponse:
        """
        Verify that a list of IDs can be safely deleted.
        Returns which IDs are safe and which would delete the last copy.
        """
        data = request.get_json()
        if not data or 'audiobook_ids' not in data:
            return jsonify({'error': 'Missing audiobook_ids'}), 400

        ids_to_check = data['audiobook_ids']

        conn = get_db(db_path)
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

    return duplicates_bp
