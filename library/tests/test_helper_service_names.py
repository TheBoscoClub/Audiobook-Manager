"""Verify upgrade-helper-process uses correct singular service names."""

import re
from pathlib import Path

HELPER_PATH = Path(__file__).resolve().parents[2] / "scripts" / "upgrade-helper-process"


def test_no_plural_service_names():
    """All service references must use audiobook-* (singular), never audiobooks-* (plural)."""
    content = HELPER_PATH.read_text()
    plural_refs = []
    for i, line in enumerate(content.splitlines(), 1):
        matches = re.findall(
            r"audiobooks-(?:api|proxy|converter|mover|downloader|redirect|scheduler|shutdown-saver|upgrade)",
            line,
        )
        if matches:
            plural_refs.append((i, line.strip(), matches))
    assert plural_refs == [], (
        f"Found {len(plural_refs)} plural service name references (audiobooks-* instead of audiobook-*):\n"
        + "\n".join(f"  Line {ln}: {txt}" for ln, txt, _ in plural_refs)
    )


def test_valid_services_array_correct():
    """VALID_SERVICES array must contain only singular audiobook-* names."""
    content = HELPER_PATH.read_text()
    in_array = False
    services = []
    for line in content.splitlines():
        if "VALID_SERVICES=(" in line:
            in_array = True
            continue
        if in_array:
            if ")" in line:
                break
            svc = line.strip().strip('"').strip("'")
            if svc:
                services.append(svc)
    for svc in services:
        assert svc.startswith(
            "audiobook-"
        ), f"Service '{svc}' should start with 'audiobook-' (singular)"
        assert not svc.startswith(
            "audiobooks-"
        ), f"Service '{svc}' uses plural 'audiobooks-' — must be singular"
