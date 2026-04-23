"""Regression guard for v8.3.2 QA 502 incident.

Root cause: ``upgrade.sh`` gated ``enable_new_services()`` on
``MAJOR_VERSION=true`` so patch upgrades (8.3.1 -> 8.3.2) shipped new
systemd units (stream-translate) that were never enabled. After host
reboot, the QA reverse-proxy returned Cloudflare 502 because nothing
started at boot.

These tests enforce two invariants going forward:

1. ``upgrade.sh::enable_new_services`` is invoked unconditionally
   (no MAJOR_VERSION / is_major conditional guarding the call).
2. Both ``install.sh`` and ``upgrade.sh`` enable the full canonical
   audiobook unit set, including standalone timers (enrichment) that
   are NOT declared in ``audiobook.target``'s ``Wants=`` lines.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Units that MUST be enabled by a fresh install or an upgrade. Keep in
# sync with upgrade.sh::enable_new_services() and install.sh's enable
# loop. Subdivided so we can report which category drifted.
TARGET_WANTED_SERVICES = (
    "audiobook-api",
    "audiobook-proxy",
    "audiobook-redirect",
    "audiobook-converter",
    "audiobook-mover",
    "audiobook-stream-translate",
    "audiobook-scheduler",
    "audiobook-downloader.timer",
)

STANDALONE_UNITS = (
    "audiobook-enrichment.timer",
    "audiobook-shutdown-saver.service",
)


def _read(path: str) -> str:
    return (REPO / path).read_text()


def test_upgrade_enable_is_unconditional():
    """The call site must NOT be wrapped in an ``if MAJOR_VERSION`` guard.

    If a future refactor re-introduces any conditional wrapper, this
    test catches it before the v8.3.2-class bug ships again.
    """
    upgrade = _read("upgrade.sh")

    # Locate the call line
    call_match = re.search(r"^\s*enable_new_services\s+", upgrade, re.MULTILINE)
    assert call_match, "upgrade.sh no longer invokes enable_new_services"

    # Walk back up to the nearest top-level function body statement and
    # verify no `if [[ ... MAJOR_VERSION ... ]]` or similar wraps it.
    preceding = upgrade[: call_match.start()].splitlines()[-10:]
    window = "\n".join(preceding)
    assert "MAJOR_VERSION" not in window, (
        "enable_new_services must run on every upgrade (not gated on "
        "MAJOR_VERSION). Window preceding the call:\n" + window
    )


def test_enable_new_services_does_not_gate_on_use_sudo():
    """enable_new_services must NOT short-circuit when use_sudo is empty.

    Root cause of the 2026-04-19 QA re-break: an earlier fix preserved a
    guard ``if [[ -z "$use_sudo" ]] ... return 0``, reasoning that the
    function "only applied" when sudo was needed. But ``--remote`` upgrades
    run inside the VM as root (an empty ``use_sudo`` string means root
    already, not missing privilege), so the guard silently skipped
    enablement for every remote upgrade.

    The function's real precondition is "the target file exists" (i.e.,
    the app has been installed at least once). Whether we need to prefix
    systemctl with ``sudo`` is a how-to-run detail, not a whether-to-run
    one.
    """
    upgrade = _read("upgrade.sh")

    # Locate the function body (from def line to closing brace).
    func_match = re.search(
        r"^enable_new_services\s*\(\)\s*\{\n(.*?)^\}",
        upgrade,
        re.DOTALL | re.MULTILINE,
    )
    assert func_match, "enable_new_services function not found in upgrade.sh"
    body = func_match.group(1)

    # The specific pattern that broke QA: `-z "$use_sudo"` followed by
    # `return 0` within the same guard block. Either operand order is a bug.
    bad_guard = re.search(
        r'if\s*\[\[.*-z\s*"\$use_sudo".*\]\].*\n[^}]*return\s+0',
        body,
        re.DOTALL,
    )
    assert bad_guard is None, (
        "enable_new_services must NOT return early when use_sudo is empty — "
        "root-mode --remote upgrades legitimately run with use_sudo=''. "
        'Drop the -z "$use_sudo" branch from the guard and prefix '
        "systemctl calls with $use_sudo (empty prefix is fine when root)."
    )


def test_upgrade_enables_standalone_timers():
    """Standalone timers outside audiobook.target Wants= must be enabled."""
    upgrade = _read("upgrade.sh")
    for unit in STANDALONE_UNITS:
        assert unit in upgrade, (
            f"{unit} must appear in upgrade.sh's enable set — it is not "
            f"declared in audiobook.target Wants= and will otherwise be "
            f"orphaned after upgrade"
        )


def test_install_enables_all_units():
    """Fresh install must enable every canonical unit.

    install.sh's enable loop is the sole enablement path for a fresh
    system (no prior audiobook.target exists for Wants=-parsing).
    """
    install = _read("install.sh")
    for unit in TARGET_WANTED_SERVICES + STANDALONE_UNITS:
        # Strip any .timer / .service suffix variations; install.sh uses
        # either "svc" or "svc.timer" tokens — just require substring.
        bare = unit.removesuffix(".service")
        assert bare in install, (
            f"{unit} must appear in install.sh — a fresh install will "
            f"otherwise leave the unit shipped but not enabled at boot"
        )


def test_upgrade_parses_target_wants():
    """Upgrade must parse ``Wants=`` from audiobook.target to enable services.

    We don't pin specific service names into upgrade.sh for the
    target-wanted set (the parser is the canonical source); we just
    verify the parser branch still exists and iterates ``systemctl
    enable`` over what it finds.
    """
    upgrade = _read("upgrade.sh")
    assert (
        re.search(r"grep\s+'\^Wants='\s+/etc/systemd/system/audiobook\.target", upgrade) is not None
    ), "upgrade.sh no longer parses Wants= from audiobook.target"
    assert (
        re.search(
            r"for\s+svc\s+in\s+\$target_wants.*?systemctl\s+enable\s+\"\$svc\"",
            upgrade,
            re.DOTALL,
        )
        is not None
    ), "upgrade.sh Wants= loop no longer calls systemctl enable per service"


def test_target_declares_expected_wants():
    """audiobook.target must declare Wants= for every target-wanted service.

    Paired with ``test_upgrade_parses_target_wants``, this locks the
    canonical set so dropping a Wants= line fails a test rather than
    silently orphaning a unit at the next upgrade.
    """
    target = _read("systemd/audiobook.target")
    for unit in TARGET_WANTED_SERVICES:
        # .timer tokens end in .timer, .service tokens are implicit
        if not unit.endswith(".timer"):
            unit = f"{unit}.service"
        assert re.search(rf"^Wants={re.escape(unit)}\s*$", target, re.MULTILINE), (
            f"audiobook.target missing Wants={unit}"
        )


def test_all_standalone_timer_unit_files_exist():
    """Sanity: the timers listed above actually ship with the project."""
    for unit in STANDALONE_UNITS:
        path = REPO / "systemd" / unit
        assert path.exists(), (
            f"systemd/{unit} does not exist — either ship the unit or "
            f"remove it from the STANDALONE_UNITS allowlist in this test"
        )
