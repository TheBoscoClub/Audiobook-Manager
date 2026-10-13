"""CI gate for library/requirements.lock (exact release-tested package versions).

Raising a floor in requirements.txt without regenerating the lock, or adding a
requirement with no lock entry, fails here. The generator's refusal to write an
under-floor lock is exercised against real freezes in tmp dirs.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

pytestmark = pytest.mark.requires_repo_source

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "library"
LOCK = LIB / "requirements.lock"
REQ_FILES = [LIB / "requirements.txt", LIB / "requirements-docker.txt"]
GENERATOR = ROOT / "scripts" / "generate-requirements-lock.sh"


def _parse_requirements(path: Path) -> list[Requirement]:
    reqs = []
    for raw in path.read_text().splitlines():
        text = raw.split("#", 1)[0].strip()
        if text:
            reqs.append(Requirement(text))
    return reqs


def _parse_lock(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.fullmatch(r"([a-z0-9][a-z0-9-]*)==(\S+)", line)
        assert m, f"lock line is not a normalised name==version pin: {line!r}"
        assert m.group(1) not in pins, f"duplicate lock entry {m.group(1)}"
        pins[m.group(1)] = m.group(2)
    return pins


def violations(req_path: Path, lock_path: Path) -> list[str]:
    pins = _parse_lock(lock_path)
    out = []
    for req in _parse_requirements(req_path):
        name = canonicalize_name(req.name)
        if name not in pins:
            out.append(f"{req.name}: no lock entry")
        elif not req.specifier.contains(Version(pins[name]), prereleases=True):
            out.append(f"{req.name}{req.specifier}: locked {pins[name]} does not satisfy")
    return out


@pytest.mark.parametrize("req_file", REQ_FILES, ids=lambda p: p.name)
def test_lock_satisfies_every_requirement(req_file):
    assert LOCK.is_file(), (
        "library/requirements.lock missing — run scripts/generate-requirements-lock.sh"
    )
    assert violations(req_file, LOCK) == [], (
        f"{req_file.name} and requirements.lock disagree — regenerate the lock from the test VM"
    )


def test_lock_is_sorted_normalised_and_excludes_installer_tooling():
    names = list(_parse_lock(LOCK))
    assert names == sorted(names)
    assert not {"pip", "setuptools", "wheel"} & set(names)
    body = [ln for ln in LOCK.read_text().splitlines() if ln and not ln.startswith("#")]
    assert not any(" @ " in ln or ln.startswith("-e") for ln in body)


def test_raising_a_floor_without_regenerating_fails(tmp_path):
    """Falsification: the gate must go red on a floor above the locked version."""
    pins = _parse_lock(LOCK)
    req = tmp_path / "requirements.txt"
    req.write_text(f"flask>={Version(pins['flask']).major + 1}.0.0\n")
    assert violations(req, LOCK), "raised floor not detected"
    req.write_text("brand-new-package>=1.0\n")
    assert violations(req, LOCK) == ["brand-new-package: no lock entry"]


# ---------------------------------------------------------------- generator
def _gen(tmp_path: Path, freeze: str, out: Path):
    f = tmp_path / "freeze.txt"
    f.write_text(freeze)
    return subprocess.run(
        [
            str(GENERATOR), "testhost", "--freeze-file", str(f),
            "--python-version", "3.14.0", "--output", str(out),
        ],
        capture_output=True, text=True, check=False, timeout=60,
    )  # fmt: skip


def _good_freeze() -> str:
    lines = [f"{n}=={v}" for n, v in _parse_lock(LOCK).items()]
    return "\n".join(["pip==26.0", "setuptools==80.0", "wheel==0.45", *lines]) + "\n"


def test_generator_writes_normalised_lock_from_good_freeze(tmp_path):
    out = tmp_path / "out.lock"
    freeze = _good_freeze().replace("typing-extensions==", "Typing_Extensions==")
    freeze += "-e git+https://example.invalid/x#egg=x\nlocal @ file:///tmp/local\n"
    proc = _gen(tmp_path, freeze, out)
    assert proc.returncode == 0, proc.stderr
    text = out.read_text()
    assert "# Source host:    testhost" in text and "# Python:         3.14.0" in text
    assert _parse_lock(out) == _parse_lock(LOCK)  # normalised, sorted, tooling excluded


def test_generator_refuses_lock_below_a_floor(tmp_path):
    out = tmp_path / "out.lock"
    out.write_text("# previous\nflask==1.0.0\n")
    pins = _parse_lock(LOCK)
    freeze = _good_freeze().replace(f"flask=={pins['flask']}", "flask==1.0.0")
    proc = _gen(tmp_path, freeze, out)
    assert proc.returncode == 1
    assert "REFUSING" in proc.stderr and "Flask" in proc.stderr
    assert out.read_text() == "# previous\nflask==1.0.0\n", "existing lock must be untouched"


def test_generator_refuses_missing_requirement(tmp_path):
    out = tmp_path / "out.lock"
    freeze = "\n".join(ln for ln in _good_freeze().splitlines() if not ln.startswith("mutagen=="))
    proc = _gen(tmp_path, freeze + "\n", out)
    assert proc.returncode == 1
    assert "mutagen has no entry" in proc.stderr
    assert not out.exists()
