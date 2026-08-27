"""Every version pin in the repo must equal the VERSION file.

This is the durable half of a fix that has now been applied by hand more than
once. ``docker-compose.yml`` was pinned to ``8.3.10.6`` and
``install-manifest.json`` still said ``8.3.10.4`` while ``VERSION`` read
``8.4.2.0`` — six and eight releases stale respectively. Both are
user-facing: the compose pin is what a `docker compose up` actually pulls, so
the documented quick-start was installing a version from two minor releases
ago, and the manifest version is what the installer and upgrade machinery
report as "installed".

Nothing detects that drift, because nothing reads these files during a normal
test or build — which is exactly why the recurrence keeps happening. This test
is the mechanism: a release that bumps VERSION and forgets a pin fails here.
"""

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.requires_repo_source

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VERSION_FILE = PROJECT_ROOT / "VERSION"
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.yml"
MANIFEST_FILE = PROJECT_ROOT / "install-manifest.json"
DOCKERFILE = PROJECT_ROOT / "Dockerfile"
README = PROJECT_ROOT / "README.md"
UPGRADE_SH = PROJECT_ROOT / "upgrade.sh"
INSTALL_SH = PROJECT_ROOT / "install.sh"


@pytest.fixture(scope="module")
def project_version() -> str:
    version = VERSION_FILE.read_text().strip()
    assert re.fullmatch(r"\d+(\.\d+)+", version), f"malformed VERSION: {version!r}"
    return version


def test_docker_compose_image_pin_matches_version(project_version):
    """`docker compose up` must pull the version this tree builds."""
    text = COMPOSE_FILE.read_text()
    match = re.search(r"^\s*image:\s*(\S+):(\S+)\s*$", text, re.MULTILINE)
    assert match, "docker-compose.yml has no pinned image line"
    repository, tag = match.group(1), match.group(2)
    assert "audiobook-manager" in repository, f"unexpected image repository: {repository}"
    assert tag == project_version, (
        f"docker-compose.yml pins {tag}, VERSION says {project_version} — "
        "the documented quick-start would install the wrong release"
    )


def test_install_manifest_version_matches_version(project_version):
    """The installer/upgrade machinery reports this as the installed version."""
    manifest = json.loads(MANIFEST_FILE.read_text())
    assert manifest["version"] == project_version, (
        f"install-manifest.json says {manifest['version']}, VERSION says {project_version}"
    )


def test_dockerfile_app_version_matches_version(project_version):
    """The image label and /app/VERSION both come from this ARG."""
    match = re.search(r"^ARG APP_VERSION=(\S+)\s*$", DOCKERFILE.read_text(), re.MULTILINE)
    assert match, "Dockerfile has no APP_VERSION ARG"
    assert match.group(1) == project_version, (
        f"Dockerfile APP_VERSION is {match.group(1)}, VERSION says {project_version}"
    )


# The README release table labels its top row by the KIND of the release —
# "Latest major", "Latest minor", "Latest patch" or "Latest tweak" — per the
# status-label rule in the /git-release skill. Matching the literal string
# "Latest patch" made this guard pass only for patch releases and fail every
# other kind; v8.4.2.1 is a tweak, and the guard failed on a README that was
# correct. Match the row by its "Latest " prefix so the assertion is about the
# version, which is what this test exists to check.
LATEST_ROW = re.compile(r"\|\s*Latest (major|minor|patch|tweak)\s*\|")


def test_readme_latest_release_row_matches_version(project_version):
    """The README's first release-table row names the current version."""
    for line in README.read_text().splitlines():
        if not LATEST_ROW.search(line):
            continue
        match = re.search(r"\[v([0-9.]+)\]", line)
        assert match, f"'Latest ...' row has no version tag link: {line}"
        assert match.group(1) == project_version, (
            f"README 'Latest ...' row says v{match.group(1)}, VERSION says {project_version}"
        )
        return
    pytest.fail("README.md has no 'Latest major|minor|patch|tweak' release-table row")


def test_deploy_scripts_ship_install_manifest():
    """install.sh and upgrade.sh must copy install-manifest.json to the target.

    A correct repo manifest (asserted above) is useless if the deploy never
    propagates it: the installed manifest froze at first-install and drifted to
    6.6.2.6 while the app ran 8.4.0.3, because neither script copied it
    (Audiobook-Manager-2ob). This asserts both scripts reference the manifest
    so the installed copy tracks VERSION on every deploy.
    """
    for script in (INSTALL_SH, UPGRADE_SH):
        text = script.read_text()
        assert "install-manifest.json" in text and re.search(
            r"cp\b[^\n]*install-manifest\.json", text
        ), f"{script.name} does not copy install-manifest.json to the target"
