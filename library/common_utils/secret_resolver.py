"""Resolve credentials from env var OR *_FILE pointer.

Existing inline env-var values (SMTP_PASS=..., AUDIOBOOKS_DEEPL_API_KEY=...) work
unchanged. Additionally, operators can store the secret in a separate 0600 file
and set the *_FILE env var to that path — useful for keeping secrets out of the
main config and avoiding drift between credential stores.

Precedence: env var value (if non-empty) wins over *_FILE pointer. If neither is
set or non-empty, returns `default`.

Lives in `library/common_utils/` (not `library/utils/`) because the project
already has multiple sibling `utils/` namespaces — `library/scripts/utils/` for
OpenLibrary client code and `library/scanner/utils/` for scanner helpers. A
top-level `library/utils/` would shadow the `from utils.openlibrary_client
import ...` pattern used by `library/scripts/populate_from_openlibrary.py` etc.
"""
from __future__ import annotations

import logging
import os

_log = logging.getLogger(__name__)


def resolve_secret(name: str, default: str = "") -> str:
    """Return the value of credential `name` from env or `${name}_FILE`.

    Args:
        name: env var name (e.g. "SMTP_PASS", "AUDIOBOOKS_DEEPL_API_KEY").
        default: fallback when neither env var nor *_FILE is set.

    Returns:
        The secret value as a string (whitespace-stripped), or `default`.
    """
    inline = os.environ.get(name, "").strip()
    if inline:
        return inline

    file_path = os.environ.get(f"{name}_FILE", "").strip()
    if not file_path:
        return default

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            value = f.read().strip()
        if not value:
            _log.warning("Credential file %s is empty for %s", file_path, name)
            return default
        return value
    except FileNotFoundError:
        _log.warning(
            "Credential file %s referenced by %s_FILE does not exist",
            file_path,
            name,
        )
        return default
    except PermissionError:
        _log.warning(
            "Cannot read credential file %s for %s (check permissions)",
            file_path,
            name,
        )
        return default
    except OSError as exc:
        _log.warning("Error reading credential file %s for %s: %s", file_path, name, exc)
        return default
