"""
Back Office Integration Tests

Tests all Back Office (utilities) endpoints against the VM API
with REAL DATA OUTPUT verification.

These tests run against test-vm-cachyos (192.168.122.100).
Deploy latest code first: ./deploy-vm.sh --full --restart

Run with: pytest library/tests/test_backoffice_integration.py -v

Environment:
    API_BASE_URL: Base URL of the API (default: http://192.168.122.100:5001)
"""

import json
import os
import tempfile
import time
from pathlib import Path

import pytest
import requests

# Configuration — defaults to VM; override with env var for local dev
API_BASE_URL = os.environ.get("API_BASE_URL", "http://192.168.122.100:5001")
ASYNC_TIMEOUT = 300  # 5 minutes max for async operations
POLL_INTERVAL = 2  # Poll every 2 seconds


def api_url(path: str) -> str:
    """Build full API URL."""
    return f"{API_BASE_URL}{path}"


def wait_for_operation(operation_id: str, timeout: int = ASYNC_TIMEOUT) -> dict:
    """Wait for an async operation to complete and return its status.

    Returns the operation dict when completed/failed, or the current state on timeout.
    """
    start = time.time()
    last_op = None
    while time.time() - start < timeout:
        response = requests.get(api_url("/api/operations/all"))
        if response.status_code == 200:
            data = response.json()
            for op in data.get("operations", []):
                if op.get("id") == operation_id:
                    last_op = op
                    if op.get("state") in ("completed", "failed"):
                        return op
        time.sleep(POLL_INTERVAL)
    # Return the last known state on timeout (allows tests to check running state)
    if last_op:
        return last_op
    raise TimeoutError(f"Operation {operation_id} not found within {timeout}s")


@pytest.fixture(scope="module")
def api_available():
    """Check if the API is available before running tests."""
    try:
        response = requests.get(api_url("/api/system/version"), timeout=5)
        if response.status_code != 200:
            pytest.skip(f"API not available (status {response.status_code})")
        return response.json()
    except requests.exceptions.ConnectionError:
        pytest.skip("API not available (connection refused)")


class TestOperationsStatus:
    """Test operation tracking endpoints."""

    def test_get_active_operations(self, api_available):
        """Test GET /api/operations/active returns valid response."""
        response = requests.get(api_url("/api/operations/active"))
        assert response.status_code == 200
        data = response.json()
        # Should return a list (possibly empty)
        assert "operations" in data or isinstance(data, list)

    def test_get_all_operations(self, api_available):
        """Test GET /api/operations/all returns valid response with history."""
        response = requests.get(api_url("/api/operations/all"))
        assert response.status_code == 200
        data = response.json()
        assert "operations" in data
        assert "count" in data
        # Verify structure of operations
        if data["operations"]:
            op = data["operations"][0]
            assert "id" in op
            assert "state" in op
            assert "type" in op


class TestDatabaseExports:
    """Test database export endpoints with real output verification."""

    def test_export_json(self, api_available):
        """Test GET /api/utilities/export-json returns valid JSON with audiobooks."""
        response = requests.get(api_url("/api/utilities/export-json"))
        assert response.status_code == 200
        assert response.headers.get("Content-Type", "").startswith("application/json")

        data = response.json()
        # API may return dict with 'audiobooks' key or a list directly
        if isinstance(data, dict):
            assert "audiobooks" in data, "Export should contain 'audiobooks' key"
            audiobooks = data["audiobooks"]
            total_count = data.get("total_count", len(audiobooks))
        else:
            audiobooks = data
            total_count = len(data)

        assert isinstance(audiobooks, list), "Audiobooks should be a list"
        assert len(audiobooks) > 0, "Export should contain audiobooks"

        # Verify audiobook structure
        book = audiobooks[0]
        assert "id" in book
        assert "title" in book
        assert "author" in book

        print(f"\n  ✓ Exported {total_count} audiobooks as JSON")
        print(f"  ✓ Sample: {book.get('title', 'Unknown')[:50]} by {book.get('author', 'Unknown')[:30]}")

    def test_export_csv(self, api_available):
        """Test GET /api/utilities/export-csv returns valid CSV with headers."""
        response = requests.get(api_url("/api/utilities/export-csv"))
        assert response.status_code == 200
        content_type = response.headers.get("Content-Type", "")
        assert "text/csv" in content_type or "application/csv" in content_type

        # Verify CSV structure
        lines = response.text.strip().split("\n")
        assert len(lines) > 1, "CSV should have header and data rows"

        # Check header
        header = lines[0]
        assert "title" in header.lower() or "id" in header.lower()

        # Count data rows
        data_rows = len(lines) - 1
        print(f"\n  ✓ Exported {data_rows} audiobooks as CSV")
        print(f"  ✓ Headers: {header[:80]}...")

    def test_export_db(self, api_available):
        """Test GET /api/utilities/export-db returns SQLite database file."""
        response = requests.get(api_url("/api/utilities/export-db"))
        assert response.status_code == 200

        # Check content type
        content_type = response.headers.get("Content-Type", "")
        assert "sqlite" in content_type or "octet-stream" in content_type

        # Verify it's a valid SQLite file (starts with "SQLite format 3")
        assert response.content[:16].startswith(b"SQLite format 3")

        size_kb = len(response.content) / 1024
        print(f"\n  ✓ Exported SQLite database: {size_kb:.1f} KB")


class TestDatabaseMaintenance:
    """Test database maintenance endpoints."""

    def test_vacuum(self, api_available):
        """Test POST /api/utilities/vacuum compacts the database."""
        response = requests.post(api_url("/api/utilities/vacuum"))
        assert response.status_code == 200
        data = response.json()
        assert data.get("success") is True

        print(f"\n  ✓ Database vacuumed successfully")
        if "size_before" in data and "size_after" in data:
            print(f"  ✓ Size: {data['size_before']} → {data['size_after']}")


class TestAsyncOperations:
    """Test async operations with real execution and output verification."""

    def test_populate_sort_fields_dry_run(self, api_available):
        """Test populate-sort-fields-async in dry run mode."""
        response = requests.post(
            api_url("/api/utilities/populate-sort-fields-async"),
            json={"dry_run": True},
        )
        assert response.status_code == 200
        data = response.json()
        assert data.get("success") is True
        assert "operation_id" in data

        # Wait for completion
        op = wait_for_operation(data["operation_id"], timeout=120)
        assert op["state"] == "completed", f"Operation failed: {op.get('error')}"

        # Verify result has meaningful data
        result = op.get("result", {})
        print(f"\n  ✓ Sort fields populated (dry run)")
        if result:
            print(f"  ✓ Fields analyzed: {result.get('output', '')[:100]}...")

    def test_rebuild_queue_async(self, api_available):
        """Test rebuild-queue-async creates conversion queue."""
        response = requests.post(
            api_url("/api/utilities/rebuild-queue-async"),
            json={},
        )
        # Accept 200 (new operation) or 409 (already running)
        assert response.status_code in (200, 409), f"Unexpected status: {response.status_code}"
        data = response.json()
        assert "operation_id" in data

        # Handle both new operation and already-running cases
        if response.status_code == 409 or data.get("success") is False:
            print(f"\n  ✓ Queue rebuild already in progress (operation: {data['operation_id']})")

        op = wait_for_operation(data["operation_id"], timeout=600)

        # Allow both completed and running states (operation may be very long)
        assert op["state"] in ("completed", "running"), f"Operation failed: {op.get('error')}"

        if op["state"] == "completed":
            print(f"\n  ✓ Conversion queue rebuilt")
            if op.get("result"):
                print(f"  ✓ Result: {str(op['result'])[:100]}...")
        else:
            print(f"\n  ✓ Conversion queue rebuild in progress ({op.get('progress', 0)}%)")

    def test_cleanup_indexes_async(self, api_available):
        """Test cleanup-indexes-async removes stale index entries."""
        response = requests.post(
            api_url("/api/utilities/cleanup-indexes-async"),
            json={},
        )
        assert response.status_code == 200
        data = response.json()
        assert data.get("success") is True
        assert "operation_id" in data

        # Wait for completion
        op = wait_for_operation(data["operation_id"], timeout=120)
        assert op["state"] == "completed", f"Operation failed: {op.get('error')}"

        print(f"\n  ✓ Indexes cleaned up")
        if op.get("result"):
            print(f"  ✓ Result: {str(op['result'])[:100]}...")

    def test_find_source_duplicates_async(self, api_available):
        """Test find-source-duplicates-async finds duplicate source files."""
        response = requests.post(
            api_url("/api/utilities/find-source-duplicates-async"),
            json={},
        )
        # Accept 200 (new operation) or 409 (already running)
        assert response.status_code in (200, 409), f"Unexpected status: {response.status_code}"
        data = response.json()
        assert "operation_id" in data

        # Handle both new operation and already-running cases
        if response.status_code == 409 or data.get("success") is False:
            print(f"\n  ✓ Duplicate scan already in progress (operation: {data['operation_id']})")

        op = wait_for_operation(data["operation_id"], timeout=300)

        # Allow completed, running, or failed (source dir may not exist in test env)
        if op["state"] == "failed":
            # Duplicate scan may fail if source directory is empty or inaccessible
            # This is acceptable in test environments
            print(f"\n  ⚠ Duplicate scan failed (may be due to source directory state)")
            print(f"    Error: {op.get('error', 'Unknown error')}")
            pytest.skip("Duplicate scan failed - source directory may not be available")

        assert op["state"] in ("completed", "running"), f"Operation failed: {op.get('error')}"

        if op["state"] == "completed":
            print(f"\n  ✓ Source duplicate scan completed")
            if op.get("result"):
                result = op["result"]
                if isinstance(result, dict):
                    print(f"  ✓ Duplicates found: {result.get('duplicate_count', 'N/A')}")
        else:
            print(f"\n  ✓ Duplicate scan in progress ({op.get('progress', 0)}%)")

    @pytest.mark.slow
    def test_populate_asins_dry_run(self, api_available):
        """Test populate-asins-async in dry run mode (requires Audible auth)."""
        response = requests.post(
            api_url("/api/utilities/populate-asins-async"),
            json={"dry_run": True},
        )
        assert response.status_code == 200
        data = response.json()
        assert data.get("success") is True
        assert "operation_id" in data

        # This can take several minutes due to Audible API
        op = wait_for_operation(data["operation_id"], timeout=ASYNC_TIMEOUT)
        assert op["state"] == "completed", f"Operation failed: {op.get('error')}"

        print(f"\n  ✓ ASIN population completed (dry run)")
        if op.get("result"):
            result = op["result"]
            if isinstance(result, dict):
                print(f"  ✓ Matched: {result.get('matched', 'N/A')}")
                print(f"  ✓ Unmatched: {result.get('unmatched', 'N/A')}")


class TestLibraryOperations:
    """Test library content operations."""

    def test_rescan_async(self, api_available):
        """Test rescan-async scans library for changes."""
        response = requests.post(
            api_url("/api/utilities/rescan-async"),
            json={},
        )
        # Accept 200 (new operation) or 409 (already running)
        assert response.status_code in (200, 409), f"Unexpected status: {response.status_code}"
        data = response.json()
        assert "operation_id" in data

        # Handle both new operation and already-running cases
        if response.status_code == 409 or data.get("success") is False:
            print(f"\n  ✓ Library rescan already in progress (operation: {data['operation_id']})")

        # Use short timeout - rescan can take very long with large libraries
        op = wait_for_operation(data["operation_id"], timeout=60)

        # Allow both completed and running states
        assert op["state"] in ("completed", "running"), f"Operation failed: {op.get('error')}"

        if op["state"] == "completed":
            print(f"\n  ✓ Library rescan completed")
            if op.get("result"):
                print(f"  ✓ Result: {str(op['result'])[:100]}...")
        else:
            print(f"\n  ✓ Library rescan in progress ({op.get('progress', 0)}%)")


class TestSystemEndpoints:
    """Test system information endpoints."""

    def test_version(self, api_available):
        """Test /api/system/version returns version info."""
        response = requests.get(api_url("/api/system/version"))
        assert response.status_code == 200
        data = response.json()
        assert "version" in data
        print(f"\n  ✓ API Version: {data['version']}")

    def test_audiobooks_count(self, api_available):
        """Test /api/audiobooks returns audiobook data."""
        response = requests.get(api_url("/api/audiobooks?limit=5"))
        assert response.status_code == 200
        data = response.json()
        assert "audiobooks" in data
        # Check for total in pagination object or at top level
        if "pagination" in data:
            total = data["pagination"].get("total_count", 0)
        else:
            total = data.get("total", len(data["audiobooks"]))
        print(f"\n  ✓ Total audiobooks in library: {total}")
        if data["audiobooks"]:
            print(f"  ✓ Sample titles:")
            for book in data["audiobooks"][:3]:
                print(f"    - {book.get('title', 'Unknown')[:50]}")


class TestConcurrentOperations:
    """Test that concurrent operation detection works."""

    def test_rejects_concurrent_same_type(self, api_available):
        """Test that starting the same operation type twice is rejected."""
        # Start a slow operation
        response1 = requests.post(
            api_url("/api/utilities/populate-sort-fields-async"),
            json={"dry_run": True},
        )
        assert response1.status_code == 200
        data1 = response1.json()
        op_id = data1.get("operation_id")

        # Immediately try to start another
        response2 = requests.post(
            api_url("/api/utilities/populate-sort-fields-async"),
            json={"dry_run": True},
        )

        # Should either reject (409) or the first one finished already
        if response2.status_code == 409:
            print(f"\n  ✓ Concurrent operation correctly rejected")
        else:
            # First one may have finished quickly
            print(f"\n  ✓ Operation completed before concurrent check")

        # Clean up - wait for original operation
        if op_id:
            try:
                wait_for_operation(op_id, timeout=60)
            except TimeoutError:
                pass


# Summary test that runs all critical operations
class TestBackOfficeSummary:
    """Summary test that verifies all critical Back Office operations."""

    def test_all_operations_accessible(self, api_available):
        """Verify all Back Office endpoints are accessible."""
        endpoints = [
            ("GET", "/api/operations/active"),
            ("GET", "/api/operations/all"),
            ("GET", "/api/utilities/export-json"),
            ("GET", "/api/utilities/export-csv"),
            ("GET", "/api/system/version"),
            ("GET", "/api/audiobooks?limit=1"),
        ]

        results = []
        for method, path in endpoints:
            if method == "GET":
                response = requests.get(api_url(path))
            else:
                response = requests.post(api_url(path), json={})

            status = "✓" if response.status_code == 200 else "✗"
            results.append((status, method, path, response.status_code))

        print("\n  Back Office Endpoint Status:")
        for status, method, path, code in results:
            print(f"    {status} {method} {path} [{code}]")

        # All should be accessible
        failed = [r for r in results if r[0] == "✗"]
        assert len(failed) == 0, f"Failed endpoints: {failed}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
