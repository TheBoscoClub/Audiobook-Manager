"""
Targeted coverage tests for utilities_system.py uncovered lines.

Covers: 452, 526, 540, 584, 612, 619, 670, 677-678, 680-681, 731-804.
"""

import json
from email.message import Message
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

# utilities_system endpoints resolve project_root per-request from
# current_app.config (Audiobook-Manager-1wz), so version/health assertions are
# already order-independent under the session app. The utilities_globals
# fixture is kept only to snapshot/restore the shared VERSION file for tests
# that write or delete it (see utilities_globals in conftest.py).
pytestmark = pytest.mark.usefixtures("utilities_globals")

# =========================================================================
# Line 452: check_upgrade — version field with non-github source
# The existing extended test hits 440 (invalid path) before reaching 452.
# We need a VALID project path so validation passes, then hit line 452.
# =========================================================================


class TestCheckUpgradeVersionFieldValidation:
    """Line 452: version field only valid with github source."""

    @patch("backend.api_modular.utilities_system._read_status")
    def test_check_upgrade_version_with_project_source_valid_path(
        self, mock_read, flask_app, temp_dir
    ):
        """Version field with project source and valid path returns 400."""
        mock_read.return_value = {"running": False}

        # Create a valid project directory with VERSION file
        (temp_dir / "VERSION").write_text("1.0.0")

        with flask_app.test_client() as client:
            response = client.post(
                "/api/system/upgrade/check",
                json={"source": "project", "project_path": str(temp_dir), "version": "2.0.0"},
            )

        assert response.status_code == 400
        data = response.get_json()
        assert "version field is only valid with source 'github'" in data["error"]


# =========================================================================
# Line 526: start_upgrade — VERSION symlink traversal check
# version_file.resolve().parent != project_path_obj
# =========================================================================


class TestStartUpgradeVersionSymlinkTraversal:
    """Line 526: VERSION file symlink pointing outside project dir."""

    @patch("backend.api_modular.utilities_system._read_status")
    def test_upgrade_version_symlink_escape(self, mock_read, flask_app, temp_dir):
        """VERSION file that resolves outside the project dir is rejected."""
        mock_read.return_value = {"running": False}

        project_dir = temp_dir / "project"
        project_dir.mkdir()

        # Create a real VERSION file outside the project dir
        outside_version = temp_dir / "evil_VERSION"
        outside_version.write_text("999.0.0")

        # Symlink VERSION inside project to the outside file
        version_link = project_dir / "VERSION"
        version_link.symlink_to(outside_version)

        with flask_app.test_client() as client:
            response = client.post(
                "/api/system/upgrade",
                json={"source": "project", "project_path": str(project_dir), "force": True},
            )

        assert response.status_code == 400
        data = response.get_json()
        assert "Invalid project path" in data["error"]


# =========================================================================
# Line 540: start_upgrade — version with non-github source
# Need valid project path that passes all checks, then version + non-github
# =========================================================================


class TestStartUpgradeVersionWithProjectSource:
    """Line 540: version field with project source (valid path)."""

    @patch("backend.api_modular.utilities_system._read_status")
    def test_upgrade_version_with_valid_project_source(self, mock_read, flask_app, temp_dir):
        """Version field with project source and valid path returns 400."""
        mock_read.return_value = {"running": False}

        (temp_dir / "VERSION").write_text("1.0.0")

        with flask_app.test_client() as client:
            response = client.post(
                "/api/system/upgrade",
                json={
                    "source": "project",
                    "project_path": str(temp_dir),
                    "version": "2.0.0",
                    "force": True,
                },
            )

        assert response.status_code == 400
        data = response.get_json()
        assert "version field is only valid with source 'github'" in data["error"]


# =========================================================================
# Line 584: start_upgrade — version added to upgrade request_data
# Need github source with version and force=True to skip preflight
# =========================================================================


class TestStartUpgradeWithVersionParam:
    """Line 584: version parameter included in upgrade request."""

    @patch("backend.api_modular.utilities_system._write_request")
    @patch("backend.api_modular.utilities_system._read_status")
    def test_upgrade_github_with_version(self, mock_read, mock_write, flask_app):
        """GitHub upgrade with version passes version to request."""
        mock_read.return_value = {"running": False}
        mock_write.return_value = True

        with flask_app.test_client() as client:
            response = client.post(
                "/api/system/upgrade", json={"source": "github", "version": "7.5.0", "force": True}
            )

        assert response.status_code == 200
        # Verify version was included in the request data
        call_args = mock_write.call_args[0][0]
        assert call_args["version"] == "7.5.0"
        assert call_args["type"] == "upgrade"
        assert call_args["source"] == "github"


# =========================================================================
# Line 612: get_version — VERSION file doesn't exist
# =========================================================================


class TestGetVersionMissingFile:
    """Line 612: VERSION file does not exist returns 'unknown'."""

    def test_version_file_missing_returns_unknown(self, flask_app, session_temp_dir):
        """When VERSION file is absent, version is 'unknown'."""
        version_file = session_temp_dir / "VERSION"
        # Ensure the file does not exist
        if version_file.exists():
            version_file.unlink()

        with flask_app.test_client() as client:
            response = client.get("/api/system/version")

        assert response.status_code == 200
        data = response.get_json()
        assert data["version"] == "unknown"
        # VERSION restore is handled by the utilities_globals fixture


# =========================================================================
# Line 619: get_version — INSTANCE_BADGE environment variable
# =========================================================================


class TestGetVersionInstanceBadge:
    """Line 619: INSTANCE_BADGE env var included in response."""

    def test_instance_badge_included_when_set(self, flask_app, session_temp_dir, monkeypatch):
        """INSTANCE_BADGE env var appears in version response."""
        (session_temp_dir / "VERSION").write_text("1.0.0")
        monkeypatch.setenv("INSTANCE_BADGE", "QA")

        with flask_app.test_client() as client:
            response = client.get("/api/system/version")

        data = response.get_json()
        assert data["instance_badge"] == "QA"

    def test_instance_badge_absent_when_not_set(self, flask_app, session_temp_dir, monkeypatch):
        """No instance_badge key when env var is empty."""
        (session_temp_dir / "VERSION").write_text("1.0.0")
        monkeypatch.delenv("INSTANCE_BADGE", raising=False)

        with flask_app.test_client() as client:
            response = client.get("/api/system/version")

        data = response.get_json()
        assert "instance_badge" not in data


# =========================================================================
# Line 670: _scan_projects_in_dir — skip non-Audiobook dirs without VERSION
# =========================================================================


class TestScanProjectsSkipNonAudiobook:
    """Line 670: directories without VERSION and non-Audiobook prefix skipped."""

    def test_non_audiobook_dir_without_version_skipped(self, flask_app, temp_dir, monkeypatch):
        """Regular directory without VERSION file is skipped."""
        # Create dir that doesn't start with 'Audiobook' and has no VERSION
        plain_dir = temp_dir / "SomeRandomProject"
        plain_dir.mkdir()

        # Create a dir that DOES start with Audiobook (no VERSION needed)
        audiobook_dir = temp_dir / "AudiobookTest"
        audiobook_dir.mkdir()

        monkeypatch.setenv("AUDIOBOOKS_PROJECT_DIR", str(temp_dir))

        with flask_app.test_client() as client:
            response = client.get("/api/system/projects")

        data = response.get_json()
        names = [p["name"] for p in data["projects"]]
        # SomeRandomProject should be skipped (no VERSION, no Audiobook prefix)
        assert "SomeRandomProject" not in names
        # AudiobookTest should be included (starts with Audiobook)
        assert "AudiobookTest" in names


# =========================================================================
# Lines 677-678: _scan_projects_in_dir — exception reading VERSION file
# =========================================================================


class TestScanProjectsVersionReadError:
    """Lines 677-678: exception reading VERSION file yields version=None."""

    def test_version_file_unreadable(self, flask_app, temp_dir, monkeypatch):
        """Unreadable VERSION file results in version=None."""
        project_dir = temp_dir / "Audiobook-BadVersion"
        project_dir.mkdir()
        version_file = project_dir / "VERSION"
        version_file.write_text("1.0.0")

        monkeypatch.setenv("AUDIOBOOKS_PROJECT_DIR", str(temp_dir))

        # Patch open to raise when reading the VERSION file
        original_open = open

        def patched_open(path, *args, **kwargs):
            if str(path).endswith("VERSION") and "BadVersion" in str(path):
                raise PermissionError("denied")
            return original_open(path, *args, **kwargs)

        with patch("builtins.open", side_effect=patched_open):
            with flask_app.test_client() as client:
                response = client.get("/api/system/projects")

        data = response.get_json()
        matching = [p for p in data["projects"] if p["name"] == "Audiobook-BadVersion"]
        assert len(matching) == 1
        assert matching[0]["version"] is None


# =========================================================================
# Lines 680-681: _scan_projects_in_dir — exception listing directory
# =========================================================================


class TestScanProjectsDirectoryError:
    """Lines 680-681: exception scanning directory is silently skipped."""

    def test_listdir_raises_permission_error(self, flask_app, temp_dir, monkeypatch):
        """PermissionError from os.listdir is silently handled."""
        monkeypatch.setenv("AUDIOBOOKS_PROJECT_DIR", str(temp_dir))

        with patch("os.listdir", side_effect=PermissionError("denied")):
            with flask_app.test_client() as client:
                response = client.get("/api/system/projects")

        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data["projects"], list)


# =========================================================================
# purge_cdn_cache — Cloudflare cache purge endpoint
#
# The only credential this endpoint accepts is an API token scoped to
# Zone > Cache Purge, sent as an ``Authorization: Bearer`` header. The legacy
# Global API Key path (X-Auth-Key / X-Auth-Email) was removed: it granted full
# account access for an operation that needs a single zone permission, and a
# hand-populated key cannot learn about a rotation (Audiobook-Manager-8s8).
# =========================================================================


def _cf_ok_response():
    """A urlopen context manager yielding Cloudflare's success payload."""
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({"success": True}).encode()
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)
    return mock_response


def _clear_cf_token(monkeypatch):
    """Remove every input the purge-token resolver reads."""
    monkeypatch.delenv("CLOUDFLARE_PURGE_TOKEN", raising=False)
    monkeypatch.delenv("CLOUDFLARE_PURGE_TOKEN_FILE", raising=False)


class TestPurgeCdnCache:
    """Cloudflare CDN cache purge endpoint."""

    def test_missing_zone_id_returns_503(self, flask_app, monkeypatch):
        """Missing CF_ZONE_ID returns 503."""
        monkeypatch.delenv("CF_ZONE_ID", raising=False)

        with flask_app.test_client() as client:
            response = client.post("/api/system/purge-cache")

        assert response.status_code == 503
        data = response.get_json()
        assert data["success"] is False
        assert "CF_ZONE_ID" in data["error"]

    def test_no_credentials_returns_503(self, flask_app, monkeypatch):
        """No purge token configured returns 503 with a clear message."""
        monkeypatch.setenv("CF_ZONE_ID", "test-zone-id")
        _clear_cf_token(monkeypatch)

        with patch("urllib.request.urlopen") as mock_urlopen:
            with flask_app.test_client() as client:
                response = client.post("/api/system/purge-cache")

        assert response.status_code == 503
        data = response.get_json()
        assert data["success"] is False
        assert "Cloudflare credentials not configured" in data["error"]
        # No credential means no request may be attempted at all.
        mock_urlopen.assert_not_called()

    def test_cloudflare_api_returns_failure(self, flask_app, monkeypatch):
        """Cloudflare API returns success=false."""
        monkeypatch.setenv("CF_ZONE_ID", "test-zone-id")
        monkeypatch.setenv("CLOUDFLARE_PURGE_TOKEN", "scoped-purge-token")
        monkeypatch.delenv("CLOUDFLARE_PURGE_TOKEN_FILE", raising=False)

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"success": False}).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            with flask_app.test_client() as client:
                response = client.post("/api/system/purge-cache")

        assert response.status_code == 502
        data = response.get_json()
        assert data["success"] is False
        assert "returned failure" in data["error"]

    def test_cloudflare_http_error(self, flask_app, monkeypatch):
        """Cloudflare API HTTP error returns 502."""
        import urllib.error

        monkeypatch.setenv("CF_ZONE_ID", "test-zone-id")
        monkeypatch.setenv("CLOUDFLARE_PURGE_TOKEN", "scoped-purge-token")
        monkeypatch.delenv("CLOUDFLARE_PURGE_TOKEN_FILE", raising=False)

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                url="https://api.cloudflare.com/...",
                code=403,
                msg="Forbidden",
                hdrs=Message(),
                fp=BytesIO(b""),
            ),
        ):
            with flask_app.test_client() as client:
                response = client.post("/api/system/purge-cache")

        assert response.status_code == 502
        data = response.get_json()
        assert data["success"] is False
        assert "request failed" in data["error"]

    def test_cloudflare_url_error(self, flask_app, monkeypatch):
        """Cloudflare API URL error (network) returns 502."""
        import urllib.error

        monkeypatch.setenv("CF_ZONE_ID", "test-zone-id")
        monkeypatch.setenv("CLOUDFLARE_PURGE_TOKEN", "scoped-purge-token")
        monkeypatch.delenv("CLOUDFLARE_PURGE_TOKEN_FILE", raising=False)

        with patch(
            "urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")
        ):
            with flask_app.test_client() as client:
                response = client.post("/api/system/purge-cache")

        assert response.status_code == 502
        data = response.get_json()
        assert data["success"] is False
        assert "request failed" in data["error"]

    def test_cloudflare_timeout_error(self, flask_app, monkeypatch):
        """Cloudflare API timeout returns 504."""
        monkeypatch.setenv("CF_ZONE_ID", "test-zone-id")
        monkeypatch.setenv("CLOUDFLARE_PURGE_TOKEN", "scoped-purge-token")
        monkeypatch.delenv("CLOUDFLARE_PURGE_TOKEN_FILE", raising=False)

        with patch("urllib.request.urlopen", side_effect=TimeoutError("Connection timed out")):
            with flask_app.test_client() as client:
                response = client.post("/api/system/purge-cache")

        assert response.status_code == 504
        data = response.get_json()
        assert data["success"] is False
        assert "timeout" in data["error"].lower()

    def test_custom_zone_id(self, flask_app, monkeypatch):
        """Custom CF_ZONE_ID is used in the API URL."""
        monkeypatch.setenv("CF_ZONE_ID", "custom-zone-123")
        monkeypatch.setenv("CLOUDFLARE_PURGE_TOKEN", "scoped-purge-token")
        monkeypatch.delenv("CLOUDFLARE_PURGE_TOKEN_FILE", raising=False)

        with patch("urllib.request.urlopen", return_value=_cf_ok_response()) as mock_urlopen:
            with flask_app.test_client() as client:
                response = client.post("/api/system/purge-cache")

        assert response.status_code == 200
        req = mock_urlopen.call_args[0][0]
        assert "custom-zone-123" in req.full_url


# =========================================================================
# purge_cdn_cache — scoped API token (Bearer) authentication
# =========================================================================


class TestPurgeCdnCacheScopedToken:
    """Bearer-token auth for the Cloudflare cache purge endpoint."""

    def test_scoped_token_from_env_uses_bearer_header(self, flask_app, monkeypatch):
        """CLOUDFLARE_PURGE_TOKEN is sent as an Authorization: Bearer header."""
        monkeypatch.setenv("CF_ZONE_ID", "test-zone-id")
        monkeypatch.setenv("CLOUDFLARE_PURGE_TOKEN", "scoped-purge-token")
        monkeypatch.delenv("CLOUDFLARE_PURGE_TOKEN_FILE", raising=False)

        with patch("urllib.request.urlopen", return_value=_cf_ok_response()) as mock_urlopen:
            with flask_app.test_client() as client:
                response = client.post("/api/system/purge-cache")

        assert response.status_code == 200
        req = mock_urlopen.call_args[0][0]
        assert req.get_header("Authorization") == "Bearer scoped-purge-token"
        # The removed Global-API-Key headers must never be sent.
        assert req.get_header("X-auth-key") is None
        assert req.get_header("X-auth-email") is None

    def test_scoped_token_from_file_pointer(self, flask_app, temp_dir, monkeypatch):
        """CLOUDFLARE_PURGE_TOKEN_FILE resolves through the shared secret resolver."""
        token_file = temp_dir / "cloudflare-purge-token"
        token_file.write_text("token-from-file\n")

        monkeypatch.setenv("CF_ZONE_ID", "test-zone-id")
        monkeypatch.delenv("CLOUDFLARE_PURGE_TOKEN", raising=False)
        monkeypatch.setenv("CLOUDFLARE_PURGE_TOKEN_FILE", str(token_file))

        with patch("urllib.request.urlopen", return_value=_cf_ok_response()) as mock_urlopen:
            with flask_app.test_client() as client:
                response = client.post("/api/system/purge-cache")

        assert response.status_code == 200
        req = mock_urlopen.call_args[0][0]
        # Trailing newline must be stripped, or Cloudflare rejects the header.
        assert req.get_header("Authorization") == "Bearer token-from-file"

    def test_empty_token_file_returns_503(self, flask_app, temp_dir, monkeypatch):
        """An empty token file is not a credential — 503, and no request sent.

        Regression guard: /etc/audiobooks/ has historically contained zero-byte
        placeholder secret files created at install time. With no fallback left,
        a zero-byte file must fail closed rather than send an empty Bearer.
        """
        token_file = temp_dir / "empty-purge-token"
        token_file.write_text("")

        monkeypatch.setenv("CF_ZONE_ID", "test-zone-id")
        monkeypatch.delenv("CLOUDFLARE_PURGE_TOKEN", raising=False)
        monkeypatch.setenv("CLOUDFLARE_PURGE_TOKEN_FILE", str(token_file))

        with patch("urllib.request.urlopen") as mock_urlopen:
            with flask_app.test_client() as client:
                response = client.post("/api/system/purge-cache")

        assert response.status_code == 503
        assert "not configured" in response.get_json()["error"]
        mock_urlopen.assert_not_called()
