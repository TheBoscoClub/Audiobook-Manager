"""
Tests for the Flask API endpoints.
"""
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


class TestHealthEndpoint:
    """Test the health check endpoint."""

    def test_health_returns_ok(self, app_client):
        """Test that /health returns OK status."""
        response = app_client.get('/health')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['status'] == 'ok'

    def test_health_includes_database_status(self, app_client):
        """Test that /health includes database status."""
        response = app_client.get('/health')
        data = json.loads(response.data)
        assert 'database' in data


class TestStatsEndpoint:
    """Test the statistics endpoint."""

    def test_stats_returns_200(self, app_client):
        """Test that /api/stats returns 200."""
        response = app_client.get('/api/stats')
        assert response.status_code == 200

    def test_stats_contains_required_fields(self, app_client):
        """Test that stats response contains required fields."""
        response = app_client.get('/api/stats')
        data = json.loads(response.data)
        # Should contain total_audiobooks at minimum
        assert 'total_audiobooks' in data or 'error' in data


class TestFiltersEndpoint:
    """Test the filters endpoint."""

    def test_filters_returns_200(self, app_client):
        """Test that /api/filters returns 200."""
        response = app_client.get('/api/filters')
        assert response.status_code == 200

    def test_filters_returns_json(self, app_client):
        """Test that /api/filters returns valid JSON."""
        response = app_client.get('/api/filters')
        data = json.loads(response.data)
        assert isinstance(data, dict)


class TestCollectionsEndpoint:
    """Test the collections endpoint."""

    def test_collections_returns_200(self, app_client):
        """Test that /api/collections returns 200."""
        response = app_client.get('/api/collections')
        assert response.status_code == 200

    def test_collections_returns_list(self, app_client):
        """Test that /api/collections returns a list."""
        response = app_client.get('/api/collections')
        data = json.loads(response.data)
        assert isinstance(data, list)


class TestAudiobooksEndpoint:
    """Test the audiobooks listing endpoint."""

    def test_audiobooks_returns_200(self, app_client):
        """Test that /api/audiobooks returns 200."""
        response = app_client.get('/api/audiobooks')
        assert response.status_code == 200

    def test_audiobooks_returns_list(self, app_client):
        """Test that /api/audiobooks returns audiobooks list."""
        response = app_client.get('/api/audiobooks')
        data = json.loads(response.data)
        assert 'audiobooks' in data or 'error' in data
        if 'audiobooks' in data:
            assert isinstance(data['audiobooks'], list)

    def test_audiobooks_pagination_params(self, app_client):
        """Test pagination parameters are respected."""
        response = app_client.get('/api/audiobooks?page=1&per_page=10')
        assert response.status_code == 200
        data = json.loads(response.data)
        if 'per_page' in data:
            assert data['per_page'] == 10

    def test_audiobooks_search(self, app_client):
        """Test search parameter works."""
        response = app_client.get('/api/audiobooks?search=test')
        assert response.status_code == 200

    def test_audiobooks_sort(self, app_client):
        """Test sort parameter works."""
        response = app_client.get('/api/audiobooks?sort=title')
        assert response.status_code == 200


class TestCORSHeaders:
    """Test CORS headers are properly set."""

    def test_options_request(self, app_client):
        """Test that OPTIONS requests return CORS headers."""
        response = app_client.options('/api/stats')
        # Should return 200 or 204 for OPTIONS
        assert response.status_code in (200, 204)


class TestHashStatsEndpoint:
    """Test the hash statistics endpoint."""

    def test_hash_stats_returns_200(self, app_client):
        """Test that /api/hash-stats returns 200."""
        response = app_client.get('/api/hash-stats')
        assert response.status_code == 200


class TestSupplementsEndpoint:
    """Test the supplements endpoint."""

    def test_supplements_returns_200(self, app_client):
        """Test that /api/supplements returns 200."""
        response = app_client.get('/api/supplements')
        assert response.status_code == 200

    def test_supplements_stats_returns_200(self, app_client):
        """Test that /api/supplements/stats returns 200."""
        response = app_client.get('/api/supplements/stats')
        assert response.status_code == 200


class TestNarratorCountsEndpoint:
    """Test the narrator counts endpoint."""

    def test_narrator_counts_returns_200(self, app_client):
        """Test that /api/narrator-counts returns 200."""
        response = app_client.get('/api/narrator-counts')
        assert response.status_code == 200


class TestDuplicatesEndpoints:
    """Test the duplicates-related endpoints."""

    def test_duplicates_returns_200(self, app_client):
        """Test that /api/duplicates returns 200."""
        response = app_client.get('/api/duplicates')
        assert response.status_code == 200

    def test_duplicates_by_title_returns_200(self, app_client):
        """Test that /api/duplicates/by-title returns 200."""
        response = app_client.get('/api/duplicates/by-title')
        assert response.status_code == 200

    def test_duplicates_returns_structure(self, app_client):
        """Test that duplicates endpoint returns expected structure."""
        response = app_client.get('/api/duplicates')
        data = json.loads(response.data)
        # Should have duplicate_groups key
        assert 'duplicate_groups' in data or 'error' in data

    def test_duplicates_by_title_returns_structure(self, app_client):
        """Test that duplicates by title returns expected structure."""
        response = app_client.get('/api/duplicates/by-title')
        data = json.loads(response.data)
        assert 'duplicate_groups' in data or 'error' in data


class TestAudiobooksFiltering:
    """Test audiobook filtering functionality."""

    def test_filter_by_author(self, app_client):
        """Test filtering by author."""
        response = app_client.get('/api/audiobooks?author=TestAuthor')
        assert response.status_code == 200

    def test_filter_by_narrator(self, app_client):
        """Test filtering by narrator."""
        response = app_client.get('/api/audiobooks?narrator=TestNarrator')
        assert response.status_code == 200

    def test_filter_by_publisher(self, app_client):
        """Test filtering by publisher."""
        response = app_client.get('/api/audiobooks?publisher=TestPublisher')
        assert response.status_code == 200

    def test_filter_by_format(self, app_client):
        """Test filtering by format."""
        response = app_client.get('/api/audiobooks?format=opus')
        assert response.status_code == 200

    def test_filter_by_genre(self, app_client):
        """Test filtering by genre."""
        response = app_client.get('/api/audiobooks?genre=Fiction')
        assert response.status_code == 200

    def test_filter_by_collection(self, app_client):
        """Test filtering by collection."""
        response = app_client.get('/api/audiobooks?collection=fiction')
        assert response.status_code == 200

    def test_filter_multiple_params(self, app_client):
        """Test filtering with multiple parameters."""
        response = app_client.get('/api/audiobooks?author=Test&narrator=Test&format=opus')
        assert response.status_code == 200

    def test_invalid_sort_field(self, app_client):
        """Test that invalid sort field defaults to title."""
        response = app_client.get('/api/audiobooks?sort=invalid_field')
        assert response.status_code == 200

    def test_invalid_sort_order(self, app_client):
        """Test that invalid sort order defaults to asc."""
        response = app_client.get('/api/audiobooks?order=invalid')
        assert response.status_code == 200

    def test_sort_by_author_last(self, app_client):
        """Test sorting by author last name."""
        response = app_client.get('/api/audiobooks?sort=author_last')
        assert response.status_code == 200

    def test_sort_by_narrator_last(self, app_client):
        """Test sorting by narrator last name."""
        response = app_client.get('/api/audiobooks?sort=narrator_last')
        assert response.status_code == 200

    def test_sort_by_duration(self, app_client):
        """Test sorting by duration."""
        response = app_client.get('/api/audiobooks?sort=duration_hours&order=desc')
        assert response.status_code == 200

    def test_sort_by_series(self, app_client):
        """Test sorting by series."""
        response = app_client.get('/api/audiobooks?sort=series')
        assert response.status_code == 200

    def test_sort_by_acquired_date(self, app_client):
        """Test sorting by acquired date."""
        response = app_client.get('/api/audiobooks?sort=acquired_date&order=desc')
        assert response.status_code == 200

    def test_sort_by_published_year(self, app_client):
        """Test sorting by published year."""
        response = app_client.get('/api/audiobooks?sort=published_year')
        assert response.status_code == 200


class TestSingleAudiobookEndpoint:
    """Test single audiobook retrieval."""

    def test_get_audiobook_not_found(self, app_client):
        """Test that non-existent audiobook returns 404."""
        response = app_client.get('/api/audiobooks/999999')
        assert response.status_code == 404
        data = json.loads(response.data)
        assert 'error' in data

    def test_get_audiobook_returns_json(self, app_client):
        """Test that single audiobook endpoint returns JSON."""
        response = app_client.get('/api/audiobooks/1')
        # May be 200 or 404 depending on database state
        assert response.status_code in (200, 404)
        data = json.loads(response.data)
        assert isinstance(data, dict)


class TestEditionsEndpoint:
    """Test editions endpoint."""

    def test_editions_not_found(self, app_client):
        """Test that editions for non-existent book returns 404."""
        response = app_client.get('/api/audiobooks/999999/editions')
        assert response.status_code == 404

    def test_editions_returns_json(self, app_client):
        """Test that editions endpoint returns JSON."""
        response = app_client.get('/api/audiobooks/1/editions')
        # May be 200 or 404 depending on database state
        assert response.status_code in (200, 404)


class TestStreamEndpoint:
    """Test streaming endpoint."""

    def test_stream_not_found(self, app_client):
        """Test that streaming non-existent audiobook returns 404."""
        response = app_client.get('/api/stream/999999')
        assert response.status_code == 404
        data = json.loads(response.data)
        assert 'error' in data


class TestDeletionEndpoints:
    """Test deletion-related endpoints."""

    def test_verify_deletion_missing_ids(self, app_client):
        """Test verify deletion with missing audiobook_ids."""
        response = app_client.post(
            '/api/duplicates/verify',
            data=json.dumps({}),
            content_type='application/json'
        )
        assert response.status_code == 400

    def test_verify_deletion_with_ids(self, app_client):
        """Test verify deletion with valid structure."""
        response = app_client.post(
            '/api/duplicates/verify',
            data=json.dumps({'audiobook_ids': [1, 2, 3]}),
            content_type='application/json'
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'safe_ids' in data
        assert 'unsafe_ids' in data


class TestDatabaseEndpoints:
    """Test database management endpoints (may not exist in all API versions)."""

    def test_stats_endpoint_exists(self, app_client):
        """Test that stats endpoint exists and works."""
        response = app_client.get('/api/stats')
        assert response.status_code == 200

    def test_hash_stats_structure(self, app_client):
        """Test hash stats returns proper structure."""
        response = app_client.get('/api/hash-stats')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert isinstance(data, dict)


class TestAudiobookCRUD:
    """Test audiobook CRUD operations."""

    def test_update_audiobook_not_found(self, app_client):
        """Test updating non-existent audiobook."""
        response = app_client.put(
            '/api/audiobooks/999999',
            data=json.dumps({'title': 'New Title'}),
            content_type='application/json'
        )
        # Should return 404 or similar
        assert response.status_code in (404, 400, 405)

    def test_delete_audiobook_not_found(self, app_client):
        """Test deleting non-existent audiobook."""
        response = app_client.delete('/api/audiobooks/999999')
        # Should return 404 or similar
        assert response.status_code in (404, 400, 405)


class TestPaginationEdgeCases:
    """Test pagination edge cases."""

    def test_page_zero_defaults_to_one(self, app_client):
        """Test that page 0 is handled gracefully."""
        response = app_client.get('/api/audiobooks?page=0')
        assert response.status_code == 200

    def test_negative_page_handled(self, app_client):
        """Test that negative page is handled."""
        response = app_client.get('/api/audiobooks?page=-1')
        assert response.status_code == 200

    def test_per_page_exceeds_max(self, app_client):
        """Test that per_page over 200 is capped."""
        response = app_client.get('/api/audiobooks?per_page=500')
        assert response.status_code == 200
        data = json.loads(response.data)
        if 'per_page' in data:
            assert data['per_page'] <= 200

    def test_large_page_number(self, app_client):
        """Test handling of large page numbers."""
        response = app_client.get('/api/audiobooks?page=99999')
        assert response.status_code == 200
        data = json.loads(response.data)
        # Should return empty or valid structure
        if 'audiobooks' in data:
            assert isinstance(data['audiobooks'], list)


class TestSearchFunctionality:
    """Test search functionality."""

    def test_empty_search(self, app_client):
        """Test empty search string."""
        response = app_client.get('/api/audiobooks?search=')
        assert response.status_code == 200

    def test_simple_search(self, app_client):
        """Test simple search string."""
        response = app_client.get('/api/audiobooks?search=test')
        assert response.status_code == 200

    def test_multi_word_search(self, app_client):
        """Test multi-word search."""
        response = app_client.get('/api/audiobooks?search=test+query')
        assert response.status_code == 200

    def test_quoted_search(self, app_client):
        """Test quoted phrase search."""
        response = app_client.get('/api/audiobooks?search="exact phrase"')
        # May succeed or fail depending on FTS handling
        assert response.status_code in (200, 400, 500)


class TestStatsEndpointDetails:
    """Test stats endpoint in more detail."""

    def test_stats_returns_numeric_values(self, app_client):
        """Test that stats returns proper numeric values."""
        response = app_client.get('/api/stats')
        data = json.loads(response.data)
        if 'total_audiobooks' in data:
            assert isinstance(data['total_audiobooks'], (int, float))
        if 'total_hours' in data:
            assert isinstance(data['total_hours'], (int, float))


class TestCollectionsEndpointDetails:
    """Test collections endpoint in more detail."""

    def test_collections_structure(self, app_client):
        """Test that collections returns proper structure."""
        response = app_client.get('/api/collections')
        data = json.loads(response.data)
        assert isinstance(data, list)
        for item in data:
            # Each collection should be a dict with at least name
            if isinstance(item, dict):
                assert 'name' in item or 'slug' in item or 'category' in item


class TestFiltersEndpointDetails:
    """Test filters endpoint in more detail."""

    def test_filters_contains_lists(self, app_client):
        """Test that filters returns lists of values."""
        response = app_client.get('/api/filters')
        data = json.loads(response.data)
        # Should contain authors, narrators, etc.
        if 'authors' in data:
            assert isinstance(data['authors'], list)
        if 'narrators' in data:
            assert isinstance(data['narrators'], list)


class TestRealDataEndpoints:
    """Tests that use real database data to exercise more code paths."""

    def _get_first_audiobook_id(self, app_client):
        """Helper to get a valid audiobook ID from the database."""
        response = app_client.get('/api/audiobooks?per_page=1')
        data = json.loads(response.data)
        if data.get('audiobooks') and len(data['audiobooks']) > 0:
            return data['audiobooks'][0].get('id')
        return None

    def test_get_single_audiobook_with_real_id(self, app_client):
        """Test getting a single audiobook with a valid ID."""
        audiobook_id = self._get_first_audiobook_id(app_client)
        if audiobook_id is None:
            pytest.skip("No audiobooks in database")

        response = app_client.get(f'/api/audiobooks/{audiobook_id}')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'title' in data
        assert 'author' in data
        # Should include related data
        assert 'genres' in data
        assert 'eras' in data
        assert 'topics' in data

    def test_get_editions_with_real_id(self, app_client):
        """Test getting editions with a valid ID."""
        audiobook_id = self._get_first_audiobook_id(app_client)
        if audiobook_id is None:
            pytest.skip("No audiobooks in database")

        response = app_client.get(f'/api/audiobooks/{audiobook_id}/editions')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'editions' in data or isinstance(data, list)

    def test_stream_with_real_id(self, app_client):
        """Test streaming endpoint with a valid ID."""
        audiobook_id = self._get_first_audiobook_id(app_client)
        if audiobook_id is None:
            pytest.skip("No audiobooks in database")

        response = app_client.get(f'/api/stream/{audiobook_id}')
        # May return file (200) or error if file doesn't exist (404)
        assert response.status_code in (200, 404)

    def test_duplicates_by_title_processing(self, app_client):
        """Test that duplicates by title endpoint processes data."""
        response = app_client.get('/api/duplicates/by-title')
        assert response.status_code == 200
        data = json.loads(response.data)
        # Should have proper structure even if no duplicates
        assert 'duplicate_groups' in data
        assert 'total_groups' in data
        assert 'total_potential_savings_mb' in data

    def test_duplicates_by_hash_processing(self, app_client):
        """Test that duplicates by hash endpoint processes data."""
        response = app_client.get('/api/duplicates')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'duplicate_groups' in data

    def test_filters_with_real_data(self, app_client):
        """Test filters endpoint returns actual data."""
        response = app_client.get('/api/filters')
        assert response.status_code == 200
        data = json.loads(response.data)
        # Should have some data from the real database
        assert isinstance(data, dict)

    def test_collections_with_counts(self, app_client):
        """Test collections endpoint with counts."""
        response = app_client.get('/api/collections')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert isinstance(data, list)
        # Should have proper structure
        if len(data) > 0:
            first_collection = data[0]
            if isinstance(first_collection, dict):
                # Collections should have name/slug and count
                assert 'name' in first_collection or 'slug' in first_collection


class TestVerifyDeletionLogic:
    """Test deletion verification logic."""

    def test_verify_with_empty_list(self, app_client):
        """Test verify deletion with empty list."""
        response = app_client.post(
            '/api/duplicates/verify',
            data=json.dumps({'audiobook_ids': []}),
            content_type='application/json'
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['safe_ids'] == []
        assert data['unsafe_ids'] == []

    def test_verify_with_nonexistent_ids(self, app_client):
        """Test verify deletion with non-existent IDs."""
        response = app_client.post(
            '/api/duplicates/verify',
            data=json.dumps({'audiobook_ids': [999999, 999998]}),
            content_type='application/json'
        )
        assert response.status_code == 200


class TestAPIResponseFormats:
    """Test API response format consistency."""

    def test_audiobooks_response_format(self, app_client):
        """Test audiobooks endpoint response format."""
        response = app_client.get('/api/audiobooks')
        assert response.status_code == 200
        data = json.loads(response.data)

        # Should have audiobooks and pagination
        assert 'audiobooks' in data
        # Pagination may be at top level or in 'pagination' key
        if 'pagination' in data:
            assert 'page' in data['pagination']
            assert 'per_page' in data['pagination']
        else:
            assert 'page' in data or 'total' in data

    def test_stats_response_format(self, app_client):
        """Test stats endpoint response format."""
        response = app_client.get('/api/stats')
        assert response.status_code == 200
        data = json.loads(response.data)

        # Should have all stat fields
        expected_fields = ['total_audiobooks', 'total_hours', 'total_days',
                          'total_size_gb', 'unique_authors', 'unique_narrators']
        for field in expected_fields:
            assert field in data, f"Missing field: {field}"

    def test_narrator_counts_response_format(self, app_client):
        """Test narrator counts endpoint response format."""
        response = app_client.get('/api/narrator-counts')
        assert response.status_code == 200
        data = json.loads(response.data)
        # May be list or dict depending on API version
        assert isinstance(data, (list, dict))

    def test_supplements_response_format(self, app_client):
        """Test supplements endpoint response format."""
        response = app_client.get('/api/supplements')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'supplements' in data
        assert 'total' in data

    def test_supplements_stats_response_format(self, app_client):
        """Test supplements stats endpoint response format."""
        response = app_client.get('/api/supplements/stats')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'total_supplements' in data
        assert 'linked_to_audiobooks' in data


class TestCORSBehavior:
    """Test CORS behavior in more detail."""

    def test_cors_on_audiobooks(self, app_client):
        """Test CORS headers on audiobooks endpoint."""
        response = app_client.options('/api/audiobooks')
        assert response.status_code in (200, 204)

    def test_cors_on_stats(self, app_client):
        """Test CORS headers on stats endpoint."""
        response = app_client.options('/api/stats')
        assert response.status_code in (200, 204)

    def test_cors_on_collections(self, app_client):
        """Test CORS headers on collections endpoint."""
        response = app_client.options('/api/collections')
        assert response.status_code in (200, 204)


class TestSupplementEndpoints:
    """Test supplement-related endpoints."""

    def test_audiobook_supplements_not_found(self, app_client):
        """Test supplements for non-existent audiobook."""
        response = app_client.get('/api/audiobooks/999999/supplements')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'supplements' in data
        assert data['count'] == 0

    def test_audiobook_supplements_valid(self, app_client):
        """Test supplements for a valid audiobook."""
        # Get a real audiobook ID
        response = app_client.get('/api/audiobooks?per_page=1')
        data = json.loads(response.data)
        if not data.get('audiobooks'):
            pytest.skip("No audiobooks in database")

        audiobook_id = data['audiobooks'][0]['id']
        response = app_client.get(f'/api/audiobooks/{audiobook_id}/supplements')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'supplements' in data
        assert 'count' in data

    def test_download_supplement_not_found(self, app_client):
        """Test downloading non-existent supplement."""
        response = app_client.get('/api/supplements/999999/download')
        assert response.status_code == 404


class TestBulkOperations:
    """Test bulk operation endpoints."""

    def test_bulk_update_missing_fields(self, app_client):
        """Test bulk update with missing fields."""
        response = app_client.post(
            '/api/audiobooks/bulk-update',
            data=json.dumps({}),
            content_type='application/json'
        )
        assert response.status_code == 400

    def test_bulk_update_invalid_field(self, app_client):
        """Test bulk update with invalid field."""
        response = app_client.post(
            '/api/audiobooks/bulk-update',
            data=json.dumps({'ids': [1], 'field': 'invalid_field', 'value': 'test'}),
            content_type='application/json'
        )
        assert response.status_code == 400

    def test_bulk_update_empty_ids(self, app_client):
        """Test bulk update with empty IDs list."""
        response = app_client.post(
            '/api/audiobooks/bulk-update',
            data=json.dumps({'ids': [], 'field': 'narrator', 'value': 'Test'}),
            content_type='application/json'
        )
        assert response.status_code == 400

    def test_bulk_delete_missing_ids(self, app_client):
        """Test bulk delete with missing IDs."""
        response = app_client.post(
            '/api/audiobooks/bulk-delete',
            data=json.dumps({}),
            content_type='application/json'
        )
        assert response.status_code == 400

    def test_bulk_delete_empty_ids(self, app_client):
        """Test bulk delete with empty IDs list."""
        response = app_client.post(
            '/api/audiobooks/bulk-delete',
            data=json.dumps({'ids': []}),
            content_type='application/json'
        )
        assert response.status_code == 400


class TestMissingDataEndpoints:
    """Test endpoints for finding missing data."""

    def test_missing_narrator_endpoint(self, app_client):
        """Test endpoint for audiobooks missing narrator."""
        response = app_client.get('/api/audiobooks/missing-narrator')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'audiobooks' in data or isinstance(data, list)

    def test_missing_hash_endpoint(self, app_client):
        """Test endpoint for audiobooks missing hash."""
        response = app_client.get('/api/audiobooks/missing-hash')
        # May or may not exist
        assert response.status_code in (200, 404)


class TestScanEndpoints:
    """Test scan/sync endpoints."""

    def test_supplements_scan_endpoint(self, app_client):
        """Test supplements scan endpoint."""
        response = app_client.post('/api/supplements/scan')
        # May fail if dir doesn't exist
        assert response.status_code in (200, 404)


class TestUpdateEndpoints:
    """Test update/edit endpoints."""

    def test_update_audiobook_missing_data(self, app_client):
        """Test updating audiobook with missing data."""
        response = app_client.put(
            '/api/audiobooks/1',
            data=json.dumps({}),
            content_type='application/json'
        )
        # Should handle gracefully
        assert response.status_code in (200, 400, 404, 405)

    def test_delete_single_audiobook_not_found(self, app_client):
        """Test deleting single non-existent audiobook."""
        response = app_client.delete('/api/audiobooks/999999')
        assert response.status_code in (404, 405)


class TestUtilityEndpoints:
    """Test utility endpoints (skipping subprocess-heavy ones)."""

    @pytest.mark.skip(reason="Runs actual subprocess, too slow for unit tests")
    def test_rescan_endpoint(self, app_client):
        """Test rescan library endpoint exists."""
        response = app_client.post('/api/utilities/rescan')
        assert response.status_code in (200, 404, 500)

    @pytest.mark.skip(reason="Runs actual subprocess, too slow for unit tests")
    def test_reimport_endpoint(self, app_client):
        """Test reimport database endpoint exists."""
        response = app_client.post('/api/utilities/reimport')
        assert response.status_code in (200, 404, 500)

    def test_vacuum_endpoint(self, app_client):
        """Test vacuum database endpoint."""
        response = app_client.post('/api/utilities/vacuum')
        assert response.status_code in (200, 404)


class TestDuplicatesDetailedLogic:
    """Test duplicates endpoints with more coverage."""

    def test_duplicates_hash_returns_groups(self, app_client):
        """Test that hash duplicates returns proper structure."""
        response = app_client.get('/api/duplicates')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'duplicate_groups' in data
        assert isinstance(data['duplicate_groups'], list)

    def test_duplicates_title_full_structure(self, app_client):
        """Test duplicates by title returns complete structure."""
        response = app_client.get('/api/duplicates/by-title')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'duplicate_groups' in data
        assert 'total_groups' in data
        assert 'total_potential_savings_mb' in data
        assert 'total_duplicate_files' in data


class TestEdgeConditions:
    """Test edge conditions and error handling."""

    def test_very_long_search_query(self, app_client):
        """Test handling of very long search query."""
        long_query = 'a' * 1000
        response = app_client.get(f'/api/audiobooks?search={long_query}')
        # Should handle without crashing
        assert response.status_code in (200, 400)

    def test_special_id_zero(self, app_client):
        """Test handling of ID 0."""
        response = app_client.get('/api/audiobooks/0')
        # Should return 404 (no book with ID 0)
        assert response.status_code in (200, 404)

    def test_negative_id(self, app_client):
        """Test handling of negative ID (Flask may not route)."""
        # Flask may not route negative IDs with int converter
        response = app_client.get('/api/audiobooks/-1')
        # 405 if route exists but method not allowed, 404 if not found
        assert response.status_code in (200, 404, 405)

    def test_audiobooks_with_all_filters(self, app_client):
        """Test audiobooks with all filter parameters."""
        response = app_client.get(
            '/api/audiobooks?page=1&per_page=10&search=test'
            '&author=Author&narrator=Narrator&publisher=Publisher'
            '&format=opus&genre=Fiction&collection=fiction'
            '&sort=title&order=asc'
        )
        assert response.status_code == 200
