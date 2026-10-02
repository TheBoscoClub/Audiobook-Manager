"""The Dependabot auto-merge gate must refuse a PR whose head carries a red check.

Branch protection on ``main`` deliberately has ``required_status_checks: null``
(solo-workflow baseline), and ``gh pr merge --auto`` waits for REQUIRED checks
only. On 2026-09-29 PRs #155 and #177 therefore merged with ``Docker Build
Check: FAILURE`` on their own heads (Audiobook-Manager-0xl). The gate now lives
in ``.github/scripts/dependabot-merge-gate.sh``; the merge step in
``dependabot-auto-merge.yml`` runs only on its GREEN verdict.

Fixtures under ``fixtures/dependabot-gate/`` are real API responses, projected
down to the fields the script reads:

* ``pr177-*``      — head ``13096d9a`` of the PR that merged red (the defect)
* ``pr169-*``      — head ``74075076`` of a PR with every check green
* ``main-inflight`` — ``928cd6d6`` on main while a CI run was in progress

The two cases that have no real capture (commit-status failure, truncated
payload) are synthetic and say so in their test names.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.requires_repo_source

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GATE = PROJECT_ROOT / ".github" / "scripts" / "dependabot-merge-gate.sh"
WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "dependabot-auto-merge.yml"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "dependabot-gate"

GREEN, RED, PENDING, ERROR = 0, 1, 2, 3

# Run ids present in the pr177 capture.
CI_RUN_WITH_RED_DOCKER = "36518682166"  # the CI run whose Docker Build Check failed
AUTO_MERGE_RUN = "36518682252"  # the Auto-Merge Dependabot run on that head


def _evaluate(check_runs: Path, status: Path, run_id: str | None = None):
    assert shutil.which("jq"), "jq is required by the gate script and must be installed"
    env = dict(os.environ)
    env.pop("GITHUB_RUN_ID", None)
    if run_id is not None:
        env["GITHUB_RUN_ID"] = run_id
    return subprocess.run(
        [str(GATE), "evaluate", str(check_runs), str(status)],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        check=False,
    )


def _fake_gh(tmp_path: Path, check_runs: Path, status: Path) -> Path:
    """A `gh` shim on PATH that answers the two API GETs the gate makes.

    `wait` mode is where the exit code actually reaches the workflow, so it
    must be exercised end to end — a RETURN trap once fired twice and turned a
    GREEN verdict into exit 1 (run 37066878118, 2026-10-02).
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    shim = bindir / "gh"
    shim.write_text(
        "#!/bin/bash\n"
        'case "$2" in\n'
        f'  *check-runs*) cat "{check_runs}" ;;\n'
        f'  *status) cat "{status}" ;;\n'
        '  *) echo "unexpected gh call: $*" >&2; exit 99 ;;\n'
        "esac\n"
    )
    shim.chmod(0o755)
    return bindir


def _wait(tmp_path: Path, check_runs: Path, status: Path, timeout_min: str = "1"):
    env = dict(os.environ)
    env.pop("GITHUB_RUN_ID", None)
    env["PATH"] = f"{_fake_gh(tmp_path, check_runs, status)}:{env['PATH']}"
    return subprocess.run(
        [str(GATE), "wait", "owner/repo", "deadbeef", timeout_min, "0"],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        check=False,
    )


def _fx(name: str) -> Path:
    path = FIXTURES / name
    assert path.is_file(), f"missing fixture {path}"
    return path


def test_pr177_red_docker_check_is_red():
    result = _evaluate(_fx("pr177-check-runs.json"), _fx("pr177-status.json"))
    assert result.returncode == RED, result.stdout + result.stderr
    assert "VERDICT=RED" in result.stdout
    assert "Docker Build Check=failure" in result.stdout


def test_pr169_all_green_is_green():
    result = _evaluate(_fx("pr169-check-runs.json"), _fx("pr169-status.json"))
    assert result.returncode == GREEN, result.stdout + result.stderr
    assert "VERDICT=GREEN" in result.stdout


def test_skipped_and_neutral_conclusions_are_acceptable():
    """pr169 carries semgrep/ci=skipped and CodeQL=neutral; both must not block."""
    data = json.loads(_fx("pr169-check-runs.json").read_text())
    conclusions = {run["name"]: run["conclusion"] for run in data["check_runs"]}
    assert conclusions["semgrep/ci"] == "skipped"
    assert conclusions["CodeQL"] == "neutral"
    result = _evaluate(_fx("pr169-check-runs.json"), _fx("pr169-status.json"))
    assert result.returncode == GREEN


def test_inflight_checks_are_pending_not_green():
    result = _evaluate(_fx("main-inflight-check-runs.json"), _fx("pr169-status.json"))
    assert result.returncode == PENDING, result.stdout + result.stderr
    assert "VERDICT=PENDING" in result.stdout
    assert "Mutation Gate" in result.stdout


@pytest.mark.parametrize(
    ("run_id", "expected_rc", "expected_token"),
    [
        (CI_RUN_WITH_RED_DOCKER, GREEN, "VERDICT=GREEN"),
        (AUTO_MERGE_RUN, RED, "Docker Build Check=failure"),
    ],
)
def test_own_workflow_run_is_excluded_by_github_run_id(run_id, expected_rc, expected_token):
    """GITHUB_RUN_ID excludes exactly that run's check runs and nothing else.

    Excluding the CI run that owns the red Docker check turns pr177 green —
    proving the exclusion acts. Excluding the auto-merge run leaves it red —
    proving the exclusion is scoped to one run id.
    """
    result = _evaluate(_fx("pr177-check-runs.json"), _fx("pr177-status.json"), run_id=run_id)
    assert result.returncode == expected_rc, result.stdout + result.stderr
    assert expected_token in result.stdout


def test_empty_payload_is_an_error_not_green(tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text("{}")
    result = _evaluate(empty, _fx("pr169-status.json"))
    assert result.returncode == ERROR, result.stdout + result.stderr
    assert "VERDICT=GREEN" not in result.stdout


def test_no_check_runs_yet_is_pending_not_green(tmp_path):
    none_yet = tmp_path / "none.json"
    none_yet.write_text(json.dumps({"total_count": 0, "check_runs": []}))
    result = _evaluate(none_yet, _fx("pr169-status.json"))
    assert result.returncode == PENDING, result.stdout + result.stderr
    assert "no other check runs" in result.stdout


def test_synthetic_truncated_payload_is_refused(tmp_path):
    data = json.loads(_fx("pr169-check-runs.json").read_text())
    data["total_count"] = len(data["check_runs"]) + 1
    truncated = tmp_path / "truncated.json"
    truncated.write_text(json.dumps(data))
    result = _evaluate(truncated, _fx("pr169-status.json"))
    assert result.returncode == ERROR, result.stdout + result.stderr
    assert "truncated" in result.stderr


def test_synthetic_commit_status_failure_is_red(tmp_path):
    status = tmp_path / "status.json"
    status.write_text(
        json.dumps(
            {
                "state": "failure",
                "total_count": 1,
                "statuses": [{"context": "external/ci", "state": "failure"}],
            }
        )
    )
    result = _evaluate(_fx("pr169-check-runs.json"), status)
    assert result.returncode == RED, result.stdout + result.stderr
    assert "external/ci=failure" in result.stdout


def test_wait_mode_exits_zero_on_green(tmp_path):
    result = _wait(tmp_path, _fx("pr169-check-runs.json"), _fx("pr169-status.json"))
    assert result.returncode == GREEN, result.stdout + result.stderr
    assert "VERDICT=GREEN" in result.stdout
    assert result.stderr == "", result.stderr


def test_wait_mode_exits_one_on_red(tmp_path):
    result = _wait(tmp_path, _fx("pr177-check-runs.json"), _fx("pr177-status.json"))
    assert result.returncode == RED, result.stdout + result.stderr
    assert "Docker Build Check=failure" in result.stdout
    assert result.stderr == "", result.stderr


def test_wait_mode_times_out_as_pending(tmp_path):
    result = _wait(tmp_path, _fx("main-inflight-check-runs.json"), _fx("pr169-status.json"), "0")
    assert result.returncode == PENDING, result.stdout + result.stderr
    assert "VERDICT=TIMEOUT" in result.stdout
    assert result.stderr == "", result.stderr


def test_gate_script_is_executable_in_git():
    result = subprocess.run(
        ["git", "ls-files", "-s", str(GATE.relative_to(PROJECT_ROOT))],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.stdout.strip(), "gate script is not tracked by git"
    assert result.stdout.split()[0] == "100755", result.stdout


def _steps() -> list[dict]:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    return workflow["jobs"]["auto-merge"]["steps"]


def test_workflow_never_uses_auto_merge():
    """`--auto` is the defect: it defers to required checks, of which there are none."""
    for step in _steps():
        assert "--auto" not in step.get("run", ""), step.get("name")


def test_workflow_merge_is_gated_and_pinned_to_evaluated_sha():
    steps = _steps()
    by_id = {step.get("id"): step for step in steps if step.get("id")}
    gate = by_id["gate"]
    assert "dependabot-merge-gate.sh wait" in gate["run"]

    merge = next(step for step in steps if "gh pr merge" in step.get("run", ""))
    assert steps.index(merge) > steps.index(gate), "merge step must come after the gate"
    assert "--match-head-commit" in merge["run"]
    assert "steps.pr.outputs.eligible == 'true'" in merge["if"]
    assert "steps.pr.outputs.merge == 'true'" in merge["if"]


def test_workflow_gate_fails_the_job_on_non_green_verdict():
    gate = next(step for step in _steps() if step.get("id") == "gate")
    assert 'exit "$rc"' in gate["run"], "a RED or TIMEOUT verdict must fail the job"
    assert "gh pr comment" in gate["run"], "a withheld merge must be visible on the PR"
