"""
Operation status tracking endpoints.

Provides endpoints for querying and managing background operation status.
"""

from flask import Blueprint, jsonify
from operation_status import get_tracker

from ..core import FlaskResponse

utilities_ops_status_bp = Blueprint("utilities_ops_status", __name__)


def init_status_routes():
    """Initialize operation status routes."""

    @utilities_ops_status_bp.route(
        "/api/operations/status/<operation_id>", methods=["GET"]
    )
    def get_operation_status(operation_id: str) -> FlaskResponse:
        """Get status of a specific operation."""
        tracker = get_tracker()
        status = tracker.get_status(operation_id)

        if not status:
            return jsonify({"error": "Operation not found"}), 404

        return jsonify(status)

    @utilities_ops_status_bp.route("/api/operations/active", methods=["GET"])
    def get_active_operations() -> FlaskResponse:
        """Get all active (running) operations."""
        tracker = get_tracker()
        operations = tracker.get_active_operations()
        return jsonify({"operations": operations, "count": len(operations)})

    @utilities_ops_status_bp.route("/api/operations/all", methods=["GET"])
    def get_all_operations() -> FlaskResponse:
        """Get all tracked operations (including completed)."""
        tracker = get_tracker()
        operations = tracker.get_all_operations()
        return jsonify({"operations": operations, "count": len(operations)})

    @utilities_ops_status_bp.route(
        "/api/operations/cancel/<operation_id>", methods=["POST"]
    )
    def cancel_operation(operation_id: str) -> FlaskResponse:
        """Cancel an operation (sets flag, actual cancellation depends on operation)."""
        tracker = get_tracker()
        if tracker.cancel_operation(operation_id):
            return jsonify(
                {"success": True, "message": "Operation marked for cancellation"}
            )
        return jsonify({"error": "Operation not found"}), 404

    return utilities_ops_status_bp
