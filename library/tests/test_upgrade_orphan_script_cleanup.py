"""Regression guard for v8.3.2 orphan-script retirement gap.

Root cause: ``upgrade.sh::upgrade_application`` copies ``scripts/`` with a
per-file ``cp`` loop (no ``rsync --delete``). When v8.3.2 retired three
scripts (``fleet-watchdog.sh``, ``translation-check.sh``,
``translation-daemon.sh``), their files persisted in
``/opt/audiobooks/scripts/`` on every upgraded install. This is the same
class of bug as the 8.3.2 systemd-enablement gap — drift between what the
project ships and what survives on installed systems.

Paired with ``test_service_enablement_unconditional.py``: that test guards
the "new units must be enabled" side; this one guards the "retired scripts
must be removed" side. Together they fence the upgrade contract.

These tests enforce:

1. ``audit_and_cleanup`` contains a scripts-orphan check that diffs against
   ``${PROJECT_DIR}/scripts/`` — not a hardcoded allowlist that bitrots.
2. The allowlist for root-level scripts (``upgrade.sh``, ``migrate-api.sh``)
   is present so every upgrade doesn't nuke them.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (REPO / path).read_text()


def _audit_body() -> str:
    upgrade = _read("upgrade.sh")
    func_match = re.search(
        r"^audit_and_cleanup\s*\(\)\s*\{\n(.*?)^\}", upgrade, re.DOTALL | re.MULTILINE
    )
    assert func_match, "audit_and_cleanup function not found in upgrade.sh"
    return func_match.group(1)


def test_audit_scans_scripts_directory_for_orphans():
    """audit_and_cleanup must iterate files under target/scripts/."""
    body = _audit_body()
    # A ``find ... target/scripts ... -maxdepth 1 -type f`` walk is the
    # mechanism. Any equivalent diff-based walk is acceptable, but the
    # current implementation uses find — pin it to keep the invariant
    # mechanical and easy to review.
    assert re.search(r'find\s+"\$\{target\}/scripts"\s+-maxdepth\s+1\s+-type\s+f', body), (
        "audit_and_cleanup no longer walks ${target}/scripts/ to detect "
        "orphaned files — retired scripts will survive upgrade. Same class "
        "of bug as 8.3.2 systemd-enablement gap."
    )


def test_audit_diffs_against_project_scripts_dir():
    """Orphan detection must compare installed against project source.

    A hardcoded allowlist (like section (d)'s ``legacy_files``) bitrots:
    maintainers forget to add each retired file. A diff against
    ``${PROJECT_DIR}/scripts/`` is self-maintaining — any file removed
    from the project is automatically detected as an orphan on the next
    upgrade.
    """
    body = _audit_body()
    # The check must consult PROJECT_DIR (the source of truth for what
    # ships) and test membership with -f.
    assert "PROJECT_DIR" in body, (
        "audit_and_cleanup's orphan-script check must reference PROJECT_DIR "
        "to diff installed scripts against the project source"
    )
    assert re.search(r'\[\[\s*!\s*-f\s+"\$\{project_scripts_dir\}/', body), (
        "audit_and_cleanup must test for script absence in the project "
        "scripts/ dir (not-exists -> orphan)"
    )


def test_audit_allowlists_root_level_scripts():
    """upgrade.sh and migrate-api.sh live at the project root, not in scripts/.

    They get copied into target/scripts/ by the ``Upgrade root-level
    management scripts`` block. Without an allowlist, the orphan diff
    would flag and delete them on every upgrade — bricking the target.
    """
    body = _audit_body()
    assert re.search(r"root_level_scripts=\([^)]*upgrade\.sh[^)]*\)", body), (
        "audit_and_cleanup must allowlist upgrade.sh from the orphan-script "
        "check (it's copied from project root, not project/scripts/)"
    )
    assert re.search(r"root_level_scripts=\([^)]*migrate-api\.sh[^)]*\)", body), (
        "audit_and_cleanup must allowlist migrate-api.sh from the "
        "orphan-script check (it's copied from project root)"
    )


def test_audit_reports_orphan_removal():
    """Every removal must be logged so upgrade output shows what changed.

    Silent filesystem mutations are a debugging nightmare — the existing
    sections (a)-(f) all echo per-removal. This mirrors that.
    """
    body = _audit_body()
    assert "Removed orphan script:" in body, (
        "audit_and_cleanup must echo per-removal so the upgrade log "
        "records which scripts were removed"
    )


def test_retired_v8_3_2_scripts_no_longer_in_project():
    """Sanity: the 3 files that motivated this fix must not be in scripts/.

    If any of them reappear in the project, this test fails loud — either
    someone reverted the retirement, or a new script happened to reuse
    the name. Either way, the delta needs a conscious decision.
    """
    retired = ("fleet-watchdog.sh", "translation-check.sh", "translation-daemon.sh")
    scripts_dir = REPO / "scripts"
    for name in retired:
        assert not (scripts_dir / name).exists(), (
            f"scripts/{name} was retired in v8.3.2 Phase 3 but has "
            f"reappeared. If this is intentional, update the audit "
            f"and this test together."
        )
