"""The auth command-line tools start when run the way their wrappers run them.

The installed wrappers exec each tool as a script (``python .../auth/<tool>.py``)
from whatever directory the caller is in, with no PYTHONPATH. An import that
only resolves under pytest's sys.path makes the installed command crash before
it parses its arguments (bd Audiobook-Manager-b0my.5).
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

AUTH_DIR = Path(__file__).resolve().parent.parent / "auth"


@pytest.mark.parametrize("script", ["cli.py", "notify_cli.py"])
def test_script_starts_from_a_neutral_directory(script, tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, str(AUTH_DIR / script), "--help"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-600:]
    assert "usage:" in result.stdout.lower()
