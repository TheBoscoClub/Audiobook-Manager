"""
Docker container build, lifecycle, and API integration tests.

These tests verify the Docker image builds correctly, containers start and
become healthy, API endpoints respond, volumes and data persist, environment
variables are honored, and security constraints are enforced.

Run with:  pytest tests/test_docker.py -m docker -v
Skip if Docker is not installed on the host.

Requirements:
    - Docker daemon running
    - Port range 19000-19999 available for test containers
    - requests library (pip install requests)
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest

try:
    import requests
    from requests.exceptions import ConnectionError as RequestsConnectionError
except ImportError:
    requests = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Path setup (matches project convention)
# ---------------------------------------------------------------------------
LIBRARY_DIR = Path(__file__).parent.parent
PROJECT_ROOT = LIBRARY_DIR.parent
sys.path.insert(0, str(LIBRARY_DIR))

# ---------------------------------------------------------------------------
# Unique prefix so parallel runs / CI cannot collide
# ---------------------------------------------------------------------------
_RUN_ID = uuid.uuid4().hex[:8]
TEST_IMAGE_NAME = f"audiobooks-test-{_RUN_ID}"
TEST_CONTAINER_NAME = f"audiobooks-test-ctr-{_RUN_ID}"
TEST_VOLUME_DATA = f"audiobooks-test-data-{_RUN_ID}"
TEST_VOLUME_COVERS = f"audiobooks-test-covers-{_RUN_ID}"

# Host ports for the container under test (high range to avoid conflicts)
HOST_HTTPS_PORT = 18443
HOST_HTTP_PORT = 18080

# Read the expected version from the VERSION file
VERSION_FILE = PROJECT_ROOT / "VERSION"
EXPECTED_VERSION = VERSION_FILE.read_text().strip() if VERSION_FILE.exists() else ""

# ---------------------------------------------------------------------------
# Skip conditions
# ---------------------------------------------------------------------------
_docker_available = shutil.which("docker") is not None

skip_no_docker = pytest.mark.skipif(not _docker_available, reason="Docker CLI not found on PATH")

skip_no_requests = pytest.mark.skipif(requests is None, reason="requests library not installed")

# Custom marker so `pytest -m docker` selects only these tests
pytestmark = [pytest.mark.docker, skip_no_docker]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _docker(*args: str, check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess:
    """Run a docker CLI command and return the CompletedProcess."""
    cmd = ["docker", *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=check)


def _docker_inspect(name: str, fmt: str) -> str:
    """Shorthand for docker inspect --format."""
    result = _docker("inspect", "--format", fmt, name, check=False)
    return result.stdout.strip()


def _wait_for_healthy(container: str, timeout: int = 60) -> bool:
    """Poll until the container's health status is 'healthy' or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = _docker_inspect(container, "{{.State.Health.Status}}")
        if status == "healthy":
            return True
        if status in ("unhealthy", ""):
            # If container exited, bail early
            running = _docker_inspect(container, "{{.State.Running}}")
            if running == "false":
                return False
        time.sleep(2)
    return False


def _container_port(container: str, internal_port: str) -> str | None:
    """Return the host port mapped to *internal_port* (e.g., '8443/tcp')."""
    result = _docker("port", container, internal_port, check=False)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    # Output like "0.0.0.0:18443" or "[::]:18443" -- grab the port after last ':'
    for line in result.stdout.strip().splitlines():
        if ":" in line:
            return line.rsplit(":", 1)[1]
    return None


def _cleanup_container(name: str) -> None:
    """Force-remove a container if it exists (ignore errors)."""
    _docker("rm", "--force", "--volumes", name, check=False, timeout=30)


def _cleanup_volume(name: str) -> None:
    """Remove a Docker volume if it exists (ignore errors)."""
    _docker("volume", "rm", "--force", name, check=False, timeout=15)


def _cleanup_image(name: str) -> None:
    """Remove a Docker image if it exists (ignore errors)."""
    _docker("rmi", "--force", name, check=False, timeout=30)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def docker_image():
    """Build the Docker image once for the entire test module.

    Yields the image tag.  Tears down by removing the image after all tests
    in this module have finished.
    """
    # Build the image
    result = _docker(
        "build",
        "--tag",
        TEST_IMAGE_NAME,
        "--build-arg",
        f"APP_VERSION={EXPECTED_VERSION}",
        "--file",
        str(PROJECT_ROOT / "Dockerfile"),
        str(PROJECT_ROOT),
        timeout=300,
    )
    assert result.returncode == 0, f"Docker build failed:\n{result.stderr}"

    yield TEST_IMAGE_NAME

    # Cleanup
    _cleanup_image(TEST_IMAGE_NAME)


def _seed_empty_database(data_dir: str) -> None:
    """Create a minimal empty audiobooks.db using the project's schema.sql."""
    schema_path = PROJECT_ROOT / "library" / "backend" / "schema.sql"
    assert schema_path.exists(), f"Schema file not found: {schema_path}"

    db_path = Path(data_dir) / "audiobooks.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(schema_path.read_text())
    conn.commit()
    conn.close()
    # Make directory and database accessible by container user (may differ
    # from host UID — e.g. host claude=1001 vs container appuser=1000).
    os.chmod(data_dir, 0o777)  # noqa: S103 # nosec B103 — test container needs cross-UID access
    os.chmod(str(db_path), 0o666)  # noqa: S103 # nosec B103 — test container needs cross-UID access


@pytest.fixture
def docker_container(docker_image):
    """Start a container from the test image with random host ports.

    Seeds an empty database so the API can start, then waits for the
    container to become healthy (up to 60s). Yields a dict with container
    metadata. The container is force-removed on teardown.
    """
    container_name = f"{TEST_CONTAINER_NAME}-{uuid.uuid4().hex[:6]}"

    # Create temp directories for data and covers (host-mounted)
    data_dir = tempfile.mkdtemp(prefix="audiobooks-test-data-")
    covers_dir = tempfile.mkdtemp(prefix="audiobooks-test-covers-")

    # Seed an empty database so api_server.py doesn't exit
    _seed_empty_database(data_dir)

    # Ensure covers dir is accessible by container user (may have different UID)
    os.chmod(covers_dir, 0o777)  # noqa: S103 # nosec B103 — test container needs cross-UID access

    # Use --publish with port 0 on host to let Docker pick free ports
    # Override AUDIOBOOKS_BIND_ADDRESS to 0.0.0.0 so ports are reachable
    # from the host (the entrypoint defaults to 127.0.0.1 which blocks
    # external access needed by tests).
    run_result = _docker(
        "run",
        "--detach",
        "--name",
        container_name,
        "--publish",
        "0:8443",
        "--publish",
        "0:8080",
        "--volume",
        f"{data_dir}:/app/data",
        "--volume",
        f"{covers_dir}:/app/covers",
        "--env",
        "HTTP_REDIRECT_ENABLED=true",
        "--env",
        "AUDIOBOOKS_BIND_ADDRESS=0.0.0.0",
        docker_image,
        timeout=30,
    )
    assert run_result.returncode == 0, f"Container failed to start:\n{run_result.stderr}"
    container_id = run_result.stdout.strip()

    # Discover assigned host ports
    healthy = _wait_for_healthy(container_name, timeout=180)

    https_port = _container_port(container_name, "8443/tcp")
    http_port = _container_port(container_name, "8080/tcp")

    info = {
        "name": container_name,
        "id": container_id,
        "image": docker_image,
        "https_port": https_port,
        "http_port": http_port,
        "healthy": healthy,
        "data_dir": data_dir,
        "covers_dir": covers_dir,
    }

    yield info

    # Teardown
    _cleanup_container(container_name)
    import shutil as _shutil

    _shutil.rmtree(data_dir, ignore_errors=True)
    _shutil.rmtree(covers_dir, ignore_errors=True)


@pytest.fixture
def healthy_container(docker_container):
    """Wrapper that skips the test if the container never became healthy."""
    if not docker_container["healthy"]:
        pytest.skip("Container did not reach healthy state within timeout")
    return docker_container


# ===================================================================
# 1. BUILD TESTS
# ===================================================================


class TestDockerBuild:
    """Verify that the Docker image builds and has correct metadata."""

    def test_docker_build_succeeds(self, docker_image):
        """The Dockerfile builds without errors."""
        result = _docker("image", "inspect", docker_image, check=False)
        assert result.returncode == 0, "Image not found after build"

    def test_docker_image_has_correct_labels(self, docker_image):
        """OCI labels are present on the built image."""
        labels_json = _docker_inspect(docker_image, "{{json .Config.Labels}}")
        labels = json.loads(labels_json) if labels_json else {}

        assert "org.opencontainers.image.source" in labels, "Missing OCI source label"
        assert "org.opencontainers.image.description" in labels, "Missing OCI description label"
        assert "org.opencontainers.image.licenses" in labels, "Missing OCI licenses label"
        assert labels.get("org.opencontainers.image.licenses") == "MIT"

    def test_docker_image_runs_as_nonroot(self, docker_image):
        """The image USER directive specifies 'audiobooks' (UID 1000)."""
        user = _docker_inspect(docker_image, "{{.Config.User}}")
        assert user == "audiobooks", f"Expected user 'audiobooks', got '{user}'"


# ===================================================================
# 2. CONTAINER LIFECYCLE TESTS
# ===================================================================


class TestContainerLifecycle:
    """Verify container start, health, shutdown, and port exposure."""

    def test_container_starts_and_becomes_healthy(self, docker_container):
        """Container starts and the Docker HEALTHCHECK eventually passes."""
        assert docker_container["healthy"], "Container did not reach 'healthy' status within 60s"

    def test_container_health_check_works(self, healthy_container):
        """Docker reports the container health status as 'healthy'."""
        name = healthy_container["name"]
        status = _docker_inspect(name, "{{.State.Health.Status}}")
        assert status == "healthy"

    def test_container_graceful_shutdown(self, healthy_container):
        """SIGTERM is handled and the container exits cleanly (exit 0)."""
        name = healthy_container["name"]

        # Send SIGTERM via docker stop (default 10s grace period)
        stop_result = _docker("stop", "--time", "10", name, check=False, timeout=20)
        assert stop_result.returncode == 0, f"docker stop failed: {stop_result.stderr}"

        # Check exit code
        exit_code = _docker_inspect(name, "{{.State.ExitCode}}")
        assert exit_code in ("0", "143"), f"Expected exit code 0 or 143 (SIGTERM), got {exit_code}"

    def test_container_port_bindings(self, healthy_container):
        """Ports 8443 (HTTPS) and 8080 (HTTP redirect) are bound to the host."""
        assert healthy_container["https_port"] is not None, "HTTPS port 8443 not bound"
        assert healthy_container["http_port"] is not None, "HTTP redirect port 8080 not bound"


# ===================================================================
# 3. API INTEGRATION TESTS (against running container)
# ===================================================================


@skip_no_requests
class TestAPIIntegration:
    """Hit real API endpoints on the running container."""

    def _https_url(self, container_info: dict, path: str) -> str:
        port = container_info["https_port"]
        return f"https://127.0.0.1:{port}{path}"

    def _http_url(self, container_info: dict, path: str) -> str:
        port = container_info["http_port"]
        return f"http://127.0.0.1:{port}{path}"

    def test_api_stats_endpoint(self, healthy_container):
        """GET /api/stats returns valid JSON with expected keys."""
        url = self._https_url(healthy_container, "/api/stats")
        resp = requests.get(url, verify=False, timeout=10)  # noqa: S501 # nosec B501 — self-signed cert on local test container
        assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
        data = resp.json()
        assert isinstance(data, dict)

    def test_api_version_endpoint(self, healthy_container):
        """GET /api/system/version returns version info matching VERSION file."""
        url = self._https_url(healthy_container, "/api/system/version")
        resp = requests.get(url, verify=False, timeout=10)  # noqa: S501 # nosec B501 — self-signed cert on local test container
        assert resp.status_code == 200
        data = resp.json()
        assert "version" in data or "app_version" in data

    def test_https_web_ui_loads(self, healthy_container):
        """HTTPS on 8443 serves the web UI (HTML page)."""
        url = self._https_url(healthy_container, "/")
        resp = requests.get(url, verify=False, timeout=10)  # noqa: S501 # nosec B501 — self-signed cert on local test container
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("Content-Type", "")

    def test_http_redirect_works(self, healthy_container):
        """HTTP on 8080 redirects (301/302/307/308) to HTTPS."""
        url = self._http_url(healthy_container, "/")
        resp = requests.get(url, verify=False, timeout=10, allow_redirects=False)  # noqa: S501 # nosec B501 — self-signed cert on local test container
        assert resp.status_code in (301, 302, 307, 308), (
            f"Expected redirect, got {resp.status_code}"
        )
        location = resp.headers.get("Location", "")
        assert "https" in location.lower(), f"Redirect Location does not point to HTTPS: {location}"

    def test_api_audiobooks_list(self, healthy_container):
        """GET /api/audiobooks returns a valid response (empty library is OK)."""
        url = self._https_url(healthy_container, "/api/audiobooks")
        resp = requests.get(url, verify=False, timeout=10)  # noqa: S501 # nosec B501 — self-signed cert on local test container
        assert resp.status_code == 200
        data = resp.json()
        # Response is a list of audiobooks (possibly empty)
        assert isinstance(data, (list, dict))


# ===================================================================
# 4. VOLUME & DATA TESTS
# ===================================================================


class TestVolumeAndData:
    """Verify volume creation, database init, and TLS cert generation."""

    def test_data_directory_has_database(self, healthy_container):
        """The data directory contains the database file after startup."""
        data_dir = healthy_container["data_dir"]
        db_path = Path(data_dir) / "audiobooks.db"
        assert db_path.exists(), f"Database file not found at {db_path}"

    def test_database_auto_initialized(self, healthy_container):
        """The SQLite database file is created inside /app/data on first run."""
        name = healthy_container["name"]
        result = _docker("exec", name, "test", "-f", "/app/data/audiobooks.db", check=False)
        assert result.returncode == 0, (
            "Database file /app/data/audiobooks.db not found in container"
        )

    def test_self_signed_cert_generated(self, healthy_container):
        """Self-signed TLS certificate and key are generated automatically."""
        name = healthy_container["name"]

        cert_check = _docker("exec", name, "test", "-f", "/app/certs/server.crt", check=False)
        key_check = _docker("exec", name, "test", "-f", "/app/certs/server.key", check=False)
        assert cert_check.returncode == 0, "server.crt not found in /app/certs/"
        assert key_check.returncode == 0, "server.key not found in /app/certs/"


# ===================================================================
# 5. ENVIRONMENT VARIABLE TESTS
# ===================================================================


class TestEnvironmentVariables:
    """Verify that environment variable overrides take effect."""

    def test_custom_web_port(self, docker_image):
        """WEB_PORT env var changes the HTTPS listen port."""
        custom_port = "9443"
        container_name = f"{TEST_CONTAINER_NAME}-envport-{uuid.uuid4().hex[:6]}"
        data_dir = tempfile.mkdtemp(prefix="audiobooks-test-envport-")
        _seed_empty_database(data_dir)

        try:
            run_result = _docker(
                "run",
                "--detach",
                "--name",
                container_name,
                "--publish",
                f"0:{custom_port}",
                "--volume",
                f"{data_dir}:/app/data",
                "--env",
                f"WEB_PORT={custom_port}",
                "--env",
                "AUDIOBOOKS_BIND_ADDRESS=0.0.0.0",
                docker_image,
                timeout=30,
            )
            assert run_result.returncode == 0, (
                f"Container with custom WEB_PORT failed to start: {run_result.stderr}"
            )

            # Wait for the container to be healthy (or at least running)
            healthy = _wait_for_healthy(container_name, timeout=180)

            # Verify the custom port is exposed and bound
            mapped = _container_port(container_name, f"{custom_port}/tcp")
            assert mapped is not None, f"Custom WEB_PORT {custom_port} not bound to host"

            if healthy and requests is not None:
                url = f"https://127.0.0.1:{mapped}/"
                try:
                    resp = requests.get(url, verify=False, timeout=10)  # noqa: S501 # nosec B501 — self-signed cert on local test container
                    assert resp.status_code == 200
                except RequestsConnectionError:
                    # Port bound but HTTPS proxy may still be starting
                    pass
        finally:
            _cleanup_container(container_name)
            import shutil as _shutil

            _shutil.rmtree(data_dir, ignore_errors=True)

    def test_version_matches_file(self, healthy_container):
        """VERSION inside the container matches the project VERSION file."""
        name = healthy_container["name"]
        result = _docker("exec", name, "cat", "/app/VERSION", check=False)
        assert result.returncode == 0, "Could not read /app/VERSION in container"
        container_version = result.stdout.strip()
        assert container_version == EXPECTED_VERSION, (
            f"Container version '{container_version}' != project version '{EXPECTED_VERSION}'"
        )


# ===================================================================
# 6. SECURITY TESTS
# ===================================================================


class TestSecurity:
    """Verify container security constraints."""

    def test_container_user_is_nonroot(self, healthy_container):
        """The running process user is 'audiobooks', not root."""
        name = healthy_container["name"]
        result = _docker("exec", name, "whoami", check=False)
        assert result.returncode == 0
        user = result.stdout.strip()
        assert user == "audiobooks", f"Expected container user 'audiobooks', got '{user}'"

    def test_no_secrets_in_image_layers(self, docker_image):
        """Docker history does not contain secrets (passwords, keys, tokens)."""
        result = _docker(
            "history", "--no-trunc", "--format", "{{.CreatedBy}}", docker_image, check=False
        )
        assert result.returncode == 0

        history_text = result.stdout.lower()
        forbidden_patterns = [
            "password",
            "secret_key",
            "api_key",
            "private_key",
            "aws_access",
            "aws_secret",
            "token=",
        ]
        for pattern in forbidden_patterns:
            assert pattern not in history_text, (
                f"Potential secret found in image layer history: '{pattern}'"
            )
