"""
CodeQL py/path-injection triage — group A (Audiobook-Manager-o9s).

Covers the two user-controlled path inputs in ``utilities_system``:

* ``project_path`` (JSON body) on ``POST /api/system/upgrade/check`` and
  ``POST /api/system/upgrade`` — both forward the path to the root-privileged
  upgrade helper, so both must apply the same strict validation.
* ``base_path`` (query string) on ``GET /api/system/projects``.

The attacks exercised here are the ones the validators claim to refuse:
``..`` components, a ``VERSION`` symlink that escapes the project directory,
an embedded NUL byte, and an unresolved symlinked directory being forwarded
verbatim.  Each test was observed RED against the pre-fix code.
"""

from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

import pytest

pytestmark = pytest.mark.usefixtures("utilities_globals")

_READ_STATUS = "backend.api_modular.utilities_system._read_status"
_WRITE_REQUEST = "backend.api_modular.utilities_system._write_request"


def _make_project(root: Path) -> Path:
    project = root / "project"
    project.mkdir()
    (project / "VERSION").write_text("1.0.0")
    return project


class TestCheckUpgradeStrictValidation:
    """``/api/system/upgrade/check`` must refuse what ``/api/system/upgrade`` refuses."""

    @patch(_WRITE_REQUEST)
    @patch(_READ_STATUS)
    def test_dotdot_component_rejected(self, mock_read, mock_write, flask_app, temp_dir):
        """A path with a ``..`` component is refused even when it resolves to a project."""
        mock_read.return_value = {"running": False}
        mock_write.return_value = True
        project = _make_project(temp_dir)
        (project / "sub").mkdir()

        with flask_app.test_client() as client:
            response = client.post(
                "/api/system/upgrade/check",
                json={"source": "project", "project_path": str(project / "sub" / "..")},
            )

        assert response.status_code == 400
        assert response.get_json()["error"] == "Invalid project path"
        mock_write.assert_not_called()

    @patch(_WRITE_REQUEST)
    @patch(_READ_STATUS)
    def test_version_symlink_escape_rejected(self, mock_read, mock_write, flask_app, temp_dir):
        """A ``VERSION`` symlink resolving outside the project directory is refused."""
        mock_read.return_value = {"running": False}
        mock_write.return_value = True
        project = temp_dir / "project"
        project.mkdir()
        outside = temp_dir / "outside_VERSION"
        outside.write_text("999.0.0")
        (project / "VERSION").symlink_to(outside)

        with flask_app.test_client() as client:
            response = client.post(
                "/api/system/upgrade/check",
                json={"source": "project", "project_path": str(project)},
            )

        assert response.status_code == 400
        assert response.get_json()["error"] == "Invalid project path"
        mock_write.assert_not_called()

    @patch(_WRITE_REQUEST)
    @patch(_READ_STATUS)
    def test_null_byte_rejected_as_invalid(self, mock_read, mock_write, flask_app, temp_dir):
        """An embedded NUL is refused as an invalid path, not reported as 'not found'."""
        mock_read.return_value = {"running": False}
        mock_write.return_value = True
        project = _make_project(temp_dir)

        with flask_app.test_client() as client:
            response = client.post(
                "/api/system/upgrade/check",
                json={"source": "project", "project_path": f"{project}\u0000"},
            )

        assert response.status_code == 400
        assert response.get_json()["error"] == "Invalid project path"
        mock_write.assert_not_called()

    @patch(_WRITE_REQUEST)
    @patch(_READ_STATUS)
    def test_forwards_resolved_path_to_helper(self, mock_read, mock_write, flask_app, temp_dir):
        """The helper receives the resolved real path, never the caller's symlink."""
        mock_read.return_value = {"running": False}
        mock_write.return_value = True
        project = _make_project(temp_dir)
        link = temp_dir / "link"
        link.symlink_to(project)

        with flask_app.test_client() as client:
            response = client.post(
                "/api/system/upgrade/check",
                json={"source": "project", "project_path": str(link)},
            )

        assert response.status_code == 200
        mock_write.assert_called_once()
        forwarded = mock_write.call_args.args[0]["project_path"]
        assert forwarded == str(project.resolve())
        assert "link" not in Path(forwarded).parts


class TestStartUpgradeNullByte:
    """The NUL guard must run before any filesystem call can raise on the NUL."""

    @patch(_WRITE_REQUEST)
    @patch(_READ_STATUS)
    def test_null_byte_returns_400_not_500(self, mock_read, mock_write, flask_app, temp_dir):
        mock_read.return_value = {"running": False}
        mock_write.return_value = True
        project = _make_project(temp_dir)

        with flask_app.test_client() as client:
            response = client.post(
                "/api/system/upgrade",
                json={"source": "project", "project_path": f"{project}\u0000", "force": True},
            )

        assert response.status_code == 400
        assert response.get_json()["error"] == "Invalid project path"
        mock_write.assert_not_called()


class TestListProjectsBasePathNullByte:
    """``base_path`` with an embedded NUL is ignored, never an unhandled exception."""

    def test_null_byte_base_path_ignored(self, flask_app, temp_dir, monkeypatch):
        monkeypatch.delenv("AUDIOBOOKS_PROJECT_DIR", raising=False)
        payload = quote(f"{temp_dir}/\x00x", safe="/")

        with flask_app.test_client() as client:
            response = client.get(f"/api/system/projects?base_path={payload}")

        assert response.status_code == 200
        assert isinstance(response.get_json()["projects"], list)
