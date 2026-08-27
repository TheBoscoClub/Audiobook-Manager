"""`.env.example` must document every variable docker-compose.yml interpolates.

An undocumented `${VAR}` in the compose file cannot be discovered by a user:
compose supplies a default for each one, so nothing errors and nothing warns —
the default is silently in force and the knob may as well not exist.
``INSTANCE_BADGE`` was in exactly that state.

The check runs the other way too: `.env` itself is deliberately empty
(gitignored, compose-only), so this test is what keeps `.env.example` honest.
"""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.requires_repo_source

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.yml"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"

# ${VAR} and ${VAR:-default}
_INTERPOLATION_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)")


def _compose_variables() -> set[str]:
    return set(_INTERPOLATION_RE.findall(COMPOSE_FILE.read_text()))


def _documented_variables() -> set[str]:
    """Names on an assignment line, commented-out or not."""
    documented = set()
    for line in ENV_EXAMPLE.read_text().splitlines():
        match = re.match(r"^\s*#?\s*([A-Z_][A-Z0-9_]*)=", line)
        if match:
            documented.add(match.group(1))
    return documented


def test_every_compose_variable_is_documented():
    missing = sorted(_compose_variables() - _documented_variables())
    assert not missing, (
        ".env.example does not document these docker-compose.yml variables: " + ", ".join(missing)
    )


def test_compose_variables_were_actually_found():
    """Guard the guard — a regex that matches nothing would pass silently."""
    variables = _compose_variables()
    assert len(variables) >= 5, f"suspiciously few interpolations found: {variables}"
    assert "AUDIOBOOK_DIR" in variables


def test_env_example_states_its_scope():
    """The file's whole confusion was that it read like global config."""
    header = ENV_EXAMPLE.read_text()[:1200]
    assert "docker" in header.lower(), ".env.example must say it is Docker-scoped"
    assert "audiobooks.conf" in header, (
        ".env.example must point native installs at /etc/audiobooks/audiobooks.conf"
    )
