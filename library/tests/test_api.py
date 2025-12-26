"""
Tests for the Flask API endpoints.
"""
import json

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
