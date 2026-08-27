#!/usr/bin/env python3
"""Run mutmut over a defined scope and gate on the counters, not the exit code.

Why this exists
---------------
A mutation tool's own exit code is not a gate. mutmut exits 0 when every mutant
was caught AND when no mutant was ever generated — a glob that matches nothing,
an excluded path, a broken config all produce a silent, cheerful pass. The same
degenerate shape exists in every mutation tool. So this script asserts two
things independently:

    1. Mutants were actually generated (something was tested at all).
    2. No mutant survived, except ones explicitly allowlisted with a reason.

The allowlist is deliberately explicit rather than a percentage threshold. A
score of "95% killed" hides *which* 5% lived; a named list forces each survivor
to be justified once, in writing, and makes a new survivor fail the build even
if the overall score improves.

Run it from the repository root; it invokes mutmut from library/ because
conftest.py puts that directory on sys.path, so tests import `localization.…`
while a project-root run would have mutmut looking for `library.localization.…`
and abort on a module-key mismatch.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIBRARY_DIR = REPO_ROOT / "library"
ALLOWLIST = LIBRARY_DIR / "mutants-allowlist.txt"

STATS_JSON = LIBRARY_DIR / "mutants" / "mutmut-cicd-stats.json"


def load_allowlist() -> dict[str, str]:
    """Return {mutant_name: reason}. Blank lines and # comments are ignored."""
    allowed: dict[str, str] = {}
    if not ALLOWLIST.is_file():
        return allowed
    for raw in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, _, reason = line.partition("#")
        name = name.strip()
        if name:
            allowed[name] = reason.strip() or "(no reason recorded)"
    return allowed


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=LIBRARY_DIR, text=True, capture_output=True, **kw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mutmut", default=str(REPO_ROOT / "venv" / "bin" / "mutmut"))
    ap.add_argument("--max-children", default="8")
    args = ap.parse_args()

    mutmut = args.mutmut if Path(args.mutmut).exists() else "mutmut"

    print("running mutmut …", flush=True)
    run([mutmut, "run", "--max-children", args.max_children])

    # Totals come from the stats export, NOT from parsing `mutmut results`:
    # that command prints ONLY survivors, so counting "killed" from its output
    # silently yields zero. (This gate reported `killed: 0` on its first run for
    # exactly that reason — a check that fabricated a number it never read.)
    export = run([mutmut, "export-cicd-stats"])
    if not STATS_JSON.is_file():
        print("FAIL: mutmut produced no stats file", file=sys.stderr)
        print(export.stdout[-1000:] + export.stderr[-1000:], file=sys.stderr)
        return 2
    stats = json.loads(STATS_JSON.read_text(encoding="utf-8"))
    total = int(stats.get("total", 0))
    killed = int(stats.get("killed", 0))
    survived_n = int(stats.get("survived", 0))
    no_tests = int(stats.get("no_tests", 0))

    results = run([mutmut, "results"])
    survived = sorted(
        line.strip().rsplit(":", 1)[0].strip()
        for line in results.stdout.replace("\r", "\n").splitlines()
        if line.strip().endswith(": survived")
    )

    print(f"  mutants: {total}   killed: {killed}   survived: {survived_n}   no-tests: {no_tests}")

    # Gate 1: something was actually tested.
    if total == 0:
        print(
            "FAIL: mutmut produced ZERO mutants. That is a broken scope, not a "
            "clean run — check source_paths/only_mutate in library/setup.cfg.",
            file=sys.stderr,
        )
        return 1
    if killed == 0:
        print(
            "FAIL: ZERO mutants were killed. The test command is almost certainly "
            "failing inside the mutants/ copy (a missing also_copy entry will do "
            "it) — that is a broken run, not a weak suite.",
            file=sys.stderr,
        )
        return 1
    if len(survived) != survived_n:
        print(
            f"FAIL: stats report {survived_n} survivors but `mutmut results` "
            f"listed {len(survived)}. Refusing to gate on inconsistent data.",
            file=sys.stderr,
        )
        return 2

    allowed = load_allowlist()
    unexpected = [n for n in survived if n not in allowed]
    stale = [n for n in allowed if n not in survived]

    print(f"  allowlisted survivors: {len(allowed)}")

    # Gate 2: no unexplained survivor.
    if unexpected:
        print(
            f"\nFAIL: {len(unexpected)} surviving mutant(s) not in the allowlist:", file=sys.stderr
        )
        for n in unexpected:
            print(f"  {n}", file=sys.stderr)
        print(
            f"\nEither strengthen the tests until they die, or add each to "
            f"{ALLOWLIST.relative_to(REPO_ROOT)} with a written reason.",
            file=sys.stderr,
        )
        return 1

    if stale:
        print(f"\nNOTE: {len(stale)} allowlist entr(ies) no longer survive — remove them:")
        for n in stale:
            print(f"  {n}")

    print("\nPASS: mutants were generated, and every survivor is explained.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
