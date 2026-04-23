"""Tests for v8.3.8 pass-6d provider-agnostic reframing.

Validates:
  1. _probe_stt_warmth handles all 4 provider-config scenarios
     (RunPod-only, Vast.ai-only, both configured, neither configured).
  2. get_stt_provider dual-farm round-robin actually rotates — a regression
     test against the 8.3.7 primary/fallback behavior where RunPod always won.
  3. Repo-level regression pins:
     a. release-requirements.sh does NOT list RunPod/Vast.ai keys as
        required_for_feature (STT backend choice is operator-specific).
     b. install.sh's audiobooks.conf template mentions Vast.ai stubs alongside
        RunPod (both providers documented as peer options).
  4. sampler-burst.sh syntax-checks clean + rejects bad --workers args.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"


# ─── _probe_stt_warmth: 4 scenarios ──────────────────────────────────────────


def _reset_warmth_cache(module) -> None:
    module._STT_WARMTH_CACHE.update(
        {"ts": 0.0, "streaming_ready": 0, "cold": True, "providers": []}
    )


def test_probe_stt_warmth_no_provider():
    """No keys → cold=True, ready=0, providers=[] (streaming disabled)."""
    from library.backend.api_modular import streaming_translate as st  # type: ignore

    _reset_warmth_cache(st)
    with patch.dict(os.environ, {}, clear=False):
        for key in (
            "AUDIOBOOKS_RUNPOD_API_KEY",
            "AUDIOBOOKS_RUNPOD_STREAMING_WHISPER_ENDPOINT",
            "AUDIOBOOKS_VASTAI_SERVERLESS_API_KEY",
            "AUDIOBOOKS_VASTAI_SERVERLESS_STREAMING_ENDPOINT",
        ):
            os.environ.pop(key, None)
        cold, ready, providers = st._probe_stt_warmth()
    assert cold is True
    assert ready == 0
    assert providers == []


def test_probe_stt_warmth_runpod_only_unreachable():
    """RunPod keys set but unreachable endpoint → cold, 0 ready, 1 provider entry."""
    from library.backend.api_modular import streaming_translate as st  # type: ignore

    _reset_warmth_cache(st)
    env = {
        "AUDIOBOOKS_RUNPOD_API_KEY": "fake-key",
        "AUDIOBOOKS_RUNPOD_STREAMING_WHISPER_ENDPOINT": "fake-endpoint-rp",
    }
    for key in (
        "AUDIOBOOKS_VASTAI_SERVERLESS_API_KEY",
        "AUDIOBOOKS_VASTAI_SERVERLESS_STREAMING_ENDPOINT",
    ):
        os.environ.pop(key, None)
    with patch.dict(os.environ, env, clear=False):
        # Network probe will fail → provider still listed with ready=0.
        cold, ready, providers = st._probe_stt_warmth()
    assert cold is True
    assert ready == 0
    # At least one RunPod entry present (Vast.ai absent because no key).
    names = [p["name"] for p in providers]
    assert "runpod" in names
    assert "vastai" not in names


def test_probe_stt_warmth_vastai_only_unreachable():
    """Vast.ai keys set but unreachable endpoint → 1 Vast.ai entry, no RunPod."""
    from library.backend.api_modular import streaming_translate as st  # type: ignore

    _reset_warmth_cache(st)
    env = {
        "AUDIOBOOKS_VASTAI_SERVERLESS_API_KEY": "fake-vast-key",
        "AUDIOBOOKS_VASTAI_SERVERLESS_STREAMING_ENDPOINT": "fake-endpoint-vast",
    }
    for key in (
        "AUDIOBOOKS_RUNPOD_API_KEY",
        "AUDIOBOOKS_RUNPOD_STREAMING_WHISPER_ENDPOINT",
    ):
        os.environ.pop(key, None)
    with patch.dict(os.environ, env, clear=False):
        cold, ready, providers = st._probe_stt_warmth()
    assert cold is True
    names = [p["name"] for p in providers]
    assert "vastai" in names
    assert "runpod" not in names


def test_probe_stt_warmth_both_configured():
    """Both configured → 2 provider entries appear (order: runpod, vastai)."""
    from library.backend.api_modular import streaming_translate as st  # type: ignore

    _reset_warmth_cache(st)
    env = {
        "AUDIOBOOKS_RUNPOD_API_KEY": "fake-rp-key",
        "AUDIOBOOKS_RUNPOD_STREAMING_WHISPER_ENDPOINT": "ep-rp",
        "AUDIOBOOKS_VASTAI_SERVERLESS_API_KEY": "fake-vast-key",
        "AUDIOBOOKS_VASTAI_SERVERLESS_STREAMING_ENDPOINT": "ep-vast",
    }
    with patch.dict(os.environ, env, clear=False):
        cold, ready, providers = st._probe_stt_warmth()
    names = [p["name"] for p in providers]
    assert "runpod" in names
    assert "vastai" in names
    # cold may be True (unreachable) or False (somehow reachable) — both valid.
    assert isinstance(cold, bool)
    assert ready >= 0


def test_probe_runpod_warmth_backcompat_shim():
    """Legacy two-tuple caller sees (cold, ready) unpacked without error."""
    from library.backend.api_modular import streaming_translate as st  # type: ignore

    _reset_warmth_cache(st)
    result = st._probe_runpod_warmth()
    assert isinstance(result, tuple)
    assert len(result) == 2
    cold, ready = result
    assert isinstance(cold, bool)
    assert isinstance(ready, int)


# ─── Dual-farm round-robin regression ────────────────────────────────────────


def test_round_robin_fans_out_across_both_providers():
    """8 back-to-back get_stt_provider() calls must hit BOTH farms — not
    the 8-to-0 split the pre-8.3.8 primary/fallback selector would return."""
    # Force reload because module-level RR counter is process-wide.
    import importlib

    import library.localization.config as cfg_mod  # type: ignore
    import library.localization.pipeline as pipe_mod  # type: ignore

    env = {
        "AUDIOBOOKS_RUNPOD_API_KEY": "fake",
        "AUDIOBOOKS_RUNPOD_BACKLOG_WHISPER_ENDPOINT": "ep-rp-backlog",
        "AUDIOBOOKS_VASTAI_SERVERLESS_API_KEY": "fake",
        "AUDIOBOOKS_VASTAI_SERVERLESS_BACKLOG_ENDPOINT": "ep-vast-backlog",
        "AUDIOBOOKS_STT_DISTRIBUTION": "round_robin",
    }
    with patch.dict(os.environ, env, clear=False):
        importlib.reload(cfg_mod)
        importlib.reload(pipe_mod)
        from library.localization.selection import WorkloadHint  # type: ignore

        names = [pipe_mod.get_stt_provider(workload=WorkloadHint.LONG_FORM).name for _ in range(8)]
    unique = {n.split(":")[0] for n in names}  # strip endpoint suffix if any
    # Both providers must have been picked at least once.
    assert len(unique) >= 2, f"expected both farms to be picked; got {unique}"


def test_primary_mode_pins_to_remote0():
    """primary distribution mode = legacy behavior — always the first candidate."""
    import importlib

    import library.localization.config as cfg_mod  # type: ignore
    import library.localization.pipeline as pipe_mod  # type: ignore

    env = {
        "AUDIOBOOKS_RUNPOD_API_KEY": "fake",
        "AUDIOBOOKS_RUNPOD_BACKLOG_WHISPER_ENDPOINT": "ep-rp",
        "AUDIOBOOKS_VASTAI_SERVERLESS_API_KEY": "fake",
        "AUDIOBOOKS_VASTAI_SERVERLESS_BACKLOG_ENDPOINT": "ep-vast",
        "AUDIOBOOKS_STT_DISTRIBUTION": "primary",
    }
    with patch.dict(os.environ, env, clear=False):
        importlib.reload(cfg_mod)
        importlib.reload(pipe_mod)
        from library.localization.selection import WorkloadHint  # type: ignore

        names = [pipe_mod.get_stt_provider(workload=WorkloadHint.LONG_FORM).name for _ in range(4)]
    unique = {n.split(":")[0] for n in names}
    assert len(unique) == 1, f"primary mode should pin to remote[0]; got {unique}"


# ─── release-requirements.sh grep pins ───────────────────────────────────────


def test_release_requirements_does_not_list_stt_providers_as_required():
    """Operator-specific STT backend keys must NOT be project-level requirements.
    Only DeepL (currently the sole translation backend) and TTS_PROVIDER stay."""
    content = (SCRIPTS_DIR / "release-requirements.sh").read_text()
    # Ensure none of the STT provider keys appear in REQUIRED_CONFIG_KEYS.
    # We look for the key name immediately followed by a SEVERITY token.
    forbidden = [
        "AUDIOBOOKS_RUNPOD_API_KEY|required",
        "AUDIOBOOKS_RUNPOD_STREAMING_WHISPER_ENDPOINT|required",
        "AUDIOBOOKS_RUNPOD_BACKLOG_WHISPER_ENDPOINT|required",
        "AUDIOBOOKS_VASTAI_SERVERLESS_API_KEY|required",
        "AUDIOBOOKS_VASTAI_SERVERLESS_STREAMING_ENDPOINT|required",
        "AUDIOBOOKS_VASTAI_SERVERLESS_BACKLOG_ENDPOINT|required",
    ]
    for token in forbidden:
        assert token not in content, (
            f"release-requirements.sh re-added operator-specific STT key as project "
            f"requirement: {token}. STT backend choice belongs to the operator's "
            f"audiobooks.conf, not the project contract."
        )
    # But DeepL and TTS_PROVIDER should still be there.
    assert "AUDIOBOOKS_DEEPL_API_KEY|required_for_feature|translation" in content
    assert "AUDIOBOOKS_TTS_PROVIDER|optional" in content


def test_install_sh_template_has_vastai_stubs_alongside_runpod():
    """install.sh's audiobooks.conf template must document BOTH RunPod and
    Vast.ai as peer STT backend options (not RunPod-only)."""
    content = (REPO_ROOT / "install.sh").read_text()
    for needle in (
        "AUDIOBOOKS_RUNPOD_API_KEY",
        "AUDIOBOOKS_RUNPOD_STREAMING_WHISPER_ENDPOINT",
        "AUDIOBOOKS_VASTAI_SERVERLESS_API_KEY",
        "AUDIOBOOKS_VASTAI_SERVERLESS_STREAMING_ENDPOINT",
        "AUDIOBOOKS_WHISPER_GPU_HOST",  # self-hosted option
    ):
        assert content.count(needle) >= 1, (
            f"install.sh audiobooks.conf template is missing {needle} — "
            f"provider-agnostic options must all be documented."
        )


# ─── sampler-burst.sh shell hardening ────────────────────────────────────────


def test_sampler_burst_syntax_checks():
    script = SCRIPTS_DIR / "sampler-burst.sh"
    assert script.is_file()
    result = subprocess.run(
        ["bash", "-n", str(script)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, f"sampler-burst.sh has syntax errors: {result.stderr}"


@pytest.mark.parametrize(
    "bad_value",
    [
        "abc",  # non-numeric
        "0",  # below min
        "17",  # above max
        "-3",  # negative
        "5; rm -rf /",  # injection attempt
        "",  # empty
    ],
)
def test_sampler_burst_rejects_bad_workers_arg(bad_value):
    """sampler-burst.sh must exit 2 on invalid --workers values (shell-injection
    defense — the value lands in a `for i in $(seq 1 N)` loop)."""
    script = SCRIPTS_DIR / "sampler-burst.sh"
    # Use --help-like path: pass invalid --workers then a value that'd stop
    # processing. We expect exit 2 before reaching systemctl is-active.
    result = subprocess.run(
        ["bash", str(script), "--workers", bad_value],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 2, (
        f"sampler-burst.sh accepted invalid --workers='{bad_value}' "
        f"(exit={result.returncode}, stderr={result.stderr!r})"
    )


def test_sampler_burst_accepts_valid_workers_arg_via_help():
    """Help short-circuits argument parsing entirely — so we use -h to verify
    the script is callable and argument-parsing works at all."""
    script = SCRIPTS_DIR / "sampler-burst.sh"
    result = subprocess.run(
        ["bash", str(script), "--help"],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    assert result.returncode == 0
    assert "sampler-burst.sh" in result.stdout
    assert "--workers" in result.stdout
