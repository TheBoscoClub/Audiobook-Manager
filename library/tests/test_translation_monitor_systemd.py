"""Regression guard for Audiobook-Manager-oep.

The v8.3.10 GPU-pod monitoring tier ships two systemd services that
watch for stuck/dead/failed claims:

  * audiobook-translation-monitor-live    — every 30 s
  * audiobook-translation-monitor-sampler — every 5 min

Both have StartLimit settings to cap restart storms if the service is
persistently broken. The settings MUST allow each timer's normal cadence —
otherwise systemd blocks the timer-driven starts after a few firings.

Prod 2026-05-02 discovery: live tier was firing every 30 s but the service
had StartLimitBurst=5 / StartLimitIntervalSec=300 — that's 1 start/min ceiling
against a 2 starts/min timer, so the burst quota saturated at t≈150 s and
the next 2-3 min of firings were silently blocked. The live tier was running
~5×/5 min instead of the intended 10×/5 min — half the 'claim reset' sweeps
silently dropped.

These tests parse the unit files and assert StartLimit budget ≥ timer
cadence with margin.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

# Resolve unit-file root: prefer the project tree (`<repo>/systemd/`) for
# dev-machine runs, fall back to `/etc/systemd/system/` for installed-app
# environments (test/dev/QA VMs running these tests against /opt/audiobooks).
# Skip the whole module if neither layout has the unit files — that's a
# deeply broken env where the tests can't add value.
_PROJECT_SYSTEMD_DIR = REPO / "systemd"
_INSTALLED_SYSTEMD_DIR = Path("/etc/systemd/system")
_SENTINEL_UNIT = "audiobook-translation-monitor-live.service"

if (_PROJECT_SYSTEMD_DIR / _SENTINEL_UNIT).exists():
    SYSTEMD_DIR = _PROJECT_SYSTEMD_DIR
elif (_INSTALLED_SYSTEMD_DIR / _SENTINEL_UNIT).exists():
    SYSTEMD_DIR = _INSTALLED_SYSTEMD_DIR
else:
    pytest.skip(
        f"Neither {_PROJECT_SYSTEMD_DIR} nor {_INSTALLED_SYSTEMD_DIR} contains "
        f"{_SENTINEL_UNIT} — cannot validate systemd unit file structure",
        allow_module_level=True,
    )


def _parse_unit(path: Path) -> dict:
    """Parse INI-style systemd unit file. Returns flat dict of key=value."""
    out = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def _parse_seconds(value: str) -> int:
    """Parse systemd time spec ('30s', '5min', '300', '1h') to seconds."""
    m = re.match(r"^\s*(\d+)\s*(s|sec|min|m|h|hour)?\s*$", value, re.IGNORECASE)
    assert m, f"unrecognized systemd time spec: {value!r}"
    n = int(m.group(1))
    unit = (m.group(2) or "s").lower()
    return {"s": 1, "sec": 1, "min": 60, "m": 60, "h": 3600, "hour": 3600}[unit] * n


def test_live_monitor_timer_exists():
    timer = SYSTEMD_DIR / "audiobook-translation-monitor-live.timer"
    service = SYSTEMD_DIR / "audiobook-translation-monitor-live.service"
    assert timer.exists(), "live tier timer unit missing"
    assert service.exists(), "live tier service unit missing"


def test_sampler_monitor_timer_exists():
    timer = SYSTEMD_DIR / "audiobook-translation-monitor-sampler.timer"
    service = SYSTEMD_DIR / "audiobook-translation-monitor-sampler.service"
    assert timer.exists(), "sampler tier timer unit missing"
    assert service.exists(), "sampler tier service unit missing"


def test_live_monitor_startlimit_allows_30s_cadence():
    """Live tier fires every 30 s = 10 firings per 5-min window. The
    StartLimit budget must accommodate the cadence with margin —
    otherwise systemd blocks normal operation. Pre-v8.3.10.1 had Burst=5
    which permitted only 5 starts/window vs the timer's 10 firings/window."""
    timer = _parse_unit(SYSTEMD_DIR / "audiobook-translation-monitor-live.timer")
    service = _parse_unit(SYSTEMD_DIR / "audiobook-translation-monitor-live.service")

    cadence_sec = _parse_seconds(timer["OnUnitActiveSec"])
    interval_sec = _parse_seconds(service["StartLimitIntervalSec"])
    burst = int(service["StartLimitBurst"])

    # Min-required burst = ceil(interval / cadence) + 1 buffer
    min_required = (interval_sec // cadence_sec) + 1

    assert burst >= min_required, (
        f"StartLimitBurst={burst} is below the minimum {min_required} required "
        f"for OnUnitActiveSec={cadence_sec}s within StartLimitIntervalSec="
        f"{interval_sec}s. Timer fires {interval_sec // cadence_sec} times per "
        f"window — burst must allow that plus margin or systemd blocks the "
        f"timer-driven starts."
    )


def test_live_monitor_startlimit_has_runaway_cap():
    """Burst should not be set absurdly high — defeats the runaway-cap intent.
    For a 30s cadence in a 300s window (10 healthy firings), 30 burst is a
    reasonable upper bound (3× headroom). Catches accidental Burst=999 or =0."""
    service = _parse_unit(SYSTEMD_DIR / "audiobook-translation-monitor-live.service")
    burst = int(service["StartLimitBurst"])
    assert 11 <= burst <= 50, (
        f"StartLimitBurst={burst} outside reasonable range [11, 50] for the "
        f"30s/300s timer-cadence/limit-window combination"
    )


def test_sampler_monitor_startlimit_allows_5min_cadence():
    """Sampler tier fires every 5 min within a 10-min limit window — only
    2 firings per window, well under the existing Burst=5. Test exists to
    catch accidental cadence/window changes that would put it over."""
    timer = _parse_unit(SYSTEMD_DIR / "audiobook-translation-monitor-sampler.timer")
    service = _parse_unit(SYSTEMD_DIR / "audiobook-translation-monitor-sampler.service")

    cadence_sec = _parse_seconds(timer["OnUnitActiveSec"])
    interval_sec = _parse_seconds(service["StartLimitIntervalSec"])
    burst = int(service["StartLimitBurst"])

    min_required = (interval_sec // cadence_sec) + 1
    assert burst >= min_required, (
        f"sampler tier StartLimitBurst={burst} below minimum {min_required} "
        f"for OnUnitActiveSec={cadence_sec}s / StartLimitIntervalSec={interval_sec}s"
    )


def test_neither_monitor_uses_zero_interval():
    """feedback_systemd_startlimit.md forbids StartLimitIntervalSec=0
    (cascade retry lockups). Both monitor services must keep a real
    interval."""
    for service_name in (
        "audiobook-translation-monitor-live.service",
        "audiobook-translation-monitor-sampler.service",
    ):
        unit = _parse_unit(SYSTEMD_DIR / service_name)
        interval = unit.get("StartLimitIntervalSec", "")
        assert interval not in ("0", "0s"), (
            f"{service_name} has StartLimitIntervalSec={interval!r} — forbidden "
            f"by feedback_systemd_startlimit.md (zero interval causes cascade "
            f"retry lockups)"
        )
