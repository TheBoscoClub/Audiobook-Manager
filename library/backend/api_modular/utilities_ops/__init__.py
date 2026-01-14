"""
Async operations with progress tracking.

This package provides modular endpoints for background operations:
- status: Operation tracking (status, active, all, cancel)
- library: Library content (add-new, rescan, reimport)
- hashing: File integrity (generate-hashes, generate-checksums)
- audible: Audible integration (download, sync-genres, sync-narrators, prereqs)
- maintenance: System maintenance (rebuild-queue, cleanup-indexes, sort-fields, duplicates)
"""

import sys
from pathlib import Path

# Path setup for sibling module import (operation_status is in backend/, not api_modular/)
# This must be done before importing sub-modules that use operation_status
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from flask import Blueprint

from .audible import init_audible_routes, utilities_ops_audible_bp
from .hashing import init_hashing_routes, utilities_ops_hashing_bp
from .library import init_library_routes, utilities_ops_library_bp
from .maintenance import init_maintenance_routes, utilities_ops_maintenance_bp
from .status import init_status_routes, utilities_ops_status_bp

# Combined blueprint for backwards compatibility
utilities_ops_bp = Blueprint("utilities_ops", __name__)

# Export all sub-blueprints
__all__ = [
    # Main combined blueprint
    "utilities_ops_bp",
    "init_ops_routes",
    # Sub-blueprints
    "utilities_ops_status_bp",
    "utilities_ops_library_bp",
    "utilities_ops_hashing_bp",
    "utilities_ops_audible_bp",
    "utilities_ops_maintenance_bp",
    # Init functions
    "init_status_routes",
    "init_library_routes",
    "init_hashing_routes",
    "init_audible_routes",
    "init_maintenance_routes",
]


def init_ops_routes(db_path, project_root):
    """
    Initialize all operation routes.

    This is the main entry point that initializes all sub-modules
    and returns the combined list of blueprints for registration.

    Args:
        db_path: Path to the SQLite database
        project_root: Path to the project root directory

    Returns:
        List of initialized blueprints to register with the Flask app
    """
    # Initialize each module
    init_status_routes()
    init_library_routes(db_path, project_root)
    init_hashing_routes(project_root)
    init_audible_routes(project_root)
    init_maintenance_routes(project_root)

    # Return all blueprints for registration
    return [
        utilities_ops_status_bp,
        utilities_ops_library_bp,
        utilities_ops_hashing_bp,
        utilities_ops_audible_bp,
        utilities_ops_maintenance_bp,
    ]
