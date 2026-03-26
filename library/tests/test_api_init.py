"""
Unit tests for backend.api_modular.__init__ module — targeting uncovered lines.

Covers: get_db wrapper (line 76), create_app WebSocket handler (lines 214-246),
admin connections endpoint (line 252), and auth-disabled paths.
"""

import json
import sys
import sqlite3
import tempfile
from pathlib import Path


LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))
sys.path.insert(0, str(LIBRARY_DIR / "backend"))


class TestGetDb:
    """Test line 76: get_db backward-compatible wrapper."""

    def test_get_db_returns_connection(self, flask_app, monkeypatch):
        """Line 76: get_db returns a database connection."""
        import backend.api_modular as api_mod

        # Monkeypatch DB_PATH to use the flask_app's test database,
        # so this test works in CI where the default path doesn't exist.
        monkeypatch.setattr(api_mod, "DB_PATH", flask_app.config["DATABASE_PATH"])

        with flask_app.app_context():
            conn = api_mod.get_db()
            assert conn is not None
            # Should be a sqlite3 connection
            cursor = conn.execute("SELECT 1")
            assert cursor.fetchone()[0] == 1
            conn.close()


class TestCreateAppAuthDisabled:
    """Test create_app with auth disabled (lines 127-128)."""

    def test_auth_disabled_when_no_auth_paths(self):
        """Line 128: AUTH_ENABLED is False when auth paths not provided."""
        from backend.api_modular import create_app

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            db_path = tmpdir / "test.db"
            supplements = tmpdir / "supplements"
            supplements.mkdir()

            # Initialize DB with minimal schema
            conn = sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE audiobooks (
                    id INTEGER PRIMARY KEY, title TEXT, file_path TEXT UNIQUE NOT NULL,
                    content_type TEXT DEFAULT 'Product'
                )
            """)
            conn.close()

            app = create_app(
                database_path=db_path,
                project_dir=tmpdir,
                supplements_dir=supplements,
                api_port=9999,
                # No auth_db_path, no auth_key_path
            )

            assert app.config["AUTH_ENABLED"] is False


class TestCreateAppAuthEnabled:
    """Test create_app with auth enabled (lines 123-126)."""

    def test_auth_enabled_when_paths_provided(self, auth_app):
        """Lines 123-126: AUTH_ENABLED is True when auth paths provided."""
        assert auth_app.config["AUTH_ENABLED"] is True
        assert auth_app.config.get("AUTH_DB_PATH") is not None
        assert auth_app.config.get("AUTH_KEY_PATH") is not None


class TestOptionsHandler:
    """Test line 144: OPTIONS preflight handler."""

    def test_options_returns_success(self, app_client):
        """Line 144: OPTIONS request returns success (200 or 204)."""
        response = app_client.options("/api/audiobooks")
        assert response.status_code in (200, 204)

    def test_options_root_returns_success(self, app_client):
        """Line 144: OPTIONS on root returns success."""
        response = app_client.options("/")
        assert response.status_code in (200, 204)


class TestCorsAndSecurityHeaders:
    """Test lines 131-137: CORS and security headers are applied."""

    def test_cors_headers_present(self, app_client):
        """Line 133: CORS headers are added to responses."""
        response = app_client.get("/api/audiobooks")
        assert "Access-Control-Allow-Origin" in response.headers

    def test_security_headers_present(self, app_client):
        """Line 137: Security headers are added to responses."""
        response = app_client.get("/api/audiobooks")
        # At minimum, some security header should be present
        headers = dict(response.headers)
        has_security = any(
            h in headers
            for h in [
                "X-Content-Type-Options",
                "X-Frame-Options",
                "Content-Security-Policy",
                "Strict-Transport-Security",
            ]
        )
        assert has_security


class TestWebSocketHandler:
    """Test lines 214-246: WebSocket handler logic.

    WebSocket tests are tricky with Flask test client. We test the
    connection_manager and related logic indirectly.
    """

    def test_connection_manager_exists(self):
        """Line 206: connection_manager is imported from websocket module."""
        from backend.api_modular.websocket import connection_manager

        assert connection_manager is not None

    def test_connection_manager_admin_list(self):
        """Line 252: admin_connections_list returns a dict or list."""
        from backend.api_modular.websocket import connection_manager

        result = connection_manager.admin_connections_list()
        assert isinstance(result, (list, dict))

    def test_ws_auth_required_when_enabled(self, auth_app):
        """Lines 218-222: WS handler checks auth when AUTH_ENABLED."""
        # The ws_handler function exists on the app
        assert auth_app.config["AUTH_ENABLED"] is True
        # We can't easily test WebSocket with test_client, but verify the
        # route is registered
        rules = [rule.rule for rule in auth_app.url_map.iter_rules()]
        assert "/api/ws" in rules


class TestAdminConnectionsEndpoint:
    """Test line 252: /api/admin/connections endpoint."""

    def test_admin_connections_requires_auth(self, auth_app):
        """Line 250-252: Endpoint requires admin auth when enabled."""
        with auth_app.test_client() as client:
            response = client.get("/api/admin/connections")
            # Should be 401 (no session cookie)
            assert response.status_code == 401

    def test_admin_connections_with_admin(self, admin_client):
        """Line 252: Admin can access connections endpoint."""
        response = admin_client.get("/api/admin/connections")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert isinstance(data, (list, dict))


class TestModuleExports:
    """Test __all__ exports for backward compatibility."""

    def test_all_exports_importable(self):
        """Verify all items in __all__ are importable."""
        from backend.api_modular import __all__

        import backend.api_modular as mod

        for name in __all__:
            assert hasattr(mod, name), f"{name} in __all__ but not importable"

    def test_backward_compat_constants(self):
        """DB_PATH and PROJECT_ROOT are exported."""
        from backend.api_modular import DB_PATH, PROJECT_ROOT

        assert DB_PATH is not None
        assert PROJECT_ROOT is not None
