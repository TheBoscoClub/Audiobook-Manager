"""Hash verification task -- verify file hashes against database records."""

import hashlib
import logging
import sqlite3
from pathlib import Path

from . import registry
from .base import ExecutionResult, MaintenanceTask, ValidationResult
from .db_vacuum import _resolve_db_path

logger = logging.getLogger(__name__)


@registry.register
class HashVerifyTask(MaintenanceTask):
    name = "hash_verify"
    display_name = "File Hash Verification"
    description = "Verify audiobook file SHA-256 hashes match database records"

    def validate(self, params: dict) -> ValidationResult:
        db_path = _resolve_db_path(params)
        if not db_path or not db_path.exists():
            return ValidationResult(ok=False, message="Database not found")
        return ValidationResult(ok=True)

    def execute(self, params: dict, progress_callback=None) -> ExecutionResult:
        db_path = _resolve_db_path(params)
        if not db_path:
            return ExecutionResult(success=False, message="Database path not available")

        try:
            conn = sqlite3.connect(str(db_path))
            rows = conn.execute(
                "SELECT id, file_path, sha256_hash FROM audiobooks WHERE sha256_hash IS NOT NULL"
            ).fetchall()
            conn.close()

            total = len(rows)
            if total == 0:
                return ExecutionResult(
                    success=True, message="No files with hashes to verify"
                )

            mismatches = []
            missing = []
            verified = 0

            for i, (aid, fpath, expected) in enumerate(rows):
                if progress_callback and i % 10 == 0:
                    progress_callback(i / total, f"Checking {i}/{total}...")

                p = Path(fpath)
                if not p.exists():
                    missing.append(fpath)
                    continue

                h = hashlib.sha256()
                with open(p, "rb") as f:
                    while chunk := f.read(65536):
                        h.update(chunk)

                if h.hexdigest() != expected:
                    mismatches.append({"id": aid, "path": fpath})
                else:
                    verified += 1

            if progress_callback:
                progress_callback(1.0, "Complete")

            ok = len(mismatches) == 0
            return ExecutionResult(
                success=ok,
                message=(
                    f"Verified {verified}/{total}, "
                    f"{len(mismatches)} mismatches, {len(missing)} missing"
                ),
                data={
                    "total": total,
                    "verified": verified,
                    "mismatches": mismatches[:20],
                    "missing_count": len(missing),
                },
            )
        except Exception as e:
            return ExecutionResult(success=False, message=str(e))

    def estimate_duration(self):
        return 600
