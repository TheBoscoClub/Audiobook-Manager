"""Credential resolution for tests that authenticate against a test VM.

This repository is public. A TOTP seed that actually mints valid codes for a
provisioned account is a credential, and three of them were committed here as
default values for ``ADMIN_TOTP_SECRET`` — living in ``library/tests/`` and,
permanently, in the git history.

The seeds have been rotated out of the source. They cannot be scrubbed from
history without a force-push rewrite of a published repository, so treat the
three historical values as compromised: any VM still provisioned with them
must have ``testadmin`` re-seeded (``audiobook-user totp-reset testadmin``).

Integration tests get the seed from the environment, with no fallback — the
same rule this suite already applies to ``VM_HOST`` ("no hardcoded default so
integration tests fail fast rather than silently targeting an unrelated
host"). Unit tests that create their OWN user should not come here at all;
they should mint a throwaway seed with ``pyotp.random_base32()``.
"""

import os

import pytest

ADMIN_TOTP_SECRET_ENV = "ADMIN_TOTP_SECRET"

_MISSING_SECRET_REASON = (
    f"{ADMIN_TOTP_SECRET_ENV} is not set — export the test VM's testadmin TOTP "
    "seed to run VM integration tests (rotate it with "
    "`audiobook-user totp-reset testadmin` on the VM). It is deliberately not "
    "checked in: this repository is public."
)


def admin_totp_secret() -> str:
    """Return the VM testadmin TOTP seed, or '' when unset."""
    return os.environ.get(ADMIN_TOTP_SECRET_ENV, "").strip()


def require_admin_totp_secret() -> str:
    """Return the seed, or skip the calling test when it is unset."""
    secret = admin_totp_secret()
    if not secret:
        pytest.skip(_MISSING_SECRET_REASON)
    return secret
