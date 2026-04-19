"""Regression guard for v8.3.2 QA Caddy-port-drift incident.

Root cause: ``upgrade.sh``'s Caddy-template substitution read upstream
ports from the shell env (``AUDIOBOOKS_WEB_PORT`` / ``AUDIOBOOKS_DOCKER_PORT``)
but never sourced them from ``/etc/audiobooks/audiobooks.conf``. Dual-stack
hosts (QA native on :8090) silently fell through to the 8443 default, so
Caddy :8084 proxied to a dead upstream and returned 502. The installer
fix enabled all units correctly — but the front door was pointed at the
wrong room.

The template itself has two placeholders, ``__NATIVE_PORT__`` and
``__DOCKER_PORT__``. This test locks in that upgrade.sh reads both ports
from the installed conf (not just from shell env) before substituting.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (REPO / path).read_text()


def test_caddy_template_has_distinct_placeholders():
    """The template must have two distinct placeholders — otherwise a single
    substitution would unintentionally overwrite both sites."""
    template = _read("caddy/audiobooks.conf")
    assert "__NATIVE_PORT__" in template, "missing __NATIVE_PORT__ placeholder"
    assert "__DOCKER_PORT__" in template, "missing __DOCKER_PORT__ placeholder"


def test_upgrade_sh_reads_ports_from_conf():
    """upgrade.sh's Caddy substitution must read the ports from
    ``/etc/audiobooks/audiobooks.conf`` — not just shell env.

    Root cause of the 2026-04-19 QA 502: a prior implementation used
    ``local native_port="${AUDIOBOOKS_WEB_PORT:-8443}"`` which reads only
    from shell env. upgrade.sh does not source the conf (can't safely eval
    arbitrary shell), so the env is empty on --remote runs. Fallback to
    8443 pointed Caddy :8084 at a dead upstream.
    """
    upgrade = _read("upgrade.sh")

    # Locate the Caddy substitution block — it's inside an
    # `if [[ "$caddy_file" == "audiobooks.conf" ]]` clause.
    block = re.search(
        r'if\s*\[\[\s*"\$caddy_file"\s*==\s*"audiobooks\.conf"\s*\]\]\s*;\s*then'
        r"(.*?)__NATIVE_PORT__.*?__DOCKER_PORT__",
        upgrade,
        re.DOTALL,
    )
    assert block, "Caddy port substitution block not found in upgrade.sh"
    body = block.group(1)

    # Must read AUDIOBOOKS_WEB_PORT from the conf file.
    assert re.search(
        r"grep\s+-oP\s+'?\^?AUDIOBOOKS_WEB_PORT.*?/etc/audiobooks/audiobooks\.conf",
        body,
        re.DOTALL,
    ), "upgrade.sh must read AUDIOBOOKS_WEB_PORT from /etc/audiobooks/audiobooks.conf"
    assert re.search(
        r"grep\s+-oP\s+'?\^?AUDIOBOOKS_DOCKER_PORT.*?/etc/audiobooks/audiobooks\.conf",
        body,
        re.DOTALL,
    ), "upgrade.sh must read AUDIOBOOKS_DOCKER_PORT from /etc/audiobooks/audiobooks.conf"


def test_install_sh_substitutes_both_placeholders():
    """install.sh must substitute both placeholders too — regression guard
    against dropping one side when refactoring."""
    install = _read("install.sh")
    # Narrow to the Caddy install block.
    block = re.search(
        r"Installing Caddy maintenance page configuration(.*?)Installed: Caddy reverse proxy",
        install,
        re.DOTALL,
    )
    assert block, "Caddy install block not found in install.sh"
    body = block.group(1)
    assert "__NATIVE_PORT__" in body, "install.sh must substitute __NATIVE_PORT__"
    assert "__DOCKER_PORT__" in body, "install.sh must substitute __DOCKER_PORT__"
