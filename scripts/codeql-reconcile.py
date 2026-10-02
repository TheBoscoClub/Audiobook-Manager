#!/usr/bin/env python3
"""Reconcile a local CodeQL scan against the triage ledger.

STAND-ALONE TOOL — deliberately not wired into the service graph
(upgrade-consistency.md exception): it is an operator/CI-side check over
`.codeql-db/results-*.sarif` and `.codeql-db/dismissed.json`, never run by
the application or its systemd units.

Every result the scanner reports must match exactly one ledger dismissal
(same rule, same file, within a window of lines — fixes shift later lines)
and every dismissal must still be reported; a dismissal nothing reports is a
sink that no longer exists and should move to `fixed`. Exit 0 when the two
sets agree, 1 otherwise. `--write` updates each matched entry's `line` to
the current scan (keeping `line_at_triage`) and stamps `scan_date`.

    scripts/codeql-reconcile.py                 # report only
    scripts/codeql-reconcile.py --write 2026-10-02
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_DIR = ROOT / ".codeql-db"
LEDGER = DB_DIR / "dismissed.json"
LINE_WINDOW = 40


def scan_results() -> list[tuple[str, str, int]]:
    found = []
    for sarif in sorted(DB_DIR.glob("results-*.sarif")):
        for run in json.loads(sarif.read_text())["runs"]:
            for result in run["results"]:
                loc = result["locations"][0]["physicalLocation"]
                found.append(
                    (result["ruleId"], loc["artifactLocation"]["uri"], loc["region"]["startLine"])
                )
    if not found and not list(DB_DIR.glob("results-*.sarif")):
        sys.exit(f"no results-*.sarif under {DB_DIR}; run codeql-scan-all first")
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--write", metavar="SCAN_DATE", help="update ledger lines and scan_date")
    args = parser.parse_args()

    ledger = json.loads(LEDGER.read_text())
    dismissed = ledger["dismissed"]
    capacity = [entry.get("sinks", 1) for entry in dismissed]
    new_line: dict[int, int] = {}
    unmatched: list[tuple[str, str, int]] = []

    results = scan_results()
    for rule, file, line in sorted(results, key=lambda r: (r[1], r[2])):
        candidates = sorted(
            (abs(entry.get("line_at_triage", entry["line"]) - line), index)
            for index, entry in enumerate(dismissed)
            if entry["rule"] == rule
            and entry["file"] == file
            and capacity[index] > 0
            and abs(entry.get("line_at_triage", entry["line"]) - line) <= LINE_WINDOW
        )
        if not candidates:
            unmatched.append((rule, file, line))
            continue
        index = candidates[0][1]
        capacity[index] -= 1
        new_line[index] = line

    stale = [
        (entry["rule"], entry["file"], entry["line"])
        for index, entry in enumerate(dismissed)
        if capacity[index] == entry.get("sinks", 1)
    ]

    print(f"scan results: {len(results)}")
    print(
        f"ledger capacity: {sum(e.get('sinks', 1) for e in dismissed)} sinks in {len(dismissed)} entries"
    )
    print(f"UNMATCHED results (need a fix or a dismissal): {unmatched}")
    print(f"dismissals the scan no longer reports (sink gone — move to fixed): {stale}")

    ok = not unmatched and not stale
    if args.write and ok:
        for index, line in new_line.items():
            entry = dismissed[index]
            if entry["line"] != line:
                entry.setdefault("line_at_triage", entry["line"])
                entry["line"] = line
        ledger["scan_date"] = args.write
        ledger["_reconciled"] = (
            f"re-scan {args.write}: {len(results)} results, each matched one-to-one to a "
            "dismissal; `line` is the current scan's line, `line_at_triage` the original where it moved"
        )
        LEDGER.write_text(json.dumps(ledger, indent=1, ensure_ascii=False) + "\n")
        print("ledger written")
    print("RECONCILED" if ok else "MISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
