"""
Library administration utilities - CRUD operations, imports, exports, and maintenance.

This is the coordinator module that combines all utility sub-modules:
- utilities_crud: CRUD operations (update, delete, bulk operations)
- utilities_db: Database maintenance (rescan, reimport, vacuum, export)
- utilities_ops: Async operations with progress tracking (modularized package)
  - status: Operation tracking
  - library: Add-new, rescan, reimport
  - hashing: Hash/checksum generation
  - audible: Download, sync genres/narrators
  - maintenance: Queue rebuild, cleanup, sort fields, duplicates
- utilities_conversion: Conversion monitoring
- utilities_system: Service control and application upgrades

Each sub-module defines focused functionality with smaller, testable functions.
"""

from flask import Blueprint

from .utilities_conversion import (init_conversion_routes,
                                   utilities_conversion_bp)
# Import sub-module blueprints and init functions
from .utilities_crud import init_crud_routes, utilities_crud_bp
from .utilities_db import init_db_routes, utilities_db_bp
from .utilities_ops import init_ops_routes  # Returns list of blueprints
from .utilities_system import init_system_routes, utilities_system_bp

# Main utilities blueprint - aggregates all sub-modules
utilities_bp = Blueprint("utilities", __name__)


def init_utilities_routes(db_path, project_root):
    """
    Initialize all utility routes with database path and project root.

    This function coordinates initialization of all utility sub-modules,
    allowing them to share the same database path and project root configuration.

    Args:
        db_path: Path to the SQLite database file
        project_root: Path to the project root (library directory)

    Returns:
        The main utilities blueprint with all routes registered
    """
    # Initialize each sub-module with required dependencies
    init_crud_routes(db_path)
    init_db_routes(db_path, project_root)
    init_conversion_routes(project_root)
    init_system_routes(project_root)

    # Initialize ops routes - returns list of blueprints
    ops_blueprints = init_ops_routes(db_path, project_root)

    # Register sub-module blueprints under the main utilities blueprint
    # Using empty url_prefix since all routes already include /api/ prefix
    utilities_bp.register_blueprint(utilities_crud_bp)
    utilities_bp.register_blueprint(utilities_db_bp)
    utilities_bp.register_blueprint(utilities_conversion_bp)
    utilities_bp.register_blueprint(utilities_system_bp)

    # Register all ops sub-blueprints
    for bp in ops_blueprints:
        utilities_bp.register_blueprint(bp)

    return utilities_bp
